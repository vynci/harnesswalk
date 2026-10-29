"""Rule-based routing by step kind and mode, configured providers only."""

import pytest

from harnesswalk.config.loader import load_settings
from harnesswalk.execution.providers import ProviderError
from harnesswalk.execution.router import Router, RouteRequest
from harnesswalk.types import ConfigError
from tests.spec.support import ROOT

MODELS = {
    "small": {"provider": "ollama"},
    "big": {"provider": "ollama"},
    "hosted": {"provider": "anthropic"},
    "fake": {"provider": "fake"},
    "eyes": {"provider": "anthropic", "modalities": ["text", "image"]},
}
RULES = [
    {"id": "vision", "when": {"modality": "image"}, "use": ["eyes"]},
    {"id": "summarize", "when": {"step": "summarize"}, "use": ["small", "hosted"]},
    {"id": "public", "when": {"mode": "public"}, "use": ["small"]},
    {"id": "first", "when": {"step": 1, "tools": True}, "use": ["small", "big"]},
    {"id": "default", "when": {}, "use": ["big", "hosted", "fake"]},
]


def router(*providers: str) -> Router:
    return Router(RULES, MODELS, lambda p: p in providers)


def test_rules_in_order() -> None:
    r = router("ollama", "anthropic")
    assert r.route(RouteRequest(step=1, step_kind="first")).rule == "first"
    assert r.route(RouteRequest(step=2, step_kind="after_tool")).model == "big"
    assert r.route(RouteRequest(step=1, step_kind="first", tools_enabled=False)).rule == "default"
    assert r.route(RouteRequest(step=0, step_kind="summarize")).model == "small"
    assert r.route(RouteRequest(step=3, step_kind="next", mode="public")).rule == "public"
    assert r.route(RouteRequest(step=1, step_kind="first", modality="image")).model == "eyes"


def test_unconfigured_providers_are_skipped_and_alternatives_kept() -> None:
    route = router("anthropic", "fake").route(RouteRequest(step=2, step_kind="next"))
    assert (route.model, route.alternatives) == ("hosted", ["fake"])
    route = router("fake").route(RouteRequest(step=2, step_kind="next"))
    assert (route.rule, route.model, route.alternatives) == ("default", "fake", [])


def test_no_usable_model_is_a_provider_error() -> None:
    with pytest.raises(ProviderError):
        router("fake").route(RouteRequest(step=0, step_kind="summarize"))


def test_config_needs_a_default_rule_and_known_aliases() -> None:
    with pytest.raises(ConfigError):
        load_settings(
            ROOT,
            provider="fake",
            overrides={
                "models.router.rules": [{"id": "x", "when": {"step": 1}, "use": ["fake-small"]}]
            },
            env={},
        )
    with pytest.raises(ConfigError):
        load_settings(
            ROOT,
            provider="fake",
            overrides={"models.router.rules": [{"id": "d", "when": {}, "use": ["ghost"]}]},
            env={},
        )


def test_shipped_config_routes_per_provider() -> None:
    for provider, model in (("fake", "fake-small"), ("ollama", "local-8b")):
        s = load_settings(ROOT, provider=provider, env={})
        rules, models = s.get("models.router.rules"), s.get("models.models")
        r = Router(rules, models, lambda p, provider=provider: p == provider)
        assert r.route(RouteRequest(step=1, step_kind="first")).model == model
