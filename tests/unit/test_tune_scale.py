"""P6: generated scenarios (disjoint splits, constructed expectations, tool-table overlays) and
the scaled, resumable, spend-capped runner (ops/tune/scenarios.py, scale.py), on the fake
provider: no model is called."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from harnesswalk.ops.eval.golden import load_golden, normalize
from harnesswalk.ops.eval.normalize import answer_match
from harnesswalk.ops.tune import scale as sc
from harnesswalk.ops.tune import scenarios as gen
from harnesswalk.runtime import Harness
from tests.spec.support import ROOT
from tests.unit.support import settings

DATASETS = ROOT / "datasets"


def tune_settings(tmp_path: Path, **extra: Any):
    overrides = {"stores.embeddings.provider": "fake", **extra}
    return settings(tmp_path, profile=None, overrides={"dataset": "ecommerce", **overrides})


def test_generation_is_deterministic_and_parses() -> None:
    a = gen.generate("ecommerce", "train", 120, 7, DATASETS)
    b = gen.generate("ecommerce", "train", 120, 7, DATASETS)
    assert a == b and len(a) == 120
    assert gen.generate("ecommerce", "train", 120, 8, DATASETS) != a
    for pack, n in gen.TEST_COUNTS.items():
        for s in gen.generate(pack, "test", n // 10, 61, DATASETS):
            normalize(s["case"], s["case"]["id"])  # a valid golden case
            assert s["pack"] == pack and s["split"] == "test"


def test_splits_share_no_prompt_and_no_entity_id() -> None:
    train = [s for p in gen.TRAIN_PACKS for s in gen.generate(p, "train", 1500, 7, DATASETS)]
    test = [s for p, n in gen.TEST_COUNTS.items() for s in gen.generate(p, "test", n, 61, DATASETS)]
    assert len(test) == 1000
    check = gen.check_disjoint(train, test)
    assert check["n_shared_prompts"] == 0 and check["n_shared_ids"] == 0, check
    golden = set()
    for path in DATASETS.glob("*/golden.jsonl"):  # every pack's golden set
        for c in load_golden(path):
            golden |= {gen._norm(x.user) for x in c.turns} or {gen._norm(c.prompt)}
    assert not gen.prompts_of(test) & golden
    assert not gen.prompts_of(train) & golden
    golden_ids = {"QF-20430", "QF-20417", "TG-604118", "KW-0027", "QT-58120394"}
    assert not gen.entity_ids(train + test) & golden_ids


def test_device_fleet_is_held_out() -> None:
    with pytest.raises(ValueError):
        gen.generate("device_fleet", "train", 10, 7, DATASETS)
    assert gen.HELD_OUT == ("device_fleet",) and "device_fleet" not in gen.TRAIN_PACKS


def test_expectations_come_from_the_generated_rows() -> None:
    for s in gen.generate("ecommerce", "test", 350, 61, DATASETS):
        case = s["case"]
        if s["kind"] == "cancel_approve":
            oid = gen.ID_PATTERN.findall(case["prompt"])[0]
            row = s["rows"]["cancel_order"][oid]
            assert case["approval"] == "approve" and case["expect"]["tool_calls"] == [
                "cancel_order"
            ]
            assert row["refund"] in case["expect"]["answer_contains"]
        if s["kind"] == "cancel_deny":
            assert case["approval"] == "deny" and "answer_regex" in case["expect"]
        if s["kind"] == "conditional_no_cancel":
            oid = gen.ID_PATTERN.findall(case["prompt"])[0]
            assert s["rows"]["lookup_order"][oid]["status"] == "shipped"
            assert case["expect"]["tool_calls"] == ["lookup_order"]
    for s in gen.generate("logistics", "test", 350, 61, DATASETS):
        if s["kind"] == "reroute_blocked":
            sid = gen.ID_PATTERN.findall(s["case"]["prompt"])[0]
            alt = s["rows"]["track"][sid]["alternate_hub"]
            assert alt in gen.BLOCKED_HUBS and "reroute" not in s["case"]["expect"]["tool_calls"]


def test_answer_patterns() -> None:
    deny = {"answer_regex": gen.NOT_DONE}
    for text in ("The cancellation was not approved, so nothing changed.",
                 "I'm sorry, the approver declined it.", "That request was denied.",
                 "The reroute wasn't approved; the shipment stays where it is."):  # fmt: skip
        assert answer_match(deny, text)[0], text
    assert not answer_match(deny, "Done: order QF-30001 is cancelled and 96.00 refunded.")[0]
    missing = {"answer_regex": gen.NOT_FOUND}
    for text in ("I couldn't find an order with that id.", "No order QF-30001 was found.",
                 "That shipment is not in our system.",
                 "I could not locate TG-610001."):  # fmt: skip
        assert answer_match(missing, text)[0], text
    # a yearless date and a full one both match the MM-DD form the generator writes
    assert answer_match({"answer_contains": ["10-18", "10:00"]}, "on October 18 from 10am")[0]
    assert answer_match({"answer_contains": ["10-18"]}, "arrives 2026-10-18")[0]


def test_tools_overlay_adds_rows() -> None:
    s = [x for x in gen.generate("ecommerce", "train", 40, 7, DATASETS) if x["rows"]][:5]
    text = gen.tools_overlay(
        (DATASETS / "ecommerce" / "tools.yaml").read_text(), [x["rows"] for x in s]
    )
    tools = {t["name"]: t for t in yaml.safe_load(text)}
    for x in s:
        for tool, table in x["rows"].items():
            assert set(table) <= set(tools[tool]["table"])
    assert "QF-20430" in tools["cancel_order"]["table"]  # the pack's own rows stay
    with pytest.raises(KeyError):
        gen.tools_overlay("- name: a\n  table: {}\n", [{"nope": {}}])


def test_wilson_and_meter() -> None:
    assert sc.wilson(0, 0) == (0.0, 0.0)
    lo, hi = sc.wilson(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-3) and hi == pytest.approx(0.5962, abs=1e-3)
    m = sc.Meter(1.0, 0.01)
    assert m.try_reserve("a", 40) and m.try_reserve("b", 40)
    assert not m.try_reserve("c", 40)  # 0.4 + 0.4 + 0.4 > 1.0
    m.settle("a", 0.05, 40)
    m.settle("b", 0.05, 40)
    assert m.per_case() == pytest.approx(0.0025)  # never below a quarter of the prior
    assert m.try_reserve("c", 40)
    m2 = sc.Meter(1.0, 0.004)
    m2.settle("a", 0.05, 40)
    assert m2.per_case() == 0.004  # the prior until two shards are done
    m2.settle("b", 0.05, 40)
    assert m2.per_case() == pytest.approx(1.5 * 0.1 / 80)


def test_shards_interleave_packs_and_keep_ids() -> None:
    scen = [{"pack": p, "case": {"id": f"{p}_{i}"}} for p in ("a", "b") for i in range(50)]
    shards = sc.shards_of(scen, 24)
    assert [s["id"] for s in shards[:4]] == ["a-00001", "b-00001", "a-00002", "b-00002"]
    assert sum(len(s["scenarios"]) for s in shards) == 100


def fixture_scenarios() -> list[dict[str, Any]]:
    """Generated cases that the fake provider can answer: knowledge questions with the golden
    fixture of their fact (they pass), and cancellations scripted on another order (the
    expected amount is the generated order's, so they fail answer_match)."""
    out = []
    for s in gen.generate("ecommerce", "train", 160, 7, DATASETS):
        if s["kind"] == "kb":
            fact = [t for t in s["case"]["tags"] if t.startswith("kb_")][0]
            s["case"]["fixture"] = fact
            out.append(s)
        elif (
            s["kind"] == "cancel_approve"
            and "129.00" not in s["case"]["expect"]["answer_contains"]
            and len([x for x in out if x["kind"] == s["kind"]]) < 6
        ):
            s["case"]["fixture"] = "cancel_approved_qf20430"
            out.append(s)
    return out


@pytest.mark.anyio
async def test_scaled_curation_resumes_caps_and_finalizes(tmp_path: Path) -> None:
    h = Harness(tune_settings(tmp_path))
    t = h.tune
    scen = fixture_scenarios()
    kb = sum(1 for s in scen if s["kind"] == "kb")
    assert kb >= 6
    state = tmp_path / "state"
    try:
        job = sc.Job(mode="curate", state=state, model="fake-small", scenarios=scen,
                     variants=1, shard_size=8, max_shards=1)  # fmt: skip
        first = await sc.run(t, job, say=lambda _line: None)
        assert first["shards"] == 1 and first["remaining_shards"] >= 1
        done = sc.load_done(state)
        rec = next(iter(done.values()))
        assert len(rec["cases"]) == 16  # 8 generated, 8 paraphrased (fake rewrites)
        assert (state / "shards" / f"{rec['shard']}.para.json").is_file()
        before = (state / "shards" / f"{rec['shard']}.done.json").stat().st_mtime_ns
        job.max_shards = None
        second = await sc.run(t, job, say=lambda _line: None)
        assert (state / "shards" / f"{rec['shard']}.done.json").stat().st_mtime_ns == before
        assert second["remaining_shards"] == 0 and second["cases"] == 2 * len(scen)
        # hash-vector retrieval misses a reworded question now and then (citations fail)
        assert second["by_kind"]["kb"]["passed"] >= kb
        assert second["by_kind"]["cancel_approve"]["passed"] == 0
        assert second["examples"] == second["by_kind"]["kb"]["passed"]
        # a different job in the same state is refused
        with pytest.raises(ValueError):
            await sc.run(t, sc.Job(mode="curate", state=state, model="fake-small",
                                   scenarios=scen[:3]), say=lambda _l: None)  # fmt: skip
        excluded = {gen._norm(scen[0]["case"]["prompt"])}
        out = sc.finalize(t, state, "p6x", exclude_prompts=excluded, sizes=[3], max_per_prompt=50)
        assert set(out) == {"p6x", "p6x_3"}
        manifest, _ = t.manifest("dataset", "p6x")
        assert manifest["teachers"] == {"fake-small": manifest["counts"]["train"] +
                                        manifest["counts"]["val"]}  # fmt: skip
        assert manifest["counts"]["by_teacher"] == {"fake-small": manifest["counts"]["train"]}
        rows = [
            json.loads(x) for x in Path(manifest["files"]["train"]["path"]).read_text().splitlines()
        ]
        assert all(r["meta"]["teacher"] == "fake-small" and r["meta"]["kind"] == "kb" for r in rows)
        for r in rows:
            users = [gen._norm(m["content"]) for m in r["messages"] if m["role"] == "user"]
            assert not set(users) & excluded
        small, _ = t.manifest("dataset", "p6x_3")
        assert small["counts"]["train"] == 3 and small["counts"]["val"] == manifest["counts"]["val"]
        sub = [
            json.loads(x) for x in Path(small["files"]["train"]["path"]).read_text().splitlines()
        ]
        assert [r["id"] for r in sub] == [r["id"] for r in rows[:3]]  # nested
    finally:
        await h.aclose()


@pytest.mark.anyio
async def test_spend_cap_stops_before_the_limit(tmp_path: Path) -> None:
    h = Harness(tune_settings(tmp_path))
    try:
        # two workers: the first shard reserves 0.04, the second would pass 0.05
        job = sc.Job(mode="eval", state=tmp_path / "s", model="fake-small", workers=2,
                     scenarios=fixture_scenarios(), shard_size=4, max_usd=0.05,
                     prior_usd_per_case=0.01)  # fmt: skip
        out = await sc.run(h.tune, job, say=lambda _l: None)
        assert out["shards"] == 1 and out["stopped"].startswith("spend cap")
        # a shard whose estimate alone passes the cap never starts
        job = sc.Job(mode="eval", state=tmp_path / "t", model="fake-small",
                     scenarios=fixture_scenarios(), shard_size=4, max_usd=0.05,
                     prior_usd_per_case=0.02)  # fmt: skip
        out = await sc.run(h.tune, job, say=lambda _l: None)
        assert out["shards"] == 0 and out["stopped"].startswith("spend cap")
    finally:
        await h.aclose()


@pytest.mark.anyio
async def test_testrun_failed_by_all(tmp_path: Path) -> None:
    h = Harness(tune_settings(tmp_path))
    scen = fixture_scenarios()
    try:
        for name in ("a", "b"):
            job = sc.Job(mode="eval", state=tmp_path / name, model="fake-small", scenarios=scen,
                         shard_size=8)  # fmt: skip
            out = await sc.run(h.tune, job, say=lambda _l: None)
            assert out["examples"] == 0 and out["all"]["n"] == len(scen)
        bad = sc.failed_by_all([tmp_path / "a", tmp_path / "b"])
        cancels = {f"ecommerce/{s['case']['id']}" for s in scen if s["kind"] == "cancel_approve"}
        assert cancels <= bad
        failed_a = {f"ecommerce/{c['case_id']}" for r in sc.load_done(tmp_path / "a").values()
                    for c in r["cases"] if c["status"] != "passed"}  # fmt: skip
        assert bad == failed_a  # the fake answers the same both times
        assert bad <= sc.failed_by_all([tmp_path / "a"])
        assert sc.summary(tmp_path / "a")["by_pack"]["ecommerce"]["ci95"][0] > 0
    finally:
        await h.aclose()
