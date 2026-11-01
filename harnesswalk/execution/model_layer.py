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

P6: every priced call a BYO key did not pay for is charged to the shared daily ledger
(`ops/budget.py`), in every mode. In public mode the router only sees the aliases of
`public.models` that can answer now (`control/public.py`); when the spent budget is what moved
a call off a priced alias (a turn that crossed the line between two calls, or one that started
after it), the call falls through to the next alias (`fake-small`) and the span and the turn
carry a `budget_exhausted` notice.

P6 review: the budget waiver is per call. A call is BYO-paid only when it is sent with the
reader's key (the turn's key reaches the internal calls too: compaction, the injection
classifier); any other priced call in public mode reserves its worst case on the ledger first
(`HostedBudget.reserve`), and a refused reservation falls through to the fake model like a
spent budget. So a bogus key header waives nothing, and concurrent turns cannot overshoot.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from harnesswalk.context.tokens import estimate
from harnesswalk.execution.providers import (
    ModelProvider,
    ProviderError,
    ProviderRequest,
    ToolSchema,
    TurnRef,
)
from harnesswalk.execution.router import Router, RouteRequest, run_router
from harnesswalk.nodes import node, register
from harnesswalk.ops import payloads
from harnesswalk.types import Message, ToolCall, TurnContext, Usage

register("model_layer", __name__)


