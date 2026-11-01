"""Approval: hold a high-risk call until a human decides, then resume the turn.

Contract: spec/contracts/approval_queue.md. Node: `approval` (control plane, kind queue).

Inside the `approval` span: persist the request, let the loop checkpoint, emit
`approval_requested` (with the exact arguments that will run) and `node_paused`, then wait.
`POST /v1/approvals/{id}` decides; expiry after `approval.timeout_s` counts as a deny with
decision `expired`. A record is decided at most once.

Restart (P3): the record, the checkpoint and the events up to `node_paused` are durable before
the turn waits, so after a restart the gateway re-attaches the turn and calls `wait` again with
the restored `approval` span (`resume_wait`). `sweep_expired` decides every pending record past
`expires_at` as `expired` and wakes its waiter; the server runs it every `approval.sweep_s`.
A graceful shutdown leaves a waiting record `pending` (the turn is detached, not cancelled).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.events import TurnCancelled, iso, parse_iso, uuid7
from harnesswalk.nodes import node, register
from harnesswalk.types import ToolCall, TurnContext

register("approval", __name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    action_class TEXT NOT NULL,
    args_preview TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    decision_reason TEXT
);
"""


class ApprovalNotFound(Exception):
    pass


class ApprovalAlreadyDecided(Exception):
    pass


@dataclass
class ApprovalDecision:
    approval_id: str
    decision: str  # approve | deny | expired
    decided_by: str | None
    reason: str | None = None


