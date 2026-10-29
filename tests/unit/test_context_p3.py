"""The context builder with real retrieval: slices, budgets, citations, degraded stores, the
summarize route, on-demand documents, and the HTTP surface of the context plane."""

from pathlib import Path

import httpx
import pytest

from harnesswalk.__main__ import main
from harnesswalk.context.records import Hit, SourceRef
from harnesswalk.control.http_api import create_app
from harnesswalk.execution.providers import ProviderResponse, ProviderUsage
from harnesswalk.runtime import Harness
from tests.unit.support import exits, harness, run_turn, settings

ALL_STORES = {"context.stores": ["procedural", "session", "semantic", "episodic", "knowledge"]}


def starter(tmp_path: Path, fixture: str = "plain_answer", **overrides) -> Harness:
    return harness(tmp_path, fixture, profile=None, overrides=overrides or None)


@pytest.mark.anyio
async def test_all_five_stores_light_on_one_question(tmp_path: Path) -> None:
    h = starter(tmp_path, "plain_answer")
    events = await run_turn(h, "Can I return order A-1003, the Trailhead backpack?")
    status = {
        k: exits(events, k)[0]["status"]
        for k in ("procedural", "session", "semantic", "episodic", "knowledge")
    }
    assert status == dict.fromkeys(status, "done")
    builder = exits(events, "context_builder")[0]["summary"]
    assert builder["retrieved"]["semantic"] >= 1 and builder["citations"] >= 1
    assert builder["used_tokens"] == sum(builder["slices"].values())
    ctx = h.context_builder.last["t1"]
    # Stable first: always tier, then semantic, episodic, knowledge, then the new input.
    system = [m.content for m in ctx.messages if m.role == "system"]
    assert "read_doc" in system[0] and "trailhead_backpack" in system[0]
    assert system[1].startswith("Facts on record")
    assert system[2].startswith("Earlier events")
    assert system[3].startswith("Knowledge passages") and "[1] " in system[3]
    assert ctx.messages[-1].role == "user"
    assert ctx.citations[0]["n"] == 1 and "doc:returns_policy" in ctx.on_demand_index


@pytest.mark.anyio
async def test_cited_answer_names_its_chunks_in_turn_end(tmp_path: Path) -> None:
    h = starter(tmp_path, "cited_answer")
    events = await run_turn(h, "How long do I have to return an item?")
    end = events[-1]["summary"]
    assert end["citations"] == [
        {"n": 1, "doc": "returns_policy", "chunk": 1, "citation": "Returns policy, Return window"},
        {"n": 2, "doc": "returns_policy", "chunk": 3, "citation": "Returns policy, Refunds"},
    ]
    knowledge = exits(events, "knowledge")[0]["summary"]
    assert knowledge["docs"] == ["returns_policy"] and knowledge["embedder"] == "fake:256"


@pytest.mark.anyio
async def test_retrieved_slices_are_trimmed_to_their_budget(tmp_path: Path) -> None:
    h = starter(
        tmp_path, "plain_answer", **{"context.slices.knowledge": 60, "context.slices.semantic": 1}
    )
    events = await run_turn(h, "Can I return order A-1003, the Trailhead backpack?")
    s = exits(events, "context_builder")[0]["summary"]
    assert s["slices"]["knowledge"] <= 60 and s["slices"]["semantic"] == 0
    # The stores trim to the budget first, so the builder rarely has to drop anything itself.
    assert s["retrieved"]["semantic"] == 0
    assert s["used_tokens"] <= s["budget_tokens"] - 1024


@pytest.mark.anyio
async def test_total_overflow_drops_retrieved_items_after_turns(tmp_path: Path) -> None:
    h = starter(
        tmp_path,
        "plain_answer",
        **{"context.total_tokens": 1330, "context.reserve_output_tokens": 1024},
    )
    events = await run_turn(h, "Can I return order A-1003, the Trailhead backpack?")
    s = exits(events, "context_builder")[0]["summary"]
    assert s["used_tokens"] <= 1330 - 1024
    assert sum(s.get("trimmed", {}).values()) > 0


