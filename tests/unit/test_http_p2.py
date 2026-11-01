"""The P2 HTTP additions (http_api.md): cancel, turn summaries, session turns, cursor replay,
the code peek, capabilities, per-turn options, web/dist serving and dev-mode CORS."""

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from harnesswalk import inspectors
from harnesswalk.control.gateway import GatewayError, TurnRequest
from harnesswalk.control.http_api import create_app
from harnesswalk.runtime import Harness
from harnesswalk.types import Identity
from tests.spec.support import check_stream
from tests.unit.support import settings, write_fixture

SUMMARY_KEYS = {
    "turn_id", "session_id", "tenant_id", "trace_id", "turn_index", "status", "exit", "reply",
    "message", "started_at", "ended_at", "cost", "event_count", "paused_at_node",
    "pending_approval_id",
    "feedback", "scores", "checkpoint", "release",  # P4
    "pack", "diagnosis",  # P5
}  # fmt: skip


def client(h: Harness, tenant: str = "t1", app: Any = None) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app or create_app(h))
    return httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Harnesswalk-Tenant": tenant}
    )


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def until(c: httpx.AsyncClient, turn_id: str, key: str, value: Any) -> dict:
    """Poll the turn summary until `key` has `value`."""
    for _ in range(200):
        summary = (await c.get(f"/v1/turns/{turn_id}")).json()
        if summary.get(key) == value:
            return summary
        await asyncio.sleep(0.01)
    raise AssertionError(f"{key} never became {value!r}: {summary}")


def slow_tool_fixture(tmp_path: Path, latency_ms: int = 150) -> Path:
    fx = tmp_path / "fx"
    first = {
        "tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "A-1001"}}],
        "usage": {"input_tokens": 100, "output_tokens": 10},
        "latency_ms": latency_ms,
    }
    write_fixture(fx, "slow_tool", [[first, {"text": "never used"}]])
    return fx


# -- turn summaries and session turns ---------------------------------------------------------


@pytest.mark.anyio
async def test_turn_summary_and_session_turns(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "two_turns"))
    async with client(h) as c, client(h, "other") as o:
        first = (await c.post("/v1/turns", json={"message": "Hello there"})).json()
        await h.gateway.wait(first["turn_id"], 5)
        summary = (await c.get(f"/v1/turns/{first['turn_id']}")).json()
        assert set(summary) == SUMMARY_KEYS
        assert summary["status"] == "done" and summary["exit"] == "answered"
        assert summary["reply"].startswith("Hello!") and summary["message"] == "Hello there"
        assert summary["turn_index"] == 0 and summary["tenant_id"] == "t1"
        assert summary["ended_at"] >= summary["started_at"]
        assert summary["cost"] == {"input_tokens": 170, "output_tokens": 14, "usd": 0.0}
        stored = h.tracer.get_turn("t1", first["turn_id"])
        assert summary["event_count"] == len(stored)
        assert summary["paused_at_node"] is None and summary["pending_approval_id"] is None

        session = first["session_id"]
        second = (
            await c.post("/v1/turns", json={"message": "Where is A-1002?", "session_id": session})
        ).json()
        await h.gateway.wait(second["turn_id"], 5)
        turns = (await c.get(f"/v1/sessions/{session}/turns")).json()
        assert [t["turn_id"] for t in turns] == [second["turn_id"], first["turn_id"]]
        assert [t["turn_index"] for t in turns] == [1, 0]
        one = (await c.get(f"/v1/sessions/{session}/turns", params={"limit": 1})).json()
        assert [t["turn_id"] for t in one] == [second["turn_id"]]
        assert (
            await c.get(f"/v1/sessions/{session}/turns", params={"limit": 0})
        ).status_code == 400
        assert (
            await c.get(f"/v1/sessions/{session}/turns", params={"limit": "x"})
        ).status_code == 400

        # Tenancy: another tenant's session and turn are 404, as is an unknown one.
        r = await o.get(f"/v1/sessions/{session}/turns")
        assert r.status_code == 404 and r.json()["error"]["code"] == "session_not_found"
        assert (await c.get("/v1/sessions/sess_nope/turns")).status_code == 404
        r = await o.get(f"/v1/turns/{first['turn_id']}")
        assert r.status_code == 404 and r.json()["error"]["code"] == "turn_not_found"