class BudgetRefused(ProviderError):
    """Every candidate of a call was priced and the shared budget could not hold its worst case."""

    def __init__(self) -> None:
        super().__init__(
            "budget_exhausted", "the shared hosted budget cannot cover this call", retryable=False
        )


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
    budget_refused: str | None = None  # P6: the priced alias the ledger could not hold first


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
        public: Any = None,
        ledger: Any = None,
        budget_notice: str = "",
        turn_keys: dict[str, str] | None = None,
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
        self.public = public  # control/public.py PublicModels (P6)
        self.ledger = ledger  # ops/budget.py HostedBudget (P6)
        self.budget_notice = budget_notice or "Today's shared budget for priced models is spent."
        # turn_id -> the reader's BYO key while that turn runs (the gateway binds and drops it),
        # so the turn's internal calls are paid by it too. Never logged, never in a span.
        self._turn_keys: dict[str, str] = turn_keys if turn_keys is not None else {}

    def key_for(self, ctx: TurnContext, api_key: str | None = None) -> str | None:
        """The BYO key a call of this turn is sent with, or None (the site pays)."""
        return api_key or self._turn_keys.get(ctx.turn_id)

    def router_for(
        self, ctx: TurnContext, *, budget: bool = True, byo: bool | None = None, spent: bool = False
    ) -> Router:
        """The router for this turn: narrowed to the turn's own providers when it picked one,
        and in public mode to the allowed aliases that can answer now (P6). `byo`: the call is
        sent with the reader's key (default: the turn has one); `spent`: route as if the shared
        budget were spent (a reservation was refused)."""
        router = self.router
        if ctx.providers is not None:
            chosen, health = ctx.providers, self.health
            router = router.restricted(lambda p: p in chosen and (health is None or health.ok(p)))
        if self.public is not None and ctx.mode == "public":
            if byo is None:
                byo = bool(self.key_for(ctx))
            router = router.gated(self.public.alias_ok(byo=byo, budget=budget, spent=spent))
        return router

    def _budget_moved(
        self, ctx: TurnContext, req: RouteRequest, route: Any, byo: bool
    ) -> str | None:
        """The priced alias this call would have used if the budget were not spent, or None."""
        if self.public is None or ctx.mode != "public" or byo or self.ledger is None:
            return None
        if self.ledger.ok():
            return None
        try:
            ungated = self.router_for(ctx, budget=False, byo=byo).route(req)
        except ProviderError:
            return None
        if ungated.model != route.model and self.public.metered(ungated.model):
            return ungated.model
        return None

    async def complete(
        self, ctx: TurnContext, request: ModelRequest, api_key: str | None = None
    ) -> ModelResponse:
        capture = self.capture_content and payloads.capturing(ctx)
        sent = payloads.request_payload(ctx, request) if capture else None
        about = {
            "payload": payloads.record("model_layer", "enter", step=request.step, request=sent)
        }
        async with node(
            ctx, "model_layer", enter_detail=about if capture else None, step=request.step
        ) as span:
            route_req = RouteRequest(
                step=request.step,
                step_kind=request.step_kind,
                risk=request.risk,
                modality=request.modality,
                tools_enabled=bool(request.tools),
                mode=ctx.mode,
                cost=request.cost,
                hint=ctx.model_hint,
            )
            api_key = self.key_for(ctx, api_key)
            byo = bool(api_key)
            route = await run_router(ctx, self.router_for(ctx, byo=byo), route_req)
            downgraded = self._budget_moved(ctx, route_req, route, byo)
            if downgraded is not None:
                span.set(notice="budget_exhausted", downgraded_from=downgraded)
                ctx.notice("budget_exhausted", self.budget_notice)
            try:
                response = await self._call_with_fallback(ctx, request, route, api_key)
            except BudgetRefused:
                # The shared budget could not hold this call (other turns in flight took the
                # rest, or the key header was not a usable key): the rest runs on the fake model.
                moved_from = route.model
                route = self.router_for(ctx, byo=byo, spent=True).route(route_req)
                span.set(notice="budget_exhausted", downgraded_from=moved_from)
                ctx.notice("budget_exhausted", self.budget_notice)
                response = await self._call_with_fallback(ctx, request, route, api_key)
            if response.budget_refused and downgraded is None:
                span.set(notice="budget_exhausted", downgraded_from=response.budget_refused)
                ctx.notice("budget_exhausted", self.budget_notice)
            routed = {"rule": route.rule, "model": route.model, "alts": route.alternatives}
            self.last_route[ctx.tenant_id] = routed
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
                if sent is not None:
                    span.detail["payload"] = payloads.record(
                        "model_layer",
                        "exit",
                        step=request.step,
                        provider=response.provider,
                        model=response.model,
                        request=sent,
                        reply={
                            **payloads.text(response.text),
                            "tool_calls": [payloads.tool_call(c) for c in response.tool_calls],
                            "finish_reason": response.finish_reason,
                        },
                    )
        return response

    def _worst_case(self, cfg: dict[str, Any], preq: ProviderRequest) -> float:
        """A call's most expensive outcome: the estimated input plus every output token."""
        sent = json.dumps(
            [[m.to_dict() for m in preq.messages], [t.__dict__ for t in preq.tools]],
            default=str,
            ensure_ascii=False,
        )
        return price(cfg, estimate(sent), int(preq.max_output_tokens or 0))[0]

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
        budget_refused: str | None = None
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
            hold = None
            site_paid = price(cfg, 0, 1)[0] > 0 or price(cfg, 1, 0)[0] > 0
            if ctx.mode == "public" and self.ledger is not None and site_paid and not preq.api_key:
                hold = self.ledger.reserve(self._worst_case(cfg, preq))
                if hold is None:
                    budget_refused = budget_refused or alias
                    continue
            try:
                async with node(ctx, "providers") as pspan:
                    pspan.set(provider=provider.name, model=alias, attempt=attempt)
                    try:
                        result = await asyncio.wait_for(provider.complete(preq), self.timeout_s)
                    except TimeoutError as exc:
                        raise ProviderError(
                            "provider_timeout", f"{alias} timed out after {self.timeout_s}s"
                        ) from exc
            except BaseException as exc:
                if self.ledger is not None:
                    self.ledger.release(hold)
                if not isinstance(exc, ProviderError):
                    raise
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
            if self.ledger is not None and hold is not None:
                with contextlib.suppress(Exception):
                    self.ledger.settle(hold, usd)
            elif self.ledger is not None and usd > 0 and not preq.api_key:
                # A BYO key pays its own calls; the shared ledger counts the site's. The ledger
                # never fails a call that already happened.
                with contextlib.suppress(Exception):
                    self.ledger.charge(usd)
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
            response.budget_refused = budget_refused
            return response
        if low_confidence is not None:  # no hosted model answered: keep the local answer
            ctx.calls_issued += len(low_confidence.tool_calls)
            ctx.model_calls += 1
            return low_confidence
        if budget_refused:
            raise BudgetRefused()
        assert last_error is not None
        raise last_error
