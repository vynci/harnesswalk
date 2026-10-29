"""Loop: plan, act, observe, adjust, and stop.

Contract: spec/contracts/loop.md. Node: `loop` (execution plane, kind code).

    step = 0
    loop:
      exit "interrupted"   if someone interrupted the turn (checked first, so a cancel wins)
      exit "max_steps"     if step + 1 > max_steps
      exit "budget_tokens" if tokens used >= budget_tokens
      exit "budget_usd"    if budget_usd > 0 and dollars used >= budget_usd
      exit "timeout"       if active wall clock >= timeout_s (paused time excluded)
      step += 1
      ask the model layer (tools enabled)
      no tool calls -> exit "answer"
      for each tool call, in order:
        exit "interrupted" if someone interrupted the turn meanwhile
        exit "stuck" if this exact (name, arguments) already ran stuck_repeats times
        run it through `tools` (policy, approval and sandbox happen in there)
        a successful `remember` call becomes a memory proposal (written after the reply)
      checkpoint the step to the session store

After `max_steps` or `stuck` the loop makes one final model call with tools disabled (when
`final_call_on_exit`). After a budget, timeout or interrupt exit it does not call the model
again and answers with a fixed notice. A denied or failed tool call is an observation, never
an exit by itself.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from harnesswalk.context.memory_writer import proposal_from_call
from harnesswalk.context.stores.session import SessionStore
from harnesswalk.execution.model_layer import ModelLayer, ModelRequest
from harnesswalk.execution.providers import ToolSchema
from harnesswalk.execution.tools import Tools
from harnesswalk.nodes import node, register
from harnesswalk.types import FinalAnswer, HarnessError, Message, TurnContext, Usage

register("loop", __name__)

DEFAULT_NOTICES = {
    "budget_tokens": "I stopped because this turn used its token budget.",
    "budget_usd": "I stopped because this turn used its cost budget.",
    "timeout": "I stopped because this turn ran out of time.",
    "interrupted": "I stopped because the turn was interrupted.",
    "max_steps": "I stopped after the maximum number of steps.",
    "stuck": "I stopped because I was repeating the same tool call.",
}

NOT_RUN = {
    "stuck": "Not run: the same call was repeated without progress.",
    "interrupted": "Not run: the turn was cancelled.",
}


@dataclass
class Limits:
    max_steps: int = 8
    budget_tokens: int = 16000
    budget_usd: float = 0.05
    timeout_s: float = 120
    stuck_repeats: int = 3
    final_call_on_exit: bool = True

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> Limits:
        names = cls.__dataclass_fields__
        return cls(**{k: v for k, v in cfg.items() if k in names})


@dataclass
class LoopResult:
    answer: FinalAnswer
    steps: int
    tool_calls: int
    exit_reason: str
    usage: Usage
    proposals: list[Any] = field(default_factory=list)  # context.memory_writer.MemoryProposal


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
        if usage.tokens >= limits.budget_tokens:
            return "budget_tokens"
        if limits.budget_usd > 0 and usage.usd >= limits.budget_usd:
            return "budget_usd"
        if ctx.active_seconds() >= limits.timeout_s:
            return "timeout"
        return None

    async def _model(self, ctx, messages, tools, step, step_kind, api_key):
        ctx.emit.edge("loop_to_model_layer")
        try:
            response = await self.model_layer.complete(
                ctx,
                ModelRequest(messages=list(messages), tools=tools, step=step, step_kind=step_kind),
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
        async with node(
            ctx,
            "loop",
            max_steps=limits.max_steps,
            budget_tokens=limits.budget_tokens,
            budget_usd=limits.budget_usd,
        ) as span:
            messages = list(messages)
            usage = Usage()
            step = tool_calls = 0
            seen: Counter[str] = Counter()
            proposals: list[Any] = []  # successful `remember` calls, for the memory writer
            exit_reason, text = None, None

            while exit_reason is None:
                exit_reason = self._exit_before_step(ctx, limits, step, usage)
                if exit_reason:
                    break
                step += 1
                kind = (
                    "first"
                    if step == 1
                    else ("after_tool" if messages[-1].role == "tool" else "next")
                )
                response = await self._model(ctx, messages, tools, step, kind, api_key)
                usage.add(response.usage)
                messages.append(
                    Message("assistant", response.text or "", tool_calls=response.tool_calls)
                )
                if not response.tool_calls:
                    exit_reason, text = "answer", response.text or ""
                    self._checkpoint(ctx, step, messages, usage)
                    break
                for i, call in enumerate(response.tool_calls):
                    key = call_key(call.name, call.arguments)
                    if ctx.interrupted:  # a cancel arrived during the model call
                        exit_reason = "interrupted"
                    elif seen[key] >= limits.stuck_repeats:
                        exit_reason = "stuck"
                    if exit_reason:
                        for skipped in response.tool_calls[i:]:  # every call needs a result
                            messages.append(
                                Message(
                                    "tool",
                                    NOT_RUN[exit_reason],
                                    name=skipped.name,
                                    tool_call_id=skipped.call_id,
                                )
                            )
                        break
                    seen[key] += 1

                    def before_pause(call=call, step=step) -> None:
                        self._checkpoint(ctx, step, messages, usage, pending=call)

                    ctx.emit.edge("loop_to_tools")
                    observation = await self.tools.invoke(ctx, call, before_pause)
                    ctx.emit.edge("tools_to_loop")
                    tool_calls += 1
                    if observation.outcome == "ok":
                        proposal = proposal_from_call(ctx, call)
                        if proposal is not None:
                            proposals.append(proposal)
                    messages.append(
                        Message(
                            "tool", observation.content, name=call.name, tool_call_id=call.call_id
                        )
                    )
                self._checkpoint(ctx, step, messages, usage)

            if ctx.interrupted:  # never call the model again once the turn is cancelled
                exit_reason = "interrupted"
            if exit_reason in ("max_steps", "stuck") and limits.final_call_on_exit:
                response = await self._model(ctx, messages, [], step + 1, "final", api_key)
                usage.add(response.usage)
                text = response.text or self.notices[exit_reason]
            elif exit_reason != "answer":
                text = self.notices[exit_reason]

            span.set(steps=step, exit_reason=exit_reason, tool_calls=tool_calls)
        return LoopResult(
            answer=FinalAnswer(text or "", exit_reason),
            steps=step,
            tool_calls=tool_calls,
            exit_reason=exit_reason,
            usage=usage,
            proposals=proposals,
        )

    def _checkpoint(self, ctx, step, messages, usage, pending=None) -> None:
        body: dict[str, Any] = {
            "messages": [m.to_dict() for m in messages],
            "usage": asdict(usage),
        }
        if pending is not None:
            body["pending_call"] = asdict(pending)
        self.session.checkpoint(ctx, "loop", step, body)