@pytest.mark.anyio
async def test_summary_of_a_blocked_turn_hides_the_message(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:
        r = await c.post("/v1/turns", json={"message": "x" * 9000})  # over the guardrail limit
        await h.gateway.wait(r.json()["turn_id"], 5)
        summary = (await c.get(f"/v1/turns/{r.json()['turn_id']}")).json()
        assert summary["exit"] == "blocked" and summary["message"] is None


# -- cancel -----------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_while_paused_on_a_breakpoint(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c, client(h, "other") as o:
        r = await c.post(
            "/v1/turns", json={"message": "What are your support hours?", "step": True}
        )
        turn_id, session = r.json()["turn_id"], r.json()["session_id"]
        paused = await until(c, turn_id, "status", "paused")
        assert paused["paused_at_node"] == "gateway" and paused["exit"] is None
        assert paused["reply"] is None and paused["ended_at"] is None

        assert (await o.post(f"/v1/turns/{turn_id}/cancel", json={})).status_code == 404
        r = await c.post(f"/v1/turns/{turn_id}/cancel", json={})
        assert r.status_code == 202 and r.json() == {"turn_id": turn_id, "cancel_requested": True}
        await h.gateway.wait(turn_id, 5)

        done = (await c.get(f"/v1/turns/{turn_id}")).json()
        assert done["status"] == "done" and done["exit"] == "cancelled"
        assert done["paused_at_node"] is None
        events = parse_sse((await c.get(f"/v1/turns/{turn_id}/events")).text)
        assert check_stream(events) == []
        assert events[-1]["status"] == "error" and events[-1]["summary"]["exit"] == "cancelled"
        closing = [e for e in events if e["type"] == "error" and e.get("node_id")]
        assert [(e["node_id"], e["error"]["code"]) for e in closing] == [("gateway", "cancelled")]
        assert not any(e["type"] == "node_resumed" for e in events)

        # Finished: 409 with the final state next to the error. Resume is 409 too.
        again = await c.post(f"/v1/turns/{turn_id}/cancel", json={})
        assert again.status_code == 409 and again.json()["error"]["code"] == "turn_finished"
        assert again.json()["turn"]["exit"] == "cancelled"
        assert (await c.post(f"/v1/turns/{turn_id}/resume", json={})).status_code == 409
        assert (await c.post("/v1/turns/turn_nope/cancel", json={})).status_code == 404

        # The cancelled turn kept its number: the next turn of the session is turn 1.
        nxt = await c.post("/v1/turns", json={"message": "hi", "session_id": session})
        assert nxt.status_code == 202
        await h.gateway.wait(nxt.json()["turn_id"], 5)
        assert (await c.get(f"/v1/turns/{nxt.json()['turn_id']}")).json()["turn_index"] == 1


@pytest.mark.anyio
async def test_cancel_while_waiting_for_approval(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "approval_approve"))
    async with client(h) as c:
        r = await c.post("/v1/turns", json={"message": "Please cancel order A-1002."})
        turn_id = r.json()["turn_id"]
        paused = await until(c, turn_id, "paused_at_node", "approval")
        approval_id = paused["pending_approval_id"]
        assert paused["status"] == "paused" and approval_id.startswith("apr_")
        assert (await c.post(f"/v1/turns/{turn_id}/cancel", json={})).status_code == 202
        await h.gateway.wait(turn_id, 5)
        events = h.tracer.get_turn("t1", turn_id)
        assert check_stream(events) == []
        closing = [e["node_id"] for e in events if e["type"] == "error" and e.get("node_id")]
        assert closing == ["approval", "tools", "loop"]
        assert h.approvals.get(approval_id, "t1")["status"] == "expired"
        assert not any(e.get("node_id") == "sandbox" for e in events)
        r = await c.post(f"/v1/approvals/{approval_id}", json={"decision": "approve"})
        assert r.status_code == 409


@pytest.mark.anyio
async def test_cancel_while_running_stops_at_the_next_step_boundary(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "slow_tool", fixtures=slow_tool_fixture(tmp_path)))
    async with client(h) as c:
        r = await c.post("/v1/turns", json={"message": "Where is order A-1001?"})
        turn_id = r.json()["turn_id"]
        await asyncio.sleep(0.05)  # inside the model call
        running = (await c.get(f"/v1/turns/{turn_id}")).json()
        assert running["status"] == "running" and running["exit"] is None
        assert (await c.post(f"/v1/turns/{turn_id}/cancel", json={})).status_code == 202
        assert (
            await c.post(f"/v1/turns/{turn_id}/cancel", json={})
        ).status_code == 202  # idempotent
        await h.gateway.wait(turn_id, 5)
        events = h.tracer.get_turn("t1", turn_id)
        assert check_stream(events) == []
        loop_exit = [e for e in events if e["type"] == "node_exit" and e["node_id"] == "loop"]
        assert loop_exit[0]["summary"]["exit_reason"] == "interrupted"
        assert not any(e.get("node_id") == "tools" for e in events)  # the call never ran
        end = events[-1]
        assert end["status"] == "done" and end["summary"]["exit"] == "cancelled"
        assert end["summary"]["reply"] == "I stopped because the turn was interrupted."
        assert end["cost"]["input_tokens"] == 100


