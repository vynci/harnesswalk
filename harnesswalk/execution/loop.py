"""Loop: plan, act, observe, adjust, and stop.

Contract: spec/contracts/loop.md. Node: `loop` (execution plane, kind code).

    step = 0
    loop:
      exit "interrupted"   if someone interrupted the turn (checked first, so a cancel wins)
      exit "max_steps"     if step + 1 > max_steps
      exit "budget_tokens" if tokens used >= budget_tokens
      exit "budget_usd"    if a dollar budget is set and dollars used >= it
      exit "timeout"       if active wall clock >= timeout_s (paused time excluded)
      step += 1
      ask the model layer (tools enabled)
      no tool calls -> exit "answer"
      for each tool call, in order:
        exit "interrupted" if someone interrupted the turn meanwhile
        exit "stuck" if this exact (name, arguments) already ran stuck_repeats times
        run it through `tools` (policy, approval and sandbox happen in there)
        exit "budget_tool_calls" if policy denied it on the tenant's tool-call budget
      checkpoint the step to the session store

After `max_steps` or `stuck` the loop makes one final model call with tools disabled (when
`final_call_on_exit`). After a budget, timeout or interrupt exit it does not call the model
again and answers with a fixed notice. A denied or failed tool call is an observation, never
an exit by itself (a spent budget is the one denial that ends the loop).

The token and dollar budgets are the smallest of the loop's own, the tenant's per turn, and
what is left of the tenant's hour (`policy.budgets`, P3); the exit summary names the source.

Each model call carries a risk: `high` once any tool call of the turn has a class other than
`read`, so the router can send consequential steps to a stronger model.

`resume` continues a turn from its checkpoint after a restart: the pending call's observation
arrives from the resumed `tools` span, the step's remaining calls run, and the loop goes on.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from harnesswalk.context.stores.session import SessionStore
from harnesswalk.events import Span
from harnesswalk.execution.model_layer import ModelLayer, ModelRequest
from harnesswalk.execution.providers import ToolSchema
from harnesswalk.execution.tools import Tools
from harnesswalk.nodes import fail_span, node, register
from harnesswalk.types import FinalAnswer, HarnessError, Message, ToolCall, TurnContext, Usage

register("loop", __name__)

DEFAULT_NOTICES = {
    "budget_tokens": "I stopped because this turn used its token budget.",
    "budget_usd": "I stopped because this turn used its cost budget.",
    "budget_tool_calls": "I stopped because this turn used its tool-call budget.",
    "timeout": "I stopped because this turn ran out of time.",
    "interrupted": "I stopped because the turn was interrupted.",
    "max_steps": "I stopped after the maximum number of steps.",
    "stuck": "I stopped because I was repeating the same tool call.",
}

NOT_RUN = {
    "stuck": "Not run: the same call was repeated without progress.",
    "interrupted": "Not run: the turn was cancelled.",
    "budget_tool_calls": "Not run: the tool-call budget is spent.",
}


@dataclass
class Limits:
    max_steps: int = 8
    budget_tokens: int | float | None = 16000
    budget_usd: float | None = 0.05  # None (or 0 in config) disables the dollar check
    timeout_s: float = 120
    stuck_repeats: int = 3
    final_call_on_exit: bool = True
    budget_source: dict[str, str] = field(default_factory=dict)  # kind -> where it came from

    @classmethod
    def from_config(cls, cfg: dict[str, Any], budget: Any = None) -> Limits:
        names = set(cls.__dataclass_fields__) - {"budget_source"}
        limits = cls(**{k: v for k, v in cfg.items() if k in names})
        if not limits.budget_usd:
            limits.budget_usd = None
        if budget is not None:  # the tenant's budgets narrow the loop's own (policy.md P3)
            for kind in ("tokens", "usd"):
                value, source = budget.loop_limit(kind, getattr(limits, f"budget_{kind}"))
                setattr(limits, f"budget_{kind}", value)
                if source and source != f"loop.budget_{kind}":
                    limits.budget_source[kind] = source
        return limits


@dataclass
class LoopResult:
    answer: FinalAnswer
    steps: int
    tool_calls: int
    exit_reason: str
    usage: Usage
    proposals: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LoopState:
    """Everything the loop carries between steps; a checkpoint restores it."""

    messages: list[Message]
    usage: Usage = field(default_factory=Usage)
    step: int = 0
    tool_calls: int = 0
    seen: Counter[str] = field(default_factory=Counter)
    risk: str = "low"


def call_key(name: str, arguments: dict[str, Any]) -> str:
    return name + json.dumps(arguments, sort_keys=True, separators=(",", ":"))


class Loop:
    def __init__(
        self,
        model_layer: ModelLayer,
        tools: Tools,
        session: SessionStore,
        notices: dict[str, str] | None = None,
    ) -> None:
        self.model_layer = model_layer
        self.tools = tools
        self.session = session
        self.notices = {**DEFAULT_NOTICES, **(notices or {})}

    def _exit_before_step(
        self, ctx: TurnContext, limits: Limits, step: int, usage: Usage
    ) -> str | None:
        # A cancel wins over every limit, so it never leads to the tools-off final call.
        if ctx.interrupted:
            return "interrupted"
        if step + 1 > limits.max_steps:
            return "max_steps"
        if limits.budget_tokens is not None and usage.tokens >= limits.budget_tokens:
            return "budget_tokens"
        if limits.budget_usd is not None and usage.usd >= limits.budget_usd:
            return "budget_usd"
        if ctx.active_seconds() >= limits.timeout_s:
            return "timeout"
        return None

    async def _model(self, ctx, state: LoopState, tools, step_kind, limits, api_key):
        ctx.emit.edge("loop_to_model_layer")
        cost = "normal"
        if limits.budget_usd and state.usage.usd >= limits.budget_usd * self.model_layer.tight:
            cost = "low"
        try:
            response = await self.model_layer.complete(
                ctx,
                ModelRequest(
                    messages=list(state.messages),
                    tools=tools,
                    step=state.step if step_kind != "final" else state.step + 1,
                    step_kind=step_kind,
                    risk=state.risk,
                    cost=cost,
                ),
                api_key,
            )
        except HarnessError as exc:
            # The model layer already closed with its own code; the loop reports provider_error.
            raise HarnessError("provider_error", exc.message) from exc
        ctx.emit.edge("model_layer_to_loop")
        return response

    async def run(
        self,
        ctx: TurnContext,
        messages: list[Message],
        tools: list[ToolSchema],
        limits: Limits,
        api_key: str | None = None,
    ) -> LoopResult:
        enter: dict[str, Any] = {
            "max_steps": limits.max_steps,
            "budget_usd": limits.budget_usd or 0,
        }
        if limits.budget_tokens is not None:
            enter["budget_tokens"] = limits.budget_tokens
        if limits.budget_source:
            enter["budget_source"] = limits.budget_source
        async with node(ctx, "loop", **enter) as span:
            result = await self._drive(ctx, span, LoopState(list(messages)), tools, limits, api_key)
        return result

    async def resume(
        self,
        ctx: TurnContext,
        span: Span,
        checkpoint: dict[str, Any],
        observation: Any,
        tools: list[ToolSchema],
        limits: Limits,
    ) -> LoopResult:
        """Continue a turn restored after a restart, inside its restored `loop` span."""
        messages = [Message.from_dict(m) for m in checkpoint["messages"]]
        usage = Usage(**checkpoint.get("usage", {}))
        state = LoopState(messages, usage, int(checkpoint["step"]))
        for m in messages:
            for c in m.tool_calls:
                state.seen[call_key(c.name, c.arguments)] += 1
                if self.tools.action_class(c.name) not in (None, "read"):
                    state.risk = "high"
        state.tool_calls = sum(1 for m in messages if m.role == "tool")
        pending = ToolCall(**checkpoint["pending_call"])
        state.messages.append(
            Message("tool", observation.content, name=pending.name, tool_call_id=pending.call_id)
        )
        state.tool_calls += 1
        assistant = next(m for m in reversed(messages) if m.role == "assistant")
        ids = [c.call_id for c in assistant.tool_calls]
        rest = (
            assistant.tool_calls[ids.index(pending.call_id) + 1 :] if pending.call_id in ids else []
        )
        exit_reason = observation.limit
        if exit_reason:
            self._not_run(state, rest, exit_reason)
        else:
            exit_reason = await self._run_calls(ctx, state, rest, limits)
        self._checkpoint(ctx, state)
        try:
            result = await self._drive(ctx, span, state, tools, limits, None, exit_reason)
        except BaseException as exc:
            fail_span(ctx, span, exc)
            raise
        if not span.closed:
            ctx.emit.exit(span)
        return result

    def _not_run(self, state: LoopState, calls: list[ToolCall], reason: str) -> None:
        for skipped in calls:  # every call needs a result
            state.messages.append(
                Message("tool", NOT_RUN[reason], name=skipped.name, tool_call_id=skipped.call_id)
            )

    async def _run_calls(
        self, ctx: TurnContext, state: LoopState, calls: list[ToolCall], limits: Limits
    ) -> str | None:
        for i, call in enumerate(calls):
            key = call_key(call.name, call.arguments)
            exit_reason = None
            if ctx.interrupted:  # a cancel arrived during the model call
                exit_reason = "interrupted"
            elif state.seen[key] >= limits.stuck_repeats:
                exit_reason = "stuck"
            if exit_reason:
                self._not_run(state, calls[i:], exit_reason)
                return exit_reason
            state.seen[key] += 1
            if self.tools.action_class(call.name) not in (None, "read"):
                state.risk = "high"

            def before_pause(call=call) -> None:
                self._checkpoint(ctx, state, pending=call)

            ctx.emit.edge("loop_to_tools")
            observation = await self.tools.invoke(ctx, call, before_pause)
            ctx.emit.edge("tools_to_loop")
            state.tool_calls += 1
            state.messages.append(
                Message("tool", observation.content, name=call.name, tool_call_id=call.call_id)
            )
            if observation.limit:
                self._not_run(state, calls[i + 1 :], observation.limit)
                return observation.limit
        return None

    async def _drive(
        self,
        ctx: TurnContext,
        span: Span,
        state: LoopState,
        tools: list[ToolSchema],
        limits: Limits,
        api_key: str | None,
        exit_reason: str | None = None,
    ) -> LoopResult:
        text = None
        while exit_reason is None:
            exit_reason = self._exit_before_step(ctx, limits, state.step, state.usage)
            if exit_reason:
                break
            state.step += 1
            kind = (
                "first"
                if state.step == 1
                else ("after_tool" if state.messages[-1].role == "tool" else "next")
            )
            response = await self._model(ctx, state, tools, kind, limits, api_key)
            state.usage.add(response.usage)
            state.messages.append(
                Message("assistant", response.text or "", tool_calls=response.tool_calls)
            )
            if not response.tool_calls:
                exit_reason, text = "answer", response.text or ""
                self._checkpoint(ctx, state)
                break
            exit_reason = await self._run_calls(ctx, state, response.tool_calls, limits)
            self._checkpoint(ctx, state)

        if ctx.interrupted:  # never call the model again once the turn is cancelled
            exit_reason = "interrupted"
        if exit_reason in ("max_steps", "stuck") and limits.final_call_on_exit:
            response = await self._model(ctx, state, [], "final", limits, api_key)
            state.usage.add(response.usage)
            text = response.text or self.notices[exit_reason]
        elif exit_reason != "answer":
            text = self.notices[exit_reason]

        span.set(steps=state.step, exit_reason=exit_reason, tool_calls=state.tool_calls)
        source = limits.budget_source.get(
            {"budget_tokens": "tokens", "budget_usd": "usd"}.get(exit_reason, "")
        )
        if exit_reason == "budget_tool_calls":
            source = "policy.budgets"
        if source:
            span.set(budget_source=source)
        return LoopResult(
            answer=FinalAnswer(text or "", exit_reason),
            steps=state.step,
            tool_calls=state.tool_calls,
            exit_reason=exit_reason,
            usage=state.usage,
        )

    def _checkpoint(self, ctx: TurnContext, state: LoopState, pending: ToolCall | None = None):
        body: dict[str, Any] = {
            "messages": [m.to_dict() for m in state.messages],
            "usage": asdict(state.usage),
        }
        if pending is not None:
            body["pending_call"] = asdict(pending)
        self.session.checkpoint(ctx, "loop", state.step, body)
