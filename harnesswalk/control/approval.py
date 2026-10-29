"""Approval: hold a high-risk call until a human decides, then resume the turn.

Contract: spec/contracts/approval_queue.md. Node: `approval` (control plane, kind queue).

Inside the `approval` span: persist the request, let the loop checkpoint, emit
`approval_requested` (with the exact arguments that will run) and `node_paused`, then wait.
`POST /v1/approvals/{id}` decides; expiry after `approval.timeout_s` counts as a deny with
decision `expired`. A record is decided at most once.

What P1 does not do yet (P3): resume a pending approval after a process restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.events import iso, uuid7
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
        status = {"approve": "approved", "deny": "denied", "expired": "expired"}[decision]
        decided_at = iso(time.time())
        cursor = self.db.execute(
            "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?, decision_reason = ?"
            " WHERE approval_id = ? AND status = 'pending'",
            (status, decider, decided_at, reason, approval_id),
        )
        if cursor.rowcount == 0:  # someone decided between the read and the write
            raise ApprovalAlreadyDecided(approval_id)
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
            waiter: asyncio.Future[ApprovalDecision] = asyncio.get_running_loop().create_future()
            self._waiters[approval_id] = waiter
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
                {"reason": "approval", "approval_id": approval_id},
            )
            started = time.monotonic()
            try:
                decided = await asyncio.wait_for(asyncio.shield(waiter), self.timeout_s)
            except TimeoutError:
                with contextlib.suppress(ApprovalAlreadyDecided):  # a human may win the race
                    self.decide(
                        approval_id, ctx.tenant_id, "system", "expired", "approval timed out"
                    )
                decided = waiter.result()
            except asyncio.CancelledError:
                with contextlib.suppress(ApprovalAlreadyDecided):
                    self.decide(approval_id, ctx.tenant_id, "system", "expired", "turn cancelled")
                raise
            finally:
                waited = time.monotonic() - started
                ctx.emit.paused_s += waited
                self._waiters.pop(approval_id, None)
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
                {"reason": "approval", "approval_id": approval_id},
            )
            span.set(
                approval_id=approval_id, decision=decided.decision, waited_ms=round(waited * 1000)
            )
        return decided
