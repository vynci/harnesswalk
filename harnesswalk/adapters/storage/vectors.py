"""Vector indexes behind the semantic, episodic and knowledge stores.

Config `storage.vectors` in config/harness.yaml picks one:

- `sqlite` (default): vectors as float32 blobs next to the store records, searched by brute
  force cosine. A starter pack has a few hundred vectors, so this is fast enough and needs no
  Docker.
- `pgvector`: a `vector` column in Postgres, searched with the cosine distance operator `<=>`.
  The connection comes from `HARNESSWALK_DATABASE_URL` or the `POSTGRES_*` variables of the
  compose stack. Needs the `pgvector` extra (psycopg).

Only vectors move to Postgres. Records, versions and the keyword index stay in SQLite, so both
backends share one code path for everything but similarity.

Every vector carries a `scope` (a tenant id, or the shared pack scope) and a `pack`. A search
names the scopes it may read, so isolation is applied by the index, not by the caller.
"""

from __future__ import annotations

import os
from array import array
from collections.abc import Mapping, Sequence
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.context.records import cosine

SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
    ns TEXT NOT NULL,
    id TEXT NOT NULL,
    embedder TEXT NOT NULL,
    scope TEXT NOT NULL,
    pack TEXT NOT NULL,
    vec BLOB NOT NULL,
    PRIMARY KEY (ns, id, embedder)
);
CREATE INDEX IF NOT EXISTS vectors_scope ON vectors (ns, embedder, scope);
"""


class VectorIndex:
    backend = "none"

    def upsert(
        self, ns: str, items: Sequence[tuple[str, str, str, list[float]]], embedder: str
    ) -> None:
        """items: (id, scope, pack, vector)."""
        raise NotImplementedError

    def search(
        self,
        ns: str,
        embedder: str,
        vector: list[float],
        scopes: Sequence[tuple[str, str | None]],
        k: int,
    ) -> list[tuple[str, float]]:
        """Top k (id, cosine) among vectors whose (scope, pack) is allowed. `pack` None means
        any pack of that scope. Ties are broken by id, so results are deterministic."""
        raise NotImplementedError

    def delete(self, ns: str, ids: Sequence[str]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class SqliteVectors(VectorIndex):
    backend = "sqlite"

    def __init__(self, db: Database) -> None:
        self.db = db  # opened with SCHEMA (see context/stores/memory_db.py)

    def upsert(self, ns, items, embedder) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?, ?, ?)",
            [
                (ns, id_, embedder, scope, pack, array("f", vec).tobytes())
                for id_, scope, pack, vec in items
            ],
        )

    def search(self, ns, embedder, vector, scopes, k) -> list[tuple[str, float]]:
        where, params = _scope_sql(scopes, "?")
        rows = self.db.query(
            f"SELECT id, vec FROM vectors WHERE ns = ? AND embedder = ? AND ({where})",
            (ns, embedder, *params),
        )
        scored = []
        for row in rows:
            vec = array("f")
            vec.frombytes(row["vec"])
            scored.append((row["id"], cosine(vector, vec)))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:k]

    def delete(self, ns, ids) -> None:
        self.db.executemany("DELETE FROM vectors WHERE ns = ? AND id = ?", [(ns, i) for i in ids])


def _scope_sql(scopes: Sequence[tuple[str, str | None]], mark: str) -> tuple[str, list[str]]:
    if not scopes:
        raise ValueError("a vector search needs at least one scope")
    parts, params = [], []
    for scope, pack in scopes:
        if pack is None:
            parts.append(f"scope = {mark}")
            params.append(scope)
        else:
            parts.append(f"(scope = {mark} AND pack = {mark})")
            params += [scope, pack]
    return " OR ".join(parts), params


def postgres_dsn(env: Mapping[str, str]) -> str:
    """The DSN from HARNESSWALK_DATABASE_URL, else from the compose POSTGRES_* variables."""
    if env.get("HARNESSWALK_DATABASE_URL"):
        return env["HARNESSWALK_DATABASE_URL"]
    host = env.get("POSTGRES_HOST", "127.0.0.1")
    port = env.get("POSTGRES_PORT", "5432")
    user = env.get("POSTGRES_USER", "harnesswalk")
    db = env.get("POSTGRES_DB", "harnesswalk")
    password = env.get("POSTGRES_PASSWORD", "harnesswalk-local")
    return f"host={host} port={port} dbname={db} user={user} password={password}"


class PgVectors(VectorIndex):
    """pgvector: one table, an untyped `vector` column (dimensions differ per embedder)."""

    backend = "pgvector"

    def __init__(self, dsn: str, table: str = "harnesswalk_vectors") -> None:
        import psycopg  # the `pgvector` extra; imported here so SQLite users never need it

        self.table = table
        self.conn = psycopg.connect(dsn, autocommit=True)
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"""CREATE TABLE IF NOT EXISTS {table} (
                    ns TEXT NOT NULL, id TEXT NOT NULL, embedder TEXT NOT NULL,
                    scope TEXT NOT NULL, pack TEXT NOT NULL, embedding vector NOT NULL,
                    PRIMARY KEY (ns, id, embedder))"""
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {table}_scope ON {table} (ns, embedder, scope)"
            )

    @staticmethod
    def _literal(vec: Sequence[float]) -> str:
        return "[" + ",".join(f"{x:.7g}" for x in vec) + "]"

    def upsert(self, ns, items, embedder) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                f"""INSERT INTO {self.table} VALUES (%s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (ns, id, embedder) DO UPDATE
                SET scope = EXCLUDED.scope, pack = EXCLUDED.pack, embedding = EXCLUDED.embedding""",
                [
                    (ns, id_, embedder, scope, pack, self._literal(vec))
                    for id_, scope, pack, vec in items
                ],
            )

    def search(self, ns, embedder, vector, scopes, k) -> list[tuple[str, float]]:
        where, params = _scope_sql(scopes, "%s")
        literal = self._literal(vector)
        with self.conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, 1 - (embedding <=> %s::vector) AS score FROM {self.table}
                WHERE ns = %s AND embedder = %s AND ({where})
                ORDER BY embedding <=> %s::vector, id LIMIT %s""",
                (literal, ns, embedder, *params, literal, k),
            )
            rows = cur.fetchall()
        out = [(row[0], float(row[1])) for row in rows]
        out.sort(key=lambda item: (-round(item[1], 9), item[0]))
        return out

    def delete(self, ns, ids) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                f"DELETE FROM {self.table} WHERE ns = %s AND id = %s", [(ns, i) for i in ids]
            )

    def close(self) -> None:
        self.conn.close()


def build_vectors(settings: Any, db: Database, env: Mapping[str, str] | None = None) -> VectorIndex:
    backend = settings.get("storage.vectors", "sqlite")
    if backend == "sqlite":
        return SqliteVectors(db)
    if backend == "pgvector":
        return PgVectors(postgres_dsn(os.environ if env is None else env))
    from harnesswalk.types import ConfigError

    raise ConfigError(f"storage.vectors must be sqlite or pgvector, got {backend!r}")
