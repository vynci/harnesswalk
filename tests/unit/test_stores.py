"""Audit is append-only and chained; trace serves turns by cursor; session checks writers."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

from harnesswalk.context.stores.session import SessionForbidden, SessionStore
from harnesswalk.control.audit import AuditEntry, AuditLog
from harnesswalk.ops.trace import TraceRejected
from tests.unit.support import harness, make_ctx, run_turn


def test_audit_is_append_only_and_chained(tmp_path: Path) -> None:
    log = AuditLog(str(tmp_path / "audit.db"))
    ctx, _ = make_ctx("tenant_a")
    other, _ = make_ctx("tenant_b")
    for i in range(3):
        log.append(
            ctx,
            AuditEntry("policy_decision", "agent", "turn", "trace", tool=f"t{i}", decision="deny"),
        )
    log.append(other, AuditEntry("policy_decision", "agent", "turn", "trace", tool="x"))
    assert log.verify("tenant_a") == {"ok": True, "records": 3}
    assert len(log.list("tenant_b")) == 1
    with pytest.raises(sqlite3.DatabaseError):
        log.db.execute("UPDATE audit SET entry = '{}'")
    with pytest.raises(sqlite3.DatabaseError):
        log.db.execute("DELETE FROM audit")
    # Tamper behind the triggers' back: verify finds the first bad record.
    log.db.execute("DROP TRIGGER audit_no_update")
    first = log.list("tenant_a")[-1]
    log.db.execute(
        'UPDATE audit SET entry = \'{"action":"x"}\' WHERE record_id = ?', (first.record_id,)
    )
    assert log.verify("tenant_a") == {
        "ok": False,
        "records": 3,
        "first_bad_record": first.record_id,
    }


@pytest.mark.anyio
async def test_trace_serves_by_tenant_and_cursor(tmp_path: Path) -> None:
    h = harness(tmp_path, "tool_call")
    events = await run_turn(h, "Where is order A-1001?", tenant="alpha")
    turn_id = events[0]["turn_id"]
    assert h.tracer.get_turn("beta", turn_id) == []
    assert not h.tracer.turn_exists("beta", turn_id)
    tail = h.tracer.get_turn("alpha", turn_id, after_seq=10)
    assert tail[0]["seq"] == 11 and tail[-1]["type"] == "turn_end"
    streamed = [e async for e in h.tracer.subscribe("alpha", turn_id, 40) if e]
    assert [e["seq"] for e in streamed] == list(range(41, len(events)))
    receipt = h.tracer.receipt("alpha", turn_id)
    assert (
        receipt["total"]
        == events[-1]["cost"]
        == {"input_tokens": 1917, "output_tokens": 45, "usd": 0.0}
    )
    assert set(receipt["by_node"]) == {"model_layer"}
    detail = next(
        e["detail_ref"]
        for e in events
        if e.get("node_id") == "context_builder" and "detail_ref" in e
    )
    assert (
        h.tracer.get_detail("alpha", detail)["messages"][-1]["content"] == "Where is order A-1001?"
    )
    assert h.tracer.get_detail("beta", detail) is None


@pytest.mark.anyio
async def test_live_subscription_sees_events_as_they_happen(tmp_path: Path) -> None:
    from harnesswalk.control.gateway import TurnRequest
    from harnesswalk.types import Identity

    h = harness(tmp_path, "approval_approve")
    state = await h.gateway.start_turn(
        TurnRequest("Cancel A-1002", Identity("t1", None, ["user"], False))
    )
    seen = []

    async def reader():
        async for e in h.tracer.subscribe("t1", state.ctx.turn_id):
            if e is None:
                continue
            seen.append(e)
            if e["type"] == "approval_requested":
                h.approvals.decide(e["summary"]["approval_id"], "t1", "user:t1", "approve", None)

    await asyncio.wait_for(reader(), 5)
    assert seen[0]["type"] == "turn_start" and seen[-1]["type"] == "turn_end"
    assert [e["seq"] for e in seen] == list(range(len(seen)))


def test_trace_rejects_foreign_events(tmp_path: Path) -> None:
    h = harness(tmp_path)
    start = {
        "turn_id": "x",
        "tenant_id": "a",
        "trace_id": "1" * 32,
        "type": "turn_start",
        "seq": 0,
        "event_id": "e0",
        "session_id": "s",
        "ts": "2026-10-29T00:00:00.000Z",
    }
    h.tracer.record(start)
    with pytest.raises(TraceRejected):
        h.tracer.record(
            {**start, "type": "node_enter", "seq": 1, "event_id": "e1", "tenant_id": "b"}
        )


def test_session_writers_and_tenants(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "s.db"))
    ctx, _ = make_ctx("a")
    store.ensure_session("a", ctx.session_id, "_starter")
    with pytest.raises(SessionForbidden):
        store.ensure_session("b", ctx.session_id, "_starter")
    with pytest.raises(PermissionError):
        store.append_turn(ctx, "loop", "hi", "hello", "answered")
    with pytest.raises(PermissionError):
        store.write_summary(ctx, "gateway", "x", 0)
    store.append_turn(ctx, "gateway", "hi", "hello", "answered")
    assert store.next_turn_index("a", ctx.session_id) == 1
    assert store.next_turn_index("b", ctx.session_id) == 0