def test_builder_drops_lowest_ranked_items_first() -> None:
    from harnesswalk.context.builder import ContextBuilder

    def hit(n: int) -> Hit:
        return Hit(
            f"h{n}",
            "semantic",
            f"fact number {n} " * 5,
            1 - n / 10,
            30,
            SourceRef("semantic", f"h{n}"),
        )

    hits = [hit(i) for i in range(4)]
    block = ContextBuilder._format("semantic", hits)
    assert block.index("fact number 0") < block.index("fact number 3")


@pytest.mark.anyio
async def test_a_failing_store_degrades_its_slice_only(tmp_path: Path) -> None:
    h = starter(tmp_path, "plain_answer")

    def broken(*args, **kwargs):
        raise RuntimeError("disk on fire")

    h.knowledge.retrieve = broken  # type: ignore[method-assign]
    events = await run_turn(h, "How long do I have to return an item?")
    err = [e for e in events if e["type"] == "error" and e.get("node_id") == "knowledge"]
    assert err and err[0]["error"]["code"] == "store_unavailable"
    assert exits(events, "memory")[0]["summary"]["degraded"] == ["knowledge"]
    assert exits(events, "context_builder")[0]["summary"]["degraded"] == ["knowledge"]
    assert events[-1]["summary"]["exit"] == "answered"


@pytest.mark.anyio
async def test_stores_left_out_of_context_stores_are_skipped(tmp_path: Path) -> None:
    h = harness(tmp_path, "plain_answer")  # the conformance profile: procedural and session
    events = await run_turn(h, "Can I return order A-1003?")
    for kind in ("semantic", "episodic", "knowledge"):
        e = exits(events, kind)[0]
        assert e["status"] == "skipped" and e["summary"]["enabled"] is False
    assert not exits(events, "memory_writer")


@pytest.mark.anyio
async def test_read_doc_returns_a_whole_document(tmp_path: Path) -> None:
    h = starter(tmp_path, "read_doc")
    events = await run_turn(h, "Tell me everything about the Trailhead 28.")
    tools = exits(events, "tools")[0]["summary"]
    assert tools["tool"] == "read_doc" and tools["outcome"] == "ok"
    assert "2 year warranty" in events[-1]["summary"]["reply"]


class Recorder:
    """A stand-in for a real provider: answers every call, keeps the requests."""

    name = "ollama"

    def __init__(self) -> None:
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        last = request.messages[-1].content
        text = (
            "SUMMARY: they talked about returns."
            if "Earlier summary" in last or (request.messages[0].content.startswith("Summarize"))
            else "ok"
        )
        return ProviderResponse(text, [], "stop", ProviderUsage(10, 5))


@pytest.mark.anyio
async def test_compaction_uses_the_summarize_route_with_a_real_provider(tmp_path: Path) -> None:
    h = starter(
        tmp_path,
        "plain_answer",
        **{"context.slices.session": 60, "context.compaction.keep_recent_turns": 1},
    )
    recorder = Recorder()
    h.providers.clear()
    h.providers["ollama"] = recorder
    session = None
    compacted = None
    for i in range(4):
        events = await run_turn(
            h, f"Question {i} about returns " + "and more words " * 8, session_id=session
        )
        session = events[0]["session_id"]
        summary = exits(events, "context_builder")[0]["summary"]
        if summary["compacted"]:
            compacted = events
            break
    assert compacted is not None
    s = exits(compacted, "context_builder")[0]["summary"]
    assert s["compaction"]["method"] == "model"
    routes = [
        e["summary"] for e in compacted if e["type"] == "node_exit" and e["node_id"] == "router"
    ]
    assert routes[0]["rule"] == "summarize" and routes[0]["model"] == "local-small"
    assert any(r.messages[0].content.startswith("Summarize") for r in recorder.requests)


def test_ingest_command(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("HARNESSWALK_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HARNESSWALK_PROFILE", raising=False)
    assert main(["ingest", "_starter", "--provider", "fake"]) == 0
    out = capsys.readouterr().out
    assert "pack _starter: 3 documents" in out and "(indexed)" in out and "fake:256" in out
    assert main(["ingest", "--provider", "fake"]) == 0
    assert "(unchanged)" in capsys.readouterr().out
    assert main(["ingest", "no_such_pack", "--provider", "fake"]) == 1


def client(h: Harness, **headers) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(h)),
        base_url="http://test",
        headers={"X-Harnesswalk-Tenant": "alpha", **headers},
    )


