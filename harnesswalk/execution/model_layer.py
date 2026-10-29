"""Model layer: one call shape in front of every model.

Contract: spec/contracts/model_provider.md. Node: `model_layer` (execution plane, kind model).

For each call: ask the router for a model (`router` child span), call that provider
(`providers` child span), fall back along the router's alternatives on a retryable failure,
assign `call_<n>` ids to tool calls that lack one, and price the usage from config/models.yaml.
The exit summary says which provider and model answered and whether it was a fallback; the
exit carries the call's `cost`.
"""

from __future__ import annotations

import asyncio
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


@dataclass
class ModelResponse:
    text: str | None
    tool_calls: list[ToolCall]
    finish_reason: str
    provider: str
    model: str
    fallback: bool
    usage: Usage = field(default_factory=Usage)


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
    ) -> None:
        self.providers = providers
        self.router = router
        self.models = models
        self.timeout_s = timeout_s
        self.max_output_tokens = max_output_tokens
        self.capture_content = capture_content
        self.last_route: dict[str, Any] | None = None

    async def complete(
        self, ctx: TurnContext, request: ModelRequest, api_key: str | None = None
    ) -> ModelResponse:
        async with node(ctx, "model_layer", step=request.step) as span:
            route = await run_router(
                ctx,
                self.router,
                RouteRequest(
                    step=request.step,
                    step_kind=request.step_kind,
                    risk=request.risk,
                    modality=request.modality,
                    tools_enabled=bool(request.tools),
                    mode=ctx.mode,
                ),
            )
            self.last_route = {"rule": route.rule, "model": route.model, "alts": route.alternatives}
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
            if self.capture_content:
                span.detail = {
                    "route": self.last_route,
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

    async def _call_with_fallback(
        self, ctx: TurnContext, request: ModelRequest, route: Any, api_key: str | None
    ) -> ModelResponse:
        candidates = [route.model, *route.alternatives]
        last_error: ProviderError | None = None
        for attempt, alias in enumerate(candidates, start=1):
            cfg = self.models[alias]
            provider = self.providers[cfg["provider"]]
            preq = ProviderRequest(
                model=cfg.get("name", alias),
                messages=request.messages,
                tools=request.tools,
                max_output_tokens=request.max_output_tokens or self.max_output_tokens,
                output_schema=request.output_schema,
                api_key=api_key,
                turn=TurnRef(ctx.tenant_id, ctx.session_id, ctx.turn_id, ctx.turn_index),
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
                continue
            calls = []
            for call in result.tool_calls:
                ctx.calls_issued += 1
                calls.append(
                    ToolCall(call.call_id or f"call_{ctx.calls_issued}", call.name, call.arguments)
                )
            usd, _ = price(cfg, result.usage.input_tokens, result.usage.output_tokens)
            return ModelResponse(
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
        assert last_error is not None
        raise last_error
