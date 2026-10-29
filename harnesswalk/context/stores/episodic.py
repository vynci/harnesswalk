"""Episodic memory: dated events, written only by the memory writer.

Contract: spec/contracts/store.md. Node: `episodic` (expert child of `memory`).

An event is `{id, tenant, session, ts, kind, text, outcome, turn_id}`. The memory writer
appends one short `turn` event per finished chat turn (when the gate allows it) and any event
the model asks it to remember. Seed events of the dataset pack live in the shared pack scope.

Ranking (store.md): relevance blended with recency,

    score = w_rel * relevance + w_rec * exp(-age_days / half_life_days)

where relevance is 1.0 for an entity match (A-1001 in the query and in the event), else the
cosine of the query and event embeddings. An event whose relevance is under
`stores.episodic.min_relevance` is left out however recent it is, so the slice does not fill
with yesterday's small talk. Ties broken by id, then trimmed to the budget.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
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
    trim_to_budget,
)
from harnesswalk.context.tokens import estimate
from harnesswalk.events import iso
from harnesswalk.nodes import node, register
from harnesswalk.types import HarnessError, TurnContext

register("episodic", __name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    pack TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    outcome TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    entities TEXT NOT NULL,
    source TEXT NOT NULL,
    version INTEGER NOT NULL,
    proposal_id TEXT
);
CREATE INDEX IF NOT EXISTS episodes_tenant ON episodes (tenant_id, pack, ts);
"""

WRITERS = {"memory_writer"}
NS = "episodic"


def parse_ts(value: str) -> float:
    """An RFC 3339 timestamp, or a plain date (seed files), as epoch seconds (UTC)."""
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


@dataclass
class Episode:
    id: str
    tenant_id: str
    session_id: str
    ts: str
    kind: str
    text: str
    outcome: str
    turn_id: str
    entities: list[str]
    source: str
    version: int

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["scope"] = "pack" if self.tenant_id == PACK_SCOPE else "tenant"
        return out


