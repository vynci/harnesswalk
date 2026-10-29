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
