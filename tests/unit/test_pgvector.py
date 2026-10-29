"""The pgvector backend. Runs only with HARNESSWALK_TEST_PG=1 and a Postgres with pgvector
(`make up`, or POSTGRES_* / HARNESSWALK_DATABASE_URL pointing at one); skipped otherwise."""

import os
import uuid
from pathlib import Path

import pytest

from harnesswalk.adapters.storage.vectors import PgVectors, SqliteVectors, postgres_dsn
from harnesswalk.context.embeddings import FakeEmbedder
from harnesswalk.context.stores.memory_db import open_memory_db
from tests.unit.support import exits, harness, run_turn

pytestmark = pytest.mark.skipif(
    os.environ.get("HARNESSWALK_TEST_PG") != "1", reason="set HARNESSWALK_TEST_PG=1 to run"
)


@pytest.fixture
def pg():
    table = "hw_test_" + uuid.uuid4().hex[:8]
    index = PgVectors(postgres_dsn(os.environ), table=table)
    yield index
    with index.conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
    index.close()


def test_pgvector_matches_sqlite(tmp_path: Path, pg: PgVectors) -> None:
    emb = FakeEmbedder()
    texts = {
        "a": "returns within 30 days of delivery",
        "b": "standard delivery takes 3 to 5 business days",
        "c": "the backpack has a 2 year warranty",
    }
    items = [
        (k, "t1", "pack", v) for k, v in zip(texts, emb.embed(list(texts.values())), strict=True)
    ]
    items.append(("x", "t2", "pack", emb.embed(["returns within 30 days"])[0]))
    lite = SqliteVectors(open_memory_db(str(tmp_path / "m.db")))
    for index in (pg, lite):
        index.upsert("knowledge", items, emb.id)
    q = emb.embed(["how many days for returns"])[0]
    scopes = [("t1", "pack")]
    got_pg = pg.search("knowledge", emb.id, q, scopes, 3)
    got_lite = lite.search("knowledge", emb.id, q, scopes, 3)
    assert [i for i, _ in got_pg] == [i for i, _ in got_lite]
    assert all(abs(a[1] - b[1]) < 1e-5 for a, b in zip(got_pg, got_lite, strict=True))
    assert "x" not in [i for i, _ in got_pg]  # tenant t2's vector never comes back for t1
    pg.delete("knowledge", ["a"])
    assert "a" not in [i for i, _ in pg.search("knowledge", emb.id, q, scopes, 3)]


@pytest.mark.anyio
async def test_a_turn_on_pgvector(tmp_path: Path) -> None:
    h = harness(tmp_path, "cited_answer", profile=None, overrides={"storage.vectors": "pgvector"})
    try:
        assert h.vectors.backend == "pgvector"
        h.ingest(force=True)
        events = await run_turn(h, "How long do I have to return an item?")
        assert exits(events, "knowledge")[0]["summary"]["docs"] == ["returns_policy"]
        assert events[-1]["summary"]["citations"][0]["doc"] == "returns_policy"
    finally:
        await h.aclose()
