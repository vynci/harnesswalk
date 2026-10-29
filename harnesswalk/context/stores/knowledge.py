"""Knowledge memory: documents for retrieval (RAG), with citations.

Contract: spec/contracts/store.md. Node: `knowledge` (expert child of `memory`).

Ingestion (`harnesswalk ingest <pack>`, and on start-up when the pack changed) reads every
markdown and PDF file in `datasets/<pack>/docs/`, chunks it by heading then by size
(`chunking.py`), and indexes each chunk twice: in a SQLite FTS5 table for keywords (BM25) and
in the vector index for similarity. Pack documents live in the shared pack scope, so every
tenant on the pack reads them; a tenant's own uploads (P6) would live in its own scope.

Retrieval is hybrid:

1. keyword: FTS5 `MATCH` on the query's words, ordered by `bm25()`, best `rerank_k`, keeping
   chunks that contain at least two of those words and `min_keyword_coverage` of them;
2. vector: cosine against every chunk vector in scope, best `rerank_k` above
   `min_vector_score`;
3. reciprocal rank fusion of the two lists (`rrf_k`), ties broken by chunk id;
4. the optional rerank hook (`reranker`, `none` by default; tests register a plain function);
5. metadata filters (tenant scope always, `pack` and `doc` when given), then `top_k` and the
   token budget.

Every hit carries `{doc, chunk, score, citation}` and a `SourceRef`, so the context builder
can number it and the output guardrails can check that the answer cites it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.adapters.storage.vectors import VectorIndex
from harnesswalk.context.chunking import Document, chunk_document, load_document
from harnesswalk.context.embeddings import Embedder
from harnesswalk.context.records import (
    PACK_SCOPE,
    Hit,
    Query,
    SourceRef,
    require_tenant,
    rrf,
    tokens,
    trim_to_budget,
)
from harnesswalk.context.tokens import estimate
from harnesswalk.events import iso
from harnesswalk.execution.registry import ToolSpec
from harnesswalk.nodes import node, register
from harnesswalk.types import ConfigError, HarnessError, TurnContext

register("knowledge", __name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS kn_docs (
    scope TEXT NOT NULL,
    pack TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    title TEXT NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    version TEXT NOT NULL,
    text TEXT NOT NULL,
    chunks INTEGER NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (scope, pack, doc_id)
);
CREATE TABLE IF NOT EXISTS kn_chunks (
    vid TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    pack TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    heading TEXT NOT NULL,
    text TEXT NOT NULL,
    tokens INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS kn_fts USING fts5(
    text, vid UNINDEXED, scope UNINDEXED, pack UNINDEXED, doc_id UNINDEXED,
    tokenize = 'porter unicode61'
);
CREATE TABLE IF NOT EXISTS kn_ingests (
    scope TEXT NOT NULL,
    pack TEXT NOT NULL,
    digest TEXT NOT NULL,
    embedder TEXT NOT NULL,
    docs INTEGER NOT NULL,
    chunks INTEGER NOT NULL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (scope, pack)
);
"""

NS = "knowledge"
WRITERS = {"release"}  # ingestion of a dataset pack or an upload
Reranker = Callable[[str, list[Hit]], list[Hit]]
RERANKERS: dict[str, Reranker] = {}


def register_reranker(name: str, fn: Reranker) -> None:
    """The rerank hook: a function from (query, fused hits) to reordered hits. No model ships
    with the reference harness; a cross encoder or a model call would register here."""
    RERANKERS[name] = fn


def citation_for(title: str, heading: str) -> str:
    return f"{title}, {heading}" if heading else title


