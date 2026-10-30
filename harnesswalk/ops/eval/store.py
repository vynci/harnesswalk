"""Eval storage (`data/evals.db`): runs, case results, online scores and feedback.

Contract: spec/contracts/evaluator.md (P4 additions). Every read takes the tenant. A run row
holds its summary; a case row holds one case's scores, expected and actual. Online scores and
feedback are per turn.
"""

from __future__ import annotations

import json
import time
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.events import iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
    seq INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    pack TEXT NOT NULL,
    suite TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    previous_run_id TEXT,
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS eval_runs_by_pack ON eval_runs (tenant_id, pack, suite, seq);
CREATE TABLE IF NOT EXISTS eval_cases (
    run_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    case_id TEXT NOT NULL,
    status TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (run_id, case_id)
);
CREATE TABLE IF NOT EXISTS turn_scores (
    tenant_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    name TEXT NOT NULL,
    node_id TEXT NOT NULL,
    value REAL NOT NULL,
    pass INTEGER NOT NULL,
    detail TEXT,
    judge_model TEXT,
    at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, turn_id, name)
);
CREATE TABLE IF NOT EXISTS feedback (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    rating TEXT NOT NULL,
    note TEXT,
    by TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS feedback_by_turn ON feedback (tenant_id, turn_id, seq);
"""


class EvalStore:
    def __init__(self, path: str) -> None:
        self.db = Database(path, SCHEMA)

    # -- runs -----------------------------------------------------------------------------------

    def new_run_id(self) -> tuple[int, str]:
        row = self.db.one("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM eval_runs")
        seq = int(row["n"]) if row else 1
        return seq, f"run_{seq:04d}"

    def create_run(self, seq: int, run: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO eval_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                seq,
                run["run_id"],
                run["tenant_id"],
                run["pack"],
                run["suite"],
                run.get("model"),
                run["status"],
                run["started_at"],
                run.get("ended_at"),
                run.get("previous_run_id"),
                json.dumps(run, ensure_ascii=False),
            ),
        )

    def update_run(self, run: dict[str, Any]) -> None:
        self.db.execute(
            "UPDATE eval_runs SET status = ?, ended_at = ?, body = ? WHERE run_id = ?",
            (
                run["status"],
                run.get("ended_at"),
                json.dumps(run, ensure_ascii=False),
                run["run_id"],
            ),
        )

    def run(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        row = self.db.one(
            "SELECT body FROM eval_runs WHERE tenant_id = ? AND run_id = ?", (tenant_id, run_id)
        )
        return json.loads(row["body"]) if row else None

    def runs(self, tenant_id: str, pack: str | None = None, limit: int = 20) -> list[dict]:
        sql, params = "SELECT body FROM eval_runs WHERE tenant_id = ?", [tenant_id]
        if pack:
            sql += " AND pack = ?"
            params.append(pack)
        rows = self.db.query(sql + " ORDER BY seq DESC LIMIT ?", (*params, limit))
        return [json.loads(r["body"]) for r in rows]

    def previous_run(self, tenant_id: str, pack: str, suite: str) -> dict[str, Any] | None:
        row = self.db.one(
            "SELECT body FROM eval_runs WHERE tenant_id = ? AND pack = ? AND suite = ?"
            " AND status = 'done' ORDER BY seq DESC LIMIT 1",
            (tenant_id, pack, suite),
        )
        return json.loads(row["body"]) if row else None

    def unfinished_runs(self) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT body FROM eval_runs WHERE status = 'running'")
        return [json.loads(r["body"]) for r in rows]

    # -- cases ----------------------------------------------------------------------------------

    def put_case(self, run_id: str, position: int, case: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO eval_cases VALUES (?, ?, ?, ?, ?)",
            (
                run_id,
                position,
                case["case_id"],
                case["status"],
                json.dumps(case, ensure_ascii=False),
            ),
        )

    def cases(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT body FROM eval_cases WHERE run_id = ? ORDER BY position", (run_id,)
        )
        return [json.loads(r["body"]) for r in rows]

    def case_of_turn(self, run_id: str, turn_id: str) -> str | None:
        """The case id whose turn this is, in that run."""
        row = self.db.one(
            "SELECT case_id FROM eval_cases WHERE run_id = ?"
            " AND json_extract(body, '$.turn_id') = ?",
            (run_id, turn_id),
        )
        return row["case_id"] if row else None

    def run_ids(self, tenant_id: str, limit: int = 1000) -> list[str]:
        rows = self.db.query(
            "SELECT run_id FROM eval_runs WHERE tenant_id = ? ORDER BY seq DESC LIMIT ?",
            (tenant_id, limit),
        )
        return [r["run_id"] for r in rows]

    def case_statuses(self, run_id: str) -> dict[str, str]:
        rows = self.db.query("SELECT case_id, status FROM eval_cases WHERE run_id = ?", (run_id,))
        return {r["case_id"]: r["status"] for r in rows}

    # -- online scores ---------------------------------------------------------------------------

    def put_scores(
        self,
        tenant_id: str,
        turn_id: str,
        scores: dict[str, dict[str, Any]],
        nodes: dict[str, str],
        judge_model: str | None,
    ) -> None:
        now = iso(time.time())
        for name, s in scores.items():
            self.db.execute(
                "INSERT OR REPLACE INTO turn_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant_id,
                    turn_id,
                    name,
                    nodes.get(name, "eval"),
                    float(s["value"]),
                    int(bool(s["pass"])),
                    s.get("detail"),
                    judge_model,
                    now,
                ),
            )

    def scores(self, tenant_id: str, turn_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT name, node_id, value, pass, detail, judge_model, at FROM turn_scores"
            " WHERE tenant_id = ? AND turn_id = ? ORDER BY name",
            (tenant_id, turn_id),
        )
        return [dict(r) | {"pass": bool(r["pass"])} for r in rows]

    # -- feedback -------------------------------------------------------------------------------

    def put_feedback(
        self, tenant_id: str, turn_id: str, rating: str, note: str | None, by: str
    ) -> dict[str, Any]:
        at = iso(time.time())
        self.db.execute(
            "INSERT INTO feedback (tenant_id, turn_id, rating, note, by, at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, turn_id, rating, note, by, at),
        )
        return {"turn_id": turn_id, "rating": rating, "note": note, "at": at, "by": by}

    def feedback(self, tenant_id: str, turn_id: str) -> dict[str, Any] | None:
        row = self.db.one(
            "SELECT rating, note, at FROM feedback WHERE tenant_id = ? AND turn_id = ?"
            " ORDER BY seq DESC LIMIT 1",
            (tenant_id, turn_id),
        )
        return dict(row) if row else None

    def feedback_history(self, tenant_id: str, turn_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT rating, note, at, by FROM feedback WHERE tenant_id = ? AND turn_id = ?"
            " ORDER BY seq DESC",
            (tenant_id, turn_id),
        )
        return [dict(r) for r in rows]
