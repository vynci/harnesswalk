"""Storage: every store keeps JSON rows in named tables behind four calls.

`insert`, `rows`, `update` and `count` are all the stores use, so the backend is one file.
@@storage_doc@@

Lesson: tenancy is structural. Every store row carries `tenant`, and every store method takes
it as a required argument (harness.md section 5), so a query without a tenant is a bug you can
see in the call, not an empty result.
"""

from __future__ import annotations

import json
#% if storage == "sqlite"
import sqlite3
#% endif
import threading
from pathlib import Path
from typing import Any

APPEND_ONLY = {"audit"}  # audit.md: no update or delete, enforced by the storage


class DB:
#% if storage == "sqlite"
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(data_dir / "harness.db", check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.tables: set[str] = set()

    def _table(self, name: str) -> str:
        if name not in self.tables:
            self.conn.execute(f"CREATE TABLE IF NOT EXISTS t_{name}"
                              " (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)")  # fmt: skip
            if name in APPEND_ONLY:
                for op in ("UPDATE", "DELETE"):
                    self.conn.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {name}_no_{op.lower()} BEFORE {op} ON"
                        f" t_{name} BEGIN SELECT RAISE(ABORT, '{name} is append-only'); END"
                    )
            self.tables.add(name)
        return f"t_{name}"

    def insert(self, table: str, row: dict[str, Any]) -> int:
        with self.lock, self.conn:
            cur = self.conn.execute(f"INSERT INTO {self._table(table)} (data) VALUES (?)",
                                    (json.dumps(row, sort_keys=True),))  # fmt: skip
            return int(cur.lastrowid or 0)

    def rows(self, table: str, **where: Any) -> list[dict[str, Any]]:
        sql = f"SELECT data FROM {self._table(table)}"
        if where:
            sql += " WHERE " + " AND ".join(f"json_extract(data, '$.{k}') = ?" for k in where)
        with self.lock:
            found = self.conn.execute(sql + " ORDER BY id", [_bind(v) for v in where.values()])
            return [json.loads(r[0]) for r in found.fetchall()]

    def update(self, table: str, where: dict[str, Any], values: dict[str, Any]) -> int:
        name = self._table(table)
        cond = " AND ".join(f"json_extract(data, '$.{k}') = ?" for k in where)
        with self.lock, self.conn:
            found = self.conn.execute(f"SELECT id, data FROM {name} WHERE {cond}",
                                      [_bind(v) for v in where.values()]).fetchall()  # fmt: skip
            for rid, data in found:
                row = {**json.loads(data), **values}
                self.conn.execute(f"UPDATE {name} SET data = ? WHERE id = ?",
                                  (json.dumps(row, sort_keys=True), rid))  # fmt: skip
            return len(found)
#% else
    """JSON Lines files, one per table, read into memory once: small and greppable."""

    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        self.dir, self.lock, self.cache = data_dir, threading.RLock(), {}

    def _load(self, table: str) -> list[dict[str, Any]]:
        if table not in self.cache:
            path = self.dir / f"{table}.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
            self.cache[table] = [json.loads(line) for line in lines if line]
        return self.cache[table]

    def insert(self, table: str, row: dict[str, Any]) -> int:
        with self.lock:
            rows = self._load(table)
            rows.append(json.loads(json.dumps(row)))
            with open(self.dir / f"{table}.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
            return len(rows)

    def rows(self, table: str, **where: Any) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self._load(table)
                    if all(r.get(k) == v for k, v in where.items())]  # fmt: skip

    def update(self, table: str, where: dict[str, Any], values: dict[str, Any]) -> int:
        if table in APPEND_ONLY:
            raise PermissionError(f"{table} is append-only")
        with self.lock:
            rows = self._load(table)
            hit = [r for r in rows if all(r.get(k) == v for k, v in where.items())]
            for r in hit:
                r.update(json.loads(json.dumps(values)))
            text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
            (self.dir / f"{table}.jsonl").write_text(text, encoding="utf-8")
            return len(hit)
#% endif

    def one(self, table: str, **where: Any) -> dict[str, Any] | None:
        found = self.rows(table, **where)
        return found[-1] if found else None


#% if storage == "sqlite"
def _bind(value: Any) -> Any:
    return int(value) if isinstance(value, bool) else value
#% endif
