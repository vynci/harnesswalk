"""Tools: resolve, validate, check, execute. Every call takes the same path.

Contract: spec/contracts/tool_registry.md ("The invoke path"). Node: `tools` (execution plane).

    registry.get     unknown tool          -> error observation, never executes
    validate         invalid arguments     -> error observation
    policy.check     on every call, no cache; a spent tool-call budget is a deny that stops
                     the loop (observation `limit: budget_tool_calls`)
    audit            when policy says the decision must be recorded
    approval         when policy says needs_approval (the turn pauses), then audit again;
                     approved but cancelled meanwhile -> audit says so, the turn stops
    sandbox          executes with a timeout; skipped (and shown so) when denied
    audit            `tool_executed` for every non-read call that ran: executed, outcome, and
                     the before and after state for writes (P3)

Whatever happens, the loop gets an `Observation`. The `tools` span closes with `error` only
when the harness itself failed, for example the audit store was unavailable.

`finish_after_approval` is the tail of the same path from the approval decision on; a turn
resumed after a restart enters there (spec/contracts/approval_queue.md, P3 additions).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import jsonschema

from harnesswalk.control.approval import ApprovalDecision, ApprovalQueue
from harnesswalk.control.audit import AuditEntry, AuditLog, args_digest, record
from harnesswalk.control.pii import PiiDetector
from harnesswalk.control.policy import Decision, Policy
from harnesswalk.events import Span, TurnCancelled
from harnesswalk.execution.registry import ToolRegistry, ToolSpec, run_registry
from harnesswalk.execution.sandbox import Sandbox, run_sandbox, skip_sandbox
from harnesswalk.nodes import node, register
from harnesswalk.types import HarnessError, Observation, ToolCall, TurnContext

register("tools", __name__)

NOT_RUN_NOTE = "approved, not run: the turn was cancelled"
SNAPSHOT_CHARS = 1024
_scrubber = PiiDetector()


def snapshot(content: str | None) -> Any:
    """A small, PII-redacted value for an audit record's before or after."""
    if content is None:
        return None
    try:
        value: Any = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        value = content
    value = _scrubber.scrub(value)
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(text) > SNAPSHOT_CHARS:
        return {"truncated": text[: SNAPSHOT_CHARS - 40]}
    return value