class KnowledgeStore:
    kind = "knowledge"

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
        self.chunk_tokens = int(cfg.get("chunk_tokens", 400))
        self.chunk_overlap = int(cfg.get("chunk_overlap", 40))
        self.rrf_k = int(cfg.get("rrf_k", 60))
        self.rerank_k = int(cfg.get("rerank_k", 20))
        self.min_vector_score = float(cfg.get("min_vector_score", 0.45))
        self.min_keyword_coverage = float(cfg.get("min_keyword_coverage", 0.25))
        self.reranker = cfg.get("reranker", "none")
        if self.reranker != "none" and self.reranker not in RERANKERS:
            raise ConfigError(
                f"stores.knowledge.reranker {self.reranker!r} is not registered (none ships)"
            )
        self.estimator = estimator

    # -- ingestion -----------------------------------------------------------------------------

    def _digest(self, files: list[Path]) -> str:
        h = hashlib.sha256()
        h.update(f"{self.chunk_tokens}:{self.chunk_overlap}:{self.embedder.id}".encode())
        for path in files:
            h.update(path.name.encode() + b"\0" + path.read_bytes())
        return h.hexdigest()[:16]

    def ingest(
        self,
        docs_dir: Path,
        *,
        writer: str = "release",
        scope: str = PACK_SCOPE,
        force: bool = False,
    ) -> dict[str, Any]:
        """Index every markdown and PDF file of a directory into one scope. Idempotent: the
        same files, chunk settings and embedder are a no-op unless `force`."""
        if writer not in WRITERS:
            raise PermissionError(f"{writer} may not write knowledge")
        files = (
            sorted(
                p
                for p in docs_dir.glob("*")
                if p.is_file() and p.suffix.lower() in (".md", ".markdown", ".pdf")
            )
            if docs_dir.is_dir()
            else []
        )
        digest = self._digest(files)
        row = self.db.one(
            "SELECT digest, embedder, docs, chunks FROM kn_ingests WHERE scope = ? AND pack = ?",
            (scope, self.pack),
        )
        if row and row["digest"] == digest and not force:
            return {
                "pack": self.pack,
                "docs": row["docs"],
                "chunks": row["chunks"],
                "changed": False,
            }
        self._clear(scope)
        docs = [d for d in (load_document(p) for p in files) if d is not None]
        total = 0
        for doc in docs:
            total += self._index(doc, scope)
        self.db.execute(
            "INSERT OR REPLACE INTO kn_ingests VALUES (?, ?, ?, ?, ?, ?, ?)",
            (scope, self.pack, digest, self.embedder.id, len(docs), total, iso(time.time())),
        )
        return {"pack": self.pack, "docs": len(docs), "chunks": total, "changed": True}

    def _clear(self, scope: str) -> None:
        old = [
            r["vid"]
            for r in self.db.query(
                "SELECT vid FROM kn_chunks WHERE scope = ? AND pack = ?", (scope, self.pack)
            )
        ]
        self.vectors.delete(NS, old)
        for table in ("kn_chunks", "kn_fts", "kn_docs"):
            self.db.execute(f"DELETE FROM {table} WHERE scope = ? AND pack = ?", (scope, self.pack))

    def _index(self, doc: Document, scope: str) -> int:
        chunks = chunk_document(doc, self.chunk_tokens, self.chunk_overlap)
        version = hashlib.sha256(doc.text.encode()).hexdigest()[:12]
        self.db.execute(
            "INSERT INTO kn_docs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scope,
                self.pack,
                doc.doc_id,
                doc.title,
                doc.path,
                doc.kind,
                version,
                doc.text,
                len(chunks),
                iso(time.time()),
            ),
        )
        rows, fts, texts = [], [], []
        for c in chunks:
            vid = f"{scope}/{self.pack}/{doc.doc_id}#{c.position}"
            indexed = (
                f"{doc.title}. {c.heading}. {c.text}" if c.heading else f"{doc.title}. {c.text}"
            )
            rows.append(
                (
                    vid,
                    scope,
                    self.pack,
                    doc.doc_id,
                    c.position,
                    c.heading,
                    c.text,
                    estimate(c.text, self.estimator),
                )
            )
            fts.append((indexed, vid, scope, self.pack, doc.doc_id))
            texts.append(indexed)
        self.db.executemany("INSERT INTO kn_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        self.db.executemany(
            "INSERT INTO kn_fts (text, vid, scope, pack, doc_id) VALUES (?, ?, ?, ?, ?)", fts
        )
        if texts:
            vectors = self.embedder.embed(texts)
            self.vectors.upsert(
                NS,
                [(r[0], scope, self.pack, v) for r, v in zip(rows, vectors, strict=True)],
                self.embedder.id,
            )
        return len(chunks)

    # -- reads ---------------------------------------------------------------------------------

    def _scopes(self, ctx: TurnContext) -> list[tuple[str, str]]:
        return [(require_tenant(ctx), self.pack), (PACK_SCOPE, self.pack)]

    def docs(self, ctx: TurnContext) -> list[dict[str, Any]]:
        tenant = require_tenant(ctx)
        rows = self.db.query(
            "SELECT scope, doc_id, title, path, kind, version, chunks FROM kn_docs"
            " WHERE pack = ? AND scope IN (?, ?) ORDER BY doc_id",
            (self.pack, tenant, PACK_SCOPE),
        )
        return [
            {**dict(r), "scope": "pack" if r["scope"] == PACK_SCOPE else "tenant"} for r in rows
        ]

    def doc(self, ctx: TurnContext, doc_id: str) -> dict[str, Any] | None:
        """One document and its chunks, in this tenant's view (its own scope wins)."""
        tenant = require_tenant(ctx)
        row = self.db.one(
            "SELECT * FROM kn_docs WHERE pack = ? AND doc_id = ? AND scope IN (?, ?)"
            " ORDER BY scope = ? LIMIT 1",
            (self.pack, doc_id, tenant, PACK_SCOPE, PACK_SCOPE),
        )
        if row is None:
            return None
        chunks = self.db.query(
            "SELECT position, heading, text, tokens FROM kn_chunks"
            " WHERE scope = ? AND pack = ? AND doc_id = ? ORDER BY position",
            (row["scope"], self.pack, doc_id),
        )
        return {
            "doc_id": doc_id,
            "title": row["title"],
            "path": row["path"],
            "kind": row["kind"],
            "version": row["version"],
            "scope": "pack" if row["scope"] == PACK_SCOPE else "tenant",
            "text": row["text"],
            "chunks": [
                {**dict(c), "citation": citation_for(row["title"], c["heading"])} for c in chunks
            ],
        }

    def pack_docs_text(self, doc_id: str) -> str | None:
        """The full text of a pack document (the read_doc tool, which has no tenant scope of
        its own: pack documents are the same for every tenant)."""
        row = self.db.one(
            "SELECT title, text FROM kn_docs WHERE scope = ? AND pack = ? AND doc_id = ?",
            (PACK_SCOPE, self.pack, doc_id),
        )
        return row["text"] if row else None

    def pack_index(self) -> list[tuple[str, str]]:
        rows = self.db.query(
            "SELECT doc_id, title FROM kn_docs WHERE scope = ? AND pack = ? ORDER BY doc_id",
            (PACK_SCOPE, self.pack),
        )
        return [(r["doc_id"], r["title"]) for r in rows]

    def count(self, tenant_id: str) -> dict[str, int]:
        own = self.db.one(
            "SELECT COUNT(*) AS d, COALESCE(SUM(chunks), 0) AS c FROM kn_docs"
            " WHERE scope = ? AND pack = ?",
            (tenant_id, self.pack),
        )
        pack = self.db.one(
            "SELECT COUNT(*) AS d, COALESCE(SUM(chunks), 0) AS c FROM kn_docs"
            " WHERE scope = ? AND pack = ?",
            (PACK_SCOPE, self.pack),
        )
        return {
            "docs": own["d"],
            "chunks": own["c"],
            "pack_docs": pack["d"],
            "pack_chunks": pack["c"],
        }

    def keyword(self, ctx: TurnContext, query: Query) -> list[tuple[str, float]]:
        """BM25 candidates that contain at least `min_keyword_coverage` of the query's words.

        FTS5 `MATCH` with OR finds every chunk that shares one word with the query; the
        coverage check (one MATCH per word over those candidates, so stemming stays FTS5's)
        drops chunks that only share one common word such as "order": a query of two or more
        words needs at least two of them, and at least `min_keyword_coverage` of them."""
        terms = sorted(set(tokens(query.text)))
        if not terms:
            return []
        quoted = ['"' + t.replace('"', "") + '"' for t in terms]
        where, params = "", []
        if query.filters.get("doc"):
            where, params = " AND doc_id = ?", [query.filters["doc"]]
        tenant = require_tenant(ctx)
        rows = self.db.query(
            "SELECT vid, bm25(kn_fts) AS rank FROM kn_fts WHERE kn_fts MATCH ?"
            " AND pack = ? AND scope IN (?, ?)" + where + " ORDER BY rank, vid LIMIT ?",
            (" OR ".join(quoted), self.pack, tenant, PACK_SCOPE, *params, self.rerank_k),
        )
        found = [(r["vid"], float(r["rank"])) for r in rows]
        if len(terms) < 2 or not found:
            return found
        marks = ", ".join("?" for _ in found)
        matched: dict[str, int] = {}
        for term in quoted:
            for r in self.db.query(
                f"SELECT vid FROM kn_fts WHERE kn_fts MATCH ? AND vid IN ({marks})",
                (term, *[vid for vid, _ in found]),
            ):
                matched[r["vid"]] = matched.get(r["vid"], 0) + 1
        need = max(2, math.ceil(self.min_keyword_coverage * len(terms)))
        return [(vid, rank) for vid, rank in found if matched.get(vid, 0) >= need]

    def vector(self, ctx: TurnContext, query: Query) -> list[tuple[str, float]]:
        qvec = self.embedder.embed([query.text])[0]
        found = self.vectors.search(
            NS, self.embedder.id, qvec, self._scopes(ctx), self.rerank_k * 4
        )
        doc = query.filters.get("doc")
        out = [
            (vid, score)
            for vid, score in found
            if score >= self.min_vector_score
            and (not doc or vid.split("/")[-1].split("#")[0] == doc)
        ]
        return out[: self.rerank_k]

    def retrieve(self, ctx: TurnContext, query: Query) -> list[Hit]:
        if query.filters.get("pack") and query.filters["pack"] != self.pack:
            return []
        keyword = self.keyword(ctx, query)
        vector = self.vector(ctx, query)
        fused = rrf([[v for v, _ in keyword], [v for v, _ in vector]], self.rrf_k)
        if not fused:
            return []
        ranked = sorted(fused, key=lambda vid: (-fused[vid], vid))[: self.rerank_k]
        bm25_rank = {vid: i + 1 for i, (vid, _) in enumerate(keyword)}
        cos = dict(vector)
        marks = ", ".join("?" for _ in ranked)
        rows = {
            r["vid"]: r
            for r in self.db.query(
                "SELECT c.*, d.title, d.version FROM kn_chunks c JOIN kn_docs d"
                " ON d.scope = c.scope AND d.pack = c.pack AND d.doc_id = c.doc_id"
                f" WHERE c.vid IN ({marks})",
                ranked,
            )
        }
        hits = []
        for vid in ranked:
            r = rows.get(vid)
            if r is None:
                continue
            citation = citation_for(r["title"], r["heading"])
            hits.append(
                Hit(
                    vid,
                    "knowledge",
                    r["text"],
                    fused[vid],
                    r["tokens"] + estimate(citation, self.estimator) + 3,
                    SourceRef(
                        "knowledge",
                        f"{r['doc_id']}#{r['position']}",
                        r["title"],
                        f"knowledge://{self.pack}/{r['doc_id']}#{r['position']}",
                        r["version"],
                    ),
                    {
                        "doc": r["doc_id"],
                        "chunk": r["position"],
                        "citation": citation,
                        "bm25_rank": bm25_rank.get(vid),
                        "vector_score": round(cos[vid], 4) if vid in cos else None,
                    },
                )
            )
        if self.reranker != "none":
            hits = RERANKERS[self.reranker](query.text, hits)
        kept, _ = trim_to_budget(hits, query.top_k, query.max_tokens)
        return kept


