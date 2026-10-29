"""Semantic and episodic stores, and the memory writer: gates by mode, versions, isolation."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from harnesswalk.adapters.storage.vectors import SqliteVectors
from harnesswalk.context.embeddings import FakeEmbedder
from harnesswalk.context.memory_writer import MemoryProposal, MemoryWriter, redact
from harnesswalk.context.records import PACK_SCOPE, Query
from harnesswalk.context.stores.episodic import EpisodicStore
from harnesswalk.context.stores.memory_db import open_memory_db
from harnesswalk.context.stores.semantic import SemanticStore
from harnesswalk.control.policy import Policy
from tests.unit.support import exits, harness, make_ctx, run_turn

POLICY = {
    "rules": [
        {
            "id": "memory.ok",
            "modes": ["dev", "private", "public"],
            "action_classes": ["memory.write"],
            "decision": "allow",
        },
    ]
}


def stores(tmp_path: Path):
    db = open_memory_db(str(tmp_path / "memory.db"))
    vectors, embedder = SqliteVectors(db), FakeEmbedder()
    return (
        db,
        SemanticStore(db, vectors, embedder, "pack"),
        EpisodicStore(db, vectors, embedder, "pack"),
    )


def writer(tmp_path: Path, mode: str = "dev", policy: dict | None = None, **cfg) -> MemoryWriter:
    db, semantic, episodic = stores(tmp_path)
    return MemoryWriter(cfg, db, semantic, episodic, Policy(policy or POLICY, mode))


def fact(key: str, value: str, **kw) -> MemoryProposal:
    return MemoryProposal("semantic", {"key": key, "value": value}, **kw)


def ctx_in(mode: str, tenant: str = "t1"):
    ctx, events = make_ctx(tenant)
    ctx.mode = mode
    return ctx, events


def types_of(events) -> list[str]:
    return [e["type"] for e in events.events if e.get("node_id") == "memory_writer"]


@pytest.mark.anyio
async def test_dev_auto_writes_and_versions_a_fact(tmp_path: Path) -> None:
    w = writer(tmp_path)
    ctx, events = ctx_in("dev")
    report = await w.run(ctx, [fact("preferred name", "Sam")])
    assert (report.proposed, report.written, report.rejected, report.pending) == (1, 1, 0, 0)
    assert types_of(events) == ["node_enter", "memory_proposed", "memory_written", "node_exit"]
    written = next(e for e in events.events if e["type"] == "memory_written")
    assert written["summary"]["version"] == 1 and written["summary"]["op"] == "create"
    record_id = written["summary"]["record_id"]

    # Same key, new value: a new version, the old one kept and diffable.
    ctx2, events2 = ctx_in("dev")
    await w.run(ctx2, [fact("preferred name", "Samantha")])
    history = w.history("t1", record_id)
    assert [(v["version"], v["value"]) for v in history] == [(1, "Sam"), (2, "Samantha")]
    last = w.recent("t1", 1)[0]
    assert last["diff"] == {"op": "update", "before": "Sam", "after": "Samantha"}

    # Same key, same value: a duplicate, rejected without a new version.
    ctx3, events3 = ctx_in("dev")
    report = await w.run(ctx3, [fact("preferred name", "Samantha")])
    assert report.rejected == 1 and "memory_written" not in types_of(events3)
    assert len(w.history("t1", record_id)) == 2


@pytest.mark.anyio
async def test_public_mode_proposes_but_never_writes(tmp_path: Path) -> None:
    w = writer(tmp_path, mode="public", gate="auto")
    ctx, events = ctx_in("public")
    report = await w.run(ctx, [fact("preferred name", "Sam")])
    assert (report.written, report.rejected) == (0, 1)
    proposed = next(e for e in events.events if e["type"] == "memory_proposed")
    assert proposed["summary"]["gate"] == "session"
    assert "memory_written" not in types_of(events)
    assert report.records[0].reason == "public_mode"
    assert w.semantic.count("t1")["facts"] == 0
    with pytest.raises(ValueError):  # nobody can accept it later either
        w.decide("t1", report.records[0].proposal_id, "user:x", "accept", "public")


@pytest.mark.anyio
async def test_private_mode_auto_only_for_opted_in_tenants(tmp_path: Path) -> None:
    w = writer(tmp_path, mode="private", auto_approve_tenants=["t1"])
    ctx, _ = ctx_in("private", "t1")
    assert (await w.run(ctx, [fact("k", "v")])).written == 1
    other, events = ctx_in("private", "t2")
    report = await w.run(other, [fact("k", "v")])
    assert (report.written, report.pending) == (0, 1)
    assert next(e for e in events.events if e["type"] == "memory_proposed")["summary"]["gate"] == (
        "review"
    )
    # A human accepts it at the review gate; it is decided at most once.
    pid = report.records[0].proposal_id
    assert [p["proposal_id"] for p in w.pending("t2")] == [pid]
    with pytest.raises(KeyError):
        w.decide("t1", pid, "user:t1", "accept", "private")  # another tenant's proposal
    accepted = w.decide("t2", pid, "user:t2", "accept", "private")
    assert accepted["status"] == "accepted" and accepted["version"] == 1
    with pytest.raises(ValueError):
        w.decide("t2", pid, "user:t2", "reject", "private")
    assert w.semantic.count("t2")["facts"] == 1


@pytest.mark.anyio
async def test_review_and_eval_gates(tmp_path: Path) -> None:
    w = writer(tmp_path, gate="review")
    ctx, _ = ctx_in("dev")
    report = await w.run(ctx, [fact("k", "v")])
    assert report.pending == 1 and report.written == 0
    rejected = w.decide("t1", report.records[0].proposal_id, "user:t1", "reject", "dev", "no")
    assert rejected["status"] == "rejected" and rejected["reason"] == "no"

    e = writer(tmp_path / "eval", gate="eval")
    ctx, _ = ctx_in("dev")
    assert (await e.run(ctx, [fact("color", "blue")])).written == 1
    ctx, _ = ctx_in("dev")
    contradiction = await e.run(ctx, [fact("color", "red")])
    assert contradiction.records[0].reason == "contradiction"


@pytest.mark.anyio
async def test_policy_disabled_writer_and_limits(tmp_path: Path) -> None:
    deny = {"rules": [{"id": "no.memory", "action_classes": ["memory.write"], "decision": "deny"}]}
    w = writer(tmp_path, policy=deny)
    ctx, _ = ctx_in("dev")
    report = await w.run(ctx, [fact("k", "v")])
    assert report.records[0].reason == "policy"

    capped = writer(tmp_path / "cap", max_proposals_per_turn=1)
    ctx, _ = ctx_in("dev")
    report = await capped.run(ctx, [fact("a", "1"), fact("b", "2")])
    assert [r.reason for r in report.records] == [None, "max_proposals_per_turn"]

    off = writer(tmp_path / "off", enabled=False)
    ctx, events = ctx_in("dev")
    report = await off.run(ctx, [fact("a", "1")])
    assert (report.proposed, report.rejected) == (1, 1)
    exit_ = [e for e in events.events if e["type"] == "node_exit"][-1]
    assert exit_["status"] == "skipped" and exit_["summary"]["disabled"] is True

    bad = writer(tmp_path / "bad")
    ctx, _ = ctx_in("dev")
    report = await bad.run(
        ctx,
        [
            fact("k", "v", tenant_id="someone_else"),
            MemoryProposal("semantic", {"key": "", "value": "x"}),
            MemoryProposal("episodic", {"text": ""}),
        ],
    )
    assert [r.reason for r in report.records] == [
        "cross_tenant",
        "invalid: a fact needs key and value",
        "invalid: an event needs text",
    ]


@pytest.mark.anyio
async def test_proposals_are_redacted_before_they_are_stored(tmp_path: Path) -> None:
    assert redact("mail sam@example.com or call +1 416 555 0199")[1] == {"email": 1, "phone": 1}
    w = writer(tmp_path)
    ctx, events = ctx_in("dev")
    await w.run(ctx, [fact("contact", "sam@example.com, card 4111 1111 1111 1111")])
    stored = w.semantic.find("t1", None, "contact")
    assert stored.value == "[email], card [card]"
    proposed = next(e for e in events.events if e["type"] == "memory_proposed")
    assert "example.com" not in str(proposed["summary"])
    assert proposed["summary"]["redactions"] == {"email": 1, "card": 1}


@pytest.mark.anyio
async def test_writes_need_the_writer_token(tmp_path: Path) -> None:
    _, semantic, episodic = stores(tmp_path)
    with pytest.raises(PermissionError):
        semantic.write("t1", None, "k", "v", writer="loop", source="x")
    with pytest.raises(PermissionError):
        episodic.append(
            "t1", writer="gateway", session_id="s", turn_id="t", kind="k", text="x", source="x"
        )


def test_semantic_ranking_and_tenant_isolation(tmp_path: Path) -> None:
    _, semantic, _ = stores(tmp_path)
    semantic.write(
        "tenant_a",
        None,
        "order A-1001 delivery note",
        "Leave it with the concierge.",
        writer="memory_writer",
        source="test",
    )
    semantic.write(
        "tenant_a", None, "favourite colour", "green", writer="memory_writer", source="test"
    )
    semantic.write(
        PACK_SCOPE, None, "store hours", "9 to 5 on weekdays", writer="memory_writer", source="seed"
    )
    a, _ = make_ctx("tenant_a")
    b, _ = make_ctx("tenant_b")
    hits = semantic.retrieve(a, Query("Where is order A-1001?"))
    assert hits[0].score == 1.0 and hits[0].meta["match"] == "entity"
    assert "concierge" in hits[0].text
    by_key = semantic.retrieve(a, Query("What is my favourite colour?"))
    assert by_key[0].text == "favourite colour: green" and by_key[0].score == 1.0
    # A fact of tenant A is never retrieved for tenant B, whatever the query.
    for q in ("Where is order A-1001?", "favourite colour green concierge"):
        assert all(h.meta["scope"] != "tenant" for h in semantic.retrieve(b, Query(q)))
    assert [f.key for f in semantic.list(b)] == ["store hours"]
    # Pack seeds are visible to every tenant, and a tenant's own fact shadows the seed.
    assert semantic.retrieve(b, Query("store hours"))[0].meta["scope"] == "pack"
    semantic.write(
        "tenant_b", None, "store hours", "closed today", writer="memory_writer", source="test"
    )
    assert [f.value for f in semantic.list(b) if f.key == "store hours"] == ["closed today"]
    # Unrelated queries fall under min_score.
    assert semantic.retrieve(a, Query("zebra migration patterns")) == []
    with pytest.raises(ValueError):
        semantic.retrieve(SimpleNamespace(tenant_id="", user_id=None), Query("x"))


def test_episodic_blends_relevance_with_recency(tmp_path: Path) -> None:
    _, _, episodic = stores(tmp_path)
    old = "2026-01-01T00:00:00Z"
    new = "2026-10-28T00:00:00Z"
    for ts, text in (
        (old, "The customer asked about a late parcel."),
        (new, "The customer asked about a late parcel again."),
    ):
        episodic.append(
            "t1",
            writer="memory_writer",
            session_id="s",
            turn_id=ts,
            kind="support",
            text=text,
            ts=ts,
            source="test",
        )
    episodic.append(
        "t2",
        writer="memory_writer",
        session_id="s",
        turn_id="x",
        kind="support",
        text="Tenant two asked about a late parcel.",
        source="test",
    )
    ctx, _ = make_ctx("t1")
    now = 1793232000.0  # 2026-10-29
    hits = episodic.retrieve(ctx, Query("late parcel", now=now))
    assert [h.meta["ts"] for h in hits] == [new, old]  # same relevance, the newer one first
    assert all("Tenant two" not in h.text for h in hits)
    assert episodic.score(1.0, new, now) > episodic.score(1.0, old, now)
    assert episodic.retrieve(ctx, Query("quarterly tax filing", now=now)) == []


@pytest.mark.anyio
async def test_a_chat_turn_appends_its_episode_and_remembers(tmp_path: Path) -> None:
    h = harness(tmp_path, "remember_fact", profile=None)
    events = await run_turn(h, "Remember that I go by Sam", tenant="dev_a")
    writer_exit = exits(events, "memory_writer")[0]["summary"]
    assert writer_exit["proposed"] == 2 and writer_exit["written"] == 2  # the fact and the turn
    stores_written = [e["summary"]["store"] for e in events if e["type"] == "memory_written"]
    assert stores_written == ["semantic", "episodic"]
    second = await run_turn(
        h, "What is my preferred name?", tenant="dev_a", session_id=events[0]["session_id"]
    )
    assert exits(second, "semantic")[0]["summary"]["hits"] >= 1
    # Another tenant sees neither the fact nor the episode.
    other = await run_turn(h, "What is my preferred name?", tenant="dev_b")
    assert exits(other, "semantic")[0]["status"] == "skipped"
    detail = h.context_builder.last["dev_b"]
    assert all("Sam" not in m.content for m in detail.messages)


@pytest.mark.anyio
async def test_blocked_turns_never_reach_the_writer(tmp_path: Path) -> None:
    h = harness(
        tmp_path, "plain_answer", profile=None, overrides={"input_guardrails.size.max_chars": 5}
    )
    events = await run_turn(h, "this is far too long")
    assert not exits(events, "memory_writer")
