"""P2 pack format additions: multi-turn golden cases, `roles`, the long-session `checks`, the
`skills/policy.md` convention and `provenance.generator` (spec/packs.md, evaluator.md "P2
additions"). The runner tests play a small golden set of their own on the starter pack."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from harnesswalk import packs
from harnesswalk.config.loader import load_settings
from harnesswalk.ops.eval.golden import GoldenError, load_golden, normalize
from harnesswalk.ops.eval.scorers import running_summary, session_actual
from harnesswalk.runtime import Harness
from tests.spec.support import ROOT

ALL = list(packs.discover(ROOT).values())


def _resp(text: str | None = None, call: tuple[str, dict] | None = None) -> dict:
    out: dict = {"usage": {"input_tokens": 100, "output_tokens": 10}}
    if text is not None:
        out["text"] = text
    if call is not None:
        out["tool_calls"] = [{"name": call[0], "arguments": call[1]}]
    return out


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# -- loading ------------------------------------------------------------------------------------


def test_a_multi_turn_case_loads_with_turns_fixture_list_and_roles() -> None:
    case = normalize(
        {
            "id": "mt",
            "turns": [
                {"user": "first", "expect": {"tool_calls": []}},
                {"user": "second", "checks": {"compacted": True, "summary": "A-1003"}},
            ],
            "fixture": ["one", "two"],
            "roles": ["viewer"],
            "expect": {"answer_contains": "done"},
        },
        "t",
    )
    assert case.multi_turn and case.prompt == "first" and case.roles == ["viewer"]
    assert [t.user for t in case.turns] == ["first", "second"]
    assert case.turns[1].checks == {"compacted": True, "summary": "A-1003"}
    assert case.fixture_for(1) == "two" and case.expect["answer_contains"] == ["done"]
    one = normalize({"id": "o", "turns": [{"user": "a"}, {"user": "b"}], "fixture": "f"}, "t")
    assert one.fixture_for(0) == one.fixture_for(1) == "f"


@pytest.mark.parametrize(
    "bad",
    [
        {"turns": []},
        {"turns": [{"user": ""}]},
        {"turns": [{"user": "a", "approval": "approve"}]},
        {"turns": [{"user": "a"}], "prompt": "p"},
        {"turns": [{"user": "a"}, {"user": "b"}], "fixture": ["only_one"]},
        {"prompt": "p", "fixture": ["a"]},
        {"prompt": "p", "roles": []},
        {"prompt": "p", "roles": ["Not A Role"]},
        {"prompt": "p", "checks": {"compacted": "yes"}},
        {"prompt": "p", "checks": {"summary": ""}},
        {"turns": [{"user": "a", "expect": {"nope": 1}}]},
    ],
)
def test_bad_multi_turn_cases_do_not_load(bad: dict) -> None:
    with pytest.raises(GoldenError):
        normalize({"id": "c", **bad}, "t")


def test_session_actual_joins_turns_and_keeps_the_last_reply() -> None:
    a = {
        "reply": "one",
        "exit": "stop",
        "status": "ok",
        "path": ["gateway"],
        "citations": [],
        "tool_calls": ["lookup_order"],
        "cost": {"usd": 0.5, "input_tokens": 3},
        "errors": [],
    }
    b = {"reply": "two", "exit": "stop", "status": "ok", "path": ["trace"],
         "citations": [{"doc": "d"}], "tool_calls": [],
         "cost": {"usd": 0.25, "input_tokens": 2}, "errors": []}  # fmt: skip
    got = session_actual([a, b])
    assert got["reply"] == "two" and got["path"] == ["gateway", "trace"]
    assert got["tool_calls"] == ["lookup_order"] and got["citations"] == [{"doc": "d"}]
    assert got["cost"] == {"usd": 0.75, "input_tokens": 5} and got["turns"] == [a, b]
    detail = {"messages": [{"role": "user", "content": '<retrieved_data kind="summary">\nS\n'
                            "</retrieved_data>"}]}  # fmt: skip
    assert running_summary(detail) == "S" and running_summary({}) == ""


# -- running ------------------------------------------------------------------------------------

LONG = "The backpack's hip belt and the frame sheet are described below in detail. " * 60


def _cases(tmp: Path) -> Path:
    """A golden set on the starter pack: a three-turn case, a long one that compacts, and a
    viewer whose cancel is denied by policy whatever the model asks."""
    fx = tmp / "fixtures"
    _write_jsonl(
        fx / "mt_three.jsonl",
        [
            {"responses": [_resp(call=("lookup_order", {"order_id": "A-1001"})),
                           _resp("A-1001 has shipped.")]},
            {"responses": [_resp("You asked about A-1001 a moment ago.")]},
            {"responses": [_resp(call=("lookup_order", {"order_id": "A-1003"})),
                           _resp("A-1003 was delivered.")]},
        ],
    )  # fmt: skip
    _write_jsonl(
        fx / "mt_long.jsonl", [{"responses": [_resp(f"Noted, part {n}.")]} for n in range(7)]
    )
    _write_jsonl(fx / "mt_viewer.jsonl", [
        {"responses": [_resp(call=("cancel_order", {"order_id": "A-1002"})),
                       _resp("I cannot cancel it: your role does not allow that.")]},
    ])  # fmt: skip
    long_turns = [
        {"user": f"Part {n} of my notes. Gate code ZX-{n}{n}. {LONG}"} for n in range(1, 7)
    ]
    long_turns.append(
        {"user": "What was the gate code in part 1?",
         "checks": {"compacted": True, "summary": "Gate code ZX-11"}}
    )  # fmt: skip
    cases = [
        {"id": "three_turns", "fixture": "mt_three", "turns": [
            {"user": "Where is order A-1001?", "expect": {"tool_calls": ["lookup_order"],
                                                          "answer_contains": ["shipped"]}},
            {"user": "Which order did I just ask about?", "expect": {"tool_calls": []}},
            {"user": "And A-1003?", "expect": {"answer_contains": ["delivered"]}},
        ], "expect": {"tool_calls": ["lookup_order", "lookup_order"],
                      "answer_contains": ["delivered"]}},
        {"id": "long_session", "fixture": "mt_long", "turns": long_turns,
         "expect": {"answer_contains": ["part 6"]}, "checks": {"compacted": True}},
        {"id": "viewer_cancel", "prompt": "Cancel A-1002.", "fixture": "mt_viewer",
         "roles": ["viewer"], "expect": {"tool_calls": ["cancel_order"],
                                         "answer_contains": ["role"]}},
    ]  # fmt: skip
    _write_jsonl(tmp / "golden.jsonl", cases)
    return tmp


async def _run(
    tmp: Path, cases: Path, suite: str = "golden", model: str | None = None
) -> tuple[dict, dict[str, dict]]:
    settings = load_settings(
        ROOT,
        provider="fake",
        fixture="plain_answer",
        overrides={
            "dataset": "_starter",
            "sandbox.level": "inprocess",
            "evals.golden_path": str(cases / "golden.jsonl"),
            "models.providers.fake.fixture_dir": str(cases / "fixtures"),
            "policy.roles.viewer": {"allow_action_classes": ["read"]},
        },
        data_dir=tmp / "data",
        env={},
    )
    h = Harness(settings)
    try:
        run = h.evals.offline.start("dev", "_starter", suite, model=model, by="test")
        await h.evals.offline.wait(run["run_id"])
        done = h.evals.store.run("dev", run["run_id"])
        return done, {c["case_id"]: c for c in h.evals.store.cases(run["run_id"])}
    finally:
        await h.aclose()


def test_multi_turn_cases_run_in_one_session_and_score_per_turn(tmp_path: Path) -> None:
    done, cases = asyncio.run(_run(tmp_path, _cases(tmp_path / "cases")))
    bad = {k: (c["status"], {n: s["detail"] for n, s in c["scores"].items() if not s["pass"]},
               c.get("error")) for k, c in cases.items() if c["status"] != "passed"}  # fmt: skip
    assert bad == {}
    three = cases["three_turns"]
    assert {"turn1.tool_calls", "turn1.answer_match", "turn2.tool_calls", "turn3.answer_match",
            "tool_calls", "answer_match", "pii_leak"} <= set(three["scores"])  # fmt: skip
    assert len(three["turn_ids"]) == 3 and three["turn_id"] == three["turn_ids"][-1]
    assert three["actual"]["tool_calls"] == ["lookup_order", "lookup_order"]
    assert [t["reply"] for t in three["actual"]["turns"]][1].startswith("You asked")
    long = cases["long_session"]
    assert long["scores"]["checks"]["pass"] and long["scores"]["turn7.checks"]["pass"]
    assert done["totals"]["passed"] == 3


def test_a_viewer_case_is_denied_by_the_roles_rule(tmp_path: Path) -> None:
    _done, cases = asyncio.run(_run(tmp_path, _cases(tmp_path / "cases")))
    viewer = cases["viewer_cancel"]
    assert viewer["status"] == "passed"
    assert "approval" not in viewer["actual"]["path"]  # denied before any human is asked


def test_checks_fail_when_the_summary_lacks_the_fact(tmp_path: Path) -> None:
    cases_dir = _cases(tmp_path / "cases")
    rows = [json.loads(line) for line in (cases_dir / "golden.jsonl").read_text().splitlines()]
    rows[1]["turns"][-1]["checks"]["summary"] = "a fact nobody said"
    _write_jsonl(cases_dir / "golden.jsonl", rows)
    _done, cases = asyncio.run(_run(tmp_path, cases_dir))
    score = cases["long_session"]["scores"]["turn7.checks"]
    assert not score["pass"] and "summary has no" in score["detail"]


# -- the validator ------------------------------------------------------------------------------


def _copy(tmp_path: Path, pack_id: str = "_starter") -> packs.Pack:
    target = tmp_path / "datasets" / pack_id
    shutil.copytree(ROOT / "datasets" / pack_id, target)
    return packs.load_pack(target)


def _issues(pack: packs.Pack) -> list[str]:
    return [str(i) for i in packs.validate(pack, ALL, config_root=ROOT)]


def test_the_validator_checks_each_turn_against_its_fixture_turn(tmp_path: Path) -> None:
    pack = _copy(tmp_path)
    cases = _cases(tmp_path / "cases")
    for f in (cases / "fixtures").iterdir():
        shutil.copy(f, pack.path / "fixtures" / "fake" / f.name)
    rows = [json.loads(line) for line in (cases / "golden.jsonl").read_text().splitlines()]
    with (pack.path / "golden.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rows[0]) + "\n")
    assert not [i for i in _issues(pack) if "three_turns" in i]
    rows[0]["turns"][1]["expect"]["tool_calls"] = ["lookup_order"]
    rows[0]["id"] = "three_turns_bad"
    rows[0]["expect"]["tool_calls"] = ["lookup_order"]
    with (pack.path / "golden.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rows[0]) + "\n")
    issues = [i for i in _issues(pack) if "three_turns_bad" in i]
    assert any("turn 2: expects tool calls ['lookup_order']" in i for i in issues)
    assert any("expects session tool calls" in i for i in issues)


def test_a_policy_skill_must_be_read_by_a_golden_case(tmp_path: Path) -> None:
    pack = _copy(tmp_path)
    (pack.path / "skills" / "policy.md").write_text(
        "---\nname: policy\ndescription: What the shop may promise.\n---\nNo refunds in cash.\n"
    )
    assert any("policy.md is read by no golden case" in i for i in _issues(pack))
    _write_jsonl(
        pack.path / "fixtures" / "fake" / "reads_policy.jsonl",
        [{"responses": [_resp(call=("read_skill", {"name": "policy"})), _resp("No cash.")]}],
    )
    with (pack.path / "golden.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "reads_policy", "prompt": "Cash refund?",
                             "fixture": "reads_policy",
                             "expect": {"tool_calls": ["read_skill"]}}) + "\n")  # fmt: skip
    assert not any("policy" in i for i in _issues(pack))


def test_provenance_may_name_its_generator(tmp_path: Path) -> None:
    import yaml

    pack = _copy(tmp_path)
    card = yaml.safe_load((pack.path / "pack.yaml").read_text())
    card["provenance"] = {
        "card": "provenance.md",
        "generator": {"model": "claude-haiku-5-5", "date": "2026-11-08", "prompt_hash": "0a1b2c3d"},
    }
    (pack.path / "pack.yaml").write_text(yaml.safe_dump(card, sort_keys=False))
    pack = packs.load_pack(pack.path)
    assert _issues(pack) == [] and pack.generator["model"] == "claude-haiku-5-5"
    assert pack.provenance_card == "provenance.md"
    card["provenance"] = {"card": "provenance.md", "generator": {"model": "x"}}  # no date
    (pack.path / "pack.yaml").write_text(yaml.safe_dump(card, sort_keys=False))
    assert any("schema" in i for i in _issues(packs.load_pack(pack.path)))
    card["provenance"] = {"card": "missing.md"}
    (pack.path / "pack.yaml").write_text(yaml.safe_dump(card, sort_keys=False))
    assert any("missing.md is missing" in i for i in _issues(packs.load_pack(pack.path)))


def test_the_golden_loader_reads_every_shipped_case() -> None:
    for p in ALL:
        if (p.path / "golden.jsonl").is_file():
            assert load_golden(p.path / "golden.jsonl")


def test_multi_turn_cases_are_model_graded_on_the_model_suite(tmp_path: Path) -> None:
    """P0b: per-turn and session scores of a multi-turn case use model grading too."""
    done, cases = asyncio.run(
        _run(tmp_path, _cases(tmp_path / "cases"), suite="model", model="fake-small")
    )
    assert done["suite"] == "model" and done["totals"]["passed"] == 3
    three = cases["three_turns"]
    assert three["scores"]["turn1.tool_calls"]["detail"].startswith("ordered:")
    assert three["scores"]["answer_match"]["detail"].endswith("(normalized)")
