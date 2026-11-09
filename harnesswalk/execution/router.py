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

0.2.0, the canary rule (router.md): a rule with `split` instead of `use` sends a share of the
pack's turns to a candidate alias, by the hash of the turn id, so every call of a turn lands on
the same arm. `when.pack` matches the turn's pack. A pinned router skips split rules, and a
split whose arms are both unusable does not apply.
"""

from __future__ import annotations

import hashlib
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
    arm: str | None = None  # 0.2.0: base or candidate, when a canary rule routed the call
    bucket: float | None = None


def bucket_of(turn_id: str) -> float:
    """A turn's place in [0, 1) for a canary split: sha256 of the turn id, first 8 hex digits."""
    return int(hashlib.sha256(turn_id.encode("utf-8")).hexdigest()[:8], 16) / 2**32


def with_canary(rules: list[dict[str, Any]], canary: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The ordered rules with `router.canary` (0.2.0) placed right after `public` (or first)."""
    if not canary:
        return list(rules)
    rule = {"id": "canary", **canary}
    at = next((i + 1 for i, r in enumerate(rules) if r.get("id") == "public"), 0)
    return [*rules[:at], rule, *rules[at:]]


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
    pack: str | None = None  # 0.2.0: the turn's pack (`when.pack`, the canary rule)
    turn_id: str = ""  # 0.2.0: the canary split's bucket
    internal: bool = False  # 0.2.0: an eval case turn, never split


class Router:
    def __init__(
        self,
        rules: list[dict[str, Any]],
        models: dict[str, dict[str, Any]],
        provider_ok: Callable[[str], bool],
        alias_ok: Callable[[str], bool] | None = None,
        pin: str | None = None,
        configured: Callable[[str], bool] | None = None,
    ) -> None:
        self._rules = rules
        self._models = models
        self._provider_ok = provider_ok
        self._alias_ok = alias_ok
        self._pin = pin
        # 0.2.0: whether a provider is built at all, health aside. A pinned run asks only this:
        # one timed-out call must fail that call, not every later case of the run.
        self._configured = configured or provider_ok

    def rules(self) -> list[dict[str, Any]]:
        return list(self._rules)

    def restricted(
        self, provider_ok: Callable[[str], bool], configured: Callable[[str], bool] | None = None
    ) -> Router:
        """The same rules over a narrower set of providers (a per-turn provider choice)."""
        return Router(self._rules, self._models, provider_ok, self._alias_ok, self._pin, configured)

    def gated(self, alias_ok: Callable[[str], bool]) -> Router:
        """The same rules over the aliases `alias_ok` lets through (P6, public mode)."""
        return Router(
            self._rules, self._models, self._provider_ok, alias_ok, self._pin, self._configured
        )

    def pinned(self, alias: str) -> Router:
        """The same rules with every call answered by `alias` (the eval `model` suite)."""
        return Router(
            self._rules, self._models, self._provider_ok, self._alias_ok, alias, self._configured
        )

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
        for key, want in when.items():
            if key == "pack":  # 0.2.0: one pack id or a list of them
                packs = want if isinstance(want, list) else [want]
                if req.pack not in packs:
                    return False
                continue
            if key not in actual or actual[key] != want:
                return False
        return True

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
        if not self._configured(model.get("provider", "")) or (
            self._alias_ok is not None and not self._alias_ok(alias)
        ):
            raise ProviderError(
                "provider_error",
                f"the pinned model {alias} has no configured provider for this request",
                retryable=False,
            )
        for rule in self._rules:
            if rule.get("split") is not None:
                continue  # a pinned run chose its model: it is never split (0.2.0)
            if self.matches(rule.get("when") or {}, req):
                return Route(rule=rule["id"], model=alias, alternatives=[])
        raise ProviderError("provider_error", "no router rule matched", retryable=False)

    def _route_split(self, rule: dict[str, Any], req: RouteRequest) -> Route | None:
        """The canary rule: the turn's arm, else the other arm, else None (the rule does not
        apply). The other arm is the only alternative, so a failing candidate falls back."""
        split = rule["split"]
        bucket = bucket_of(req.turn_id or "")
        arm = "candidate" if bucket < float(split.get("fraction", 0)) else "base"
        aliases = {"candidate": split["candidate_alias"], "base": split["base_alias"]}
        other = "base" if arm == "candidate" else "candidate"
        usable = [a for a in (arm, other) if self._usable(aliases[a], req, rule)]
        if not usable:
            return None
        chosen = usable[0]
        alternatives = [aliases[a] for a in usable[1:] if aliases[a] != aliases[chosen]]
        return Route(
            rule=rule["id"],
            model=aliases[chosen],
            alternatives=alternatives,
            arm=chosen,
            bucket=round(bucket, 4),
        )

    def route(self, req: RouteRequest) -> Route:
        if self._pin is not None:
            return self._route_pinned(req, self._pin)
        for rule in self._rules:
            if not self.matches(rule.get("when") or {}, req):
                continue
            if rule.get("split") is not None:
                if req.internal:
                    continue  # eval case turns measure the served set; they are never split
                split_route = self._route_split(rule, req)
                if split_route is None:
                    continue  # neither arm can answer here: the next rule routes the call
                return split_route
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
        if route.arm is not None:
            span.set(arm=route.arm, bucket=route.bucket)
    return route
