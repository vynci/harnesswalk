"""Approval resume after a restart, cut-off turns, and the expiry sweeper (P3)."""

import asyncio
import time
from pathlib import Path

import pytest

from harnesswalk.control.gateway import TurnRequest
from harnesswalk.runtime import Harness
from harnesswalk.turns import turn_summary
from harnesswalk.types import Identity
from tests.spec.support import check_stream, simple_path
from tests.unit.support import settings

TENANT = "t1"


def ident() -> Identity:
    return Identity(TENANT, None, ["user"], anonymous=False)


async def start(h: Harness, message: str, **kw) -> str:
    state = await h.gateway.start_turn(TurnRequest(message=message, identity=ident(), **kw))
    return state.ctx.turn_id


async def wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition never held")
        await asyncio.sleep(0.01)


def paused_on_approval(h: Harness, turn_id: str):
    def check() -> bool:
        s = turn_summary(h.tracer, h.session, TENANT, turn_id)
        return bool(s and s["paused_at_node"] == "approval")

    return check


@pytest.mark.anyio
async def test_graceful_restart_resumes_the_same_turn(tmp_path: Path) -> None:
    cfg = settings(tmp_path, "approval_approve")
    a = Harness(cfg)
    turn_id = await start(a, "Please cancel order A-1002.")
    await wait_for(paused_on_approval(a, turn_id))
    approval_id = turn_summary(a.tracer, a.session, TENANT, turn_id)["pending_approval_id"]
    await a.aclose()  # graceful: the waiting turn is detached, its record stays pending

    b = Harness(settings(tmp_path, "approval_approve"))
    assert b.approvals.get(approval_id, TENANT)["status"] == "pending"
    recovered = await b.start()
    assert recovered == {"resumed": [turn_id], "closed": []}
    summary = turn_summary(b.tracer, b.session, TENANT, turn_id)
    assert (summary["status"], summary["pending_approval_id"]) == ("paused", approval_id)
    b.approvals.decide(approval_id, TENANT, "user:t1", "approve", None)
    await b.gateway.wait(turn_id, 5)
    events = b.tracer.get_turn(TENANT, turn_id)
    assert check_stream(events) == []
    assert simple_path(events) == [
        "gateway", "input_guardrails", "context_builder", "memory", "loop", "model_layer",
        "tools", "policy", "audit", "approval", "audit", "audit", "model_layer",
        "output_guardrails", "gateway", "trace",
    ]  # fmt: skip
    assert events[-1]["summary"]["exit"] == "answered"
    assert "cancelled" in events[-1]["summary"]["reply"]
    assert b.audit.verify(TENANT)["records"] == 3
    await b.aclose()


@pytest.mark.anyio
async def test_expired_while_down_resumes_as_a_deny(tmp_path: Path) -> None:
    a = Harness(settings(tmp_path, "approval_deny", overrides={"approval.timeout_s": 1}))
    turn_id = await start(a, "Please cancel order A-1002.")
    await wait_for(paused_on_approval(a, turn_id))
    await a.aclose()
    await asyncio.sleep(1.1)
    b = Harness(settings(tmp_path, "approval_deny"))
    await b.start()
    await b.gateway.wait(turn_id, 5)
    events = b.tracer.get_turn(TENANT, turn_id)
    decided = [e for e in events if e["type"] == "approval_decided"][0]["summary"]
    assert (decided["decision"], decided["decided_by"]) == ("expired", "system")
    tools = [e for e in events if e["type"] == "node_exit" and e.get("node_id") == "tools"]
    assert tools[0]["summary"]["executed"] is False
    assert check_stream(events) == []
    await b.aclose()


@pytest.mark.anyio
async def test_a_cut_off_turn_is_closed_and_keeps_turn_index_unique(tmp_path: Path) -> None:
    a = Harness(settings(tmp_path, "plain_answer"))
    state = await a.gateway.start_turn(
        TurnRequest(message="What are your hours?", identity=ident(), step_mode=True)
    )
    turn_id, session_id = state.ctx.turn_id, state.ctx.session_id
    await wait_for(lambda: state.ctx.emit.breakpoints.paused_node is not None)
    # A crash: nothing closes the stream (the task stops without emitting anything).
    state.ctx.detached = state.ctx.emit.detached = True
    await a.aclose()

    b = Harness(settings(tmp_path, "plain_answer"))
    recovered = await b.start()
    assert recovered["closed"] == [turn_id]
    events = b.tracer.get_turn(TENANT, turn_id)
    assert check_stream(events) == []
    end = events[-1]
    assert (end["status"], end["summary"]["exit"]) == ("error", "error")
    assert {e["error"]["code"] for e in events if e["type"] == "error"} == {"restarted"}
    assert turn_summary(b.tracer, b.session, TENANT, turn_id)["status"] == "done"
    nxt = await b.gateway.start_turn(
        TurnRequest(message="Hi again", identity=ident(), session_id=session_id)
    )
    assert nxt.ctx.turn_index == 1
    await b.gateway.wait(nxt.ctx.turn_id, 5)
    await b.aclose()


@pytest.mark.anyio
async def test_cancel_works_on_a_reattached_turn(tmp_path: Path) -> None:
    a = Harness(settings(tmp_path, "approval_approve"))
    turn_id = await start(a, "Please cancel order A-1002.")
    await wait_for(paused_on_approval(a, turn_id))
    await a.aclose()
    b = Harness(settings(tmp_path, "approval_approve"))
    await b.start()
    b.gateway.cancel(TENANT, turn_id)
    await b.gateway.wait(turn_id, 5)
    events = b.tracer.get_turn(TENANT, turn_id)
    assert events[-1]["summary"]["exit"] == "cancelled" and check_stream(events) == []
    await b.aclose()


@pytest.mark.anyio
async def test_sweeper_expires_overdue_approvals_and_wakes_the_waiter(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "approval_deny"))
    turn_id = await start(h, "Please cancel order A-1002.")
    await wait_for(paused_on_approval(h, turn_id))
    record = h.approvals.pending(TENANT)[0]
    assert h.approvals.sweep_expired(now=time.time()) == 0  # not due yet
    assert h.approvals.sweep_expired(now=time.time() + 3600) == 1
    await h.gateway.wait(turn_id, 5)
    assert h.approvals.get(record["approval_id"], TENANT)["status"] == "expired"
    decided = [e for e in h.tracer.get_turn(TENANT, turn_id) if e["type"] == "approval_decided"][0]
    assert decided["summary"]["decision"] == "expired"
    await h.aclose()
