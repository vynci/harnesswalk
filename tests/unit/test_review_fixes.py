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
from tests.unit.support import settings


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
