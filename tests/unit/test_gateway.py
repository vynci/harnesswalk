"""The gateway's walk: history across turns, streamed replies, disabled nodes, provider opt-in."""

from pathlib import Path

import pytest

from harnesswalk.config.loader import load_settings
from harnesswalk.control.gateway import REPLY_MAX_BYTES, cap_reply
from tests.spec.support import ROOT, check_stream
from tests.unit.support import exits, harness, path_of, run_turn


@pytest.mark.anyio
async def test_second_turn_sees_the_first(tmp_path: Path) -> None:
    h = harness(tmp_path, "two_turns", profile=None)
    first = await run_turn(h, "Hello")
    session = first[0]["session_id"]
    second = await run_turn(h, "Is A-1002 shipped?", session_id=session)
    assert check_stream(second) == []
    assert second[0]["summary"]["turn_index"] == 1
    session_exit = exits(second, "session")[0]["summary"]
    assert session_exit["turns"] == 1 and session_exit["tokens"] > 0
    assert "still processing" in second[-1]["summary"]["reply"]


@pytest.mark.anyio
async def test_reply_streams_as_message_deltas(tmp_path: Path) -> None:
    h = harness(tmp_path, "plain_answer", overrides={"gateway.stream_reply_chars": 10})
    events = await run_turn(h, "What are your support hours?")
    assert check_stream(events) == []
    deltas = [e for e in events if e["type"] == "message_delta"]
    assert [d["summary"]["index"] for d in deltas] == list(range(len(deltas)))
    assert "".join(d["summary"]["delta"] for d in deltas) == events[-1]["summary"]["reply"]
    gateway_reply_enter = [
        e["seq"] for e in events if e.get("node_id") == "gateway" and e["type"] == "node_enter"
    ][-1]
    assert all(d["seq"] > gateway_reply_enter for d in deltas)


@pytest.mark.anyio
async def test_disabled_policy_is_skipped_but_still_audited(tmp_path: Path) -> None:
    h = harness(tmp_path, "approval_approve")
    events = await run_turn(h, "Please cancel order A-1002.", disabled=["policy"])
    policy = exits(events, "policy")[0]
    assert policy["status"] == "skipped" and policy["summary"]["rule"] == "disabled"
    assert exits(events, "audit")[0]["summary"]["decision"] == "allow"  # the bypass is on record
    assert "approval" not in path_of(events)
    assert exits(events, "tools")[0]["summary"]["executed"] is True


def test_reply_cap() -> None:
    assert cap_reply("short") == "short"
    capped = cap_reply("é" * REPLY_MAX_BYTES)
    assert len(capped.encode()) <= REPLY_MAX_BYTES and capped.endswith("[truncated]")


def test_hosted_providers_need_an_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    default = load_settings(ROOT, env={})
    assert not default.provider_configured("anthropic")
    assert default.provider_configured("ollama") and not default.provider_configured("fake")
    enabled = load_settings(ROOT, overrides={"models.providers.anthropic.enabled": True}, env={})
    assert enabled.provider_configured("anthropic")
    selected = load_settings(ROOT, provider="anthropic", env={})
    assert selected.provider_configured("anthropic") and not selected.provider_configured("ollama")
