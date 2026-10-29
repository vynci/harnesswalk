"""Tools: resolve, validate, check, execute. Every call takes the same path.

Contract: spec/contracts/tool_registry.md ("The invoke path"). Node: `tools` (execution plane).

    registry.get     unknown tool          -> error observation, never executes
    validate         invalid arguments     -> error observation
    policy.check     on every call, no cache
    audit            when policy says the decision must be recorded
    approval         when policy says needs_approval (the turn pauses), then audit again
    sandbox          executes with a timeout; skipped (and shown so) when denied

Whatever happens, the loop gets an `Observation`. The `tools` span closes with `error` only
when the harness itself failed, for example the audit store was unavailable.
"""

from __future__ import annotations

from collections.abc import Callable

import jsonschema

from harnesswalk.control.approval import ApprovalQueue
from harnesswalk.control.audit import AuditEntry, AuditLog, args_digest, record
from harnesswalk.control.policy import Policy
from harnesswalk.execution.registry import ToolRegistry, run_registry
from harnesswalk.execution.sandbox import Sandbox, run_sandbox, skip_sandbox
from harnesswalk.nodes import node, register
from harnesswalk.types import HarnessError, Observation, ToolCall, TurnContext

register("tools", __name__)


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
        actor = f"user:{ctx.user_id or ctx.tenant_id}"
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
                ),
            )
        if decision.decision == "deny":
            await skip_sandbox(ctx, spec, call, "denied by policy")
            return Observation(
                call.call_id, "denied", f"Denied by policy ({decision.rule}): {decision.reason}."
            )

        if decision.decision == "needs_approval":
            ctx.emit.edge("policy_to_approval")
            decided = await self.approvals.request(
                ctx, call, spec.name, spec.action_class, decision.reason, before_pause
            )
            ctx.emit.edge("approval_to_audit")
            await record(
                ctx,
                self.audit,
                AuditEntry(
                    action="approval_decision",
                    actor=decided.decided_by or actor,
                    turn_id=ctx.turn_id,
                    trace_id=ctx.trace_id,
                    tool=spec.name,
                    action_class=spec.action_class,
                    decision=decided.decision,
                    approval_id=decided.approval_id,
                    args_digest=args_digest(call.arguments),
                ),
            )
            ctx.emit.edge("approval_to_tools")
            if decided.decision != "approve":
                await skip_sandbox(ctx, spec, call, f"approval {decided.decision}")
                return Observation(
                    call.call_id,
                    "denied",
                    f"The action was not approved (decision: {decided.decision}).",
                )

        result = await run_sandbox(ctx, self.sandbox, spec, call)
        span.set(executed=True)
        content = result.content
        if len(content) > self.max_observation_chars:
            content = content[: self.max_observation_chars] + " [truncated]"
        return Observation(call.call_id, result.outcome, content)  # type: ignore[arg-type]
