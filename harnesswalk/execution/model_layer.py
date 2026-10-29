"""Model layer: one call shape in front of every model.

Contract: spec/contracts/model_provider.md. Node: `model_layer` (execution plane, kind model).

For each call: ask the router for a model (`router` child span), call that provider
(`providers` child span), fall back along the router's alternatives on a retryable failure,
assign `call_<n>` ids to tool calls that lack one, and price the usage from config/models.yaml.
The exit summary says which provider and model answered and whether it was a fallback; the
exit carries the call's `cost`.

P3: a retryable failure marks the provider unhealthy for the router (passive health). The
cascade (`router.cascade`, off by default) retries a low-confidence answer from a local model on
the next hosted alternative; confidence is a deterministic signal, see `confidence()`.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from harnesswalk.execution.providers import (
    ModelProvider,
    ProviderError,
    ProviderRequest,
    ToolSchema,
    TurnRef,
)
from harnesswalk.execution.router import Router, RouteRequest, run_router
from harnesswalk.nodes import node, register
from harnesswalk.types import Message, ToolCall, TurnContext, Usage

register("model_layer", __name__)


@dataclass
class ModelRequest:
    messages: list[Message]
    tools: list[ToolSchema]  # empty means tools disabled
    step: int
    step_kind: str = "next"
    risk: str = "low"
    modality: str = "text"
    output_schema: dict[str, Any] | None = None
    max_output_tokens: int | None = None
    cost: str = "normal"  # low once the turn has spent most of its dollar budget


@dataclass
class ModelResponse:
    text: str | None
    tool_calls: list[ToolCall]
    finish_reason: str
    provider: str
    model: str
    fallback: bool
    usage: Usage = field(default_factory=Usage)
    cascade: str | None = None  # why a later alias answered: low_confidence


HEDGES = re.compile(
    r"\b(i('m| am) not (sure|certain)|i don'?t know|i cannot (tell|determine)|unclear to me)\b",
    re.IGNORECASE,
)


def confidence(text: str | None, calls: list[ToolCall], finish: str, offered: set[str]) -> float:
    """A deterministic confidence signal for the cascade: 0, 0.5 or 1."""
    if finish == "length" or (not (text or "").strip() and not calls):
        return 0.0
    if any(c.name not in offered for c in calls):
        return 0.0
    if text and HEDGES.search(text):
        return 0.5
    return 1.0


def price(model_cfg: dict[str, Any], input_tokens: int, output_tokens: int) -> tuple[float, bool]:
    """Dollars for one call, and whether the price was known (unknown means 0)."""
    p_in, p_out = model_cfg.get("price_in_per_mtok"), model_cfg.get("price_out_per_mtok")
    if p_in is None or p_out is None:
        return 0.0, False
    return (p_in * input_tokens + p_out * output_tokens) / 1_000_000, True


class ModelLayer:
    def __init__(
        self,
        providers: dict[str, ModelProvider],
        router: Router,
        models: dict[str, dict[str, Any]],
        *,
        timeout_s: float = 60.0,
        max_output_tokens: int = 1024,
        capture_content: bool = True,
        health: Any = None,
        cascade: dict[str, Any] | None = None,
        tight_fraction: float = 0.75,
    ) -> None:
        self.providers = providers
        self.router = router
        self.models = models
        self.timeout_s = timeout_s
        self.max_output_tokens = max_output_tokens
        self.capture_content = capture_content
        self.last_route: dict[str, dict[str, Any]] = {}  # tenant -> last routing decision
        self.health = health
        self.cascade = cascade or {}
        self.tight = tight_fraction

    def router_for(self, ctx: TurnContext) -> Router:
        """The router for this turn: narrowed to the turn's own providers when it picked one."""
        if ctx.providers is None:
            return self.router
        chosen, health = ctx.providers, self.health
        return self.router.restricted(lambda p: p in chosen and (health is None or health.ok(p)))

    async def complete(
        self, ctx: TurnContext, request: ModelRequest, api_key: str | None = None
    ) -> ModelResponse:
        async with node(ctx, "model_layer", step=request.step) as span:
            route = await run_router(
                ctx,
                self.router_for(ctx),
                RouteRequest(
                    step=request.step,
                    step_kind=request.step_kind,
                    risk=request.risk,
                    modality=request.modality,
                    tools_enabled=bool(request.tools),
                    mode=ctx.mode,
                    cost=request.cost,
                    hint=ctx.model_hint,
                ),
            )
            routed = {"rule": route.rule, "model": route.model, "alts": route.alternatives}
            self.last_route[ctx.tenant_id] = routed
            response = await self._call_with_fallback(ctx, request, route, api_key)
            span.cost = response.usage.cost()
            span.set(
                step=request.step,
                provider=response.provider,
                model=response.model,
                finish_reason=response.finish_reason,
                tool_calls=len(response.tool_calls),
                fallback=response.fallback,
                tools_enabled=bool(request.tools),
                price_known=self._price_known(response.model),
            )
            if response.usage.cached_tokens:
                span.set(cached_tokens=response.usage.cached_tokens)
            if response.cascade:
                span.set(cascade=response.cascade)
            if self.capture_content:
                span.detail = {
                    "route": routed,
                    "request": [m.to_dict() for m in request.messages],
                    "tools": [t.name for t in request.tools],
                    "response": {
                        "text": response.text,
                        "tool_calls": [c.__dict__ for c in response.tool_calls],
                    },
                }
        return response

    def _price_known(self, alias: str) -> bool:
        return price(self.models.get(alias, {}), 0, 0)[1]

    def _is_local(self, alias: str) -> bool:
        local = self.cascade.get("local_providers", ["ollama", "fake"])
        return self.models[alias]["provider"] in local

    async def _call_with_fallback(
        self, ctx: TurnContext, request: ModelRequest, route: Any, api_key: str | None
    ) -> ModelResponse:
        candidates = [route.model, *route.alternatives]
        # A BYO key belongs to the provider of the routed model; fallbacks never see it.
        key_provider = self.models[route.model]["provider"]
        last_error: ProviderError | None = None
        offered = {t.name for t in request.tools}
        low_confidence: ModelResponse | None = None
        for attempt, alias in enumerate(candidates, start=1):
            cfg = self.models[alias]
            if low_confidence is not None and self._is_local(alias):
                continue  # the cascade only climbs to a hosted model
            provider = (ctx.providers or self.providers)[cfg["provider"]]
            preq = ProviderRequest(
                model=cfg.get("name", alias),
                messages=request.messages,
                tools=request.tools,
                max_output_tokens=request.max_output_tokens or self.max_output_tokens,
                output_schema=request.output_schema,
                api_key=api_key if cfg["provider"] == key_provider else None,
                turn=TurnRef(
                    ctx.tenant_id, ctx.session_id, ctx.turn_id, ctx.turn_index, ctx.model_calls
                ),
                params=cfg.get("params"),
            )
            try:
                async with node(ctx, "providers") as pspan:
                    pspan.set(provider=provider.name, model=alias, attempt=attempt)
                    try:
                        result = await asyncio.wait_for(provider.complete(preq), self.timeout_s)
                    except TimeoutError as exc:
                        raise ProviderError(
                            "provider_timeout", f"{alias} timed out after {self.timeout_s}s"
                        ) from exc
            except ProviderError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
                # A caller's own key (BYO) or a refused request is not the provider's health:
                # one bad key must not take the provider down for every tenant (P3 review).
                if self.health is not None and not preq.api_key and not exc.client_error:
                    self.health.mark_failed(cfg["provider"], exc.code)
                continue
            calls = []
            issued = ctx.calls_issued
            for call in result.tool_calls:
                issued += 1
                calls.append(ToolCall(call.call_id or f"call_{issued}", call.name, call.arguments))
            usd, _ = price(cfg, result.usage.input_tokens, result.usage.output_tokens)
            response = ModelResponse(
                text=result.text,
                tool_calls=calls,
                finish_reason=result.finish_reason,
                provider=provider.name,
                model=alias,
                fallback=attempt > 1,
                usage=Usage(
                    result.usage.input_tokens,
                    result.usage.output_tokens,
                    result.usage.cached_tokens,
                    usd,
                ),
            )
            if low_confidence is not None:
                response.usage.add(low_confidence.usage)  # both calls were paid for
                response.cascade = "low_confidence"
            elif (
                self.cascade.get("enabled")
                and self._is_local(alias)
                and confidence(result.text, calls, result.finish_reason, offered)
                < float(self.cascade.get("min_confidence", 0.5))
                and any(not self._is_local(a) for a in candidates[attempt:])
            ):
                low_confidence = response
                continue
            ctx.calls_issued = issued
            ctx.model_calls += 1
            return response
        if low_confidence is not None:  # no hosted model answered: keep the local answer
            ctx.calls_issued += len(low_confidence.tool_calls)
            ctx.model_calls += 1
            return low_confidence
        assert last_error is not None
        raise last_error