class ApprovalQueue:
    def __init__(self, path: str, *, timeout_s: float = 900, show_args: bool = True) -> None:
        self.db = Database(path, SCHEMA)
        self.timeout_s = timeout_s
        self.show_args = show_args
        self._waiters: dict[str, asyncio.Future[ApprovalDecision]] = {}
        self._waiter_turns: dict[str, str] = {}  # approval_id -> turn_id, for cancel

    def get(self, approval_id: str, tenant_id: str) -> dict[str, Any]:
        row = self.db.one(
            "SELECT * FROM approvals WHERE approval_id = ? AND tenant_id = ?",
            (approval_id, tenant_id),
        )
        if row is None:
            raise ApprovalNotFound(approval_id)
        out = dict(row)
        out["args_preview"] = json.loads(out["args_preview"])
        return out

    def pending(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT approval_id FROM approvals WHERE tenant_id = ? AND status = 'pending'",
            (tenant_id,),
        )
        return [self.get(r["approval_id"], tenant_id) for r in rows]

    def decide(
        self, approval_id: str, tenant_id: str, decider: str, decision: str, reason: str | None
    ) -> dict[str, Any]:
        record = self.get(approval_id, tenant_id)  # raises ApprovalNotFound for other tenants
        if record["status"] != "pending":
            raise ApprovalAlreadyDecided(approval_id)
        decided_at = self._record(approval_id, decision, decider, reason)
        waiter = self._waiters.get(approval_id)
        if waiter and not waiter.done():
            waiter.set_result(ApprovalDecision(approval_id, decision, decider, reason))
        return {
            "approval_id": approval_id,
            "turn_id": record["turn_id"],
            "decision": decision,
            "decided_by": decider,
            "decided_at": decided_at,
        }

    def _record(self, approval_id: str, decision: str, decider: str, reason: str | None) -> str:
        """Write the decision once. Raises ApprovalAlreadyDecided when it is not pending."""
        status = {"approve": "approved", "deny": "denied", "expired": "expired"}[decision]
        decided_at = iso(time.time())
        cursor = self.db.execute(
            "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, decision_reason = ?"
            " WHERE approval_id = ? AND status = 'pending'",
            (status, decider, decided_at, reason, approval_id),
        )
        if cursor.rowcount == 0:  # someone decided between the read and the write
            raise ApprovalAlreadyDecided(approval_id)
        return decided_at

    def cancel_turn(self, turn_id: str) -> int:
        """Expire every approval this turn waits on, then wake it with TurnCancelled.

        The record becomes `expired` (decided by `system`) right here, before the turn's task
        runs again, so a human decision that arrives after the cancel is a 409 and never
        leaves an `approved` record on a cancelled turn. The waiting span then closes with
        `error` `cancelled` (approval_queue.md, "Turn cancelled while pending"). Returns how
        many approvals were woken.
        """
        woken = 0
        for approval_id, owner in list(self._waiter_turns.items()):
            waiter = self._waiters.get(approval_id)
            if owner == turn_id and waiter is not None and not waiter.done():
                with contextlib.suppress(ApprovalAlreadyDecided):
                    self._record(approval_id, "expired", "system", "turn cancelled")
                waiter.set_exception(TurnCancelled())
                woken += 1
        return woken

    async def request(
        self,
        ctx: TurnContext,
        call: ToolCall,
        tool_name: str,
        action_class: str,
        reason: str,
        before_pause: Any = None,
    ) -> ApprovalDecision:
        async with node(ctx, "approval") as span:
            if ctx.interrupted:  # cancelled on the way here: ask nobody, stop now
                raise TurnCancelled()
            approval_id = "apr_" + uuid7().replace("-", "")[:20]
            now = time.time()
            expires_at = iso(now + self.timeout_s)
            preview = call.arguments if self.show_args else {}
            self.db.execute(
                "INSERT INTO approvals (approval_id, tenant_id, turn_id, session_id, tool,"
                " action_class, args_preview, reason, status, requested_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    approval_id,
                    ctx.tenant_id,
                    ctx.turn_id,
                    ctx.session_id,
                    tool_name,
                    action_class,
                    json.dumps(preview),
                    reason,
                    iso(now),
                    expires_at,
                ),
            )
            if before_pause:
                before_pause()  # the loop checkpoints with the pending call
            # Wait from here on, so a decision that arrives right after the event is not lost.
            self._register(approval_id, ctx.turn_id)
            ctx.emit.point(
                "approval_requested",
                "approval",
                "waiting",
                {
                    "approval_id": approval_id,
                    "tool": tool_name,
                    "action_class": action_class,
                    "args_preview": preview,
                    "reason": reason,
                    "expires_at": expires_at,
                },
            )
            ctx.emit.point(
                "node_paused",
                "approval",
                "waiting",
                {"reason": "approval", "approval_id": approval_id, "mode": "approval"},
            )
            decided = await self.wait(ctx, span, approval_id, self.timeout_s)
        return decided

    async def wait(
        self, ctx: TurnContext, span: Any, approval_id: str, timeout_s: float
    ) -> ApprovalDecision:
        """Wait for the decision inside the open `approval` span and report it.

        The waiter must already be registered (`request` does it before the events; a resumed
        turn does it through `resume_wait`). Emits `approval_decided` and `node_resumed` and
        fills the span's exit summary; the caller closes the span.
        """
        waiter = self._waiters[approval_id]
        started = time.monotonic()
        try:
            decided = await asyncio.wait_for(asyncio.shield(waiter), max(0.0, timeout_s))
        except TimeoutError:
            with contextlib.suppress(ApprovalAlreadyDecided):  # a human may win the race
                self.decide(approval_id, ctx.tenant_id, "system", "expired", "approval timed out")
            decided = waiter.result()
        except asyncio.CancelledError:
            if not ctx.detached:  # a shutdown leaves the record pending for the next boot
                with contextlib.suppress(ApprovalAlreadyDecided):
                    self.decide(approval_id, ctx.tenant_id, "system", "expired", "turn cancelled")
            raise
        finally:
            waited = time.monotonic() - started
            ctx.emit.paused_s += waited
            self._waiters.pop(approval_id, None)
            self._waiter_turns.pop(approval_id, None)
        point: dict[str, Any] = {"approval_id": approval_id, "decision": decided.decision}
        if decided.decided_by:
            point["decided_by"] = decided.decided_by
        if decided.reason:
            point["reason"] = decided.reason[:200]
        ctx.emit.point("approval_decided", "approval", "done", point)
        ctx.emit.point(
            "node_resumed",
            "approval",
            "active",
            {"reason": "approval", "approval_id": approval_id, "mode": "approval"},
        )
        span.set(approval_id=approval_id, decision=decided.decision, waited_ms=round(waited * 1000))
        return decided

    def _register(self, approval_id: str, turn_id: str) -> asyncio.Future[ApprovalDecision]:
        waiter: asyncio.Future[ApprovalDecision] = asyncio.get_running_loop().create_future()
        self._waiters[approval_id] = waiter
        self._waiter_turns[approval_id] = turn_id
        return waiter

    def waiting_turns(self) -> set[str]:
        """Turns that wait on an approval in this process right now."""
        return {t for a, t in self._waiter_turns.items() if not self._waiters[a].done()}

    async def resume_wait(
        self, ctx: TurnContext, span: Any, record: dict[str, Any]
    ) -> ApprovalDecision:
        """A turn re-attached after a restart: wait again on its persisted record.

        A record that is no longer pending (expired by the sweeper meanwhile) resolves at once
        with its decision. Otherwise the wait lasts until `expires_at`.
        """
        approval_id = record["approval_id"]
        waiter = self._register(approval_id, ctx.turn_id)
        # Read it again now that the waiter is in place: a decision made between recovery and
        # this line is in the database, and one made after it wakes the waiter.
        record = self.get(approval_id, ctx.tenant_id)
        status = record["status"]
        if ctx.interrupted and status == "pending":  # cancelled before this task ran
            with contextlib.suppress(ApprovalAlreadyDecided):
                self._record(approval_id, "expired", "system", "turn cancelled")
            waiter.set_exception(TurnCancelled())
        elif status != "pending":
            decision = {"approved": "approve", "denied": "deny"}.get(status, "expired")
            waiter.set_result(
                ApprovalDecision(
                    approval_id, decision, record.get("decided_by"), record.get("decision_reason")
                )
            )
        left = parse_iso(record["expires_at"]) - time.time()
        return await self.wait(ctx, span, approval_id, left)

    def sweep_expired(self, now: float | None = None) -> int:
        """Decide every pending record past `expires_at` as `expired`. Returns how many."""
        stamp = iso(now if now is not None else time.time())
        rows = self.db.query(
            "SELECT approval_id, tenant_id FROM approvals WHERE status = 'pending'"
            " AND expires_at < ?",
            (stamp,),
        )
        expired = 0
        for r in rows:
            with contextlib.suppress(ApprovalAlreadyDecided, ApprovalNotFound):
                self.decide(
                    r["approval_id"], r["tenant_id"], "system", "expired", "approval expired"
                )
                expired += 1
        return expired

    def pending_all(self) -> list[dict[str, Any]]:
        """Every pending record, any tenant. Recovery at boot only."""
        rows = self.db.query(
            "SELECT approval_id, tenant_id FROM approvals WHERE status = 'pending'"
        )
        return [self.get(r["approval_id"], r["tenant_id"]) for r in rows]

    def for_turn(self, tenant_id: str, turn_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT approval_id FROM approvals WHERE tenant_id = ? AND turn_id = ?"
            " ORDER BY requested_at",
            (tenant_id, turn_id),
        )
        return [self.get(r["approval_id"], tenant_id) for r in rows]
