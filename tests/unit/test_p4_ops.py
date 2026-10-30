"""P4 trace query and receipts, step-over, checkpoints after a restart, the owner lease, and
the OpenTelemetry mapping. Fake provider only."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from harnesswalk.control.gateway import TurnRequest
from harnesswalk.ops.lease import Lease, LeaseHeld
from harnesswalk.ops.otel import (
    OtelExporter,
    build_exporter,
    span_from_ops_event,
    spans_from_events,
)
from harnesswalk.ops.trace import Tracer
from harnesswalk.runtime import Harness
from harnesswalk.turns import turn_summary
from harnesswalk.types import Identity
from tests.spec.support import ROOT, SPEC, check_stream, load_jsonl, simple_path
from tests.unit.support import run_turn, settings, write_fixture
from tests.unit.test_http_api import client
from tests.unit.test_restart import paused_on_approval, wait_for

# -- trace query and receipts --------------------------------------------------------------------


@pytest.mark.anyio
async def test_trace_query_filters_and_pages(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "two_turns", profile=None))
    async with client(h, "t1") as c:
        plain = await run_turn(h, "Hello", session_id="sess_one")
        tool = await run_turn(h, "What about A-1002?", session_id="sess_one")
        other = await run_turn(h, "Hello again", session_id="sess_two")
        r = await c.get("/v1/trace/turns")
        body = r.json()
        ids = [t["turn_id"] for t in body["turns"]]
        assert ids == [other[0]["turn_id"], tool[0]["turn_id"], plain[0]["turn_id"]]
        first = body["turns"][1]
        assert set(first) >= {
            "turn_id", "session_id", "trace_id", "started_at", "ended_at", "status", "exit",
            "cost", "steps", "duration_ms", "path",
        }  # fmt: skip
        assert first["status"] == "done" and first["exit"] == "answered" and first["steps"] == 2
        assert first["cost"] == tool[-1]["cost"] and first["path"] == simple_path(tool)
        assert first["kind"] == "chat" and first["duration_ms"] > 0

        tools = (await c.get("/v1/trace/turns", params={"node": "tools"})).json()["turns"]
        assert [t["turn_id"] for t in tools] == [tool[0]["turn_id"]]
        expert = (await c.get("/v1/trace/turns", params={"node": "sandbox"})).json()["turns"]
        assert [t["turn_id"] for t in expert] == [tool[0]["turn_id"]]
        two = (await c.get("/v1/trace/turns", params={"session": "sess_two"})).json()["turns"]
        assert [t["turn_id"] for t in two] == [other[0]["turn_id"]]
        assert (await c.get("/v1/trace/turns", params={"status": "error"})).json()["turns"] == []
        page = (await c.get("/v1/trace/turns", params={"limit": 2})).json()
        assert len(page["turns"]) == 2 and page["next_after"] == tool[0]["turn_id"]
        rest = (await c.get("/v1/trace/turns", params={"after": page["next_after"]})).json()
        assert [t["turn_id"] for t in rest["turns"]] == [plain[0]["turn_id"]]
        assert rest["next_after"] is None
        later = (await c.get("/v1/trace/turns", params={"since": "2999-01-01T00:00:00Z"})).json()[
            "turns"
        ]
        assert later == []
        assert (await c.get("/v1/trace/turns", params={"tenant": "other"})).status_code == 404
        assert (await c.get("/v1/trace/turns", params={"since": "yesterday"})).status_code == 400
        assert (await c.get("/v1/trace/turns", params={"after": "turn_nope"})).status_code == 400

        receipt = (await c.get(f"/v1/trace/turns/{tool[0]['turn_id']}/receipt")).json()
        assert receipt["total"] == tool[-1]["cost"]
        assert {"turn_id", "total", "by_node", "by_model", "latency_ms"} <= set(receipt)
        assert [x["step"] for x in receipt["calls"]] == [1, 2]
        assert (
            receipt["nodes"]["tools"]["count"] == 1
            and receipt["nodes"]["model_layer"]["count"] == 2
        )
        assert receipt["scores"] is None and receipt["feedback"] is None
        assert (await c.get("/v1/trace/turns/turn_nope/receipt")).status_code == 404
    await h.aclose()


@pytest.mark.anyio
async def test_the_turn_index_is_backfilled(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    events = await run_turn(h, "Hi")
    h.tracer.db.execute("DELETE FROM turn_index")
    await h.aclose()
    tracer = Tracer(str(tmp_path / "data" / "trace.db"))
    turns, _ = tracer.query_turns("t1")
    assert [t["turn_id"] for t in turns] == [events[0]["turn_id"]]
    assert turns[0]["status"] == "done" and turns[0]["cost"] == events[-1]["cost"]
    tracer.close()


# -- step-over -----------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_step_over_skips_expert_children(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    pauses: list[str] = []
    loop = asyncio.get_running_loop()
    holder: dict[str, str] = {}

    def on_event(event: dict) -> None:
        if event["type"] != "node_paused":
            return
        pauses.append(event["node_id"])
        mode = {"memory": "step_over", "loop": "continue"}.get(event["node_id"], "step")
        loop.call_soon(lambda: h.gateway.resume("t1", holder["turn_id"], mode))

    h.bus.subscribe(on_event)
    state = await h.gateway.start_turn(
        TurnRequest(message="Hi", identity=Identity("t1", None, ["user"], False), step_mode=True)
    )
    holder["turn_id"] = state.ctx.turn_id
    await h.gateway.wait(state.ctx.turn_id, 5)
    assert pauses == ["gateway", "input_guardrails", "context_builder", "memory", "loop"]
    events = h.tracer.get_turn("t1", state.ctx.turn_id)
    assert check_stream(events) == []
    assert any(e["type"] == "node_enter" and e["node_id"] == "procedural" for e in events)
    await h.aclose()


@pytest.mark.anyio
async def test_step_over_over_http(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:
        turn = (await c.post("/v1/turns", json={"message": "Hi", "step": True})).json()
        await wait_for(lambda: h.gateway.turns[turn["turn_id"]].ctx.emit.breakpoints.paused_node)
        r = await c.post(f"/v1/turns/{turn['turn_id']}/resume", json={"mode": "step_over"})
        assert r.status_code == 200 and r.json()["mode"] == "step_over"
        r = await c.post(f"/v1/turns/{turn['turn_id']}/resume", json={"mode": "sideways"})
        assert r.status_code == 400
        h.gateway.turns[turn["turn_id"]].ctx.emit.breakpoints.finish()
        await h.gateway.wait(turn["turn_id"], 5)
    await h.aclose()


# -- checkpoints across a restart ------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "required": ["order_id", "status"],
    "properties": {"status": {"enum": ["cancelled", "processing"]}},
}


def restart_fixture(tmp_path: Path) -> Path:
    d = tmp_path / "fixtures"
    write_fixture(
        d,
        "keeps_schema",
        [
            [
                {
                    "tool_calls": [
                        {
                            "call_id": "call_1",
                            "name": "remember",
                            "arguments": {"kind": "fact", "key": "preferred name", "value": "Sam"},
                        },
                        {
                            "call_id": "call_2",
                            "name": "cancel_order",
                            "arguments": {"order_id": "A-1002"},
                        },
                    ],  # fmt: skip
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                },
                {
                    "text": '{"order_id": "A-1002", "status": "cancelled"}',
                    "usage": {"input_tokens": 120, "output_tokens": 12},
                    "expect": {"last_message_role": "tool"},
                },
            ]
        ],
    )
    return d


@pytest.mark.anyio
async def test_a_restart_keeps_schema_proposals_and_byo_flag(tmp_path: Path) -> None:
    fixtures = restart_fixture(tmp_path)
    a = Harness(settings(tmp_path, "keeps_schema", fixtures=fixtures))
    state = await a.gateway.start_turn(
        TurnRequest(
            message="Call me Sam, and cancel A-1002.",
            identity=Identity("t1", None, ["user"], False),
            output_schema=SCHEMA,
            api_key="sk-test-not-a-real-key",
        )
    )
    turn_id = state.ctx.turn_id
    await wait_for(paused_on_approval(a, turn_id))
    checkpoint = a.session.last_checkpoint("t1", turn_id)
    assert checkpoint["turn"]["output_schema"] == SCHEMA and checkpoint["turn"]["byo_key"] is True
    assert checkpoint["proposals"][0]["content"]["value"] == "Sam"
    assert "sk-test" not in json.dumps(checkpoint)
    await a.aclose()

    b = Harness(settings(tmp_path, "keeps_schema", fixtures=fixtures))
    assert (await b.start())["resumed"] == [turn_id]
    approval_id = turn_summary(b.tracer, b.session, "t1", turn_id)["pending_approval_id"]
    b.approvals.decide(approval_id, "t1", "user:t1", "approve", None)
    await b.gateway.wait(turn_id, 5)
    events = b.tracer.get_turn("t1", turn_id)
    assert check_stream(events) == []
    guard = next(
        e for e in events if e.get("node_id") == "output_guardrails" and e["type"] == "node_exit"
    )
    assert guard["summary"]["schema"] == "valid"
    assert any(e["type"] == "memory_written" for e in events)
    end = events[-1]
    assert end["summary"]["exit"] == "answered" and end["summary"]["byo_key_dropped"] is True
    async with client(b) as c:
        summary = (await c.get(f"/v1/turns/{turn_id}")).json()
    assert summary["checkpoint"]["restored"] is True and summary["checkpoint"]["step"] >= 1
    await b.aclose()


# -- the owner lease -----------------------------------------------------------------------------


def test_lease_refuses_a_live_holder_and_replaces_a_stale_one(tmp_path: Path) -> None:
    lock = tmp_path / "owner.lock"
    host = socket.gethostname()
    lock.write_text(json.dumps({"pid": os.getppid(), "host": host, "heartbeat": time.time()}))
    with pytest.raises(LeaseHeld):
        Lease(tmp_path, heartbeat_s=5).acquire()
    lock.write_text(json.dumps({"pid": os.getppid(), "host": host, "heartbeat": time.time() - 60}))
    lease = Lease(tmp_path, heartbeat_s=5)
    assert lease.acquire() is None and json.loads(lock.read_text())["pid"] == os.getpid()
    lease.release()
    assert not lock.exists()
    lock.write_text(json.dumps({"pid": 99999999, "host": host, "heartbeat": time.time()}))
    lease = Lease(tmp_path, heartbeat_s=5)
    lease.acquire()  # the pid is gone: a crash
    lease.release()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(data_dir: Path, port: int, *extra: str) -> subprocess.Popen:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HARNESSWALK_ROOT": str(ROOT),
        "HARNESSWALK_DATA_DIR": str(data_dir),
        "HARNESSWALK_WEB_DIST": str(data_dir / "no-web"),
        "HARNESSWALK_EMBEDDINGS": "fake",
    }
    cmd = [sys.executable, "-m", "harnesswalk", "serve", "--provider", "fake", "--port", str(port),
           "--log-level", "warning", "--set", "release.lease.heartbeat_s=0.2", *extra]  # fmt: skip
    return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _healthy(port: int, deadline_s: float = 20) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    return False


def test_a_second_serve_refuses_unless_it_takes_over(tmp_path: Path) -> None:
    first_port, second_port = _free_port(), _free_port()
    first = _serve(tmp_path, first_port)
    procs = [first]
    try:
        assert _healthy(first_port)
        refused = _serve(tmp_path, second_port)
        procs.append(refused)
        assert refused.wait(20) == 2
        assert b"--takeover" in refused.stderr.read()
        taker = _serve(tmp_path, second_port, "--takeover")
        procs.append(taker)
        assert first.wait(20) is not None  # the holder stepped down
        assert _healthy(second_port)
        assert json.loads((tmp_path / "owner.lock").read_text())["pid"] == taker.pid
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
                p.wait(10)


# -- OpenTelemetry ------------------------------------------------------------------------------


def test_spans_follow_the_events_md_mapping() -> None:
    events = load_jsonl(SPEC / "examples" / "tool_call.jsonl")
    spans = spans_from_events(events)
    root = spans[0]
    assert root["name"] == "harnesswalk.turn" and root["parent_span_id"] is None
    assert root["attributes"]["gen_ai.conversation.id"] == events[0]["session_id"]
    by_name = {s["name"]: s for s in spans}
    chat = next(s for s in spans if s["name"].startswith("chat "))
    assert chat["attributes"]["gen_ai.operation.name"] == "chat"
    assert "gen_ai.usage.input_tokens" in chat["attributes"]
    tool = next(s for s in spans if s["name"].startswith("execute_tool "))
    assert tool["attributes"]["gen_ai.tool.name"] == "lookup_order"
    agent = by_name["invoke_agent"]
    assert chat["parent_span_id"] == agent["span_id"] and tool["parent_span_id"] == agent["span_id"]
    assert all(s["trace_id"] == events[0]["trace_id"] for s in spans)
    assert len({s["span_id"] for s in spans}) == len(spans)
    assert spans == spans_from_events(events)  # deterministic ids
    nodes = sum(1 for e in events if e["type"] == "node_enter")
    assert len(spans) == nodes + 1
    assert all(s["end_ns"] >= s["start_ns"] for s in spans)


def test_the_exporter_hands_finished_turns_to_the_sdk() -> None:
    pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from harnesswalk.events import EventBus

    memory = InMemorySpanExporter()
    bus = EventBus()
    exporter = OtelExporter(memory, bus)
    events = load_jsonl(SPEC / "examples" / "plain_answer.jsonl")
    for event in events:
        bus.publish(event)
    exporter.on_ops_event(
        {"schema_version": "0.1.0", "event_id": "e1", "seq": 0, "ts": events[0]["ts"],
         "trace_id": events[0]["trace_id"], "turn_id": None, "session_id": None,
         "tenant_id": "t", "type": "artifact_changed", "node_id": "release", "status": "done",
         "summary": {"kind": "skill", "name": "returns", "version": 2}}
    )  # fmt: skip
    exporter.flush()
    spans = memory.get_finished_spans()
    assert len(spans) == len(spans_from_events(events)) + 1
    assert spans[0].name == "harnesswalk.turn"
    assert format(spans[0].context.trace_id, "032x") == events[0]["trace_id"]
    assert spans[-1].name == "harnesswalk.artifact_changed"
    assert spans[-1].attributes["harnesswalk.version"] == 2
    exporter.shutdown()


def test_export_is_off_without_an_endpoint(monkeypatch) -> None:
    from harnesswalk.events import EventBus

    monkeypatch.delenv("HARNESSWALK_OTLP_ENDPOINT", raising=False)

    class S:
        def get(self, key, default=None):
            return default

    assert build_exporter(S(), EventBus()) is None
    assert span_from_ops_event(
        {"event_id": "e", "seq": 0, "ts": "2026-10-29T00:00:00.000Z", "trace_id": "a" * 32,
         "tenant_id": "t", "type": "feedback_recorded", "node_id": "eval", "status": "done",
         "turn_id": "turn_1", "session_id": "s", "summary": {"rating": "good"}}
    )["attributes"]["harnesswalk.rating"] == "good"  # fmt: skip
