"""Audit: an append-only, per-tenant hash chain of every decision with consequences.

Contract: spec/contracts/audit.md. Node: `audit` (on with policy and approval). The storage
refuses updates and deletes (db.APPEND_ONLY); each record carries the hash of the one before.

Lesson: the trace is sampled and pruned; the audit log is not. It is how you answer "who
approved this cancel, under which rule" a month later, and the chain shows if a row was edited.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from harness.core.events import iso

ZERO = "0" * 64


def _hash(prev: str, entry: dict[str, Any], ts: str, record_id: str) -> str:
    body = json.dumps({"entry": entry, "ts": ts, "record_id": record_id}, sort_keys=True)
    return hashlib.sha256((prev + body).encode()).hexdigest()


def append_record(db: Any, tenant: str, entry: dict[str, Any]) -> dict[str, Any]:
    with db.lock:
        last = db.rows("audit", tenant=tenant)
        prev = last[-1]["hash"] if last else ZERO
        rid, ts = "aud_" + uuid.uuid4().hex[:20], iso(time.time())
        entry = {k: v for k, v in entry.items() if v is not None}
        record = {"record_id": rid, "tenant": tenant, "ts": ts, "entry": entry,
                  "prev_hash": prev, "hash": _hash(prev, entry, ts, rid)}  # fmt: skip
        db.insert("audit", record)
    return record


def append(h: Any, turn: Any, entry: dict[str, Any]) -> dict[str, Any]:
    with turn.span("audit") as span:
        rec = append_record(h.db, turn.ids["tenant_id"], entry)
        e = rec["entry"]
        span.set(record_id=rec["record_id"], action=e["action"], tool=e.get("tool"),
                 decision=e.get("decision"), decided_by=e.get("approver"),
                 executed=e.get("executed"), prev_hash=rec["prev_hash"][:12],
                 hash=rec["hash"][:12])  # fmt: skip
    return rec


def verify(db: Any, tenant: str) -> dict[str, Any]:
    prev, records = ZERO, db.rows("audit", tenant=tenant)
    for r in records:
        if r["prev_hash"] != prev or _hash(prev, r["entry"], r["ts"], r["record_id"]) != r["hash"]:
            return {"ok": False, "records": len(records), "first_bad_record": r["record_id"]}
        prev = r["hash"]
    return {"ok": True, "records": len(records)}


def export(db: Any, tenant: str, limit: int = 500) -> dict[str, Any]:
    records = [{**r, "tenant_id": r["tenant"]} for r in db.rows("audit", tenant=tenant)]
    return {"tenant_id": tenant, "verify": verify(db, tenant), "records": records[:limit],
            "next_since": None}  # fmt: skip
