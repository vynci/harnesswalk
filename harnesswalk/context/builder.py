"""Context builder: assemble what the model sees, inside a token budget.

Contract: spec/contracts/context_builder.md. Node: `context_builder` (context plane).

Slices, stable first so prefix caching works:

    procedural   system prompt (skill zero) and the skills index        tier always
    semantic     facts            (stub in P1, always empty)           tier retrieved
    episodic     dated events     (stub in P1, always empty)           tier retrieved
    knowledge    document chunks  (stub in P1, always empty)           tier retrieved
    session      running summary, then recent turns verbatim           tier retrieved
    user         the new, guardrail-cleaned input

Each slice has a budget from config/context.yaml. When the session slice passes
`compaction.threshold` of its budget, the oldest turns (all but `keep_recent_turns`) fold into
the running summary. The summary is written by a model when the router has a `summarize`
model, and by plain truncation otherwise (always with the fake provider). Skill bodies stay
out: the model reads them on demand with `read_skill`.

The exit summary reports tokens per slice; the full assembled context goes to the trace as a
detail record.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from harnesswalk.context.stores import Memory
from harnesswalk.context.stores.session import SessionStore, Turn
from harnesswalk.context.tokens import estimate
from harnesswalk.nodes import node, register
from harnesswalk.types import Message, TurnContext

register("context_builder", __name__)

SLICES = ("procedural", "semantic", "episodic", "knowledge", "session", "user")
Summarizer = Callable[[TurnContext, str, list[Turn]], Awaitable[str | None]]


@dataclass
class AssembledContext:
    messages: list[Message]
    slices: dict[str, int]
    used_tokens: int
    budget_tokens: int
    stable_prefix_tokens: int
    compaction: dict[str, Any] | None = None
    on_demand_index: list[str] = field(default_factory=list)


def truncation_summary(previous: str, turns: list[Turn], max_chars: int) -> str:
    """The summary without a model: the previous summary plus short previews of each turn."""
    lines = [previous] if previous else []
    for t in turns:
        lines.append(f"Turn {t.index + 1}. User: {t.user[:160]} Assistant: {t.assistant[:160]}")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else "..." + text[-(max_chars - 3) :]


class ContextBuilder:
    def __init__(
        self,
        cfg: dict[str, Any],
        memory: Memory,
        session: SessionStore,
        summarizer: Summarizer | None = None,
    ) -> None:
        self.cfg = cfg
        self.memory = memory
        self.session = session
        self.summarizer = summarizer
        self.estimator = cfg.get("token_estimator", "chars_div_4")
        self.last: dict[str, AssembledContext] = {}  # tenant -> last context, for the inspector

    def _tok(self, text: str) -> int:
        return estimate(text, self.estimator)

    def _turn_tokens(self, turns: list[Turn]) -> int:
        return sum(self._tok(t.user) + self._tok(t.assistant) for t in turns)

    async def build(self, ctx: TurnContext, user_text: str) -> AssembledContext:
        total = int(self.cfg.get("total_tokens", 16000))
        reserve = int(self.cfg.get("reserve_output_tokens", 1024))
        slice_budget = self.cfg.get("slices", {})
        async with node(ctx, "context_builder", budget_tokens=total) as span:
            retrieved = await self.memory.retrieve_all(ctx)
            ctx.emit.edge("memory_to_context_builder")
            view = retrieved.session
            summary, through, turns = view.summary, view.summary_through, list(view.turns)

            compaction = None
            session_budget = int(slice_budget.get("session", 5000))
            threshold = float(self.cfg.get("compaction", {}).get("threshold", 0.8))
            keep = int(self.cfg.get("compaction", {}).get("keep_recent_turns", 4))
            if self._tok(summary) + self._turn_tokens(turns) > threshold * session_budget:
                summary, through, turns, compaction = await self._compact(
                    ctx, summary, through, turns, keep, session_budget
                )

            summary_text = f"Summary of the conversation so far:\n{summary}" if summary else ""
            limit = total - reserve
            slices = dict.fromkeys(SLICES, 0)
            slices["procedural"] = self._tok(retrieved.procedural)
            slices["user"] = self._tok(user_text)
            # Only a huge input can overflow the total; then keep the newest turns that fit.
            while (
                turns
                and sum(slices.values()) + self._tok(summary_text) + self._turn_tokens(turns)
                > limit
            ):
                turns.pop(0)
            slices["session"] = self._tok(summary_text) + self._turn_tokens(turns)
            used = sum(slices.values())

            messages = [Message("system", retrieved.procedural)]
            if summary_text:
                messages.append(Message("system", summary_text))
            for t in turns:
                messages.append(Message("user", t.user))
                messages.append(Message("assistant", t.assistant))
            messages.append(Message("user", user_text))

            assembled = AssembledContext(
                messages=messages,
                slices=slices,
                used_tokens=used,
                budget_tokens=total,
                stable_prefix_tokens=slices["procedural"],
                compaction=compaction,
                on_demand_index=sorted(self.memory.procedural.skills),
            )
            self.last[ctx.tenant_id] = assembled
            span.set(
                budget_tokens=total,
                used_tokens=used,
                slices=slices,
                compacted=compaction is not None,
                stable_prefix_tokens=slices["procedural"],
                headroom_tokens=limit - used,
                token_estimator=self.estimator,
            )
            if compaction:
                span.set(compaction={k: v for k, v in compaction.items() if k != "pointers"})
            span.detail = {
                "token_estimator": self.estimator,
                "slices": slices,
                "compaction": compaction,
                "messages": [m.to_dict() for m in messages],
            }
        return assembled

    async def _compact(self, ctx, summary, through, turns, keep, session_budget):
        old, recent = turns[: max(0, len(turns) - keep)], turns[max(0, len(turns) - keep) :]
        # A few very long recent turns can still overflow: fold them too, oldest first.
        while recent and self._turn_tokens(recent) > 0.6 * session_budget:
            old.append(recent.pop(0))
        if not old:
            return summary, through, turns, None
        max_tokens = int(self.cfg.get("compaction", {}).get("summary_tokens", 600))
        text, method = None, "truncation"
        if self.summarizer is not None:
            text = await self.summarizer(ctx, summary, old)
            method = "model" if text else method
        if not text:
            text = truncation_summary(summary, old, max_tokens * 4)
        text = text[: max_tokens * 4]
        through = old[-1].index
        self.session.write_summary(ctx, "loop", text, through)  # the loop side owns the summary
        compaction = {
            "turns_compacted": len(old),
            "summary_tokens": self._tok(text),
            "method": method,
            "pointers": [f"session://{ctx.session_id}/turns/{t.index}" for t in old],
        }
        return text, through, recent, compaction
