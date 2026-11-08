"""Router: choose a model for each call by rules, before any model has a say.

Contract: spec/contracts/router.md. Node: `router` (expert child of `model_layer`, kind config).

Rules come from config/models.yaml and are evaluated in order; the first whose `when` matches
wins. The chosen model is the first alias in the rule's `use` list whose provider is
configured; the rest are the only fallbacks the model layer may try.

`when` keys: `step` (`first`, `after_tool`, `next`, `final`, `summarize`, `classify`, `judge`,
or a step number), `risk`, `modality`, `tools` (whether tools are enabled), `mode` and `cost`
(`low` once the turn has spent most of its dollar budget). A rule may also set
`max_price_out_per_mtok` to drop aliases priced above it.

P3: a provider must be configured and healthy (`providers/health.py`), and a request may carry
a model hint that wins when the matched rule allows it.

P6: a router may also carry an alias filter (`gated`): in public mode the model layer narrows
each call to the aliases of `public.models` that can answer now (site key or BYO key, and the
shared daily budget for priced aliases, control/public.py).

Model suite (evaluator.md, "Model suite"): a router may be `pinned` to one alias for an eval
case turn (`model` runs, and `judge` runs with a model). Rules still match by their `when` (so
a case's `router_rules` keep their meaning), but neither the rule's `use` list nor its
`max_price_out_per_mtok` is consulted: every call answers on the pinned alias, with no
fallback; the route reports the first rule whose `when` matches.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from harnesswalk.execution.providers import ProviderError
from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("router", __name__)


@dataclass
class Route:
    rule: str
    model: str  # alias from config/models.yaml
    alternatives: list[str]


@dataclass
class RouteRequest:
    step: int
    step_kind: str  # first | after_tool | final | summarize | next
    risk: str = "low"
    modality: str = "text"
    tools_enabled: bool = True
    mode: str = "dev"
    cost: str = "normal"  # low once the turn has spent router.cost.tight_fraction of its budget
    hint: str | None = None  # a model alias asked for by the turn (dev mode)


class Router:
    def __init__(
        self,
        rules: list[dict[str, Any]],
        models: dict[str, dict[str, Any]],
        provider_ok: Callable[[str], bool],
        alias_ok: Callable[[str], bool] | None = None,
        pin: str | None = None,
    ) -> None:
        self._rules = rules
        self._models = models
        self._provider_ok = provider_ok
        self._alias_ok = alias_ok
        self._pin = pin

    def rules(self) -> list[dict[str, Any]]:
        return list(self._rules)

    def restricted(self, provider_ok: Callable[[str], bool]) -> Router:
        """The same rules over a narrower set of providers (a per-turn provider choice)."""
        return Router(self._rules, self._models, provider_ok, self._alias_ok, self._pin)

    def gated(self, alias_ok: Callable[[str], bool]) -> Router:
        """The same rules over the aliases `alias_ok` lets through (P6, public mode)."""
        return Router(self._rules, self._models, self._provider_ok, alias_ok, self._pin)

    def pinned(self, alias: str) -> Router:
        """The same rules with every call answered by `alias` (the eval `model` suite)."""
        return Router(self._rules, self._models, self._provider_ok, self._alias_ok, alias)

    @staticmethod
    def matches(when: dict[str, Any], req: RouteRequest) -> bool:
        actual = {
            "step": req.step if isinstance(when.get("step"), int) else req.step_kind,
            "risk": req.risk,
            "modality": req.modality,
            "tools": req.tools_enabled,
            "mode": req.mode,
            "cost": req.cost,
        }
        return all(key in actual and actual[key] == want for key, want in when.items())

    def _usable(self, alias: str, req: RouteRequest, rule: dict[str, Any]) -> bool:
        model = self._models.get(alias, {})
        if not self._provider_ok(model.get("provider", "")):
            return False
        if self._alias_ok is not None and not self._alias_ok(alias):
            return False
        cap = rule.get("max_price_out_per_mtok")
        if cap is not None and float(model.get("price_out_per_mtok") or 0) > float(cap):
            return False
        return not (req.modality == "image" and "image" not in model.get("modalities", []))

    def _priced_out(self, rule: dict[str, Any], req: RouteRequest) -> bool:
        """Whether the cap alone empties the rule: some alias would be usable without it."""
        if rule.get("max_price_out_per_mtok") is None:
            return False
        uncapped = {k: v for k, v in rule.items() if k != "max_price_out_per_mtok"}
        return any(self._usable(alias, req, uncapped) for alias in rule.get("use", []))

    def _route_pinned(self, req: RouteRequest, alias: str) -> Route:
        """The first rule whose `when` matches; the pinned alias answers. A price cap does not
        apply: the run chose the alias on purpose, and a cap that pushed a priced alias to the
        next rule would misreport the step (a hosted model's after-tool call as `default`)."""
        model = self._models.get(alias)
        if model is None or model.get("embedding"):
            raise ProviderError("provider_error", f"unknown model alias {alias!r}", retryable=False)
        if not self._provider_ok(model.get("provider", "")) or (
            self._alias_ok is not None and not self._alias_ok(alias)
        ):
            raise ProviderError(
                "provider_error",
                f"the pinned model {alias} has no configured provider for this request",
                retryable=False,
            )
        for rule in self._rules:
            if self.matches(rule.get("when") or {}, req):
                return Route(rule=rule["id"], model=alias, alternatives=[])
        raise ProviderError("provider_error", "no router rule matched", retryable=False)

    def route(self, req: RouteRequest) -> Route:
        if self._pin is not None:
            return self._route_pinned(req, self._pin)
        for rule in self._rules:
            if not self.matches(rule.get("when") or {}, req):
                continue
            usable = [alias for alias in rule.get("use", []) if self._usable(alias, req, rule)]
            if not usable and self._priced_out(rule, req):
                # Every configured model of the rule costs more than its cap: the rule asks for
                # a cheap model and none exists, so the next matching rule routes the call
                # (P5 integration: a priced fake model used to fail the `lookup` step).
                continue
            if not usable:
                raise ProviderError(
                    "provider_error",
                    f"router rule {rule['id']} has no configured model for this request",
                    retryable=False,
                )
            if req.hint in usable:  # a hint picks among what the rule allows, nothing more
                usable.remove(req.hint)
                usable.insert(0, req.hint)
            return Route(rule=rule["id"], model=usable[0], alternatives=usable[1:])
        raise ProviderError("provider_error", "no router rule matched", retryable=False)


async def run_router(ctx: TurnContext, router: Router, req: RouteRequest) -> Route:
    async with node(ctx, "router") as span:
        route = router.route(req)
        span.set(rule=route.rule, model=route.model, alternatives=route.alternatives)
    return route
