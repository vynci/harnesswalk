"""The fake provider is scripted, deterministic and strict (model_provider.md)."""

from pathlib import Path

import pytest

from harnesswalk.execution.providers import ProviderError, ProviderRequest, ToolSchema, TurnRef
from harnesswalk.execution.providers.fake import (
    FakeProvider,
    fake_embed,
    find_fixture,
    load_fixture,
)
from harnesswalk.types import ConfigError, Message
from tests.spec.support import ROOT
from tests.unit.support import write_fixture

SPEC_FIXTURES = ROOT / "spec" / "conformance" / "fixtures"
TOOL = ToolSchema("lookup_order", "d", {"type": "object"})


def req(turn_id="t0", index=0, session="s1", messages=None, tools=(TOOL,)) -> ProviderRequest:
    return ProviderRequest(
        model="fake-small",
        messages=messages or [Message("user", "Where is A-1001?")],
        tools=list(tools),
        turn=TurnRef("tenant", session, turn_id, index),
    )


@pytest.mark.anyio
async def test_replays_responses_in_order_then_exhausts() -> None:
    fake = FakeProvider(load_fixture(SPEC_FIXTURES / "tool_call.yaml"))
    first = await fake.complete(req())
    assert first.finish_reason == "tool_calls"
    assert first.tool_calls[0].name == "lookup_order" and first.tool_calls[0].call_id == "call_1"
    assert (first.usage.input_tokens, first.usage.output_tokens) == (905, 24)
    second = await fake.complete(req(messages=[Message("tool", '{"status":"shipped"}')]))
    assert second.finish_reason == "stop" and "30 October" in second.text
    with pytest.raises(ProviderError) as exc:
        await fake.complete(req())
    assert exc.value.code == "fixture_exhausted" and not exc.value.retryable
    assert fake.unused() == []


@pytest.mark.anyio
async def test_expect_mismatch() -> None:
    fake = FakeProvider(load_fixture(SPEC_FIXTURES / "plain_answer.yaml"))
    with pytest.raises(ProviderError) as exc:
        await fake.complete(req(tools=()))  # fixture expects tools_enabled: true
    assert exc.value.code == "fixture_mismatch"


@pytest.mark.anyio
async def test_cursor_per_session_and_turn(tmp_path: Path) -> None:
    write_fixture(tmp_path, "two", [[{"text": "first"}], [{"text": "second"}]])
    fake = FakeProvider(load_fixture(tmp_path / "two.yaml"))
    assert (await fake.complete(req("a", 0, "s1"))).text == "first"
    assert (await fake.complete(req("b", 0, "s2"))).text == "first"  # its own session
    assert (await fake.complete(req("c", 1, "s1"))).text == "second"
    with pytest.raises(ProviderError):
        await fake.complete(req("d", 2, "s1"))


def test_jsonl_fixtures_and_lookup() -> None:
    starter = ROOT / "datasets" / "_starter" / "fixtures" / "fake"
    path = find_fixture("two_turns", [starter, SPEC_FIXTURES])
    fixture = load_fixture(path)
    assert fixture["fixture"] == "two_turns" and len(fixture["turns"]) == 2
    with pytest.raises(ConfigError):
        find_fixture("nope", [starter])


def test_fake_embed_is_deterministic() -> None:
    a, b = fake_embed("hello"), fake_embed("hello")
    assert a == b and len(a) == 64 and fake_embed("other") != a
    assert all(-1 <= x <= 1 for x in a)
