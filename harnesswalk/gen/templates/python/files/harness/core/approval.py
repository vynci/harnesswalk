"""Approval: hold a risky call until a human approves or denies it, then resume.

Contract: spec/contracts/approval_queue.md. Node: `approval` (on with policy and audit).
The request and the loop checkpoint are written before the turn pauses, so a restart finds
the same turn waiting and a later decision resumes it (`gateway.recover`).

Lesson: there is no safe middle ground between "never allowed" and "always allowed" without
it. The card shows the exact arguments that will run, a decision is taken once, and expiry
counts as a deny. Keep it as long as any tool can do something you cannot undo.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from harness.core.events import Cancelled, iso


def request(h: Any, turn: Any, call: dict[str, Any], spec: dict[str, Any],
            decision: dict[str, Any], resume: dict[str, Any] | None) -> str:  # fmt: skip
    with turn.span("approval", resume=bool(resume)) as span:
        if resume:
            rec = h.db.one("approvals", approval_id=resume["approval_id"])
        else:
            timeout = float(h.settings.get("approval.timeout_s", 900))
            rec = {"approval_id": "apr_" + uuid.uuid4().hex[:16], "tenant": turn.ids["tenant_id"],
                   "turn": turn.ids["turn_id"], "tool": call["name"],
                   "action_class": spec["action_class"], "args_preview": call.get("arguments"),
                   "reason": decision.get("reason", ""), "status": "pending",
                   "requested_at": iso(time.time()), "expires": time.time() + timeout,
                   "expires_at": iso(time.time() + timeout)}  # fmt: skip
            h.db.insert("approvals", rec)
            turn.emit("approval_requested", "approval", "waiting", summary={
                "approval_id": rec["approval_id"], "tool": rec["tool"],
                "action_class": rec["action_class"], "args_preview": rec["args_preview"],
                "reason": rec["reason"], "expires_at": rec["expires_at"]})  # fmt: skip
            turn.data["checkpoint"](approval_id=rec["approval_id"], decision=decision)
            auto = turn.data.get("auto_approval")  # an eval run decides with the case's answer
            if auto:
                decide(h, rec["approval_id"], turn.ids["tenant_id"], auto, "eval")
        aid, t0 = rec["approval_id"], time.monotonic()

        def ready() -> bool:
            return h.db.one("approvals", approval_id=aid)["status"] != "pending"

        try:
            turn.pause("approval", "approval", aid, timeout=max(0.0, rec["expires"] - time.time()),
                       ready=ready, announce=not resume)  # fmt: skip
        except Cancelled:
            h.db.update("approvals", {"approval_id": aid, "status": "pending"},
                        {"status": "expired", "decided_by": "system"})  # fmt: skip
            raise
        h.db.update("approvals", {"approval_id": aid, "status": "pending"},
                    {"status": "expired", "decided_by": "system"})  # fmt: skip
        rec = h.db.one("approvals", approval_id=aid)
        answer = {"approved": "approve", "denied": "deny"}.get(rec["status"], "expired")
        turn.emit("approval_decided", "approval", "done", summary={
            "approval_id": aid, "decision": answer, "decided_by": rec.get("decided_by")})  # fmt: skip
        turn.resumed("approval", "approval", "approval", aid)
        turn.data["approver"] = rec.get("decided_by")
        span.set(approval_id=aid, decision=answer, waited_ms=int((time.monotonic() - t0) * 1000))
    return answer


def decide(h: Any, approval_id: str, tenant: str, decision: str, by: str) -> dict[str, Any]:
    """Decide once. Returns the record, raises KeyError (unknown) or ValueError (decided)."""
    rec = h.db.one("approvals", approval_id=approval_id)
    if rec is None or rec["tenant"] != tenant:
        raise KeyError(approval_id)
    status = {"approve": "approved", "deny": "denied"}[decision]
    if not h.db.update("approvals", {"approval_id": approval_id, "status": "pending"},
                       {"status": status, "decided_by": by, "decided_at": iso(time.time())}):  # fmt: skip
        raise ValueError("approval_already_decided")
    turn = h.live.get(rec["turn"])
    if turn is not None:
        turn.resume("approval")
    return h.db.one("approvals", approval_id=approval_id)
