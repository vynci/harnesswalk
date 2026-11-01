"""P5: node_resumed says which control released the pause (events.md section 11)."""

import asyncio
from pathlib import Path

import pytest

from harnesswalk.control.gateway import TurnRequest
from harnesswalk.runtime import Harness
from harnesswalk.types import Identity
from tests.spec.support import event_validator
from tests.unit.support import run_turn, settings


@pytest.mark.anyio
async def test_node_resumed_names_the_resume_mode(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    loop = asyncio.get_running_loop()
    holder: dict[str, str] = {}
    modes = {"gateway": "step", "memory": "step_over", "loop": "continue"}

    def on_event(event: dict) -> None:
        if event["type"] == "node_paused":
            mode = modes.get(event["node_id"], "step")
            loop.call_soon(lambda: h.gateway.resume("t1", holder["turn_id"], mode))

    h.bus.subscribe(on_event)
    state = await h.gateway.start_turn(
        TurnRequest(message="Hi", identity=Identity("t1", None, ["user"], False), step_mode=True)
    )
    holder["turn_id"] = state.ctx.turn_id
    await h.gateway.wait(state.ctx.turn_id, 5)
    events = h.tracer.get_turn("t1", state.ctx.turn_id)
    resumed = {e["node_id"]: e["summary"] for e in events if e["type"] == "node_resumed"}
    assert resumed["gateway"] == {"reason": "breakpoint", "mode": "step"}
    assert resumed["input_guardrails"]["mode"] == "step"
    assert resumed["memory"]["mode"] == "step_over"
    assert resumed["loop"]["mode"] == "continue"
    validator = event_validator()
    assert all(not list(validator.iter_errors(e)) for e in events)
    await h.aclose()


@pytest.mark.anyio
async def test_an_approval_resume_reports_mode_approval(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "approval_approve"))
    events = await run_turn(h, "Please cancel order A-1002.", approval="approve")
    resumed = [e for e in events if e["type"] == "node_resumed"]
    assert [e["summary"]["mode"] for e in resumed] == ["approval"]
    assert resumed[0]["summary"]["reason"] == "approval"
    await h.aclose()


def test_schema_rejects_an_unknown_resume_mode() -> None:
    validator = event_validator()
    bad = {
        "schema_version": "0.1.0", "event_id": "e1", "seq": 3, "ts": "2026-11-01T00:00:00Z",
        "trace_id": "a" * 32, "turn_id": "t", "session_id": "s", "tenant_id": "x",
        "type": "node_resumed", "node_id": "loop", "status": "active",
        "summary": {"reason": "breakpoint", "mode": "skip"},
    }  # fmt: skip
    assert list(validator.iter_errors(bad))
    bad["summary"]["mode"] = "step_over"
    assert not list(validator.iter_errors(bad))
