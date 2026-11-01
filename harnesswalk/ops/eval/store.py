"""Eval storage (`data/evals.db`): runs, case results, online scores and feedback.

Contract: spec/contracts/evaluator.md (P4 additions). Every read takes the tenant. A run row
holds its summary; a case row holds one case's scores, expected and actual. Online scores and
feedback are per turn. P5: diagnoses, per case and per turn; a run summary and a case read back
with their latest diagnosis.
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
CREATE TABLE IF NOT EXISTS diagnoses (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    diagnosis_id TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    target TEXT NOT NULL,
    run_id TEXT,
    case_id TEXT,
    turn_id TEXT,
    layer TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS diagnoses_by_turn ON diagnoses (tenant_id, turn_id, seq);
CREATE INDEX IF NOT EXISTS diagnoses_by_case ON diagnoses (run_id, case_id, seq);
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
        return self._with_diagnoses(json.loads(row["body"])) if row else None

    def runs(self, tenant_id: str, pack: str | None = None, limit: int = 20) -> list[dict]:
        sql, params = "SELECT body FROM eval_runs WHERE tenant_id = ?", [tenant_id]
        if pack:
            sql += " AND pack = ?"
            params.append(pack)
        rows = self.db.query(sql + " ORDER BY seq DESC LIMIT ?", (*params, limit))
        return [self._with_diagnoses(json.loads(r["body"])) for r in rows]

    def _with_diagnoses(self, run: dict[str, Any]) -> dict[str, Any]:
        """P5: `diagnoses: {case_id: layer}`, the latest diagnosis of each case of the run."""
        rows = self.db.query(
            "SELECT case_id, layer FROM diagnoses WHERE run_id = ? AND case_id IS NOT NULL"
            " ORDER BY seq",
            (run["run_id"],),
        )
        run["diagnoses"] = {r["case_id"]: r["layer"] for r in rows}
        run.setdefault("purpose", None)
        run.setdefault("case_tenant", f"evalrun-{run['run_id']}")
        return run

    def runs_before(
        self, tenant_id: str, pack: str, suite: str, run_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Finished runs of the same tenant, pack and suite older than `run_id`, newest first."""
        row = self.db.one("SELECT seq FROM eval_runs WHERE run_id = ?", (run_id,))
        before = int(row["seq"]) if row else 1 << 62
        rows = self.db.query(
            "SELECT body FROM eval_runs WHERE tenant_id = ? AND pack = ? AND suite = ?"
            " AND status = 'done' AND seq < ? ORDER BY seq DESC LIMIT ?",
            (tenant_id, pack, suite, before, limit),
        )
        return [json.loads(r["body"]) for r in rows]

    def run_of(self, run_id: str) -> dict[str, Any] | None:
        """A run by id, whoever's (for code that already checked the tenant)."""
        row = self.db.one("SELECT body FROM eval_runs WHERE run_id = ?", (run_id,))
        return json.loads(row["body"]) if row else None

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
        out = [json.loads(r["body"]) for r in rows]
        for case in out:
            case["diagnosis"] = self.diagnosis_for_case(run_id, case["case_id"])
        return out

    def case(self, run_id: str, case_id: str) -> dict[str, Any] | None:
        row = self.db.one(
            "SELECT body FROM eval_cases WHERE run_id = ? AND case_id = ?", (run_id, case_id)
        )
        return json.loads(row["body"]) if row else None

    def latest_failed_case(self, tenant_id: str, case_id: str) -> str | None:
        """The newest run of this tenant where the case failed or errored."""
        row = self.db.one(
            "SELECT r.run_id FROM eval_runs r JOIN eval_cases c ON c.run_id = r.run_id"
            " WHERE r.tenant_id = ? AND c.case_id = ? AND c.status IN ('failed', 'error')"
            " ORDER BY r.seq DESC LIMIT 1",
            (tenant_id, case_id),
        )
        return row["run_id"] if row else None

    def case_of_turn(self, run_id: str, turn_id: str) -> str | None:
        """The case id whose turn this is, in that run."""
        row = self.db.one(
            "SELECT case_id FROM eval_cases WHERE run_id = ?"
            " AND json_extract(body, '$.turn_id') = ?",
            (run_id, turn_id),
        )
        return row["case_id"] if row else None

    def case_by_turn(self, tenant_id: str, turn_id: str) -> tuple[str, str] | None:
        """(run_id, case_id) of the case turn `turn_id` in one of this tenant's runs."""
        row = self.db.one(
            "SELECT c.run_id, c.case_id FROM eval_cases c JOIN eval_runs r ON r.run_id = c.run_id"
            " WHERE r.tenant_id = ? AND json_extract(c.body, '$.turn_id') = ?",
            (tenant_id, turn_id),
        )
        return (row["run_id"], row["case_id"]) if row else None

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

    # -- diagnoses (P5) ---------------------------------------------------------------------------

    def put_diagnosis(self, d: dict[str, Any]) -> dict[str, Any]:
        with self.db.transaction() as conn:
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM diagnoses").fetchone()
            d["diagnosis_id"] = f"dg_{int(row[0]):04d}"
            conn.execute(
                "INSERT INTO diagnoses (diagnosis_id, tenant_id, target, run_id, case_id, turn_id,"
                " layer, body, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    d["diagnosis_id"],
                    d["tenant_id"],
                    d["target"],
                    d.get("run_id"),
                    d.get("case_id"),
                    d.get("turn_id"),
                    d["layer"],
                    json.dumps(d, ensure_ascii=False),
                    d["created_at"],
                ),
            )
        return d

    def diagnosis_for_case(self, run_id: str, case_id: str) -> dict[str, Any] | None:
        row = self.db.one(
            "SELECT body FROM diagnoses WHERE run_id = ? AND case_id = ? ORDER BY seq DESC LIMIT 1",
            (run_id, case_id),
        )
        return json.loads(row["body"]) if row else None

    def diagnosis_for_turn(self, tenant_id: str, turn_id: str) -> dict[str, Any] | None:
        row = self.db.one(
            "SELECT body FROM diagnoses WHERE tenant_id = ? AND turn_id = ? AND target = 'turn'"
            " ORDER BY seq DESC LIMIT 1",
            (tenant_id, turn_id),
        )
        return json.loads(row["body"]) if row else None

    def diagnoses(self, tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT body FROM diagnoses WHERE tenant_id = ? ORDER BY seq DESC LIMIT ?",
            (tenant_id, limit),
        )
        return [json.loads(r["body"]) for r in rows]