class EpisodicStore:
    kind = "episodic"

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
        cfg = cfg or {}
        weights = cfg.get("weights") or {}
        self.w_rel = float(weights.get("relevance", 0.7))
        self.w_rec = float(weights.get("recency", 0.3))
        self.half_life_days = float(cfg.get("half_life_days", 30))
        self.min_relevance = float(cfg.get("min_relevance", 0.2))
        self.estimator = estimator

    def _row(self, row: Any) -> Episode:
        return Episode(
            row["id"],
            row["tenant_id"],
            row["session_id"],
            row["ts"],
            row["kind"],
            row["text"],
            row["outcome"],
            row["turn_id"],
            json.loads(row["entities"]),
            row["source"],
            row["version"],
        )

    def _visible(self, ctx: TurnContext) -> list[Episode]:
        tenant = require_tenant(ctx)
        rows = self.db.query(
            "SELECT * FROM episodes WHERE pack = ? AND tenant_id IN (?, ?) ORDER BY ts DESC, id",
            (self.pack, tenant, PACK_SCOPE),
        )
        return [self._row(r) for r in rows]

    def get(self, ctx: TurnContext, record_id: str) -> Episode | None:
        return next((e for e in self._visible(ctx) if e.id == record_id), None)

    def list(self, ctx: TurnContext, limit: int = 20) -> list[Episode]:
        return self._visible(ctx)[:limit]

    def count(self, tenant_id: str) -> dict[str, int]:
        own = self.db.one(
            "SELECT COUNT(*) AS n FROM episodes WHERE tenant_id = ? AND pack = ?",
            (tenant_id, self.pack),
        )
        seeds = self.db.one(
            "SELECT COUNT(*) AS n FROM episodes WHERE tenant_id = ? AND pack = ?",
            (PACK_SCOPE, self.pack),
        )
        return {"episodes": own["n"], "pack_episodes": seeds["n"]}

    def exists_for_turn(self, tenant_id: str, turn_id: str, kind: str) -> bool:
        row = self.db.one(
            "SELECT 1 FROM episodes WHERE tenant_id = ? AND turn_id = ? AND kind = ?",
            (tenant_id, turn_id, kind),
        )
        return row is not None

    def score(self, relevance: float, ts: str, now: float) -> float:
        age_days = max(0.0, (now - parse_ts(ts)) / 86400)
        recency = math.exp(-age_days / self.half_life_days) if self.half_life_days > 0 else 0.0
        return self.w_rel * relevance + self.w_rec * recency

    def retrieve(self, ctx: TurnContext, query: Query) -> list[Hit]:
        events = self._visible(ctx)
        if not events:
            return []
        q_entities = query.all_entities()
        scopes = [(require_tenant(ctx), self.pack), (PACK_SCOPE, self.pack)]
        qvec = self.embedder.embed([query.text])[0]
        similar = dict(self.vectors.search(NS, self.embedder.id, qvec, scopes, k=len(events)))
        hits = []
        for e in events:
            matched = bool(q_entities & set(e.entities))
            relevance = 1.0 if matched else max(0.0, similar.get(e.id, 0.0))
            if relevance < self.min_relevance:
                continue
            text = f"{e.ts[:10]} {e.kind}: {e.text}" + (
                f" (outcome: {e.outcome})" if e.outcome else ""
            )
            hits.append(
                Hit(
                    e.id,
                    "episodic",
                    text,
                    self.score(relevance, e.ts, query.now),
                    estimate(text, self.estimator) + 2,
                    SourceRef("episodic", e.id, e.kind, f"episodic://{e.id}", str(e.version)),
                    {"relevance": round(relevance, 4), "ts": e.ts, "scope": e.to_dict()["scope"]},
                )
            )
        hits.sort(key=lambda h: (-h.score, h.id))
        kept, _ = trim_to_budget(hits, query.top_k, query.max_tokens)
        return kept

    def append(
        self,
        tenant_id: str,
        *,
        writer: str,
        session_id: str,
        turn_id: str,
        kind: str,
        text: str,
        outcome: str = "",
        ts: str | None = None,
        source: str,
        proposal_id: str | None = None,
        extra_entities: list[str] | None = None,
    ) -> str:
        """Append one event. Events are never edited, so every event is version 1."""
        if writer not in WRITERS:
            raise PermissionError(f"{writer} may not write episodic memory")
        if not tenant_id:
            raise ValueError("an episodic write needs a tenant")
        event_id = "ep_" + uuid.uuid4().hex[:16]
        ts = ts or iso(time.time())
        ents = sorted(entities(text) | {e.lower() for e in extra_entities or []})
        self.db.execute(
            "INSERT INTO episodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                tenant_id,
                self.pack,
                session_id,
                ts,
                kind,
                text,
                outcome,
                turn_id,
                json.dumps(ents),
                source,
                1,
                proposal_id,
            ),
        )
        self.vectors.upsert(
            NS, [(event_id, tenant_id, self.pack, self.embedder.embed([text])[0])], self.embedder.id
        )
        return event_id

    def reindex(self) -> int:
        rows = self.db.query("SELECT id, tenant_id, pack, text FROM episodes")
        if not rows:
            return 0
        vecs = self.embedder.embed([r["text"] for r in rows])
        self.vectors.upsert(
            NS,
            [(r["id"], r["tenant_id"], r["pack"], v) for r, v in zip(rows, vecs, strict=True)],
            self.embedder.id,
        )
        return len(rows)


async def run_episodic(
    ctx: TurnContext, store: EpisodicStore | None, query: Query | None
) -> list[Hit]:
    async with node(ctx, "episodic") as span:
        if store is None or query is None:
            span.skip(hits=0, tokens=0, enabled=False)
            return []
        try:
            hits = await asyncio.to_thread(store.retrieve, ctx, query)
        except Exception as exc:  # store.md: a failing store reports store_unavailable
            raise HarnessError("store_unavailable", f"episodic: {type(exc).__name__}") from exc
        if not hits:
            span.skip(hits=0, tokens=0)
            return []
        span.set(
            hits=len(hits), tokens=sum(h.tokens for h in hits), top_score=round(hits[0].score, 4)
        )
        span.detail = {"hits": [h.to_dict() for h in hits]}
    return hits
