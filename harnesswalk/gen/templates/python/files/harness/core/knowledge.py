"""Knowledge: the pack's documents, chunked by heading, found by keyword and by vector.

Contract: spec/contracts/store.md (`knowledge`). Node: `knowledge` (on from the `grounded`
rung). Ranking: BM25 over an in-memory SQLite FTS5 index (chunks that share at least two of
the query's words, and a quarter of them) fused by reciprocal rank with hash-vector search
(cosine at least 0.45). Every hit carries `{doc, chunk, citation}`; the context builder numbers
them [1], [2] and the reply cites them. A PDF's pages were extracted to text by the generator
(`sources/<doc>.pages.json`), so no PDF library is needed here.

Lesson: an answer drawn from documents should say which passage it used, and code checks it.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any

from harness.core.text import cosine, embed, words

HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def split_markdown(text: str) -> tuple[str, list[tuple[str, str]]]:
    if text.startswith("---\n"):
        text = text[4:].partition("\n---\n")[2]
    title, heading, lines, sections = "", "", [], []
    for line in text.splitlines():
        m = HEADING.match(line)
        if not m:
            lines.append(line)
            continue
        if "\n".join(lines).strip():
            sections.append((heading, "\n".join(lines).strip()))
        if len(m.group(1)) == 1 and not title:
            title, heading = m.group(2).strip(), ""
        else:
            heading = m.group(2).strip()
        lines = []
    if "\n".join(lines).strip():
        sections.append((heading, "\n".join(lines).strip()))
    return title, sections


def windows(text: str, max_chars: int = 1600, overlap: int = 160) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    ws, out, start = text.split(), [], 0
    while start < len(ws):
        end, size = start, 0
        while end < len(ws) and size + len(ws[end]) + (end > start) <= max_chars:
            size, end = size + len(ws[end]) + (end > start), end + 1
        end = max(end, start + 1)
        out.append(" ".join(ws[start:end]))
        if end >= len(ws):
            break
        back, kept = end, 0
        while back > start + 1 and kept + len(ws[back - 1]) + 1 <= overlap:
            back, kept = back - 1, kept + len(ws[back - 1]) + 1
        start = back
    return out


class KnowledgeStore:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.tables: dict[str, str] = {}  # one FTS5 table per pack: BM25 statistics stay its own
        self.lock = threading.Lock()
        self.chunks: dict[str, dict[str, Any]] = {}
        self.docs: dict[str, dict[str, dict[str, Any]]] = {}

    def ingest(self, pack: str, docs_dir: Path | None) -> int:
        if pack in self.docs or not docs_dir or not docs_dir.is_dir():
            return 0
        self.docs[pack], table = {}, f"fts_{len(self.tables)}"
        self.tables[pack] = table
        self.conn.execute(f"CREATE VIRTUAL TABLE {table} USING fts5(text, vid UNINDEXED,"
                          " tokenize = 'porter unicode61')")  # fmt: skip
        for path in sorted(docs_dir.iterdir()):
            doc_id = re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_")
            pages = path.parent.parent / "sources" / f"{path.stem}.pages.json"
            if path.suffix == ".md":
                text = path.read_text(encoding="utf-8")
                title, sections = split_markdown(text)
            elif path.suffix == ".pdf" and pages.exists():  # text extracted at generation time
                doc = json.loads(pages.read_text(encoding="utf-8"))
                title, text = doc["title"], "\n\n".join(p for p in doc["pages"] if p)
                sections = [(f"page {n}", p) for n, p in enumerate(doc["pages"], start=1) if p]
            else:
                continue
            title = title or path.stem.replace("_", " ")
            self.docs[pack][doc_id] = {"title": title, "text": text}
            n = 0
            for heading, body in sections:
                for piece in windows(body):
                    vid = f"_pack/{pack}/{doc_id}#{n}"
                    indexed = f"{title}. {heading}. {piece}" if heading else f"{title}. {piece}"
                    self.chunks[vid] = {"doc": doc_id, "chunk": n, "text": piece, "pack": pack,
                                        "citation": f"{title}, {heading}" if heading else title,
                                        "vec": embed(indexed)}  # fmt: skip
                    with self.lock:
                        self.conn.execute(f"INSERT INTO {table} VALUES (?, ?)", (indexed, vid))
                    n += 1
        return len(self.docs[pack])

    def keyword(self, pack: str, query: str, k: int = 20) -> list[str]:
        terms, table = sorted(set(words(query))), self.tables.get(pack)
        if not terms or not table:
            return []
        quoted = ['"' + t.replace('"', "") + '"' for t in terms]
        with self.lock:
            found = [r[0] for r in self.conn.execute(
                f"SELECT vid FROM {table} WHERE {table} MATCH ? ORDER BY bm25({table}), vid LIMIT ?",
                (" OR ".join(quoted), k))]  # fmt: skip
            if len(terms) < 2 or not found:
                return found
            marks = ", ".join("?" for _ in found)
            matched: dict[str, int] = {}
            for term in quoted:
                for (vid,) in self.conn.execute(
                        f"SELECT vid FROM {table} WHERE {table} MATCH ? AND vid IN ({marks})",
                        (term, *found)):  # fmt: skip
                    matched[vid] = matched.get(vid, 0) + 1
        need = max(2, math.ceil(0.25 * len(terms)))
        return [vid for vid in found if matched.get(vid, 0) >= need]

    def vector(self, pack: str, query: str, k: int = 20) -> list[str]:
        q = embed(query)
        scored = sorted(((-cosine(q, c["vec"]), vid) for vid, c in self.chunks.items()
                         if c["pack"] == pack), key=lambda x: (x[0], x[1]))  # fmt: skip
        return [vid for s, vid in scored[: k * 4] if -s >= 0.45][:k]

    def search(self, pack: str, query: str, top_k: int = 6, budget: int = 4000):
        fused: dict[str, float] = {}
        for ranking in (self.keyword(pack, query), self.vector(pack, query)):
            for rank, vid in enumerate(ranking, start=1):
                fused[vid] = fused.get(vid, 0.0) + 1.0 / (60 + rank)
        hits, used = [], 0
        for vid in sorted(fused, key=lambda v: (-fused[v], v))[:20]:
            c = self.chunks[vid]
            cost = (len(c["text"]) + 3) // 4 + (len(c["citation"]) + 3) // 4 + 3
            if len(hits) >= top_k or used + cost > budget:
                continue
            used += cost
            hits.append({**c, "score": fused[vid], "vec": None})
        return hits


def retrieve(h: Any, turn: Any, query: str, budget: int) -> list[dict[str, Any]]:
    top_k = int((h.settings.get("context.retrieval.top_k") or {}).get("knowledge", 6))
    return h.knowledge.search(turn.data["pack"], query, top_k, budget)
