"""Fixes from the Phase 3 integration review (plans/harnesswalk/phase-3), one test each."""

from pathlib import Path

import pytest

from harnesswalk.execution.registry import ToolRegistry, ToolSpec
from harnesswalk.types import ConfigError, ToolCall
from tests.unit.support import make_ctx


def spec(name: str, action_class: str = "read", **kw) -> ToolSpec:
    return ToolSpec(name, "d", {"type": "object"}, action_class, 1000, impl=lambda a: "ok", **kw)


def test_public_mode_offers_pure_read_tools_only() -> None:
    reg = ToolRegistry()
    reg.register(spec("table_lookup", pure=True, source="dataset"))
    reg.register(spec("pack_code", pure=False, source="dataset", entrypoint="x:y"))
    assert [s.name for s in reg.list("public", None)] == ["table_lookup"]
    assert [s.name for s in reg.list("dev", None)] == ["table_lookup", "pack_code"]


def test_memory_write_is_reserved_for_remember() -> None:
    reg = ToolRegistry()
    with pytest.raises(ConfigError, match="reserved for remember"):
        reg.register(spec("sneaky", "memory.write", source="dataset"))
    reg.register(spec("remember", "memory.write", pure=True))


@pytest.mark.anyio
async def test_a_tool_never_offered_in_this_mode_is_not_run(tmp_path: Path) -> None:
    from tests.unit.support import harness

    h = harness(tmp_path, mode="public")
    ran: list[str] = []
    h.registry.register(spec("pack_code", pure=False, source="dataset", entrypoint="x:y"))
    h.registry.get("pack_code").impl = lambda a: ran.append("ran") or "ok"
    ctx, _ = make_ctx()
    ctx.mode = "public"
    obs = await h.tools.invoke(ctx, ToolCall("c1", "pack_code", {}))
    assert obs.outcome == "error" and "not offered" in obs.content and not ran


@pytest.mark.anyio
async def test_a_refused_request_or_a_byo_key_does_not_mark_the_provider_down() -> None:
    """One tenant's bad key used to mark the operator's provider unhealthy for everyone."""
    from harnesswalk.execution.model_layer import ModelLayer, ModelRequest
    from harnesswalk.execution.providers import ProviderError, ProviderResponse, ProviderUsage
    from harnesswalk.execution.providers.health import ProviderHealth
    from harnesswalk.execution.router import Router
    from harnesswalk.types import Message

    errors = {"next": ProviderError("provider_error", "HTTP 401", client_error=True)}

    class Flaky:
        name = "anthropic"

        async def complete(self, request):
            raise errors["next"]

    class Up:
        name = "ollama"

        async def complete(self, request):
            return ProviderResponse("hi", [], "stop", ProviderUsage(1, 1))

    models = {
        "hosted": {"provider": "anthropic", "price_in_per_mtok": 0, "price_out_per_mtok": 0},
        "local": {"provider": "ollama", "price_in_per_mtok": 0, "price_out_per_mtok": 0},
    }
    providers = {"anthropic": Flaky(), "ollama": Up()}
    health = ProviderHealth(providers)
    router = Router([{"id": "d", "when": {}, "use": ["hosted", "local"]}], models, health.ok)
    layer = ModelLayer(providers, router, models, health=health)
    request = ModelRequest([Message("user", "hi")], [], step=1)

    await layer.complete(make_ctx()[0], request)  # a 401: refused, not down
    assert health.report()["anthropic"] != "unhealthy"
    errors["next"] = ProviderError("provider_error", "connection refused")
    await layer.complete(make_ctx()[0], request, api_key="sk-caller-own")  # the caller's key
    assert health.report()["anthropic"] != "unhealthy"
    await layer.complete(make_ctx()[0], request)  # the server's key, provider down: marked
    assert health.report()["anthropic"] == "unhealthy"


@pytest.mark.anyio
async def test_the_writer_gate_is_policys_decision_with_roles(tmp_path: Path) -> None:
    """The writer had its own matcher that skipped roles; it now asks Policy."""
    from tests.unit.test_memory_writer import fact, writer

    admins_only = {
        "roles": {"user": {"allow_action_classes": ["read"]}},
        "rules": [{"id": "admins.remember", "roles": ["admin"], "decision": "allow"}],
    }
    w = writer(tmp_path, policy=admins_only)
    ctx, _ = make_ctx()
    report = await w.run(ctx, [fact("preferred name", "Sam")])
    assert (report.written, report.rejected) == (0, 1)
    ctx.identity.roles = ["admin"]
    assert w.policy.decide_memory_write(["admin"]).rule == "admins.remember"


@pytest.mark.anyio
async def test_memory_writes_are_audited_and_tools_scrub_with_the_pack_patterns(
    tmp_path: Path,
) -> None:
    from tests.unit.support import harness, run_turn

    h = harness(
        tmp_path,
        "memory_written",
        overrides={"context.stores": ["procedural", "session", "semantic"]},
    )
    await run_turn(h, "Please remember that my preferred name is Sam.")
    entries = [r.entry for r in h.audit.list("t1", 20)]
    written = [e for e in entries if e["action"] == "memory_written"]
    assert written and written[0]["extra"]["store"] == "semantic"
    assert written[0]["actor"] == "gate:auto" and "Sam" in str(written[0]["after"])
    assert h.audit.verify("t1")["ok"]
    # Audit snapshots of tool results go through the input guardrails' detector.
    assert h.tools.scrubber is h.input_guardrails.pii
