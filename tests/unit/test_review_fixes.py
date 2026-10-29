"""Regressions from the Phase 2 API review (cancel races, streams, request checks, serving)."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from harnesswalk.control.approval import ApprovalAlreadyDecided
from harnesswalk.control.http_api import create_app
from harnesswalk.runtime import Harness
from tests.spec.support import check_stream
from tests.unit.support import settings, write_fixture


def client(h: Harness, tenant: str = "t1", app: Any = None) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app or create_app(h))
    return httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Harnesswalk-Tenant": tenant}
    )


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def until(c: httpx.AsyncClient, turn_id: str, key: str, value: Any) -> dict:
    for _ in range(200):
        summary = (await c.get(f"/v1/turns/{turn_id}")).json()
        if summary.get(key) == value:
            return summary
        await asyncio.sleep(0.01)
    raise AssertionError(f"{key} never became {value!r}: {summary}")


# -- cancel and approvals ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_then_decide_is_409_and_the_record_is_expired_at_once(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "approval_approve"))
    async with client(h) as c:
        r = await c.post("/v1/turns", json={"message": "Please cancel order A-1002."})
        turn_id = r.json()["turn_id"]
        approval_id = (await until(c, turn_id, "paused_at_node", "approval"))["pending_approval_id"]
        h.gateway.cancel("t1", turn_id)
        # Before the turn's task wakes up, the record is already decided by the cancel.
        assert h.approvals.get(approval_id, "t1")["status"] == "expired"
        with pytest.raises(ApprovalAlreadyDecided):
            h.approvals.decide(approval_id, "t1", "user:t1", "approve", None)
        await h.gateway.wait(turn_id, 5)
        record = h.approvals.get(approval_id, "t1")
        assert record["status"] == "expired" and record["decided_by"] == "system"
        r = await c.post(f"/v1/approvals/{approval_id}", json={"decision": "approve"})
        assert r.status_code == 409
        events = h.tracer.get_turn("t1", turn_id)
        assert check_stream(events) == []
        assert not any(e.get("node_id") == "sandbox" for e in events)
        assert events[-1]["summary"]["exit"] == "cancelled"


@pytest.mark.anyio
async def test_decide_then_cancel_does_not_run_the_tool(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "approval_approve"))
    async with client(h) as c:
        r = await c.post("/v1/turns", json={"message": "Please cancel order A-1002."})
        turn_id = r.json()["turn_id"]
        approval_id = (await until(c, turn_id, "paused_at_node", "approval"))["pending_approval_id"]
        # Approve, then cancel before the turn's task has woken up to run the tool.
        h.approvals.decide(approval_id, "t1", "user:t1", "approve", None)
        h.gateway.cancel("t1", turn_id)
        await h.gateway.wait(turn_id, 5)
        events = h.tracer.get_turn("t1", turn_id)
        assert check_stream(events) == []
        assert not any(e.get("node_id") == "sandbox" for e in events)
        assert h.approvals.get(approval_id, "t1")["status"] == "approved"
        end = events[-1]
        assert end["status"] == "error" and end["summary"]["exit"] == "cancelled"
        closing = [(e.get("node_id"), e["error"]["code"]) for e in events if e["type"] == "error"]
        assert closing == [("tools", "cancelled"), ("loop", "cancelled"), (None, "cancelled")]
        # The audit record says what happened: approved, and not executed.
        decisions = [
            r.entry for r in h.audit.list("t1") if r.entry["action"] == "approval_decision"
        ]
        assert len(decisions) == 1
        assert decisions[0]["decision"] == "approve"
        assert decisions[0]["extra"] == {
            "executed": False,
            "note": "approved, not run: the turn was cancelled",
        }


@pytest.mark.anyio
async def test_cancel_during_the_last_tool_call_makes_no_final_model_call(tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    call = {"tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "A-1001"}}]}
    write_fixture(fx, "one_step", [[call, {"text": "a final call that must not happen"}]])
    h = Harness(settings(tmp_path, "one_step", fixtures=fx, overrides={"loop.max_steps": 1}))
    holder: dict[str, str] = {}

    def on_event(event: dict) -> None:  # cancel while step max_steps runs its tool call
        if event["type"] == "node_enter" and event["node_id"] == "tools":
            h.gateway.cancel("t1", holder["turn_id"])

    h.bus.subscribe(on_event)
    async with client(h) as c:
        r = await c.post("/v1/turns", json={"message": "Where is order A-1001?"})
        holder["turn_id"] = turn_id = r.json()["turn_id"]
        await h.gateway.wait(turn_id, 5)
    events = h.tracer.get_turn("t1", turn_id)
    assert check_stream(events) == []
    enters = [e for e in events if e["type"] == "node_enter" and e["node_id"] == "model_layer"]
    assert len(enters) == 1  # the step's call only; no tools-off final call after the cancel
    loop_exit = [e for e in events if e["type"] == "node_exit" and e["node_id"] == "loop"]
    assert loop_exit[0]["summary"]["exit_reason"] == "interrupted"
    end = events[-1]
    assert end["status"] == "done" and end["summary"]["exit"] == "cancelled"
    assert end["summary"]["reply"] == "I stopped because the turn was interrupted."


async def step_to(h: Harness, turn_id: str, node_id: str) -> None:
    """Step a step-mode turn until it is paused on `node_id`."""
    breakpoints = h.gateway.turns[turn_id].ctx.emit.breakpoints
    for _ in range(1000):
        if breakpoints.paused_node == node_id:
            return
        if breakpoints.paused_node:
            h.gateway.resume("t1", turn_id, "step")
        await asyncio.sleep(0.002)
    raise AssertionError(f"never paused on {node_id}")


def stored_turn(h: Harness, turn_id: str) -> dict:
    row = h.session.db.one("SELECT * FROM turns WHERE turn_id = ?", (turn_id,))
    return dict(row)


@pytest.mark.anyio
async def test_cancel_at_the_trace_breakpoint_keeps_the_stored_answer(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:
        r = await c.post(
            "/v1/turns", json={"message": "What are your support hours?", "step": True}
        )
        turn_id = r.json()["turn_id"]
        await step_to(h, turn_id, "trace")
        r = await c.post(f"/v1/turns/{turn_id}/cancel")
        assert r.status_code == 202
        await h.gateway.wait(turn_id, 5)
        events = h.tracer.get_turn("t1", turn_id)
        assert check_stream(events) == []
        # Past the reply nothing is left to stop: the turn finishes as it would have.
        assert not any(e["type"] == "error" for e in events)
        end = events[-1]
        assert end["status"] == "done" and end["summary"]["exit"] == "answered"
        row = stored_turn(h, turn_id)
        assert row["user_text"] == "What are your support hours?"
        assert row["reply_text"] == end["summary"]["reply"] and row["exit"] == "answered"
        summary = (await c.get(f"/v1/turns/{turn_id}")).json()
        assert summary["message"] == "What are your support hours?"


@pytest.mark.anyio
async def test_a_failure_after_the_reply_does_not_overwrite_the_stored_turn(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:
        r = await c.post(
            "/v1/turns", json={"message": "What are your support hours?", "step": True}
        )
        turn_id = r.json()["turn_id"]
        await step_to(h, turn_id, "trace")
        task = h.gateway.turns[turn_id].task
        assert task is not None
        task.cancel()  # a shutdown while paused after the reply was recorded
        await h.gateway.wait(turn_id, 5)
        events = h.tracer.get_turn("t1", turn_id)
        assert check_stream(events) == []
        assert events[-1]["summary"]["exit"] == "cancelled"
        row = stored_turn(h, turn_id)
        assert row["user_text"] == "What are your support hours?"
        assert row["reply_text"] and row["exit"] == "answered"


# -- request checks ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_post_body_must_be_declared_json(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    body = json.dumps({"message": "What are your support hours?"})
    async with client(h) as c:
        # A cross-site form or fetch may send text/plain without a preflight: refuse it.
        for content_type in ("text/plain", "application/x-www-form-urlencoded", None):
            headers = {"Content-Type": content_type} if content_type else {}
            r = await c.post("/v1/turns", content=body, headers=headers)
            assert r.status_code == 415, content_type
            assert r.json()["error"]["code"] == "unsupported_media_type"
        assert h.gateway.turns == {}  # no turn was started
        for path in ("/v1/turns/turn_x/resume", "/v1/approvals/apr_x"):
            r = await c.post(path, content="{}", headers={"Content-Type": "text/plain"})
            assert r.status_code == 415, path
        r = await c.post(
            "/v1/turns", content=body, headers={"Content-Type": "application/json; charset=utf-8"}
        )
        assert r.status_code == 202
        await h.gateway.wait(r.json()["turn_id"], 5)
        # Cancel takes no body, so it needs no content type.
        r = await c.post("/v1/turns", json={"message": "hi", "step": True})
        assert (await c.post(f"/v1/turns/{r.json()['turn_id']}/cancel")).status_code == 202
        await h.gateway.wait(r.json()["turn_id"], 5)
