"""Semantic memory: durable facts, written only by the memory writer.

Contract: spec/contracts/store.md. Node: `semantic` (expert child of `memory`).

A fact is `{id, tenant, user, key, value, source, created, updated, version}`: a key such as
`preferred_name` or `order A-1001 delivery note`, and its value. Facts are scoped to a tenant
(and to a user inside it when the proposal named one). Seed facts of the dataset pack live in
the shared pack scope and are read by every tenant on that pack; a tenant's own fact with the
same key shadows the seed.

Ranking (store.md, high precision over recall):

1. Entity or key match first, score 1.0: an id-like entity of the query (A-1001) appears in
   the fact, or every word of the key appears in the query.
2. Otherwise cosine similarity between the query and `key: value` embeddings.
3. Drop anything under `stores.semantic.min_score`; ties broken by id; trimmed to the budget.

Writes go through `write()`, which checks the writer token: only `memory_writer` may write.
Every update keeps the previous version in `semantic_versions`, so a fact is diffable.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.adapters.storage.vectors import VectorIndex
from harnesswalk.context.embeddings import Embedder
from harnesswalk.context.records import (
    PACK_SCOPE,
    Hit,
    Query,
    SourceRef,
    entities,
    require_tenant,
    tokens,
    trim_to_budget,
)
from harnesswalk.context.tokens import estimate
from harnesswalk.events import iso
from harnesswalk.nodes import node, register
from harnesswalk.types import HarnessError, TurnContext

register("semantic", __name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_facts (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    pack TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    entities TEXT NOT NULL,
    source TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    version INTEGER NOT NULL,
    proposal_id TEXT,
    UNIQUE (tenant_id, user_id, pack, key)
);
CREATE TABLE IF NOT EXISTS semantic_versions (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT NOT NULL,
    written_at TEXT NOT NULL,
    proposal_id TEXT,
    PRIMARY KEY (id, version)
);
"""

WRITERS = {"memory_writer"}
NS = "semantic"


def normalize_key(key: str) -> str:
    return " ".join(key.strip().split())[:120]


@dataclass
class Fact:
    id: str
    tenant_id: str
    user_id: str | None
    key: str
    value: str
    source: str
    created: str
    updated: str
    version: int
    entities: list[str]

    @property
    def text(self) -> str:
        return f"{self.key}: {self.value}"

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["scope"] = "pack" if self.tenant_id == PACK_SCOPE else "tenant"
        return out


@dataclass
class WriteResult:
    record_id: str
    version: int
    op: str  # create | update | unchanged
    before: str | None
    after: str