@pytest.mark.anyio
async def test_cancel_in_step_mode_stops_at_the_next_breakpoint(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "slow_tool", fixtures=slow_tool_fixture(tmp_path)))
    state = await h.gateway.start_turn(
        TurnRequest("Where is A-1001?", Identity("t1", None, ["user"], False), step_mode=True)
    )
    turn_id = state.ctx.turn_id
    breakpoints = state.ctx.emit.breakpoints
    while breakpoints.paused_node != "providers":  # step to the provider call, then into it
        await asyncio.sleep(0.005)
        if breakpoints.paused_node and breakpoints.paused_node != "providers":
            h.gateway.resume("t1", turn_id, "step")
    h.gateway.resume("t1", turn_id, "step")
    await asyncio.sleep(0.02)  # the fake provider is sleeping now; nothing is paused
    assert breakpoints.paused_node is None
    h.gateway.cancel("t1", turn_id)
    await h.gateway.wait(turn_id, 5)
    events = h.tracer.get_turn("t1", turn_id)
    assert check_stream(events) == []
    # The model call finished; the loop saw the flag before the tool call and exited, then the
    # next node_enter (output_guardrails) hit the cancelled breakpoint and stopped the turn.
    assert not any(e.get("node_id") == "tools" for e in events)
    loop_exit = [e for e in events if e["type"] == "node_exit" and e["node_id"] == "loop"]
    assert loop_exit[0]["summary"]["exit_reason"] == "interrupted"
    closing = [(e.get("node_id"), e["error"]["code"]) for e in events if e["type"] == "error"]
    assert closing == [("output_guardrails", "cancelled"), (None, "cancelled")]
    assert events[-1]["status"] == "error" and events[-1]["summary"]["exit"] == "cancelled"
    with pytest.raises(GatewayError) as exc:
        h.gateway.cancel("t1", turn_id)
    assert exc.value.status == 409


