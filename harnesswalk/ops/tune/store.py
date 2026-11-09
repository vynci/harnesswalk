"""The tuning loop's own records: curations, tune runs, comparisons and the canary ledger.

Contract: spec/contracts/tuning.md. One SQLite file (`tune.sqlite_path`, default data/tune.db).
Jobs (a curation, a run, a comparison) are one row each with their whole record as JSON; the
canary ledger keeps one row per turn the `canary` rule routed (counts only, never content), and
one row per canary setting so a window can start where the fraction last changed.
"""

from __future__ import annotations

import json
import time
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.events import iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS tune_jobs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canary_settings (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    at_s REAL NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canary_turns (
    tenant_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    pack TEXT,
    arm TEXT NOT NULL,
    alias TEXT NOT NULL,
    at_s REAL NOT NULL,
    status TEXT,
    exit TEXT,
    bad INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, turn_id)
);
"""

PREFIX = {"curation": "cu", "run": "tr", "comparison": "cmp"}


class TuneStore:
    def __init__(self, path: str) -> None:
        self.db = Database(path, SCHEMA)

    # -- jobs -------------------------------------------------------------------------------------

    def new_id(self, kind: str) -> str:
        row = self.db.one("SELECT COUNT(*) AS n FROM tune_jobs WHERE kind = ?", (kind,))
        n = (row["n"] if row else 0) + 1
        while self.db.one("SELECT 1 FROM tune_jobs WHERE job_id = ?", (f"{PREFIX[kind]}_{n}",)):
            n += 1
        return f"{PREFIX[kind]}_{n}"

    def put(self, kind: str, record: dict[str, Any]) -> None:
        """Insert or replace a job's record (its `id`, `tenant_id`, `status`, `created_at`)."""
        body = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
        existing = self.db.one("SELECT seq FROM tune_jobs WHERE job_id = ?", (record["id"],))
        if existing is None:
            self.db.execute(
                "INSERT INTO tune_jobs (job_id, kind, tenant_id, status, created_at, body)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (record["id"], kind, record["tenant_id"], record["status"],
                 record["created_at"], body),
            )  # fmt: skip
        else:
            self.db.execute(
                "UPDATE tune_jobs SET status = ?, body = ? WHERE job_id = ?",
                (record["status"], body, record["id"]),
            )

    def get(self, job_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
        row = self.db.one("SELECT tenant_id, body FROM tune_jobs WHERE job_id = ?", (job_id,))
        if row is None or (tenant_id is not None and row["tenant_id"] != tenant_id):
            return None
        return json.loads(row["body"])

    def list(self, kind: str, tenant_id: str | None = None, limit: int = 20) -> list[dict]:
        if tenant_id is None:
            rows = self.db.query(
                "SELECT body FROM tune_jobs WHERE kind = ? ORDER BY seq DESC LIMIT ?",
                (kind, limit),
            )
        else:
            rows = self.db.query(
                "SELECT body FROM tune_jobs WHERE kind = ? AND tenant_id = ?"
                " ORDER BY seq DESC LIMIT ?",
                (kind, tenant_id, limit),
            )
        return [json.loads(r["body"]) for r in rows]

    def unfinished(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT body FROM tune_jobs WHERE status = 'running'")
        return [json.loads(r["body"]) for r in rows]

    # -- the canary ledger ------------------------------------------------------------------------

    def canary_set(self, setting: dict[str, Any]) -> None:
        now = time.time()
        self.db.execute(
            "INSERT INTO canary_settings (at, at_s, body) VALUES (?, ?, ?)",
            (iso(now), now, json.dumps(setting, sort_keys=True)),
        )

    def canary_last(self) -> dict[str, Any] | None:
        row = self.db.one("SELECT at, at_s, body FROM canary_settings ORDER BY seq DESC LIMIT 1")
        if row is None:
            return None
        return {**json.loads(row["body"]), "at": row["at"], "at_s": row["at_s"]}

    def canary_history(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT at, body FROM canary_settings ORDER BY seq DESC LIMIT ?", (limit,)
        )
        return [{**json.loads(r["body"]), "at": r["at"]} for r in rows]

    def canary_turn(self, tenant_id: str, turn_id: str, pack: str | None, arm: str, alias: str):
        self.db.execute(
            "INSERT OR IGNORE INTO canary_turns (tenant_id, turn_id, pack, arm, alias, at_s)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, turn_id, pack, arm, alias, time.time()),
        )

    def canary_end(self, tenant_id: str, turn_id: str, status: str, exit_: str | None) -> None:
        self.db.execute(
            "UPDATE canary_turns SET status = ?, exit = ? WHERE tenant_id = ? AND turn_id = ?",
            (status, exit_, tenant_id, turn_id),
        )

    def canary_bad(self, tenant_id: str, turn_id: str, bad: bool) -> None:
        self.db.execute(
            "UPDATE canary_turns SET bad = ? WHERE tenant_id = ? AND turn_id = ?",
            (int(bad), tenant_id, turn_id),
        )

    def canary_rows(self, since_s: float, tenant_id: str | None) -> list[dict[str, Any]]:
        if tenant_id is None:
            rows = self.db.query("SELECT * FROM canary_turns WHERE at_s >= ?", (since_s,))
        else:
            rows = self.db.query(
                "SELECT * FROM canary_turns WHERE at_s >= ? AND tenant_id = ?",
                (since_s, tenant_id),
            )
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.db.close()
