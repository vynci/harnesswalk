"""Knowledge: chunking, ingestion (markdown and PDF), hybrid retrieval and fusion, filters."""

from pathlib import Path

import pytest

from harnesswalk.adapters.storage.vectors import SqliteVectors
from harnesswalk.context.chunking import chunk_document, load_document, split_markdown, windows
from harnesswalk.context.embeddings import FakeEmbedder
from harnesswalk.context.records import PACK_SCOPE, Hit, Query, rrf
from harnesswalk.context.stores import knowledge as kn
from harnesswalk.context.stores.knowledge import KnowledgeStore, read_doc_tool
from harnesswalk.context.stores.memory_db import open_memory_db
from harnesswalk.types import ConfigError
from tests.unit.support import make_ctx

DOC = """---
title: ignored front matter
---
# Returns policy

Intro line before any section.

## Return window

Items can be returned within 30 days of delivery.

## Refunds

Refunds take 5 business days.
"""


def store(tmp_path: Path, **cfg) -> KnowledgeStore:
    db = open_memory_db(str(tmp_path / "memory.db"))
    return KnowledgeStore(db, SqliteVectors(db), FakeEmbedder(), "pack", cfg)


def write_docs(directory: Path, docs: dict[str, str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in docs.items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


def minimal_pdf(text: str) -> bytes:
    """A one-page PDF with one line of Helvetica text, with a correct xref table."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out


def test_markdown_splits_by_heading_then_size() -> None:
    title, sections = split_markdown(DOC)
    assert title == "Returns policy"
    assert [s.heading for s in sections] == ["", "Return window", "Refunds"]
    long = " ".join(f"word{i}" for i in range(400))
    pieces = windows(long, chunk_tokens=50, overlap_tokens=10)
    assert len(pieces) > 1 and all(len(p) <= 200 for p in pieces)
    # Consecutive windows overlap: the next one starts with the tail of the previous one.
    first, second = pieces[0].split(), pieces[1].split()
    assert second[0] in first and first[-1] in second
    assert windows("short text", 50, 10) == ["short text"]


def test_chunk_document_numbers_chunks_in_order(tmp_path: Path) -> None:
    path = write_docs(tmp_path / "docs", {"returns.md": DOC}) / "returns.md"
    doc = load_document(path)
    chunks = chunk_document(doc, chunk_tokens=400, overlap=40)
    assert doc.doc_id == "returns" and doc.kind == "markdown"
    assert [(c.position, c.heading) for c in chunks] == [
        (0, ""),
        (1, "Return window"),
        (2, "Refunds"),
    ]


def test_pdf_is_ingested_page_by_page(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "warranty_card.pdf").write_bytes(minimal_pdf("The warranty lasts two years."))
    doc = load_document(docs / "warranty_card.pdf")
    assert doc.kind == "pdf" and doc.sections[0].heading == "page 1"
    assert "warranty lasts two years" in doc.text
    k = store(tmp_path)
    assert k.ingest(docs)["chunks"] == 1
    ctx, _ = make_ctx("t1")
    hits = k.retrieve(ctx, Query("how long is the warranty", top_k=3, max_tokens=500))
    assert hits[0].meta["doc"] == "warranty_card" and hits[0].meta["citation"].endswith("page 1")


def test_ingest_is_idempotent_and_writer_checked(tmp_path: Path) -> None:
    docs = write_docs(tmp_path / "docs", {"returns.md": DOC})
    k = store(tmp_path)
    first = k.ingest(docs)
    assert first == {"pack": "pack", "docs": 1, "chunks": 3, "changed": True}
    assert k.ingest(docs)["changed"] is False
    (docs / "returns.md").write_text(DOC + "\n## Exchanges\n\nExchanges are free.\n")
    assert k.ingest(docs) == {"pack": "pack", "docs": 1, "chunks": 4, "changed": True}
    with pytest.raises(PermissionError):
        k.ingest(docs, writer="memory_writer")


def test_rrf_fuses_two_rankings() -> None:
    scores = rrf([["a", "b", "c"], ["b", "d"]], k=60)
    order = sorted(scores, key=lambda x: (-scores[x], x))
    assert order[0] == "b"  # in both lists
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert set(order) == {"a", "b", "c", "d"}


def test_hybrid_retrieval_ranks_cites_and_filters(tmp_path: Path) -> None:
    docs = write_docs(
        tmp_path / "docs",
        {
            "returns.md": DOC,
            "shipping.md": "# Shipping\n\n## Delivery\n\nDelivery takes 3 to 5 business days.\n",
        },
    )
    k = store(tmp_path)
    k.ingest(docs)
    ctx, _ = make_ctx("t1")
    hits = k.retrieve(ctx, Query("How many days to return an item?", top_k=5, max_tokens=2000))
    assert hits[0].meta == {
        "doc": "returns",
        "chunk": 1,
        "citation": "Returns policy, Return window",
        "bm25_rank": 1,
        "vector_score": hits[0].meta["vector_score"],
    }
    assert hits[0].source.store == "knowledge" and hits[0].source.id == "returns#1"
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
    # A chunk sharing one common word with a two-word query is not a keyword hit.
    assert all(h.meta["doc"] != "shipping" for h in hits)
    # Deterministic: the same query gives the same hits.
    again = k.retrieve(ctx, Query("How many days to return an item?", top_k=5, max_tokens=2000))
    assert [h.id for h in again] == [h.id for h in hits]
    # Metadata filters: doc and pack.
    only = k.retrieve(ctx, Query("delivery business days", filters={"doc": "shipping"}))
    assert {h.meta["doc"] for h in only} == {"shipping"}
    assert k.retrieve(ctx, Query("delivery business days", filters={"pack": "other"})) == []
    # The token budget trims the hits.
    tight = k.retrieve(ctx, Query("refunds return days", top_k=5, max_tokens=hits[0].tokens))
    assert len(tight) == 1


def test_tenant_documents_stay_in_their_tenant(tmp_path: Path) -> None:
    k = store(tmp_path)
    k.ingest(
        write_docs(tmp_path / "a", {"secret.md": "# Plan\n\nThe zebra launch is in May.\n"}),
        scope="tenant_a",
    )
    a, _ = make_ctx("tenant_a")
    b, _ = make_ctx("tenant_b")
    query = Query("zebra launch May", top_k=5, max_tokens=500)
    assert [h.meta["doc"] for h in k.retrieve(a, query)] == ["secret"]
    assert k.retrieve(b, query) == []
    assert k.doc(b, "secret") is None and k.doc(a, "secret")["scope"] == "tenant"
    assert [d["doc_id"] for d in k.docs(b)] == []


def test_rerank_hook(tmp_path: Path) -> None:
    def reverse(query: str, hits: list[Hit]) -> list[Hit]:
        return list(reversed(hits))

    with pytest.raises(ConfigError):
        store(tmp_path, reranker="cross_encoder")
    kn.register_reranker("test_reverse", reverse)
    try:
        plain, reranked = store(tmp_path), store(tmp_path, reranker="test_reverse")
        plain.ingest(write_docs(tmp_path / "docs", {"returns.md": DOC}))
        ctx, _ = make_ctx("t1")
        q = Query("refunds return days items", top_k=5, max_tokens=2000)
        assert [h.id for h in reranked.retrieve(ctx, q)] == [
            h.id for h in reversed(plain.retrieve(ctx, q))
        ]
    finally:
        kn.RERANKERS.pop("test_reverse")


def test_read_doc_tool(tmp_path: Path) -> None:
    k = store(tmp_path)
    k.ingest(write_docs(tmp_path / "docs", {"returns.md": DOC}))
    tool = read_doc_tool(k, max_chars=40)
    assert tool.action_class == "read" and tool.pure
    assert tool.impl({"doc": "returns"}).endswith("[truncated]")
    assert '"unknown_doc"' in tool.impl({"doc": "nope"})
    assert k.pack_index() == [("returns", "Returns policy")]
    assert k.count("anyone")["pack_docs"] == 1 and PACK_SCOPE == "_pack"