async def run_knowledge(
    ctx: TurnContext, store: KnowledgeStore | None, query: Query | None
) -> list[Hit]:
    async with node(ctx, "knowledge") as span:
        if store is None or query is None:
            span.skip(hits=0, tokens=0, enabled=False)
            return []
        try:
            hits = await asyncio.to_thread(store.retrieve, ctx, query)
        except Exception as exc:  # store.md: a failing store reports store_unavailable
            raise HarnessError("store_unavailable", f"knowledge: {type(exc).__name__}") from exc
        if not hits:
            span.skip(hits=0, tokens=0, embedder=store.embedder.id)
            return []
        span.set(
            hits=len(hits),
            top_score=round(hits[0].score, 6),
            tokens=sum(h.tokens for h in hits),
            docs=sorted({h.meta["doc"] for h in hits}),
            embedder=store.embedder.id,
            reranker=store.reranker,
        )
        span.detail = {"hits": [h.to_dict() for h in hits]}
    return hits


def read_doc_tool(store: KnowledgeStore, max_chars: int = 12000) -> ToolSpec:
    """`read_doc` returns a pack document in full, by id: the on-demand tier of knowledge.
    The documents index (ids and titles) is already in the always tier of the context."""

    def read_doc(arguments: dict[str, Any]) -> str:
        text = store.pack_docs_text(arguments["doc"])
        if text is None:
            known = [doc_id for doc_id, _ in store.pack_index()]
            return json.dumps({"error": "unknown_doc", "known": known})
        return text if len(text) <= max_chars else text[:max_chars] + " [truncated]"

    return ToolSpec(
        name="read_doc",
        description="Read a document listed in the documents index in full, by its id.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["doc"],
            "properties": {"doc": {"type": "string", "maxLength": 80}},
        },
        action_class="read",
        timeout_ms=2000,
        impl=read_doc,
        pure=True,
    )