# -- cursor replay ----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cursor_replay_follow_and_last_event_id(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:
        r = await c.post(
            "/v1/turns", json={"message": "What are your support hours?", "step": True}
        )
        turn_id, url = r.json()["turn_id"], r.json()["events_url"]
        await until(c, turn_id, "status", "paused")

        # follow=false on a paused turn: what is stored, then the stream closes.
        partial = await c.get(url, params={"follow": "false"})
        events = parse_sse(partial.text)
        assert [e["seq"] for e in events] == list(range(len(events)))
        assert events[-1]["type"] == "node_paused"
        assert all(f"id: {e['seq']}\n" in partial.text for e in events)
        tail = parse_sse((await c.get(url, params={"after": 1, "follow": "false"})).text)
        assert tail[0]["seq"] == 2

        await c.post(f"/v1/turns/{turn_id}/resume", json={"mode": "continue"})
        await h.gateway.wait(turn_id, 5)
        full = parse_sse((await c.get(url, params={"after": -1})).text)
        assert full[0]["type"] == "turn_start" and full[-1]["type"] == "turn_end"
        assert parse_sse((await c.get(url, params={"after": 0})).text)[0]["seq"] == 1
        # The largest cursor wins: a reconnect to `?after=2` with Last-Event-ID 10 starts at 11.
        r = await c.get(url, params={"after": 2}, headers={"Last-Event-ID": "10"})
        assert parse_sse(r.text)[0]["seq"] == 11
        r = await c.get(url, params={"after": 12}, headers={"Last-Event-ID": "10"})
        assert parse_sse(r.text)[0]["seq"] == 13
        assert parse_sse((await c.get(url, params={"after_seq": 5})).text)[0]["seq"] == 6
        last = full[-1]["seq"]
        assert parse_sse((await c.get(url, params={"after": last})).text) == []
        r = await c.get(url, params={"after": "x"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"


@pytest.mark.anyio
async def test_follow_streams_a_running_turn_to_its_end(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:
        r = await c.post(
            "/v1/turns", json={"message": "What are your support hours?", "step": True}
        )
        turn_id, url = r.json()["turn_id"], r.json()["events_url"]
        await until(c, turn_id, "status", "paused")

        async def release() -> None:
            await asyncio.sleep(0.05)
            await c.post(f"/v1/turns/{turn_id}/resume", json={"mode": "continue"})

        task = asyncio.create_task(release())
        events = parse_sse((await c.get(url, params={"after": 0})).text)
        await task
        assert events[0]["seq"] == 1 and events[-1]["type"] == "turn_end"


# -- code peek --------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_node_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:
        body = (await c.get("/v1/nodes/policy/source")).json()
        assert body["node_id"] == "policy" and body["path"] == "harnesswalk/control/policy.py"
        assert body["language"] == "python" and body["truncated"] is False
        text = (h.settings.root / body["path"]).read_text(encoding="utf-8")
        assert body["source"] == text and body["line_count"] == len(text.splitlines())
        names = {s["name"]: s for s in body["symbols"]}
        assert names["Policy"]["kind"] == "class"
        assert text.splitlines()[names["Policy"]["line"] - 1].startswith("class Policy")
        assert (await c.get("/v1/nodes/memory/source")).json()["path"].endswith("__init__.py")

        # Every node has its file since P5 (diagnose): point one hint at a missing file.
        nodes = copy.deepcopy(inspectors.diagram.nodes_by_id())
        nodes["diagnose"]["source_hint"] = "harnesswalk/ops/eval/not_there.py"
        monkeypatch.setattr(inspectors.diagram, "nodes_by_id", lambda: nodes)
        r = await c.get("/v1/nodes/diagnose/source")
        assert r.status_code == 404 and r.json()["error"]["code"] == "source_not_found"
        r = await c.get("/v1/nodes/not_a_node/source")
        assert r.status_code == 404 and r.json()["error"]["code"] == "node_not_found"

        # A hint that leaves the package is never served, however it is spelled.
        nodes = inspectors.diagram.nodes_by_id()
        for hint in ("pyproject.toml", "harnesswalk/../pyproject.toml", "spec/events.md"):
            patched = {**nodes, "policy": {**nodes["policy"], "source_hint": hint}}
            monkeypatch.setattr(inspectors.diagram, "nodes_by_id", lambda p=patched: p)
            assert (await c.get("/v1/nodes/policy/source")).status_code == 404, hint
            # The inspector's own `source` field goes through the same check (P3 review).
            body = (await c.get("/v1/nodes/policy")).json()
            assert body["source"]["content"] == "", hint
            assert body["inspector"]["type"] == "config"
            code_node = {**patched, "loop": {**nodes["loop"], "source_hint": hint}}
            monkeypatch.setattr(inspectors.diagram, "nodes_by_id", lambda p=code_node: p)
            body = (await c.get("/v1/nodes/loop")).json()
            assert body["source"]["content"] == "" and body["inspector"]["content"] == "", hint
        monkeypatch.undo()
        body = (await c.get("/v1/nodes/policy")).json()
        assert body["source"]["path"] == "harnesswalk/control/policy.py"
        assert body["source"]["content"] == text

        monkeypatch.setattr(inspectors, "SOURCE_CAP_BYTES", 300)
        cut = (await c.get("/v1/nodes/policy/source")).json()
        assert cut["truncated"] is True and len(cut["source"].encode()) <= 300
        assert cut["source"].endswith("\n") and text.startswith(cut["source"])
        assert cut["line_count"] == len(text.splitlines())


# -- capabilities -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_capabilities_per_mode(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer", overrides={"gateway.stream_reply_chars": 8}))
    async with client(h) as c:
        caps = (await c.get("/v1/capabilities")).json()
    assert caps["mode"] == "dev" and caps["version"] and caps["spec_version"]
    by_name = {p["name"]: p for p in caps["providers"]}
    assert list(by_name) == ["fake", "ollama", "anthropic", "openai_compatible"]
    assert by_name["fake"]["configured"] and by_name["fake"]["default"]
    # P6: the conformance profile adds a priced fake alias for the budget case.
    assert by_name["fake"]["models"] == ["fake-small", "fake-priced"]
    assert "local-embed" not in by_name["ollama"]["models"]  # embedding models are not chat
    assert not by_name["anthropic"]["configured"]  # hosted needs enabled: true and a key
    assert {"plain_answer", "cancel_running", "two_turns"} <= set(caps["fixtures"])
    assert caps["features"] == {
        "step_mode": True, "approvals": True, "message_delta": True, "cancel": True,
        "byo_key": False, "disable_nodes": True, "knowledge": False, "memory_review": True,
        "evals": True, "judge": False, "release": True, "artifact_edit": True,
        "feedback": True, "step_over": True,
        "packs": True, "diagnose": True,  # P5
    }  # fmt: skip
    assert caps["packs"] == {
        "default": "_starter",
        "ids": [
            "_starter",
            "conformance_alt",
            "ecommerce",
            "finance",
            "healthcare",
            "logistics",
            "sports",
        ],
    }
    assert caps["release"]["active_release_id"] == "r1"
    assert caps["release"]["working_set_dirty"] is False and caps["release"]["use_working_set"]
    assert caps["limits"]["max_steps"] == h.settings.get("loop.max_steps")
    assert caps["limits"]["max_message_chars"] == 32000
    assert caps["limits"]["session_ttl_seconds"] is None

    pub = Harness(
        settings(
            tmp_path / "pub",
            "plain_answer",
            profile=None,
            mode="public",
            overrides={"gateway.stream_reply_chars": 0},
        )
    )
    transport = httpx.ASGITransport(app=create_app(pub))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        caps = (await c.get("/v1/capabilities")).json()
    assert caps["mode"] == "public" and caps["fixtures"] == []
    assert caps["features"]["byo_key"] and not caps["features"]["disable_nodes"]
    assert caps["features"]["message_delta"] is False
    assert not caps["features"]["evals"] and not caps["features"]["artifact_edit"]
    assert caps["release"]["use_working_set"] is False
    assert caps["limits"]["session_ttl_seconds"] == 7200

    # Private mode has no sign-in yet, but capabilities and the code peek need no identity.
    priv = Harness(settings(tmp_path / "priv", "plain_answer", profile=None, mode="private"))
    transport = httpx.ASGITransport(app=create_app(priv))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/v1/capabilities")).json()["features"]["byo_key"] is True
        assert (await c.get("/v1/nodes/loop/source")).status_code == 200
        assert (await c.get("/v1/turns/turn_x")).status_code == 401


# -- per-turn options -------------------------------------------------------------------------


@pytest.mark.anyio
async def test_per_turn_fixture_and_step_options(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:
        first = (await c.post("/v1/turns", json={"message": "What are your support hours?"})).json()
        await h.gateway.wait(first["turn_id"], 5)
        # Turn 1 of the session, but the first on tool_call: it reads tool_call's turns[0].
        r = await c.post(
            "/v1/turns",
            json={
                "message": "Where is order A-1001?",
                "session_id": first["session_id"],
                "provider": "fake",
                "fixture": "tool_call",
            },
        )
        assert r.status_code == 202
        await h.gateway.wait(r.json()["turn_id"], 5)
        events = h.tracer.get_turn("t1", r.json()["turn_id"])
        assert events[0]["summary"]["fixture"] == "tool_call"
        assert events[0]["summary"]["provider"] == "fake"
        assert "shipped on 24 October" in events[-1]["summary"]["reply"]
        assert h.health()["fixture"] == "plain_answer"  # the start-up fixture is unchanged

        # `step` at the top level, or inside options, or the P1 `options.step_mode`.
        for body in ({"step": True}, {"options": {"step": True}}, {"options": {"step_mode": True}}):
            r = await c.post("/v1/turns", json={"message": "What are your support hours?", **body})
            turn_id = r.json()["turn_id"]
            await until(c, turn_id, "status", "paused")
            assert h.tracer.get_turn("t1", turn_id)[0]["summary"]["step_mode"] is True
            await c.post(f"/v1/turns/{turn_id}/cancel", json={})
            await h.gateway.wait(turn_id, 5)

        # `disable` merges with the P1 `options.disabled_nodes`.
        r = await c.post(
            "/v1/turns",
            json={
                "message": "What are your support hours?",
                "disable": ["output_guardrails"],
                "options": {"disabled_nodes": ["input_guardrails", "output_guardrails"]},
            },
        )
        await h.gateway.wait(r.json()["turn_id"], 5)
        state = h.gateway.get("t1", r.json()["turn_id"])
        assert state is not None
        assert state.request.disabled_nodes == ["output_guardrails", "input_guardrails"]


@pytest.mark.anyio
async def test_per_turn_option_errors_and_mode_checks(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:

        async def post(**body: Any) -> httpx.Response:
            return await c.post("/v1/turns", json={"message": "hi", **body})

        for body in (
            {"provider": "gpt"},
            {"fixture": "../../pyproject"},
            {"fixture": "no_such_fixture"},
            {"fixture": "tool_call", "provider": "ollama"},
            {"step": "yes"},
            {"provider": 3},
            {"disable": "policy"},
        ):
            r = await post(**body)
            assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request", body
        r = await post(provider="anthropic")
        assert r.status_code == 422 and r.json()["error"]["code"] == "provider_not_configured"
        assert (await post(disable=["loop"])).status_code == 422

    pub = Harness(
        settings(
            tmp_path / "pub",
            "plain_answer",
            profile=None,
            mode="public",
            overrides={"gateway.stream_reply_chars": 0},
        )
    )
    transport = httpx.ASGITransport(app=create_app(pub))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/turns", json={"message": "hi", "provider": "fake"})
        assert r.status_code == 400
        r = await c.post("/v1/turns", json={"message": "hi", "fixture": "plain_answer"})
        assert r.status_code == 400
        r = await c.post("/v1/turns", json={"message": "hi", "disable": ["policy"]})
        assert r.status_code == 422

    # Private mode (no sign-in over HTTP until P6): disable is allowed, provider is not.
    priv = Harness(settings(tmp_path / "priv", "plain_answer", profile=None, mode="private"))
    who = Identity("p1", "u1", ["user"], anonymous=False)
    with pytest.raises(GatewayError) as exc:
        await priv.gateway.start_turn(TurnRequest("hi", who, provider="fake"))
    assert exc.value.status == 400
    state = await priv.gateway.start_turn(TurnRequest("hi", who, disabled_nodes=["policy"]))
    await priv.gateway.wait(state.ctx.turn_id, 5)
    assert state.ctx.disabled_nodes == frozenset({"policy"})


# -- web/dist and CORS ------------------------------------------------------------------------


def make_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>harnesswalk</title>", "utf-8")
    (dist / "assets" / "app.js").write_text("console.log('hi')", "utf-8")
    return dist


@pytest.mark.anyio
async def test_serves_web_dist_with_spa_fallback(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    app = create_app(h, web_dist=make_dist(tmp_path))
    async with client(h, app=app) as c:
        root = await c.get("/")
        assert root.status_code == 200 and "<title>harnesswalk</title>" in root.text
        asset = await c.get("/assets/app.js")
        assert asset.status_code == 200 and "javascript" in asset.headers["content-type"]
        for route in ("/sessions/sess_1", "/inspect/policy"):
            r = await c.get(route)
            assert r.status_code == 200 and "<title>harnesswalk</title>" in r.text, route
        # The API always wins, and an unknown API path is a JSON 404, never the app.
        assert (await c.get("/healthz")).json()["status"] == "ok"
        assert (await c.get("/v1/capabilities")).json()["mode"] == "dev"
        for path in ("/v1/nope", "/v1", "/v1/turns/turn_x/nope"):
            r = await c.get(path)
            assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", path
        assert (await c.post("/v1/turns", json={"message": "hi"})).status_code == 202


@pytest.mark.anyio
async def test_web_dist_from_env_and_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    monkeypatch.setenv("HARNESSWALK_WEB_DIST", str(tmp_path / "nowhere"))
    async with client(h) as c:
        r = await c.get("/")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    monkeypatch.setenv("HARNESSWALK_WEB_DIST", str(make_dist(tmp_path)))
    async with client(h) as c:
        assert "<title>harnesswalk</title>" in (await c.get("/anything")).text


@pytest.mark.anyio
async def test_cors_in_dev_mode_only(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    preflight = {
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-harnesswalk-tenant",
    }
    async with client(h) as c:
        r = await c.options("/v1/turns", headers=preflight)
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert r.headers["access-control-allow-credentials"] == "true"
        r = await c.get("/v1/capabilities", headers={"Origin": "http://127.0.0.1:5173"})
        assert r.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        r = await c.get("/v1/capabilities", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in r.headers

    pub = Harness(
        settings(
            tmp_path / "pub",
            "plain_answer",
            profile=None,
            mode="public",
            overrides={"gateway.stream_reply_chars": 0},
        )
    )
    transport = httpx.ASGITransport(app=create_app(pub))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/v1/capabilities", headers={"Origin": "http://localhost:5173"})
        assert "access-control-allow-origin" not in r.headers
