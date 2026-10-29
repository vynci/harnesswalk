"""Router: choose a model for each call by rules, before any model has a say.

Contract: spec/contracts/router.md. Node: `router` (expert child of `model_layer`, kind config).

Rules come from config/models.yaml and are evaluated in order; the first whose `when` matches
wins. The chosen model is the first alias in the rule's `use` list whose provider is
configured; the rest are the only fallbacks the model layer may try.

`when` keys: `step` (`first`, `after_tool`, `final`, `summarize`, or a step number), `risk`,
`modality`, `tools` (whether tools are enabled) and `mode`.
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


class Router:
    def __init__(
        self,
        rules: list[dict[str, Any]],
        models: dict[str, dict[str, Any]],
        provider_ok: Callable[[str], bool],
    ) -> None:
        self._rules = rules
        self._models = models
        self._provider_ok = provider_ok

    def rules(self) -> list[dict[str, Any]]:
        return list(self._rules)

    @staticmethod
    def matches(when: dict[str, Any], req: RouteRequest) -> bool:
        actual = {
            "step": req.step if isinstance(when.get("step"), int) else req.step_kind,
            "risk": req.risk,
            "modality": req.modality,
            "tools": req.tools_enabled,
            "mode": req.mode,
        }
        return all(key in actual and actual[key] == want for key, want in when.items())

    def _usable(self, alias: str, req: RouteRequest) -> bool:
        model = self._models.get(alias, {})
        if not self._provider_ok(model.get("provider", "")):
            return False
        return not (req.modality == "image" and "image" not in model.get("modalities", []))

    def route(self, req: RouteRequest) -> Route:
        for rule in self._rules:
            if not self.matches(rule.get("when") or {}, req):
                continue
            usable = [alias for alias in rule.get("use", []) if self._usable(alias, req)]
            if not usable:
                raise ProviderError(
                    "provider_error",
                    f"router rule {rule['id']} has no configured model for this request",
                    retryable=False,
                )
            return Route(rule=rule["id"], model=usable[0], alternatives=usable[1:])
        raise ProviderError("provider_error", "no router rule matched", retryable=False)


async def run_router(ctx: TurnContext, router: Router, req: RouteRequest) -> Route:
    async with node(ctx, "router") as span:
        route = router.route(req)
        span.set(rule=route.rule, model=route.model, alternatives=route.alternatives)
    return route