class Tools:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: Policy,
        audit: AuditLog,
        approvals: ApprovalQueue,
        sandbox: Sandbox,
        *,
        max_observation_chars: int = 4000,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.audit = audit
        self.approvals = approvals
        self.sandbox = sandbox
        self.max_observation_chars = max_observation_chars

    def schemas(self, mode: str):
        return [s.schema() for s in self.registry.list(mode, self.policy.allow_tools)]

    def action_class(self, name: str) -> str | None:
        spec = self.registry.get(name)
        return spec.action_class if spec else None

    async def invoke(
        self, ctx: TurnContext, call: ToolCall, before_pause: Callable[[], None] | None = None
    ) -> Observation:
        async with node(ctx, "tools", tool=call.name, call_id=call.call_id) as span:
            span.set(tool=call.name, call_id=call.call_id, executed=False)
            try:
                observation = await self._invoke(ctx, call, span, before_pause)
            except HarnessError as exc:
                if exc.code != "store_unavailable":
                    raise
                # Fail closed: the tool does not run and the loop is told so.
                ctx.emit.fail(span, exc.code, exc.message, {"outcome": "denied"})
                return Observation(
                    call.call_id, "denied", "The action was not run: audit is unavailable."
                )
            span.set(outcome=observation.outcome)
        return observation

    def _actor(self, ctx: TurnContext) -> str:
        return f"user:{ctx.user_id or ctx.tenant_id}"

    async def _invoke(self, ctx, call, span, before_pause) -> Observation:
        spec = await run_registry(ctx, self.registry, call.name)
        if spec is None:
            return Observation(call.call_id, "error", f"Unknown tool {call.name!r}.")
        try:
            jsonschema.validate(call.arguments, spec.parameters)
        except jsonschema.ValidationError as exc:
            return Observation(call.call_id, "error", f"Invalid arguments: {exc.message}"[:500])

        ctx.emit.edge("tools_to_policy")
        decision = await self.policy.check(ctx, call, spec)
        if decision.audit:
            ctx.emit.edge("policy_to_audit")
            await record(
                ctx,
                self.audit,
                AuditEntry(
                    action="policy_decision",
                    actor="agent",
                    turn_id=ctx.turn_id,
                    trace_id=ctx.trace_id,
                    tool=spec.name,
                    action_class=spec.action_class,
                    decision=decision.decision,
                    rule=decision.rule,
                    args_digest=args_digest(call.arguments),
                    executed=False if decision.decision == "deny" else None,
                    extra={"budget": decision.budget} if decision.budget else {},
                ),
            )
        if decision.decision == "deny":
            await skip_sandbox(ctx, spec, call, "denied by policy")
            return Observation(
                call.call_id,
                "denied",
                f"Denied by policy ({decision.rule}): {decision.reason}.",
                limit=decision.limit,
            )

        if decision.decision == "needs_approval":
            ctx.emit.edge("policy_to_approval")
            decided = await self.approvals.request(
                ctx, call, spec.name, spec.action_class, decision.reason, before_pause
            )
            return await self.after_approval(ctx, call, spec, decided, decision, span)
        return await self._execute(ctx, call, spec, decision, span, approver=None)

    async def after_approval(
        self,
        ctx: TurnContext,
        call: ToolCall,
        spec: ToolSpec,
        decided: ApprovalDecision,
        decision: Decision | None,
        span: Span,
    ) -> Observation:
        """From the approval decision on: audit it, then run the call or explain why not."""
        # Approved, then cancelled before the turn woke up: record the approval, run nothing.
        not_run = decided.decision == "approve" and ctx.interrupted
        ctx.emit.edge("approval_to_audit")
        await record(
            ctx,
            self.audit,
            AuditEntry(
                action="approval_decision",
                actor=decided.decided_by or self._actor(ctx),
                approver=decided.decided_by,
                turn_id=ctx.turn_id,
                trace_id=ctx.trace_id,
                tool=spec.name,
                action_class=spec.action_class,
                decision=decided.decision,
                rule=decision.rule if decision else None,
                approval_id=decided.approval_id,
                args_digest=args_digest(call.arguments),
                executed=False if (not_run or decided.decision != "approve") else None,
                extra={"executed": False, "note": NOT_RUN_NOTE} if not_run else {},
            ),
        )
        if not_run:
            raise TurnCancelled()
        ctx.emit.edge("approval_to_tools")
        if decided.decision != "approve":
            await skip_sandbox(ctx, spec, call, f"approval {decided.decision}")
            return Observation(
                call.call_id,
                "denied",
                f"The action was not approved (decision: {decided.decision}).",
            )
        return await self._execute(ctx, call, spec, decision, span, approver=decided.decided_by)

    async def finish_after_approval(
        self,
        ctx: TurnContext,
        span: Span,
        call: ToolCall,
        decided: ApprovalDecision,
    ) -> Observation:
        """A resumed turn: close the restored `tools` span the way `invoke` would have."""
        spec = self.registry.get(call.name)
        span.set(tool=call.name, call_id=call.call_id, executed=False)
        try:
            if spec is None:
                observation = Observation(call.call_id, "error", f"Unknown tool {call.name!r}.")
            else:
                observation = await self.after_approval(ctx, call, spec, decided, None, span)
        except HarnessError as exc:
            if exc.code != "store_unavailable":
                raise
            ctx.emit.fail(span, exc.code, exc.message, {"outcome": "denied"})
            return Observation(
                call.call_id, "denied", "The action was not run: audit is unavailable."
            )
        span.set(outcome=observation.outcome)
        ctx.emit.exit(span)
        return observation

    async def _execute(
        self,
        ctx: TurnContext,
        call: ToolCall,
        spec: ToolSpec,
        decision: Decision | None,
        span: Span,
        approver: str | None,
    ) -> Observation:
        before = None
        if spec.audit_before and spec.action_class != "read":
            reader = self.registry.get(spec.audit_before)
            if reader is not None:
                snap_call = ToolCall(call.call_id + ".before", reader.name, call.arguments)
                snap = await self.sandbox.execute(ctx, reader, snap_call)
                before = snapshot(snap.content if snap.outcome == "ok" else None)
        network = spec.action_class == "external"  # policy allowed it, or we would not be here
        result = await run_sandbox(ctx, self.sandbox, spec, call, network=network)
        span.set(executed=True)
        if ctx.budget is not None:
            ctx.budget.tool_calls += 1
        # A memory.write call only proposes; the memory writer records the write itself.
        if spec.action_class not in ("read", "memory.write"):
            await record(
                ctx,
                self.audit,
                AuditEntry(
                    action="tool_executed",
                    actor="agent",
                    approver=approver,
                    turn_id=ctx.turn_id,
                    trace_id=ctx.trace_id,
                    tool=spec.name,
                    action_class=spec.action_class,
                    decision="executed",
                    rule=decision.rule if decision else None,
                    args_digest=args_digest(call.arguments),
                    executed=True,
                    outcome=result.outcome,
                    before=before,
                    after=snapshot(result.content) if result.outcome == "ok" else None,
                ),
            )
        content = result.content
        if len(content) > self.max_observation_chars:
            content = content[: self.max_observation_chars] + " [truncated]"
        return Observation(call.call_id, result.outcome, content)  # type: ignore[arg-type]
