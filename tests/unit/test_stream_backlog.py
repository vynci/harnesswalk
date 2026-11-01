"""The stored backlog of a turn or eval run stream, and a run that ends while it is sent.

A stream sends the stored events first, then live ones. When the turn (or eval run) ends while
that backlog is being sent, the rest is stored too and must still be sent, up to the end event.
"""

from pathlib import Path
from typing import Any

import pytest

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.ops.ops_events import OpsEvents
from harnesswalk.runtime import Harness
from tests.unit.support import run_turn, settings

TENANT = "t1"


@pytest.mark.anyio
async def test_a_turn_that_ends_while_its_backlog_is_sent_still_streams_to_turn_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    try:
        events = await run_turn(h, "What are your support hours?", tenant=TENANT)
        turn_id = events[0]["turn_id"]
        stored = h.tracer.get_turn
        calls = {"n": 0}

        def snapshot_before_the_end(tenant_id: str, turn: str, after_seq: int = -1) -> list:
            calls["n"] += 1
            got = stored(tenant_id, turn, after_seq)
            return got[:3] if calls["n"] == 1 else got  # the first read predates the end

        monkeypatch.setattr(h.tracer, "get_turn", snapshot_before_the_end)
        streamed = [e async for e in h.tracer.subscribe(TENANT, turn_id) if e is not None]
        assert [e["seq"] for e in streamed] == [e["seq"] for e in events]
        assert streamed[-1]["type"] == "turn_end"
    finally:
        await h.aclose()


@pytest.mark.anyio
async def test_an_eval_run_that_ends_while_its_backlog_is_sent_still_streams_to_its_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ops = OpsEvents(Database(tmp_path / "trace.db", ""), validate=False)
    for type_ in ("eval_run_started", "eval_case_finished", "eval_run_finished"):
        ops.emit("dev", "eval:run_1", type_, "evals", "done", {})
    stored = ops.list
    calls = {"n": 0}

    def snapshot_before_the_end(*args: Any, **kwargs: Any) -> list:
        calls["n"] += 1
        got = stored(*args, **kwargs)
        return got[:1] if calls["n"] == 1 else got

    monkeypatch.setattr(ops, "list", snapshot_before_the_end)
    streamed = [
        e["type"]
        async for e in ops.follow("dev", "eval:run_1", until="eval_run_finished", live=True)
        if e is not None
    ]
    assert streamed == ["eval_run_started", "eval_case_finished", "eval_run_finished"]
