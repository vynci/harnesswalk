"""Regressions from the Phase 2 integration pass: cursors, cancel, bounded turn state."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from harnesswalk.control.http_api import create_app
from harnesswalk.runtime import Harness
from tests.unit.support import settings


def client(h: Harness, tenant: str = "t1") -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(h))
    return httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Harnesswalk-Tenant": tenant}
    )


def parse_sse(text: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


# -- stream cursors ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_query_cursors_follow_the_header_rule(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c:
        r = await c.post("/v1/turns", json={"message": "What are your support hours?"})
        turn_id, url = r.json()["turn_id"], r.json()["events_url"]
        await h.gateway.wait(turn_id, 5)
        # Above SQLite's integer range this used to fail mid-stream; now it is refused up front.
        for name in ("after", "after_seq"):
            for bad in ("99999999999999999999", "1e3", "--1", "x", " 7 8 "):
                r = await c.get(url, params={name: bad})
                assert r.status_code == 400, (name, bad)
                assert r.json()["error"]["code"] == "invalid_request"
                assert name in r.json()["error"]["message"]
        # Eighteen digits is the largest cursor accepted, and it is past the end of any turn.
        r = await c.get(url, params={"after": "9" * 18})
        assert r.status_code == 200 and parse_sse(r.text) == []
        assert parse_sse((await c.get(url, params={"after": " 3 "})).text)[0]["seq"] == 4
        assert parse_sse((await c.get(url, params={"after": "-1"})).text)[0]["seq"] == 0
        assert parse_sse((await c.get(url, params={"after": ""})).text)[0]["seq"] == 0
        both = await c.get(url, params={"after": "2"}, headers={"Last-Event-ID": "5"})
        assert parse_sse(both.text)[0]["seq"] == 6  # the largest cursor still wins


# -- bounded turn state -----------------------------------------------------------------------


async def _answer(c: httpx.AsyncClient, h: Harness, session: str, text: str) -> str:
    r = await c.post("/v1/turns", json={"message": text, "session_id": session})
    assert r.status_code == 202, r.text
    turn_id = r.json()["turn_id"]
    await h.gateway.wait(turn_id, 5)
    return turn_id


@pytest.mark.anyio
async def test_finished_turns_over_the_cap_are_evicted_and_still_readable(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer", overrides={"gateway.turn_state_max": 2}))
    async with client(h) as c:
        r = await c.post("/v1/turns", json={"message": "hi", "step": True, "session_id": "s_run"})
        paused = r.json()["turn_id"]
        ids = []
        for n in range(4):
            fixture = await c.post(
                "/v1/turns",
                json={
                    "message": f"question {n}",
                    "session_id": f"s_{n}",
                    "fixture": "plain_answer",
                },
            )
            ids.append(fixture.json()["turn_id"])
            await h.gateway.wait(ids[-1], 5)
        # The two newest finished turns stay; a paused turn is never evicted.
        assert set(h.gateway.turns) == {paused, ids[2], ids[3]}
        assert h.gateway.describe()["finished_kept"] == 2
        first = ids[0]
        summary = (await c.get(f"/v1/turns/{first}")).json()
        assert summary["status"] == "done" and summary["exit"] == "answered"
        assert summary["reply"] and summary["message"] == "question 0"
        listed = (await c.get("/v1/sessions/s_0/turns")).json()
        assert [t["turn_id"] for t in listed] == [first]
        events = parse_sse((await c.get(f"/v1/turns/{first}/events")).text)
        assert events[0]["type"] == "turn_start" and events[-1]["type"] == "turn_end"
        # Control calls answer as for any finished turn, not as for an unknown one.
        r = await c.post(f"/v1/turns/{first}/cancel", json={})
        assert r.status_code == 409 and r.json()["error"]["code"] == "turn_finished"
        assert r.json()["turn"]["turn_id"] == first
        r = await c.post(f"/v1/turns/{first}/resume", json={"mode": "step"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "turn_not_paused"
        assert (
            await c.post("/v1/turns/turn_nope/resume", json={"mode": "step"})
        ).status_code == 404
        # The paused turn is still fully controllable.
        r = await c.post(f"/v1/turns/{paused}/resume", json={"mode": "continue"})
        assert r.status_code == 200
        await h.gateway.wait(paused, 5)
    await h.aclose()


@pytest.mark.anyio
async def test_finished_turns_past_the_ttl_are_evicted(tmp_path: Path) -> None:
    h = Harness(
        settings(tmp_path, "plain_answer", overrides={"gateway.turn_state_ttl_seconds": 60})
    )
    now = [1000.0]
    h.gateway._clock = lambda: now[0]
    async with client(h) as c:
        old = await _answer(c, h, "s_a", "first")
        now[0] += 30
        recent = await _answer(c, h, "s_b", "second")
        assert {old, recent} <= set(h.gateway.turns)
        now[0] += 31  # the first is now 61 s old, the second 31 s
        assert h.gateway.evict_finished() == 1
        assert old not in h.gateway.turns and recent in h.gateway.turns
        now[0] += 30
        third = await _answer(c, h, "s_c", "third")  # starting a turn evicts too
        assert set(h.gateway.turns) == {third}
        assert (await c.get(f"/v1/turns/{old}")).json()["status"] == "done"
    await h.aclose()


def test_turn_state_defaults_come_from_harness_yaml(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    assert h.gateway.turn_state_max == 500 and h.gateway.turn_state_ttl_s == 3600
