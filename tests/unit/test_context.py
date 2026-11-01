"""Context budget per slice, stable-first order, and compaction into a running summary."""

from pathlib import Path

import pytest

from harnesswalk.types import ConfigError
from tests.unit.support import exits, harness, run_turn, settings, write_fixture

LONG = "Here is a long answer that keeps going so the session slice fills up quickly. " * 3


@pytest.mark.anyio
async def test_slices_and_budget(tmp_path: Path) -> None:
    h = harness(tmp_path, "plain_answer")
    events = await run_turn(h, "What are your support hours?")
    s = exits(events, "context_builder")[0]["summary"]
    assert set(s["slices"]) == {
        "procedural",
        "semantic",
        "episodic",
        "knowledge",
        "session",
        "user",
    }
    assert s["used_tokens"] == sum(s["slices"].values()) <= 16000 - 1024
    assert s["stable_prefix_tokens"] == s["slices"]["procedural"] > 0
    assert s["compacted"] is False and s["headroom_tokens"] == 16000 - 1024 - s["used_tokens"]
    ctx = h.context_builder.last["t1"]
    assert (
        ctx.messages[0].role == "system"
        and ctx.messages[-1].content == "What are your support hours?"
    )


@pytest.mark.anyio
async def test_history_then_compaction(tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    write_fixture(
        fx, "chat", [[{"text": LONG}], [{"text": LONG}], [{"text": LONG}], [{"text": "ok"}]]
    )
    h = harness(
        tmp_path,
        "chat",
        fixtures=fx,
        overrides={"context.slices.session": 100, "context.compaction.keep_recent_turns": 1},
    )
    summaries = []
    session = None
    for i in range(4):
        events = await run_turn(h, f"Question number {i}?", session_id=session)
        session = events[0]["session_id"]
        summaries.append(exits(events, "context_builder")[0]["summary"])
    assert summaries[0]["slices"]["session"] == 0
    assert any(s["compacted"] for s in summaries[1:])
    compacted = next(s for s in summaries if s["compacted"])
    assert compacted["compaction"]["method"] == "truncation"
    assert compacted["slices"]["session"] <= 100
    last = h.context_builder.last["t1"]
    # P6: the summary is quoted data in a user-role message, never a system message.
    assert any(
        m.role == "user"
        and '<retrieved_data kind="summary">\nSummary of the conversation' in m.content
        for m in last.messages
    )
    assert not any(
        "Summary of the conversation" in m.content for m in last.messages if m.role == "system"
    )


@pytest.mark.anyio
async def test_blocked_turns_are_not_history(tmp_path: Path) -> None:
    h = harness(tmp_path, "plain_answer", overrides={"input_guardrails.size.max_chars": 10})
    events = await run_turn(h, "this message is far too long")
    assert events[-1]["summary"]["exit"] == "blocked"


def test_procedural_overflow_is_a_config_error(tmp_path: Path) -> None:
    from harnesswalk.runtime import Harness

    with pytest.raises(ConfigError):
        Harness(settings(tmp_path, overrides={"context.slices.procedural": 5}))
