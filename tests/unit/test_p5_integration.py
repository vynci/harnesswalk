"""P5 integration review: diagnose on induced failures of domain packs, pack-scoped memory
tenancy, and the release gate on a domain pack. Fake provider only."""

import asyncio
from pathlib import Path

import pytest
import yaml

from harnesswalk.config.loader import load_settings
from harnesswalk.control.gateway import TurnRequest
from harnesswalk.runtime import Harness
from harnesswalk.types import Identity
from tests.spec.support import ROOT
from tests.unit.test_http_api import client


def domain(tmp_path: Path, pack: str = "_starter", **overrides) -> Harness:
    settings = load_settings(
        ROOT,
        provider="fake",
        fixture="plain_answer",
        overrides={"dataset": pack, "sandbox.level": "inprocess", **overrides},
        data_dir=tmp_path / "data",
        env={},
    )
    return Harness(settings)


async def golden(c, pack: str, cases: list[str]) -> dict:
    r = await c.post("/v1/evals/runs", json={"pack": pack, "suite": "golden", "cases": cases})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    for _ in range(200):
        run = (await c.get(f"/v1/evals/runs/{run_id}")).json()
        if run["status"] != "running":
            return run
        await asyncio.sleep(0.05)
    raise AssertionError("the run did not finish")


@pytest.mark.anyio
async def test_diagnose_names_the_layer_and_true_evidence_on_two_induced_failures(
    tmp_path: Path,
) -> None:
    """(1) finance: the knowledge slice cut to 60 tokens drops the cited fact sheet; (2)
    ecommerce: a policy rule that denies cancel_order. Each regressed case is diagnosed with
    the layer at fault, and its evidence states what really changed and what the turn did."""
    h = domain(tmp_path)
    async with client(h, "dev") as c:
        base = await golden(c, "finance", ["cite_hurdle_hcp03"])
        assert base["totals"]["passed"] == 1
        ctx = (await c.get("/v1/artifacts/context/context")).json()["body"]
        cut = ctx.replace("    knowledge: 4000\n", "    knowledge: 60\n")
        assert cut != ctx
        assert (await c.put("/v1/artifacts/context/context", json={"body": cut})).status_code == 200
        run = await golden(c, "finance", ["cite_hurdle_hcp03"])
        assert run["regression_cases"] == ["cite_hurdle_hcp03"]
        d = run["cases"][0]["diagnosis"]
        assert d["layer"] == "retrieval", d
        facts = " ".join(e["fact"] for e in d["evidence"])
        # True statements: the artifact versions, what retrieval did, which scorer failed.
        assert (
            "context/context is v2 here" in facts
            and f"{base['run_id']}, the last pass, ran v1" in facts
        )
        assert "knowledge retrieved nothing" in facts and "fund_sheet_hcp03" in facts
        assert d["baseline"]["run_id"] == base["run_id"]
        await c.put("/v1/artifacts/context/context", json={"body": ctx})  # restore

        base = await golden(c, "ecommerce", ["cancel_approved_qf20430"])
        assert base["totals"]["passed"] == 1
        policy = yaml.safe_load((await c.get("/v1/artifacts/policy/policy")).json()["body"])
        policy["rules"].insert(0, {"id": "review.no_cancel", "modes": ["dev"],
                                   "tools": ["cancel_order"], "decision": "deny"})  # fmt: skip
        body = yaml.safe_dump(policy, sort_keys=False)
        assert (await c.put("/v1/artifacts/policy/policy", json={"body": body})).status_code == 200
        run = await golden(c, "ecommerce", ["cancel_approved_qf20430"])
        assert run["regression_cases"] == ["cancel_approved_qf20430"]
        d = run["cases"][0]["diagnosis"]
        assert d["layer"] == "guardrail", d
        facts = " ".join(e["fact"] for e in d["evidence"])
        assert "cancel_order" in facts and "review.no_cancel" in facts
    await h.aclose()


async def remember(h: Harness, tenant: str, pack: str, session: str) -> None:
    state = await h.gateway.start_turn(
        TurnRequest(
            message="Please note that I want all my reports in euro from now on.",
            identity=Identity(tenant, None, ["user"], anonymous=False),
            session_id=session,
            dataset=pack,
            fixture="mem_reporting_currency_set",
        )
    )
    await h.gateway.wait(state.ctx.turn_id, 10)


async def recall_hits(h: Harness, tenant: str, pack: str, session: str) -> int:
    state = await h.gateway.start_turn(
        TurnRequest(
            message="Which reporting currency did I ask you to use?",
            identity=Identity(tenant, None, ["user"], anonymous=False),
            session_id=session,
            dataset=pack,
            fixture="mem_reporting_currency_recall" if pack == "finance" else "plain_answer",
        )
    )
    await h.gateway.wait(state.ctx.turn_id, 10)
    events = h.tracer.get_turn(tenant, state.ctx.turn_id)
    exit_ = next(e for e in events if e["type"] == "node_exit" and e["node_id"] == "semantic")
    texts = []
    for e in events:
        if e["type"] == "node_exit" and e["node_id"] == "context_builder" and e.get("detail_ref"):
            detail = h.tracer.get_detail(tenant, e["detail_ref"]) or {}
            texts = [i.get("text", "") for i in (detail.get("items") or {}).get("semantic", [])]
    return sum("euro" in t for t in texts) if exit_["summary"].get("hits") else 0


@pytest.mark.anyio
async def test_pack_scoped_memory_is_per_tenant_and_per_pack(tmp_path: Path) -> None:
    """A preference remembered by tenant a on finance is recalled by a on finance only: not by
    tenant b on finance, not by a on another pack, and its proposal lists under ?pack=finance
    for a only."""
    h = domain(tmp_path)
    await remember(h, "a", "finance", "s1")
    assert await recall_hits(h, "a", "finance", "s2") == 1
    assert await recall_hits(h, "b", "finance", "s3") == 0
    assert await recall_hits(h, "a", "sports", "s4") == 0
    async with client(h, "a") as ca, client(h, "b") as cb:
        mine = (await ca.get("/v1/memory/proposals?status=all&pack=finance")).json()["proposals"]
        assert mine and {p["pack"] for p in mine} == {"finance"}
        other = (await ca.get("/v1/memory/proposals?status=all&pack=sports")).json()
        assert all(p["pack"] == "sports" for p in other["proposals"])
        theirs = (await cb.get("/v1/memory/proposals?status=all&pack=finance")).json()
        assert all(p["tenant_id"] == "b" for p in theirs["proposals"])
        assert not any(p["session_id"] == "s1" for p in theirs["proposals"])
    await h.aclose()
