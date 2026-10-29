"""A small SQLite helper shared by the trace, session and audit stores.

Plain `sqlite3` from the standard library. Calls are short and synchronous; the harness runs
them on the event loop thread, which is fine at teaching scale and keeps the code readable.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class Database:
    """One SQLite file. `readonly` opens an existing file without creating or changing it
    (sqlite3.OperationalError when it does not exist), for commands that only read."""

    def __init__(self, path: Path | str, schema: str, *, readonly: bool = False) -> None:
        self.path = Path(path) if str(path) != ":memory:" else path
        self._lock = threading.Lock()
        if readonly:
            uri = Path(path).resolve().as_uri() + "?mode=ro"
            self._conn = sqlite3.connect(
                uri, uri=True, check_same_thread=False, isolation_level=None
            )
            self._conn.row_factory = sqlite3.Row
            return
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(schema)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """A write transaction (BEGIN IMMEDIATE): reads inside it see no other writer."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def ping(self) -> bool:
        try:
            self.one("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()
