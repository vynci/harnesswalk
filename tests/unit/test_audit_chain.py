"""Audit P3: complete records, the per-tenant hash chain, tamper detection, the export."""

import sqlite3
from pathlib import Path

import httpx
import pytest

from harnesswalk.control.audit import GENESIS, AuditEntry, AuditLog
from harnesswalk.control.http_api import create_app
from tests.unit.support import harness, make_ctx, run_turn


def entry(n: int) -> AuditEntry:
    return AuditEntry("policy_decision", "agent", f"turn_{n}", "0" * 32, tool="t", decision="allow")


def test_chain_links_per_tenant_across_two_writers(tmp_path: Path) -> None:
    path = str(tmp_path / "audit.db")
    a, b = AuditLog(path), AuditLog(path)  # a server and a CLI on one file
    ctx1, _ = make_ctx("t1")
    ctx2, _ = make_ctx("t2")
    first = a.append(ctx1, entry(1))
    b.append(ctx1, entry(2))
    a.append(ctx2, entry(3))
    a.append(ctx1, entry(4))
    assert first.prev_hash == GENESIS
    assert a.verify("t1") == {"ok": True, "records": 3}
    assert a.verify("t2") == {"ok": True, "records": 1}
    t1 = a.export("t1")
    assert [r.prev_hash for r in t1[1:]] == [r.hash for r in t1[:-1]]


def test_storage_refuses_updates_and_verify_finds_tampering(tmp_path: Path) -> None:
    path = tmp_path / "audit.db"
    log = AuditLog(str(path))
    ctx, _ = make_ctx("t1")
    records = [log.append(ctx, entry(n)) for n in range(3)]
    with pytest.raises(sqlite3.Error):
        log.db.execute("UPDATE audit SET entry = '{}'")
    with pytest.raises(sqlite3.Error):
        log.db.execute("DELETE FROM audit")
    raw = sqlite3.connect(path)  # someone with file access drops the guard and edits a record
    raw.execute("DROP TRIGGER audit_no_update")
    raw.execute(
        "UPDATE audit SET entry = replace(entry, 'allow', 'deny') WHERE record_id = ?",
        (records[1].record_id,),
    )
    raw.commit()
    raw.close()
    assert log.verify("t1") == {"ok": False, "records": 3, "first_bad_record": records[1].record_id}


@pytest.mark.anyio
async def test_approved_write_records_who_what_and_before_after(tmp_path: Path) -> None:
    tools = tmp_path / "tools.yaml"
    tools.write_text(
        """
- name: lookup_order
  description: Look up an order.
  action_class: read
  timeout_ms: 1000
  parameters: { type: object, required: [order_id], properties: { order_id: { type: string } } }
  table: { A-1002: { order_id: A-1002, status: processing, email: ana@example.com } }
- name: cancel_order
  description: Cancel an order.
  action_class: destructive
  timeout_ms: 1000
  audit: { before: lookup_order }
  parameters: { type: object, required: [order_id], properties: { order_id: { type: string } } }
  table: { A-1002: { order_id: A-1002, status: cancelled } }
""",
        encoding="utf-8",
    )
    h = harness(tmp_path, "approval_approve", overrides={"tools.files": [str(tools)]})
    await run_turn(h, "Please cancel order A-1002.", approval="approve")
    records = [r.entry for r in h.audit.export("t1")]
    assert [r["action"] for r in records] == [
        "policy_decision",
        "approval_decision",
        "tool_executed",
    ]
    policy, approval, executed = records
    assert policy["rule"] == "dev.destructive.approve" and policy["action_class"] == "destructive"
    assert approval["approver"] == "user:test" and approval["decision"] == "approve"
    assert executed["executed"] is True and executed["outcome"] == "ok"
    assert executed["before"] == {"order_id": "A-1002", "status": "processing", "email": "[EMAIL]"}
    assert executed["after"] == {"order_id": "A-1002", "status": "cancelled"}
    assert "args_digest" in executed and "arguments" not in executed


@pytest.mark.anyio
async def test_export_endpoint(tmp_path: Path) -> None:
    h = harness(tmp_path, "approval_approve")
    await run_turn(h, "Please cancel order A-1002.", tenant="dev", approval="approve")
    app = create_app(h)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        body = (await c.get("/v1/audit")).json()
        assert body["verify"] == {"ok": True, "records": 3} and len(body["records"]) == 3
        assert body["next_since"] is None
        page = (await c.get("/v1/audit", params={"limit": 2})).json()
        assert len(page["records"]) == 2 and page["next_since"] == page["records"][-1]["ts"]
        later = (await c.get("/v1/audit", params={"since": "2999-01-01T00:00:00Z"})).json()
        assert later["records"] == []
        assert (await c.get("/v1/audit", params={"since": "yesterday"})).status_code == 400
        other = (await c.get("/v1/audit", headers={"X-Harnesswalk-Tenant": "other"})).json()
        assert other["records"] == [] and other["verify"]["records"] == 0
    public = harness(tmp_path / "pub", "plain_answer", mode="public")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(public)), base_url="http://t"
    ) as c:
        r = await c.get("/v1/audit")
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_in_mode"
