"""Failure paths: a broken trace store, downstream errors, races, and BYO key handling."""

import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest

from harnesswalk.control.http_api import create_app
from harnesswalk.execution.model_layer import ModelLayer, ModelRequest
from harnesswalk.execution.providers import ProviderError, ProviderResponse, ProviderUsage
from harnesswalk.execution.router import Router
from harnesswalk.types import Message
from tests.spec.support import check_stream
from tests.unit.support import exits, harness, make_ctx, run_turn


@pytest.mark.anyio
async def test_store_outage_keeps_the_stream_whole(tmp_path: Path, monkeypatch) -> None:
    h = harness(tmp_path, "tool_call")

    def down(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(h.tracer, "_insert", down)  # every event write fails
    monkeypatch.setattr(h.tracer.db, "execute", down)  # and every detail write
    events_seen = []
    h.bus.subscribe(events_seen.append)
    await run_turn(h, "Where is order A-1001?")
    assert check_stream(events_seen) == []
    assert events_seen[-1]["summary"]["exit"] == "answered"
    assert not any("detail_ref" in e for e in events_seen)  # details dropped, events kept
    assert exits(events_seen, "trace")[0]["summary"]["persisted"] is False
    assert [e["seq"] for e in h.tracer.get_turn("t1", events_seen[0]["turn_id"])] == list(
        range(len(events_seen))
    )


@pytest.mark.anyio
async def test_context_failure_still_replies_with_a_notice(tmp_path: Path, monkeypatch) -> None:
    h = harness(tmp_path, "plain_answer")

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("session store is gone")

    monkeypatch.setattr(h.session, "summary", boom)
    events = await run_turn(h, "What are your support hours?")
    assert check_stream(events) == []
    assert events[-1]["summary"]["exit"] == "error"
    assert "went wrong" in events[-1]["summary"]["reply"]
    assert [e["node_id"] for e in events if e["type"] == "error"][:2] == ["session", "memory"]


@pytest.mark.anyio
async def test_long_reply_is_capped_with_a_detail_ref(tmp_path: Path) -> None:
    from tests.unit.support import write_fixture

    fx = tmp_path / "fx"
    write_fixture(fx, "long", [[{"text": "x" * 20000}]])
    h = harness(tmp_path, "long", fixtures=fx, overrides={"output_guardrails.max_chars": 50000})
    events = await run_turn(h, "Tell me everything")
    end = events[-1]
    assert end["summary"]["reply"].endswith("[truncated]") and "detail_ref" in end
    assert len(h.tracer.get_detail("t1", end["detail_ref"])["reply"]) == 20000


@pytest.mark.anyio
async def test_double_resume_and_bad_params(tmp_path: Path) -> None:
    h = harness(tmp_path, "plain_answer")
    transport = httpx.ASGITransport(app=create_app(h))
    headers = {"X-Harnesswalk-Tenant": "t1"}
    async with httpx.AsyncClient(transport=transport, base_url="http://t", headers=headers) as c:
        r = await c.post("/v1/turns", json={"message": "hi", "options": {"step_mode": True}})
        turn_id = r.json()["turn_id"]
        await asyncio.sleep(0.02)
        first = await c.post(f"/v1/turns/{turn_id}/resume", json={"mode": "step"})
        second = await c.post(f"/v1/turns/{turn_id}/resume", json={"mode": "step"})
        assert first.status_code == 200
        assert second.status_code in (200, 409)
        if second.status_code == 200:  # the turn had already reached the next breakpoint
            assert second.json()["resumed_node_id"] != first.json()["resumed_node_id"]
        await c.post(f"/v1/turns/{turn_id}/resume", json={"mode": "continue"})
        await asyncio.sleep(0.05)
        await c.post(f"/v1/turns/{turn_id}/resume", json={"mode": "continue"})
        await h.gateway.wait(turn_id, 5)
        bad = await c.get(f"/v1/turns/{turn_id}/events", params={"after_seq": "abc"})
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_request"
    assert h.gateway.turns[turn_id].request.api_key is None


@pytest.mark.anyio
async def test_byo_key_never_reaches_a_fallback_provider() -> None:
    seen = {}

    class Down:
        name = "anthropic"

        async def complete(self, request):
            seen["anthropic"] = request.api_key
            raise ProviderError("provider_error", "overloaded")

    class Other:
        name = "openai_compatible"

        async def complete(self, request):
            seen["openai_compatible"] = request.api_key
            return ProviderResponse("ok", [], "stop", ProviderUsage())

    models = {"a": {"provider": "anthropic"}, "b": {"provider": "openai_compatible"}}
    router = Router([{"id": "d", "when": {}, "use": ["a", "b"]}], models, lambda p: True)
    layer = ModelLayer({"anthropic": Down(), "openai_compatible": Other()}, router, models)
    ctx, _ = make_ctx()
    await layer.complete(ctx, ModelRequest([Message("user", "hi")], [], step=1), api_key="byo-key")
    assert seen == {"anthropic": "byo-key", "openai_compatible": None}