class SemanticStore:
    kind = "semantic"

    def __init__(
        self,
        db: Database,
        vectors: VectorIndex,
        embedder: Embedder,
        pack: str,
        cfg: dict[str, Any] | None = None,
        estimator: str = "chars_div_4",
    ) -> None:
        self.db = db
        self.vectors = vectors
        self.embedder = embedder
        self.pack = pack
        self.cfg = cfg or {}
        self.min_score = float(self.cfg.get("min_score", 0.35))
        self.estimator = estimator

    # -- reads ---------------------------------------------------------------------------------

    def _row(self, row: Any) -> Fact:
        return Fact(
            row["id"],
            row["tenant_id"],
            row["user_id"] or None,
            row["key"],
            row["value"],
            row["source"],
            row["created"],
            row["updated"],
            row["version"],
            json.loads(row["entities"]),
        )

    def _visible(self, ctx: TurnContext) -> list[Fact]:
        """The tenant's facts (its user's and tenant-wide ones), then pack seeds it does not
        shadow."""
        tenant = require_tenant(ctx)
        rows = self.db.query(
            "SELECT * FROM semantic_facts WHERE pack = ? AND"
            " ((tenant_id = ? AND (user_id = '' OR user_id = ?)) OR tenant_id = ?)"
            " ORDER BY id",
            (self.pack, tenant, ctx.user_id or "", PACK_SCOPE),
        )
        facts = [self._row(r) for r in rows]
        own = {f.key for f in facts if f.tenant_id == tenant}
        return [f for f in facts if f.tenant_id == tenant or f.key not in own]

    def get(self, ctx: TurnContext, record_id: str) -> Fact | None:
        return next((f for f in self._visible(ctx) if f.id == record_id), None)

    def list(self, ctx: TurnContext, limit: int = 50) -> list[Fact]:
        """The tenant's own facts, newest first, then the pack seeds it sees."""
        facts = self._visible(ctx)
        own = sorted(
            (f for f in facts if f.tenant_id != PACK_SCOPE), key=lambda f: (f.updated, f.id)
        )
        seeds = [f for f in facts if f.tenant_id == PACK_SCOPE]
        return (own[::-1] + seeds)[:limit]

    def find(self, tenant_id: str, user_id: str | None, key: str) -> Fact | None:
        row = self.db.one(
            "SELECT * FROM semantic_facts WHERE tenant_id = ? AND user_id = ? AND pack = ?"
            " AND key = ?",
            (tenant_id, user_id or "", self.pack, normalize_key(key)),
        )
        return self._row(row) if row else None

    def history(self, tenant_id: str, record_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM semantic_versions WHERE tenant_id = ? AND id = ? ORDER BY version",
            (tenant_id, record_id),
        )
        return [dict(r) for r in rows]

    def count(self, tenant_id: str) -> dict[str, int]:
        own = self.db.one(
            "SELECT COUNT(*) AS n FROM semantic_facts WHERE tenant_id = ? AND pack = ?",
            (tenant_id, self.pack),
        )
        seeds = self.db.one(
            "SELECT COUNT(*) AS n FROM semantic_facts WHERE tenant_id = ? AND pack = ?",
            (PACK_SCOPE, self.pack),
        )
        return {"facts": own["n"], "pack_facts": seeds["n"]}

    def retrieve(self, ctx: TurnContext, query: Query) -> list[Hit]:
        facts = self._visible(ctx)
        if not facts:
            return []
        q_tokens = set(tokens(query.text))
        q_entities = query.all_entities()
        scopes = [(require_tenant(ctx), self.pack), (PACK_SCOPE, self.pack)]
        qvec = self.embedder.embed([query.text])[0]
        similar = dict(
            self.vectors.search(NS, self.embedder.id, qvec, scopes, k=max(len(facts), 1))
        )
        hits = []
        for f in facts:
            key_words = set(tokens(f.key))
            matched = bool(q_entities & {e.lower() for e in f.entities}) or bool(
                key_words and key_words <= q_tokens
            )
            score = 1.0 if matched else similar.get(f.id, 0.0)
            if score < self.min_score:
                continue
            hits.append(
                Hit(
                    f.id,
                    "semantic",
                    f.text,
                    score,
                    estimate(f.text, self.estimator) + 2,
                    SourceRef("semantic", f.id, f.key, f"semantic://{f.id}", str(f.version)),
                    {"match": "entity" if matched else "similarity", "scope": f.to_dict()["scope"]},
                )
            )
        hits.sort(key=lambda h: (-h.score, h.id))
        kept, _ = trim_to_budget(hits, query.top_k, query.max_tokens)
        return kept

    # -- writes --------------------------------------------------------------------------------

    def write(
        self,
        tenant_id: str,
        user_id: str | None,
        key: str,
        value: str,
        *,
        writer: str,
        source: str,
        proposal_id: str | None = None,
        extra_entities: list[str] | None = None,
    ) -> WriteResult:
        """Create or update one fact. Only the memory writer holds a writer token."""
        if writer not in WRITERS:
            raise PermissionError(f"{writer} may not write semantic memory")
        if not tenant_id:
            raise ValueError("a semantic write needs a tenant")
        key = normalize_key(key)
        now = iso(time.time())
        ents = sorted(entities(f"{key} {value}") | {e.lower() for e in extra_entities or []})
        existing = self.find(tenant_id, user_id, key)
        if existing and existing.value == value:
            return WriteResult(existing.id, existing.version, "unchanged", value, value)
        if existing:
            fact_id, version, created, before = (
                existing.id,
                existing.version + 1,
                existing.created,
                existing.value,
            )
        else:
            fact_id, version, created, before = "fact_" + uuid.uuid4().hex[:16], 1, now, None
        self.db.execute(
            "INSERT OR REPLACE INTO semantic_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fact_id,
                tenant_id,
                user_id or "",
                self.pack,
                key,
                value,
                json.dumps(ents),
                source,
                created,
                now,
                version,
                proposal_id,
            ),
        )
        self.db.execute(
            "INSERT INTO semantic_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (fact_id, version, tenant_id, key, value, source, now, proposal_id),
        )
        self.vectors.upsert(
            NS,
            [(fact_id, tenant_id, self.pack, self.embedder.embed([f"{key}: {value}"])[0])],
            self.embedder.id,
        )
        return WriteResult(fact_id, version, "update" if existing else "create", before, value)

    def reindex(self) -> int:
        """Embed every fact again with the current embedder (after the embedder changed)."""
        rows = self.db.query("SELECT id, tenant_id, pack, key, value FROM semantic_facts")
        if not rows:
            return 0
        vecs = self.embedder.embed([f"{r['key']}: {r['value']}" for r in rows])
        self.vectors.upsert(
            NS,
            [(r["id"], r["tenant_id"], r["pack"], v) for r, v in zip(rows, vecs, strict=True)],
            self.embedder.id,
        )
        return len(rows)


async def run_semantic(
    ctx: TurnContext, store: SemanticStore | None, query: Query | None
) -> list[Hit]:
    async with node(ctx, "semantic") as span:
        if store is None or query is None:
            span.skip(hits=0, tokens=0, enabled=False)
            return []
        try:
            hits = await asyncio.to_thread(store.retrieve, ctx, query)
        except Exception as exc:  # store.md: a failing store reports store_unavailable
            raise HarnessError("store_unavailable", f"semantic: {type(exc).__name__}") from exc
        if not hits:
            span.skip(hits=0, tokens=0)
            return []
        span.set(
            hits=len(hits),
            tokens=sum(h.tokens for h in hits),
            entity_hits=sum(1 for h in hits if h.meta.get("match") == "entity"),
            top_score=round(hits[0].score, 4),
        )
        span.detail = {"hits": [h.to_dict() for h in hits]}
    return hits
