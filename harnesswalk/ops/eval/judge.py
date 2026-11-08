"""The judge: a model that scores a reply for faithfulness and relevance, from 0 to 1.

Contract: spec/contracts/evaluator.md (P4 additions). RAGAS-style, in one call:

- faithfulness: are the reply's claims supported by what the model was shown (the system
  prompt, the retrieved items and the tool results, read from the trace)?
- relevance: does the reply answer the question, judged against the case's rubric?

The judge model is `evals.judge.model`, else the first alias of the router rule for the `judge`
step whose provider can be used (`hosted-haiku` = `claude-haiku-5-5` in the shipped config).
The judge never runs on `fake`: without a usable non-fake model `resolve()` returns None and a
judge run is refused with `judge_unavailable`. A judge call is not a turn: it emits no events,
and its cost is added to the run (or stored with the online score).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from harnesswalk.events import Event
from harnesswalk.execution.model_layer import price
from harnesswalk.execution.providers import ProviderError, ProviderRequest
from harnesswalk.types import Message, Usage

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

SYSTEM = (
    "You are an evaluation judge for a support assistant. You score one reply on two axes, each"
    " from 0 to 1. faithfulness: the share of the reply's factual claims that the CONTEXT"
    " supports (1 when every claim is supported or the reply makes no factual claim). relevance:"
    " how well the reply answers the QUESTION as the RUBRIC describes a good answer. Reply with"
    ' JSON only: {"faithfulness": {"score": <0..1>, "reason": "<one sentence>"},'
    ' "relevance": {"score": <0..1>, "reason": "<one sentence>"}}'
)
CONTEXT_CHARS = 8000
HOSTED = ("anthropic", "openai_compatible")


@dataclass
class Verdict:
    scores: dict[str, dict[str, Any]]
    usage: Usage
    model: str


def context_of(harness: Harness, tenant_id: str, events: list[Event]) -> str:
    """What the model was shown, from the trace: the last model call's request, minus the
    user's own words. Empty when the trace kept no content."""
    exits = [e for e in events if e["type"] == "node_exit" and e.get("node_id") == "model_layer"]
    for event in reversed(exits):
        detail = harness.tracer.get_detail(tenant_id, event.get("detail_ref") or "")
        if detail and detail.get("request"):
            parts = [
                f"[{m['role']}{' ' + m['name'] if m.get('name') else ''}] {m.get('content', '')}"
                for m in detail["request"]
                if m.get("role") in ("system", "tool") and m.get("content")
            ]
            return "\n\n".join(parts)[:CONTEXT_CHARS]
    return ""


def parse(text: str | None) -> dict[str, Any]:
    """The judge's JSON, also when it wrapped it in a code fence or a sentence."""
    raw = text or ""
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError("the judge did not reply with JSON")
    data = json.loads(match.group(0))
    out = {}
    for name in ("faithfulness", "relevance"):
        item = data.get(name)
        if isinstance(item, (int, float)):
            item = {"score": item}
        if not isinstance(item, dict) or not isinstance(item.get("score"), (int, float)):
            raise ValueError(f"the judge gave no {name} score")
        out[name] = {
            "score": max(0.0, min(1.0, float(item["score"]))),
            "reason": item.get("reason"),
        }
    return out


class Judge:
    def __init__(self, harness: Harness) -> None:
        self.h = harness
        self.threshold = float(harness.settings.get("evals.judge.threshold", 0.7))
        # Tests put a deterministic judge here (async (question, reply, context, rubric) ->
        # Verdict); automated tests never call a hosted model.
        self.override: Any = None

    def resolve(self) -> str | None:
        """The judge's model alias, or None when no usable non-fake model is configured."""
        if self.override is not None:
            return "override"
        s = self.h.settings
        models = s.get("models.models", {})
        configured = s.get("evals.judge.model", None)
        if configured:
            candidates = [configured]
        else:
            rules = s.get("models.router.rules", [])
            candidates = next(
                (r.get("use", []) for r in rules if (r.get("when") or {}).get("step") == "judge"),
                [],
            )
        for alias in candidates:
            cfg = models.get(alias)
            if not cfg or cfg.get("embedding") or cfg.get("provider") == "fake":
                continue
            if self.usable(cfg["provider"]):
                return alias
        return None

    def usable(self, provider: str) -> bool:
        """A provider the harness was started with, or a hosted one explicitly enabled with its
        key. Never a local server the harness was not started on (a test on `fake` must not
        reach an Ollama that happens to run on the machine)."""
        if provider in self.h.providers:
            return True
        return provider in HOSTED and self.h.settings.provider_prerequisites(provider)

    @property
    def available(self) -> bool:
        return self.resolve() is not None

    async def judge(
        self, question: str, reply: str, context: str, rubric: str | None
    ) -> Verdict | None:
        alias = self.resolve()
        if alias is None:
            return None
        if self.override is not None:
            return await self.override(question, reply, context, rubric)
        cfg = self.h.settings.get(f"models.models.{alias}")
        provider = self.h.turn_providers(cfg["provider"], None)[cfg["provider"]]
        body = (
            f"QUESTION:\n{question}\n\nRUBRIC:\n{rubric or 'A direct, correct answer.'}\n\n"
            f"CONTEXT:\n{context or '(none)'}\n\nREPLY:\n{reply}"
        )
        request = ProviderRequest(
            model=cfg.get("name", alias),
            messages=[Message("system", SYSTEM), Message("user", body)],
            tools=[],
            max_output_tokens=300,
            params=cfg.get("params"),
            context_tokens=cfg.get("context_tokens"),
        )
        response = await provider.complete(request)
        usd, _ = price(cfg, response.usage.input_tokens, response.usage.output_tokens)
        usage = Usage(response.usage.input_tokens, response.usage.output_tokens, 0, usd)
        try:
            parsed = parse(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ProviderError("judge_unparseable", str(exc), retryable=False) from exc
        scores = {
            name: {
                "value": round(item["score"], 4),
                "pass": item["score"] >= self.threshold,
                "detail": (item.get("reason") or "")[:500],
            }
            for name, item in parsed.items()
        }
        return Verdict(scores, usage, alias)
