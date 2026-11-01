"""Router P3: cost and risk rules, price ceilings, hints, health, cascade, pinned models."""

from pathlib import Path

import httpx
import pytest

from harnesswalk.config.loader import load_settings
from harnesswalk.execution.model_layer import ModelLayer, ModelRequest, confidence
from harnesswalk.execution.providers import ProviderError, ProviderResponse, ProviderUsage
from harnesswalk.execution.providers.anthropic import AnthropicProvider
from harnesswalk.execution.providers.health import ProviderHealth
from harnesswalk.execution.router import Router, RouteRequest
from harnesswalk.types import Message, ToolCall
from tests.spec.support import ROOT
from tests.unit.support import exits, harness, make_ctx, run_turn

MODELS = {
    "local": {"provider": "fake", "price_out_per_mtok": 0},
    "haiku": {"provider": "anthropic", "price_in_per_mtok": 0.1, "price_out_per_mtok": 0.5},
    "sonnet": {"provider": "anthropic", "price_in_per_mtok": 2, "price_out_per_mtok": 10},
}
RULES = [
    {
        "id": "cheap",
        "when": {"cost": "low"},
        "use": ["sonnet", "haiku"],
        "max_price_out_per_mtok": 1,
    },
    {"id": "risky", "when": {"risk": "high"}, "use": ["sonnet", "local"]},
    {"id": "default", "when": {}, "use": ["local", "haiku", "sonnet"]},
]


def test_cost_risk_price_ceiling_and_hint() -> None:
    r = Router(RULES, MODELS, lambda p: True)
    assert r.route(RouteRequest(1, "first", cost="low")).model == "haiku"  # sonnet over the cap
    assert r.route(RouteRequest(2, "after_tool", risk="high")).model == "sonnet"
    hinted = r.route(RouteRequest(1, "first", hint="sonnet"))
    assert (hinted.model, hinted.alternatives) == ("sonnet", ["local", "haiku"])
    assert r.route(RouteRequest(1, "first", hint="not-allowed")).model == "local"


def test_a_rule_priced_out_falls_through_but_an_unconfigured_one_fails() -> None:
    """P5 integration: when the cap drops every configured model of a rule, the next matching
    rule routes the call; a rule with no configured model at all is still an error."""
    pricey = {**MODELS, "haiku": {**MODELS["haiku"], "price_out_per_mtok": 5}}
    r = Router(RULES, pricey, lambda p: True)
    picked = r.route(RouteRequest(1, "first", cost="low"))
    assert (picked.rule, picked.model) == ("default", "local")
    only_fake = Router(RULES, MODELS, lambda p: p == "fake")
    with pytest.raises(ProviderError):
        only_fake.route(RouteRequest(1, "first", cost="low"))  # cheap: sonnet, haiku unconfigured


def test_unhealthy_providers_are_skipped_until_the_next_probe() -> None:
    health = ProviderHealth({"fake": object(), "anthropic": object()})
    r = Router(RULES, MODELS, lambda p: health.ok(p))
    assert r.route(RouteRequest(1, "first")).model == "local"  # never probed counts as healthy
    health.mark_failed("fake", "provider_error")
    assert r.route(RouteRequest(1, "first")).model == "haiku"
    assert health.report()["fake"] == "unhealthy"


@pytest.mark.anyio
async def test_probes_use_list_models_and_never_raise() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("anthropic-version")))
        return httpx.Response(200, json={"data": []})

    provider = AnthropicProvider(transport=httpx.MockTransport(handler))
    import os

    os.environ["HARNESSWALK_TEST_KEY"] = "sk-test"
    provider.api_key_env = "HARNESSWALK_TEST_KEY"
    try:
        health = ProviderHealth({"anthropic": provider, "fake": object()})
        assert await health.probe_all() == {"anthropic": True, "fake": True}
        assert seen == [("/v1/models", "2023-06-01")]
    finally:
        del os.environ["HARNESSWALK_TEST_KEY"]
    down = AnthropicProvider(
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x")))
    )
    down.api_key_env = "HARNESSWALK_NO_SUCH_KEY"
    assert await ProviderHealth({"anthropic": down}).probe("anthropic") is False


def test_confidence_signal() -> None:
    assert confidence("Order A-1001 shipped.", [], "stop", set()) == 1.0
    assert confidence("", [], "stop", set()) == 0.0
    assert confidence("text", [], "length", set()) == 0.0
    assert confidence(None, [ToolCall("c", "rm_rf", {})], "tool_calls", {"lookup"}) == 0.0
    assert confidence("I'm not sure, maybe Tuesday.", [], "stop", set()) == 0.5


