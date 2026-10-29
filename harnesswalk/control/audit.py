"""Audit: the append-only, hash-chained record of decisions with consequences.

Contract: spec/contracts/audit.md. Node: `audit` (control plane, kind store).

One SQLite table. Triggers reject UPDATE and DELETE, so "append-only" is enforced by storage,
not by convention. Each record carries the hash of the previous record of the same tenant;
`verify()` walks the chain. Appends run in a write transaction, so two writers (a server and a
CLI on the same file) cannot fork the chain. Arguments are recorded as a sha256 digest only;
the full call is in the trace under the same `trace_id`.

Records (P3): `policy_decision` and `approval_decision` before a call runs, `tool_executed`
after a non-read call ran (with `executed`, `outcome`, and `before` and `after` for writes),
`memory_written` from the memory writer. `GET /v1/audit` exports a tenant's chain.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.events import iso, uuid7
from harnesswalk.nodes import node, register
from harnesswalk.types import HarnessError, TurnContext

register("audit", __name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    record_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    entry TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_by_tenant ON audit (tenant_id);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
"""

GENESIS = "0" * 64


def args_digest(arguments: dict[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class AuditEntry:
    action: str  # policy_decision | approval_decision | tool_executed | ...
    actor: str
    turn_id: str
    trace_id: str
    tool: str | None = None
    action_class: str | None = None
    decision: str | None = None
    rule: str | None = None
    approval_id: str | None = None
    args_digest: str | None = None
    approver: str | None = None
    executed: bool | None = None
    outcome: str | None = None
    before: Any = None
    after: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditRecord:
    record_id: str
    tenant_id: str
    ts: str
    entry: dict[str, Any]
    prev_hash: str
    hash: str


def _hash(prev_hash: str, record_id: str, ts: str, entry: dict[str, Any]) -> str:
    canonical = json.dumps({"entry": entry, "ts": ts, "record_id": record_id}, sort_keys=True)
    return hashlib.sha256((prev_hash + canonical).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str) -> None:
        self.db = Database(path, SCHEMA)

    def append(self, ctx: TurnContext, entry: AuditEntry) -> AuditRecord:
        record_id = "aud_" + uuid7().replace("-", "")[:20]
        ts = iso(time.time())
        body = {k: v for k, v in asdict(entry).items() if v not in (None, {})}
        try:
            with self.db.transaction() as conn:
                row = conn.execute(
                    "SELECT hash FROM audit WHERE tenant_id = ? ORDER BY rowid DESC LIMIT 1",
                    (ctx.tenant_id,),
                ).fetchone()
                prev = row["hash"] if row else GENESIS
                digest = _hash(prev, record_id, ts, body)
                conn.execute(
                    "INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?)",
                    (record_id, ctx.tenant_id, ts, json.dumps(body, sort_keys=True), prev, digest),
                )
        except sqlite3.Error as exc:
            raise HarnessError("store_unavailable", f"audit store: {exc}") from exc
        return AuditRecord(record_id, ctx.tenant_id, ts, body, prev, digest)

    def export(
        self,
        tenant_id: str,
        since: str | None = None,
        limit: int = 500,
        after: str | None = None,
    ) -> list[AuditRecord]:
        """Oldest first, in chain order, from `since` (an RFC 3339 timestamp) on, and after the
        record `after` (exclusive) when given: the cursor that pages without repeats."""
        after_row = 0
        if after:
            row = self.db.query(
                "SELECT rowid FROM audit WHERE tenant_id = ? AND record_id = ?", (tenant_id, after)
            )
            if not row:
                raise KeyError(after)
            after_row = row[0]["rowid"]
        rows = self.db.query(
            "SELECT * FROM audit WHERE tenant_id = ? AND ts >= ? AND rowid > ?"
            " ORDER BY rowid LIMIT ?",
            (tenant_id, since or "", after_row, limit),
        )
        return [self._record(r) for r in rows]

    @staticmethod
    def _record(r: Any) -> AuditRecord:
        return AuditRecord(
            r["record_id"],
            r["tenant_id"],
            r["ts"],
            json.loads(r["entry"]),
            r["prev_hash"],
            r["hash"],
        )

    def list(self, tenant_id: str, limit: int = 50) -> list[AuditRecord]:
        rows = self.db.query(
            "SELECT * FROM audit WHERE tenant_id = ? ORDER BY rowid DESC LIMIT ?",
            (tenant_id, limit),
        )
        return [self._record(r) for r in rows]

    def verify(self, tenant_id: str) -> dict[str, Any]:
        rows = self.db.query("SELECT * FROM audit WHERE tenant_id = ? ORDER BY rowid", (tenant_id,))
        prev = GENESIS
        for r in rows:
            expected = _hash(prev, r["record_id"], r["ts"], json.loads(r["entry"]))
            if r["prev_hash"] != prev or r["hash"] != expected:
                return {"ok": False, "records": len(rows), "first_bad_record": r["record_id"]}
            prev = r["hash"]
        return {"ok": True, "records": len(rows)}


async def record(ctx: TurnContext, log: AuditLog, entry: AuditEntry) -> AuditRecord:
    """The `audit` span: append one record and show what was recorded."""
    async with node(ctx, "audit") as span:
        rec = log.append(ctx, entry)
        span.set(
            record_id=rec.record_id,
            action=entry.action,
            tool=entry.tool,
            decision=entry.decision,
            prev_hash=rec.prev_hash[:12],
            hash=rec.hash[:12],
        )
        if entry.action == "approval_decision":
            span.set(decided_by=entry.actor)
        if entry.executed is not None:
            span.set(executed=entry.executed)
    return rec
