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