class Scripted:
    """A stand-in provider (no network): returns one scripted text, records calls."""

    def __init__(self, name: str, text: str) -> None:
        self.name, self.text, self.calls = name, text, 0

    async def complete(self, request):
        self.calls += 1
        return ProviderResponse(self.text, [], "stop", ProviderUsage(10, 5))


@pytest.mark.anyio
async def test_cascade_climbs_from_a_hesitant_local_answer_to_hosted() -> None:
    local, hosted = Scripted("fake", "I don't know."), Scripted("anthropic", "It ships Tuesday.")
    providers = {"fake": local, "anthropic": hosted}
    rules = [{"id": "default", "when": {}, "use": ["local", "haiku"]}]
    off = ModelLayer(providers, Router(rules, MODELS, lambda p: True), MODELS)
    ctx, _ = make_ctx()
    request = ModelRequest([Message("user", "When?")], [], 1)
    plain = await off.complete(ctx, request)
    assert (plain.model, plain.cascade) == ("local", None)
    on = ModelLayer(
        providers,
        Router(rules, MODELS, lambda p: True),
        MODELS,
        cascade={"enabled": True, "min_confidence": 0.6},
    )
    climbed = await on.complete(ctx, request)
    assert (climbed.model, climbed.text, climbed.cascade) == (
        "haiku",
        "It ships Tuesday.",
        "low_confidence",
    )
    assert climbed.fallback is True and climbed.usage.input_tokens == 20  # both calls are paid


def test_pinned_anthropic_models_and_rules() -> None:
    s = load_settings(ROOT, env={})
    models = s.get("models.models")
    assert models["hosted-haiku"]["name"] == "claude-haiku-5-5"
    assert models["hosted-sonnet"]["name"] == "claude-sonnet-5-5"
    assert (
        models["hosted-haiku"]["price_in_per_mtok"],
        models["hosted-haiku"]["price_out_per_mtok"],
    ) == (0.1, 0.5)
    assert (
        models["hosted-sonnet"]["price_in_per_mtok"],
        models["hosted-sonnet"]["price_out_per_mtok"],
    ) == (2.0, 10.0)
    assert "hosted-small" not in models
    rules = {r["id"]: r for r in s.get("models.router.rules")}
    assert rules["summarize"]["use"][:2] == ["local-small", "local-8b"]  # local first
    assert "hosted-haiku" in rules["classify"]["use"] and rules["vision"]["use"] == [
        "hosted-sonnet"
    ]
    assert s.get("models.router.cascade.enabled") is False


@pytest.mark.anyio
async def test_params_pass_through_and_risk_rises_after_a_write(tmp_path: Path) -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "usage": {},
                "stop_reason": "end_turn",
            },
        )

    import os

    os.environ["HARNESSWALK_TEST_KEY"] = "sk-test"
    try:
        p = AnthropicProvider(
            api_key_env="HARNESSWALK_TEST_KEY", transport=httpx.MockTransport(handler)
        )
        from harnesswalk.execution.providers import ProviderRequest

        await p.complete(
            ProviderRequest(
                "claude-haiku-5-5",
                [Message("user", "hi")],
                [],
                params={"thinking": {"type": "disabled"}},
            )
        )
    finally:
        del os.environ["HARNESSWALK_TEST_KEY"]
    assert (
        bodies[0]["thinking"] == {"type": "disabled"} and bodies[0]["model"] == "claude-haiku-5-5"
    )

    rules = [
        {"id": "risky", "when": {"risk": "high"}, "use": ["fake-small"]},
        {"id": "default", "when": {}, "use": ["fake-small"]},
    ]
    h = harness(tmp_path, "approval_approve", overrides={"models.router.rules": rules})
    events = await run_turn(h, "Please cancel order A-1002.", approval="approve")
    assert [e["summary"]["rule"] for e in exits(events, "router")] == ["default", "risky"]


def test_unknown_alias_hint_is_rejected_over_http(tmp_path: Path) -> None:
    from harnesswalk.control.gateway import GatewayError, TurnRequest
    from harnesswalk.types import Identity

    h = harness(tmp_path, "plain_answer")
    with pytest.raises(GatewayError):
        h.gateway._validate(
            TurnRequest("hi", Identity("t1", None, ["user"], False), model="no-such-model")
        )
