"""Model layer: one call shape in front of every model, routed by rules first.

Contract: spec/contracts/model_provider.md and router.md. Node: `model_layer`, expert
children `router` and `providers`. Always on.

Lesson: the loop never names a provider. The router picks the model from rules in config, the
provider adapter speaks one API, and swapping a local model for a hosted one is a config edit.
The `fake` provider replays scripted fixtures so CI and the conformance suite are offline.
"""

from __future__ import annotations

from typing import Any

from harness.core.events import NodeError, Turn
from harness.core.providers.fake import FakeProvider
#% if "ollama" in providers
from harness.core.providers.ollama import OllamaProvider
#% endif
#% if "anthropic" in providers
from harness.core.providers.anthropic import AnthropicProvider
#% endif


class ModelLayer:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.models: dict[str, dict[str, Any]] = settings.get("models.models", {})
        self.rules: list[dict[str, Any]] = settings.get("models.router.rules", [])
        self.providers: dict[str, Any] = {"fake": FakeProvider(settings)}
#% if "ollama" in providers
        if settings.provider == "ollama":
            self.providers["ollama"] = OllamaProvider(settings)
#% endif
#% if "anthropic" in providers
        if settings.provider == "anthropic":
            self.providers["anthropic"] = AnthropicProvider(settings)
#% endif

    def route(self, turn: Turn) -> tuple[str, str, list[str]]:
        """(rule id, alias, alternatives): the first rule whose `when` matches this turn."""
        pinned = turn.data.get("model")
        if pinned:
            return "pinned", pinned, []
        for rule in self.rules:
            when = rule.get("when") or {}
            if when.get("mode") not in (None, self.settings.mode):
                continue
            use = rule.get("use") or []
            use = [use] if isinstance(use, str) else list(use)
            provider = turn.data.get("provider", self.settings.provider)
            if provider != "fake":  # the server's model takes every route (one model)
                use = [self.default_alias(provider)]
            use = [a for a in use if self.models.get(a, {}).get("provider") == provider]
            if use:
                return rule["id"], use[0], use[1:]
        raise NodeError("provider_error", "no router rule names a configured model")

    def default_alias(self, provider: str) -> str:
        chosen = self.settings.get(f"models.providers.{provider}.model")
        return chosen or next(a for a, m in self.models.items() if m["provider"] == provider)

    def complete(self, turn: Turn, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                 step: int, output_schema: dict[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
        with turn.span("model_layer", step=step) as span:
            with turn.span("router") as r:
                rule, alias, alternatives = self.route(turn)
                r.set(rule=rule, model=alias, alternatives=alternatives)
            model = self.models[alias]
            request = {"model": model.get("name", alias), "alias": alias, "messages": messages,
                       "tools": tools, "output_schema": output_schema,
                       "max_output_tokens": self.settings.get("model_layer.max_output_tokens", 1024),
                       "context_tokens": model.get("context_tokens"),
                       "params": model.get("params") or {}}  # fmt: skip
            with turn.span("providers") as p:
                p.set(provider=model["provider"], model=alias, attempt=1)
                response = self.providers[model["provider"]].complete(turn, request)
            calls = response["tool_calls"]
            for n, call in enumerate(calls, start=turn.data.get("call_ids", 0) + 1):
                call["call_id"] = call.get("call_id") or f"call_{n}"
            turn.data["call_ids"] = turn.data.get("call_ids", 0) + len(calls)
            usage = response.get("usage") or {}
            tin, tout = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            usd = (tin * model.get("price_in_per_mtok", 0)
                   + tout * model.get("price_out_per_mtok", 0)) / 1e6  # fmt: skip
            span.cost = {"input_tokens": tin, "output_tokens": tout, "usd": round(usd, 6)}
            finish = response.get("finish_reason") or ("tool_calls" if calls else "stop")
            span.set(step=step, provider=model["provider"], model=alias, finish_reason=finish,
                     tool_calls=len(calls), fallback=False, tools_enabled=bool(tools))  # fmt: skip
            turn.data["last_call"] = {"messages": [dict(m) for m in messages],
                                      "tools": [t["name"] for t in tools], "response": response}
            return {**response, "finish_reason": finish, "alias": alias, "rule": rule}