@pytest.mark.anyio
async def test_store_inspectors_and_knowledge_routes(tmp_path: Path) -> None:
    h = starter(tmp_path, "remember_fact")
    await run_turn(h, "Remember that I go by Sam", tenant="alpha")
    async with client(h) as c:
        semantic = (await c.get("/v1/nodes/semantic")).json()["inspector"]
        keys = [r["key"] for r in semantic["last_records"]]
        assert keys[0] == "preferred name" and semantic["last_records"][0]["scope"] == "tenant"
        assert "order A-1001 delivery note" in keys  # pack seeds, marked as such
        assert semantic["counts"]["facts"] == 1 and semantic["settings"]["vectors"] == "sqlite"
        episodic = (await c.get("/v1/nodes/episodic")).json()["inspector"]
        assert episodic["counts"]["episodes"] == 1 and episodic["counts"]["pack_episodes"] == 4
        knowledge = (await c.get("/v1/nodes/knowledge")).json()["inspector"]
        assert knowledge["counts"]["pack_docs"] == 3
        assert {d["doc_id"] for d in knowledge["last_records"]} == {
            "returns_policy",
            "shipping_faq",
            "trailhead_backpack",
        }
        writer = (await c.get("/v1/nodes/memory_writer")).json()
        assert writer["implemented"] and not writer["stub"]
        writes = writer["inspector"]["last_writes"]
        assert {w["store"] for w in writes} == {"semantic", "episodic"}
        fact = next(w for w in writes if w["store"] == "semantic")
        assert fact["diff"] == {"op": "create", "before": None, "after": "Sam"}
        assert writer["inspector"]["settings"]["gate_in_this_mode"] == "auto"
        docs = (await c.get("/v1/knowledge/docs")).json()
        assert docs["pack"] == "_starter" and len(docs["docs"]) == 3
        doc = (await c.get("/v1/knowledge/docs/returns_policy")).json()
        assert doc["chunks"][1]["citation"] == "Returns policy, Return window"
        assert (await c.get("/v1/knowledge/docs/nope")).status_code == 404
    async with client(h, **{"X-Harnesswalk-Tenant": "beta"}) as c:
        semantic = (await c.get("/v1/nodes/semantic")).json()["inspector"]
        assert all(r["scope"] == "pack" for r in semantic["last_records"])


@pytest.mark.anyio
async def test_review_gate_over_http(tmp_path: Path) -> None:
    h = starter(
        tmp_path,
        "remember_fact",
        **{"memory_writer.gate": "review", "memory_writer.auto_episodes": False},
    )
    events = await run_turn(h, "Remember that I go by Sam", tenant="alpha")
    assert exits(events, "memory_writer")[0]["summary"]["pending"] == 1
    async with client(h) as c:
        pending = (await c.get("/v1/memory/proposals")).json()["proposals"]
        assert len(pending) == 1 and pending[0]["content"] == {
            "key": "preferred name",
            "value": "Sam",
        }
        pid = pending[0]["proposal_id"]
        bad = await c.post(f"/v1/memory/proposals/{pid}", json={"decision": "maybe"})
        assert bad.status_code == 400
        ok = await c.post(f"/v1/memory/proposals/{pid}", json={"decision": "accept"})
        assert ok.status_code == 200 and ok.json()["status"] == "accepted"
        again = await c.post(f"/v1/memory/proposals/{pid}", json={"decision": "accept"})
        assert again.status_code == 409
        assert (
            await c.post("/v1/memory/proposals/nope", json={"decision": "accept"})
        ).status_code == 404
        everything = (await c.get("/v1/memory/proposals", params={"status": "all"})).json()
        assert everything["proposals"][0]["status"] == "accepted"
    async with client(h, **{"X-Harnesswalk-Tenant": "beta"}) as c:
        r = await c.post(f"/v1/memory/proposals/{pid}", json={"decision": "reject"})
        assert r.status_code == 404  # another tenant cannot see or decide it


@pytest.mark.anyio
async def test_public_mode_refuses_review_decisions(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer", profile=None, mode="public"))
    transport = httpx.ASGITransport(app=create_app(h))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/v1/memory/proposals/prop_x", json={"decision": "accept"})
        assert r.status_code == 403
    offered = [t.name for t in h.tools.schemas("public")]
    assert "read_doc" in offered and "remember" not in offered  # public: pure reads only
