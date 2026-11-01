"""Dataset packs: discovery, the validator, and every pack's golden suite on the fake provider.

The golden run here is the CI gate for packs (spec/packs.md): each domain pack's golden set
must pass on the `fake` provider, and each case's `checks` (router rules, retrieved memory)
must hold on the stored events of the turn that ran it.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from harnesswalk import packs
from harnesswalk.__main__ import main
from harnesswalk.config.loader import load_settings
from harnesswalk.ops.eval.golden import load_golden
from harnesswalk.ops.eval.offline import scratch_tenant
from harnesswalk.runtime import Harness
from tests.spec.support import ROOT

ALL = packs.discover(ROOT)
DOMAIN = [p.id for p in ALL if p.tier == "domain"]


def test_discovery_finds_the_starter_and_five_domain_packs() -> None:
    assert ALL[0].id == "_starter"
    assert sorted(DOMAIN) == ["ecommerce", "finance", "healthcare", "logistics", "sports"]
    for p in ALL:
        summary = p.summary()
        assert summary["licence"] == "CC0-1.0" and summary["tools"], p.id


def test_cards_read_the_same_fields_as_the_ops_index() -> None:
    """GET /v1/packs (harnesswalk/ops/pack_index.py) and packs.py read one card: the index's
    fields come out the same from both, so the integration pass can swap one for the other."""
    from harnesswalk.ops import pack_index

    settings = load_settings(ROOT, provider="fake", fixture="plain_answer", env={})
    index = pack_index.discover(settings)
    for p in ALL:
        mine = p.summary()
        theirs = index[p.id].summary(default="_starter")
        for key in ("id", "title", "domain", "feature", "prompts", "counts"):
            assert mine[key] == theirs[key], (p.id, key)
        assert mine["feature"] and len(mine["prompts"]) >= 4


def test_packs_under_profile_roots_validate(capsys) -> None:
    """The conformance profile adds spec/conformance/packs to packs.roots; its pack has no
    files of its own (the profile gives the content inline), so it is a profile-tier pack."""
    profile = "spec/conformance/profile.yaml"
    assert main(["packs", "validate", "conformance_alt", "--profile", profile]) == 0
    assert "1 of 1 pack(s) valid" in capsys.readouterr().out
    assert main(["packs", "validate", "conformance_alt"]) == 2  # not under the default roots
    roots = [ROOT / "datasets", ROOT / "spec" / "conformance" / "packs"]
    alt = packs.get_pack("conformance_alt", roots=roots)
    assert alt.tier == "profile" and packs.validate(alt, config_root=ROOT) == []


@pytest.mark.parametrize("pack_id", [p.id for p in ALL])
def test_every_pack_is_valid(pack_id: str) -> None:
    issues = packs.validate(packs.get_pack(pack_id, ROOT), ALL, config_root=ROOT)
    assert [str(i) for i in issues] == []


def _copy(tmp_path: Path, pack_id: str = "healthcare") -> packs.Pack:
    target = tmp_path / "datasets" / pack_id
    shutil.copytree(ROOT / "datasets" / pack_id, target)
    return packs.load_pack(target)


def _checks(pack: packs.Pack) -> set[str]:
    return {i.check for i in packs.validate(pack, ALL, config_root=ROOT)}


def test_validator_flags_schema_licence_and_provenance(tmp_path: Path) -> None:
    pack = _copy(tmp_path)
    meta = dict(pack.meta, licence="CC-BY-4.0")
    del meta["synthetic"]
    (pack.path / "pack.yaml").write_text(json.dumps(meta))
    (pack.path / "provenance.md").unlink()
    issues = packs.validate(packs.load_pack(pack.path), ALL, config_root=ROOT)
    text = "\n".join(map(str, issues))
    assert "licence" in text and "synthetic" in text and "provenance.md is missing" in text


def test_validator_flags_pii_in_content_but_not_test_values_in_prompts(tmp_path: Path) -> None:
    pack = _copy(tmp_path)
    assert "pii" not in _checks(pack)  # the golden prompts carry BH- numbers on purpose
    doc = pack.path / "docs" / "patient_faq.md"
    doc.write_text(doc.read_text() + "\nCall Dana on 416 778 2309 or quote BH-1111-2222-Z.\n")
    found = [i.message for i in packs.validate(pack, ALL, config_root=ROOT) if i.check == "pii"]
    assert any("BH-1111-2222-Z" in m for m in found) and any("phone" in m for m in found)


def test_another_packs_patterns_apply_too(tmp_path: Path) -> None:
    pack = _copy(tmp_path, "sports")
    doc = next((pack.path / "docs").glob("*.md"))
    doc.write_text(doc.read_text() + "\nThe sponsor's account is LP-1234-5678-90.\n")
    assert "pii" in _checks(pack)  # the finance pack's account-number pattern


def test_validator_flags_golden_problems(tmp_path: Path) -> None:
    pack = _copy(tmp_path)
    lines = (pack.path / "golden.jsonl").read_text().splitlines()
    first = json.loads(lines[0])
    first["fixture"] = "no_such_fixture"
    second = json.loads(lines[1])
    second["expect"]["tool_calls"] = ["teleport"]
    second["expect"]["citations"] = [{"doc": "missing_doc"}]
    third = json.loads(lines[2])
    third["checks"] = {"router_rules": ["nope"]}
    lines[:3] = [json.dumps(first), json.dumps(second), json.dumps(third)]
    (pack.path / "golden.jsonl").write_text("\n".join(lines) + "\n")
    golden = [i.message for i in packs.validate(pack, ALL, config_root=ROOT) if i.check == "golden"]
    joined = "\n".join(golden)
    for needle in ("no_such_fixture", "teleport", "missing_doc", "unknown router rule"):
        assert needle in joined, needle


def test_validator_flags_tools_size_and_counts(tmp_path: Path, monkeypatch) -> None:
    pack = _copy(tmp_path)
    tools = (pack.path / "tools.yaml").read_text().replace("action_class: read", "", 1)
    (pack.path / "tools.yaml").write_text(tools)
    for skill in sorted((pack.path / "skills").glob("*.md"))[:3]:
        skill.unlink()
    monkeypatch.setattr(packs, "MAX_BYTES", 1000)
    checks = _checks(pack)
    assert {"tools", "size", "files"} <= checks


def test_cli_list_and_validate(capsys) -> None:
    assert main(["packs", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {r["id"] for r in listed} >= set(DOMAIN)
    assert {"id", "title", "domain", "feature", "prompts", "counts"} <= set(listed[0])
    assert main(["packs", "validate", "sports"]) == 0
    assert "1 of 1 pack(s) valid" in capsys.readouterr().out
    assert main(["packs", "validate", "no_such_pack"]) == 2


def test_cli_ingest_one_pack(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HARNESSWALK_DATA_DIR", str(tmp_path / "data"))
    assert main(["packs", "ingest", "finance", "--provider", "fake"]) == 0
    out = capsys.readouterr().out
    docs = packs.get_pack("finance", ROOT).docs()
    assert sum(d.suffix == ".pdf" for d in docs) == 2
    # Every document, both PDFs included, is indexed (the PDFs prove PDF ingest).
    assert f"pack finance: {len(docs)} documents," in out and "chunks (indexed)" in out


async def _golden(pack_id: str, data_dir: Path) -> tuple[dict, list[dict], list[str]]:
    settings = load_settings(
        ROOT,
        provider="fake",
        fixture="plain_answer",
        overrides={"dataset": pack_id, "sandbox.level": "inprocess"},
        data_dir=data_dir,
        env={},
    )
    h = Harness(settings)
    try:
        run = h.evals.offline.start("dev", pack_id, "golden", by="test")
        await h.evals.offline.wait(run["run_id"])
        done = h.evals.store.run("dev", run["run_id"])
        cases = h.evals.store.cases(run["run_id"])
        tenant = scratch_tenant(run["run_id"])
        raw = {c.id: c.raw for c in load_golden(ROOT / "datasets" / pack_id / "golden.jsonl")}
        problems = []
        for c in cases:
            checks = raw[c["case_id"]].get("checks")
            if not checks or not c.get("turn_id"):
                continue
            events = h.tracer.get_turn(tenant, c["turn_id"])
            for p in packs.check_case(checks, events, lambda ref: h.tracer.get_detail(tenant, ref)):
                problems.append(f"{c['case_id']}: {p}")
        return done, cases, problems
    finally:
        await h.aclose()


@pytest.mark.parametrize("pack_id", DOMAIN)
def test_golden_suite_passes_on_the_fake_provider(pack_id: str, tmp_path: Path) -> None:
    done, cases, problems = asyncio.run(_golden(pack_id, tmp_path / "data"))
    failed = {
        c["case_id"]: {k: v["detail"] for k, v in c["scores"].items() if not v["pass"]}
        or c.get("error")
        for c in cases
        if c["status"] != "passed"
    }
    assert failed == {}
    assert done["totals"]["passed"] == done["totals"]["cases"] >= 20
    assert problems == []


async def _case_events(pack_id: str, case_ids: list[str], data_dir: Path):
    settings = load_settings(
        ROOT,
        provider="fake",
        fixture="plain_answer",
        overrides={"dataset": pack_id, "sandbox.level": "inprocess"},
        data_dir=data_dir,
        env={},
    )
    h = Harness(settings)
    try:
        run = h.evals.offline.start("dev", pack_id, "golden", case_ids=case_ids, by="test")
        await h.evals.offline.wait(run["run_id"])
        tenant = scratch_tenant(run["run_id"])
        out = {}
        for c in h.evals.store.cases(run["run_id"]):
            events = h.tracer.get_turn(tenant, c["turn_id"])
            details = {
                e["detail_ref"]: h.tracer.get_detail(tenant, e["detail_ref"])
                for e in events
                if e.get("detail_ref")
            }
            out[c["case_id"]] = (events, details.get)
        return out
    finally:
        await h.aclose()


def test_check_case_reads_router_rules_and_retrieved_memory(tmp_path: Path) -> None:
    sports = load_golden(ROOT / "datasets" / "sports" / "golden.jsonl")
    lookup = next(
        c
        for c in sports
        if (c.raw.get("checks") or {}).get("router_rules") == ["default", "lookup"]
    )
    got = asyncio.run(_case_events("sports", [lookup.id], tmp_path / "s"))
    events, detail = got[lookup.id]
    assert packs.check_case({"router_rules": ["default", "lookup"]}, events, detail) == []
    wrong = packs.check_case({"router_rules": ["default", "default"]}, events, detail)
    assert wrong and "router rules" in wrong[0]

    finance = load_golden(ROOT / "datasets" / "finance" / "golden.jsonl")
    ids = [c.id for c in finance]
    recall = next(c for c in finance if (c.raw.get("checks") or {}).get("retrieved"))
    remember = ids[ids.index(recall.id) - 1]
    got = asyncio.run(_case_events("finance", [remember, recall.id], tmp_path / "f"))
    events, detail = got[recall.id]
    assert packs.check_case(recall.raw["checks"], events, detail) == []
    missing = packs.check_case({"retrieved": {"semantic": "no such preference"}}, events, detail)
    assert missing and "semantic context" in missing[0]
    # Alone, the recall case has nothing to recall: the pair is what proves the writer.
    alone = asyncio.run(_case_events("finance", [recall.id], tmp_path / "f2"))
    events, detail = alone[recall.id]
    assert packs.check_case(recall.raw["checks"], events, detail) != []


def test_golden_results_do_not_depend_on_other_packs_in_the_data_dir(tmp_path: Path) -> None:
    """A pack's golden result is a function of the pack and its artifacts only (P5 gate). Two
    suites share one data directory, then the first runs again: same statuses, same citations.
    Before each pack had its own FTS5 table, BM25 statistics over every pack's documents
    reordered the starter's hits once finance was ingested, and `trailhead_cited` failed."""
    data = tmp_path / "data"

    def outcome(cases: list[dict]) -> dict[str, tuple]:
        return {
            c["case_id"]: (c["status"], [(x.get("doc"), x.get("chunk")) for x in
                                         (c.get("actual") or {}).get("citations") or []])
            for c in cases
        }  # fmt: skip

    first, first_cases, _ = asyncio.run(_golden("_starter", data))
    other, _, _ = asyncio.run(_golden("finance", data))
    again, again_cases, _ = asyncio.run(_golden("_starter", data))
    assert other["totals"]["failed"] == 0
    assert first["totals"]["failed"] == again["totals"]["failed"] == 0
    assert outcome(again_cases) == outcome(first_cases)
    assert again["regressions"] == 0
