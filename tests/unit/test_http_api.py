"""The HTTP surface (http_api.md): status codes, tenancy, SSE, resume, approvals, inspectors."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from harnesswalk.control.http_api import create_app
from harnesswalk.runtime import Harness
from tests.spec.support import check_stream
from tests.unit.support import settings


def client(h: Harness, tenant: str = "t1") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(h))
    return httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Harnesswalk-Tenant": tenant}
    )


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


@pytest.mark.anyio
async def test_turn_round_trip_and_tenancy(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c, client(h, "other") as o:
        health = (await c.get("/healthz")).json()
        assert (
            health["status"] == "ok"
            and health["provider"] == "fake"
            and health["fixture"] == "plain_answer"
        )
        r = await c.post("/v1/turns", json={"message": "What are your support hours?"})
        assert r.status_code == 202
        body = r.json()
        assert set(body) == {"turn_id", "trace_id", "session_id", "events_url"}
        await h.gateway.wait(body["turn_id"], 5)
        stream = await c.get(body["events_url"])
        assert stream.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(stream.text)
        assert check_stream(events) == [] and events[-1]["summary"]["exit"] == "answered"
        assert "id: 0\nevent: turn_start\n" in stream.text
        tail = parse_sse((await c.get(body["events_url"], params={"after_seq": 30})).text)
        assert tail[0]["seq"] == 31
        resumed = parse_sse((await c.get(body["events_url"], headers={"Last-Event-ID": "35"})).text)
        assert resumed[0]["seq"] == 36
        # Another tenant cannot see the turn, or continue the session.
        assert (await o.get(body["events_url"])).status_code == 404
        r = await o.post("/v1/turns", json={"message": "hi", "session_id": body["session_id"]})
        assert r.status_code == 404
        node = (await c.get("/v1/nodes/model_layer")).json()
        assert node["recent"][0]["summary"]["provider"] == "fake"
        assert (await o.get("/v1/nodes/model_layer")).json()["recent"] == []


@pytest.mark.anyio
async def test_request_errors(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:
        bad = await c.post(
            "/v1/turns", content=b"{not json", headers={"Content-Type": "application/json"}
        )
        assert bad.status_code == 400
        assert (await c.post("/v1/turns", json={"message": ""})).status_code == 400
        assert (await c.post("/v1/turns", json={"message": "x" * 70000})).status_code == 400
        r = await c.post(
            "/v1/turns", json={"message": "hi", "options": {"disabled_nodes": ["loop"]}}
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
        r = await c.post("/v1/turns", json={"message": "hi", "options": {"dataset": "finance"}})
        assert r.status_code == 422
        assert (await c.get("/v1/turns/turn_nope/events")).status_code == 404
        assert (
            await c.post("/v1/turns/turn_nope/resume", json={"mode": "step"})
        ).status_code == 404
        r = await c.post("/v1/approvals/apr_nope", json={"decision": "approve"})
        assert r.status_code == 404 and r.json()["error"]["code"] == "approval_not_found"
        assert (await c.get("/v1/nodes/not_a_node")).status_code == 404


@pytest.mark.anyio
async def test_step_mode_over_http(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:
        r = await c.post(
            "/v1/turns",
            json={"message": "What are your support hours?", "options": {"step_mode": True}},
        )
        turn_id, session_id = r.json()["turn_id"], r.json()["session_id"]
        await asyncio.sleep(0.02)
        busy = await c.post("/v1/turns", json={"message": "again", "session_id": session_id})
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "turn_in_progress"
        r = await c.post(f"/v1/turns/{turn_id}/resume", json={"mode": "step"})
        assert r.json() == {"turn_id": turn_id, "resumed_node_id": "gateway", "mode": "step"}
        await asyncio.sleep(0.02)
        r = await c.post(f"/v1/turns/{turn_id}/resume", json={"mode": "continue"})
        assert r.json()["resumed_node_id"] == "input_guardrails"
        await h.gateway.wait(turn_id, 5)
        assert (
            await c.post(f"/v1/turns/{turn_id}/resume", json={"mode": "step"})
        ).status_code == 409
        events = parse_sse((await c.get(f"/v1/turns/{turn_id}/events")).text)
        assert sum(e["type"] == "node_paused" for e in events) == 2
        assert check_stream(events) == []


@pytest.mark.anyio
async def test_approval_decided_once(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "approval_deny"))
    async with client(h) as c, client(h, "other") as o:
        r = await c.post("/v1/turns", json={"message": "Please cancel order A-1002."})
        turn_id = r.json()["turn_id"]
        await asyncio.sleep(0.05)
        pending = (await c.get("/v1/nodes/approval")).json()["inspector"]["pending"]
        assert len(pending) == 1 and pending[0]["args_preview"] == {"order_id": "A-1002"}
        approval_id = pending[0]["approval_id"]
        assert (
            await o.post(f"/v1/approvals/{approval_id}", json={"decision": "approve"})
        ).status_code == 404
        r = await c.post(f"/v1/approvals/{approval_id}", json={"decision": "deny", "reason": "no"})
        assert (
            r.status_code == 200
            and r.json()["decision"] == "deny"
            and r.json()["turn_id"] == turn_id
        )
        again = await c.post(f"/v1/approvals/{approval_id}", json={"decision": "approve"})
        assert (
            again.status_code == 409 and again.json()["error"]["code"] == "approval_already_decided"
        )
        await h.gateway.wait(turn_id, 5)
        audit = (await c.get("/v1/nodes/audit")).json()["inspector"]
        assert audit["verify"]["ok"] and len(audit["last_records"]) == 2


@pytest.mark.anyio
async def test_diagram_and_inspectors(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:
        doc = (await c.get("/v1/diagram")).json()
        by_id = {n["id"]: n for n in doc["nodes"]}
        assert by_id["loop"]["implemented"] and by_id["knowledge"]["implemented"]
        assert by_id["eval"]["implemented"] and by_id["release"]["implemented"]
        assert by_id["diagnose"]["implemented"] and not by_id["eval"]["stub"]
        simple = (await c.get("/v1/diagram", params={"view": "simple"})).json()
        assert len(simple["nodes"]) == 15
        policy = (await c.get("/v1/nodes/policy")).json()
        assert policy["inspector"]["path"] == "config/policy.yaml"
        assert policy["inspector"]["version"].startswith("policy@")
        assert policy["source"]["path"] == "harnesswalk/control/policy.py"
        assert "class Policy" in policy["source"]["content"]
        prompt = (await c.get("/v1/nodes/procedural")).json()["inspector"]
        assert prompt["type"] == "prompt" and prompt["items"][0]["name"] == "system_prompt"
        model = (await c.get("/v1/nodes/model_layer")).json()["inspector"]
        assert model["type"] == "model" and model["providers"] == ["fake"]


@pytest.mark.anyio
async def test_modes(tmp_path: Path) -> None:
    pub = Harness(settings(tmp_path / "pub", "plain_answer", profile=None, mode="public"))
    transport = httpx.ASGITransport(app=create_app(pub))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/v1/turns", json={"message": "hi", "options": {"disabled_nodes": ["policy"]}}
        )
        assert r.status_code == 422
        r = await c.post("/v1/turns", json={"message": "What are your support hours?"})
        assert r.status_code == 202 and "harnesswalk_tenant" in r.cookies
        await pub.gateway.wait(r.json()["turn_id"], 5)
        # The signed cookie keeps the same anonymous tenant; the events are visible to it only.
        assert (await c.get(r.json()["events_url"])).status_code == 200
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as stranger:
        assert (await stranger.get(r.json()["events_url"])).status_code == 404
    priv = Harness(settings(tmp_path / "priv", "plain_answer", profile=None, mode="private"))
    transport = httpx.ASGITransport(app=create_app(priv))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/turns", json={"message": "hi"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"
