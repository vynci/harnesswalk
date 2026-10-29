"""Every loop exit, checkpoints, and what happens when the model layer fails."""

import asyncio
from pathlib import Path

import pytest

from tests.spec.support import check_stream
from tests.unit.support import exits, harness, path_of, run_turn, write_fixture

TRACK = {"name": "track_shipment", "arguments": {"shipment_id": "S-1"}}


def calls(n: int, same: bool = False, usage=None) -> list[dict]:
    out = []
    for i in range(n):
        args = {"shipment_id": "S-1" if same else f"S-{i + 1}"}
        r = {"tool_calls": [{"name": "track_shipment", "arguments": args}]}
        if usage:
            r["usage"] = usage
        out.append(r)
    return out


def loop_exit(events) -> dict:
    return exits(events, "loop")[0]["summary"]


@pytest.mark.anyio
async def test_answer_and_checkpoints(tmp_path: Path) -> None:
    h = harness(tmp_path, "tool_call")
    events = await run_turn(h, "Where is order A-1001?")
    assert check_stream(events) == []
    assert loop_exit(events) == {"steps": 2, "exit_reason": "answer", "tool_calls": 1}
    cp = h.session.last_checkpoint("t1", events[0]["turn_id"])
    assert cp["step"] == 1 and cp["messages"][-1]["role"] == "tool"


@pytest.mark.anyio
async def test_max_steps_without_final_call(tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    write_fixture(fx, "busy", [calls(5)])
    h = harness(
        tmp_path,
        "busy",
        fixtures=fx,
        overrides={"loop.max_steps": 2, "loop.final_call_on_exit": False},
    )
    events = await run_turn(h, "Track everything")
    assert loop_exit(events)["exit_reason"] == "max_steps"
    assert len(exits(events, "model_layer")) == 2
    assert events[-1]["summary"]["exit"] == "limit"
    assert "maximum number of steps" in events[-1]["summary"]["reply"]


@pytest.mark.anyio
async def test_stuck_then_one_tools_off_answer(tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    write_fixture(
        fx,
        "stuck",
        [
            [
                *calls(3, same=True),
                {"text": "I keep getting S-1.", "expect": {"tools_enabled": False}},
            ]
        ],
    )
    h = harness(tmp_path, "stuck", fixtures=fx, overrides={"loop.stuck_repeats": 2})
    events = await run_turn(h, "Track S-1")
    assert loop_exit(events) == {"steps": 3, "exit_reason": "stuck", "tool_calls": 2}
    last_model = exits(events, "model_layer")[-1]["summary"]
    assert last_model["tools_enabled"] is False
    assert events[-1]["summary"]["reply"] == "I keep getting S-1."


@pytest.mark.anyio
async def test_token_budget_stops_without_another_call(tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    write_fixture(fx, "hungry", [calls(3, usage={"input_tokens": 90, "output_tokens": 20})])
    h = harness(tmp_path, "hungry", fixtures=fx, overrides={"loop.budget_tokens": 100})
    events = await run_turn(h, "Track")
    assert loop_exit(events)["exit_reason"] == "budget_tokens"
    assert len(exits(events, "model_layer")) == 1
    assert "token budget" in events[-1]["summary"]["reply"]


@pytest.mark.anyio
async def test_dollar_budget(tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    write_fixture(fx, "pricey", [calls(3, usage={"input_tokens": 10, "output_tokens": 10})])
    overrides = {
        "loop.budget_usd": 0.5,
        "models.models.fake-small.price_in_per_mtok": 1_000_000,
        "models.models.fake-small.price_out_per_mtok": 1_000_000,
    }
    h = harness(tmp_path, "pricey", fixtures=fx, overrides=overrides)
    events = await run_turn(h, "Track")
    assert loop_exit(events)["exit_reason"] == "budget_usd"
    assert events[-1]["cost"]["usd"] == 20.0
    assert exits(events, "model_layer")[0]["summary"]["price_known"] is True


@pytest.mark.anyio
async def test_timeout(tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    slow = calls(2)
    slow[0]["latency_ms"] = 120
    write_fixture(fx, "slow", [slow])
    h = harness(tmp_path, "slow", fixtures=fx, overrides={"loop.timeout_s": 0.05})
    events = await run_turn(h, "Track")
    assert loop_exit(events)["exit_reason"] == "timeout"
    assert len(exits(events, "model_layer")) == 1


@pytest.mark.anyio
async def test_interrupt(tmp_path: Path) -> None:
    from harnesswalk.control.gateway import TurnRequest
    from harnesswalk.types import Identity

    fx = tmp_path / "fx"
    slow = calls(2)
    slow[0]["latency_ms"] = 100
    write_fixture(fx, "slow", [slow])
    h = harness(tmp_path, "slow", fixtures=fx)
    state = await h.gateway.start_turn(TurnRequest("Track", Identity("t1", None, ["user"], False)))
    await asyncio.sleep(0.03)
    state.ctx.interrupted = True
    await h.gateway.wait(state.ctx.turn_id, 5)
    events = h.tracer.get_turn("t1", state.ctx.turn_id)
    assert loop_exit(events)["exit_reason"] == "interrupted"
    assert events[-1]["summary"]["exit"] == "cancelled"


@pytest.mark.anyio
async def test_provider_failure_ends_with_an_error_notice(tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    write_fixture(fx, "short", [calls(1)])  # the second call has no response
    h = harness(tmp_path, "short", fixtures=fx)
    events = await run_turn(h, "Track")
    assert check_stream(events) == []
    errors = [e for e in events if e["type"] == "error"]
    assert [(e["node_id"], e["error"]["code"]) for e in errors] == [
        ("providers", "fixture_exhausted"),
        ("model_layer", "fixture_exhausted"),
        ("loop", "provider_error"),
    ]
    assert events[-1]["summary"]["exit"] == "error"
    assert path_of(events)[-3:] == ["output_guardrails", "gateway", "trace"]


@pytest.mark.anyio
async def test_cancel_closes_every_span(tmp_path: Path) -> None:
    from harnesswalk.control.gateway import TurnRequest
    from harnesswalk.types import Identity

    h = harness(tmp_path, "approval_approve")
    state = await h.gateway.start_turn(
        TurnRequest("Cancel A-1002", Identity("t1", None, ["user"], False))
    )
    await asyncio.sleep(0.05)  # now waiting for approval
    h.gateway.cancel("t1", state.ctx.turn_id)
    await h.gateway.wait(state.ctx.turn_id, 5)
    events = h.tracer.get_turn("t1", state.ctx.turn_id)
    assert check_stream(events) == []
    assert events[-1]["status"] == "error" and events[-1]["summary"]["exit"] == "cancelled"
    assert h.approvals.pending("t1") == []
