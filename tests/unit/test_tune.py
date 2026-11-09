"""The tuning loop (0.2.0, spec/contracts/tuning.md): the canary rule, the scripted students,
the models.yaml edits, the gate arithmetic, curation's export, tune artifacts, the diagnose
lever, the HTTP routes in public mode and the CLI, all on the fake provider and fake trainer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from harnesswalk.__main__ import main
from harnesswalk.config.loader import validate_split
from harnesswalk.control.http_api import create_app
from harnesswalk.execution.providers import ProviderRequest, ToolSchema
from harnesswalk.execution.providers.fake import styled
from harnesswalk.execution.router import Router, RouteRequest, bucket_of, with_canary
from harnesswalk.ops.tune import (
    TuneError,
    canary_gate,
    cell_summary,
    gate_verdict,
    parse_ref,
    render_comparison,
)
from harnesswalk.ops.tune.curate import keeps_ids, parse_rewrites, rewrite_id, to_chat
from harnesswalk.ops.tune.models_edit import canary_of, with_alias
from harnesswalk.ops.tune.models_edit import with_canary as edit_canary
from harnesswalk.runtime import Harness
from harnesswalk.types import ConfigError, Message
from tests.public.support import public_settings
from tests.spec.support import ROOT
from tests.unit.support import settings

MODELS = {
    "base": {"provider": "fake"},
    "cand": {"provider": "fake"},
    "far": {"provider": "ollama"},
    "fake-small": {"provider": "fake"},
}
CANARY = {
    "when": {"pack": ["shop"]},
    "split": {"base_alias": "base", "candidate_alias": "cand", "fraction": 0.5},
}
RULES = [
    {"id": "public", "when": {"mode": "public"}, "use": ["fake-small"]},
    {"id": "default", "when": {}, "use": ["fake-small"]},
]


def router(canary: dict[str, Any] | None = None, ok: tuple[str, ...] = ("fake",)) -> Router:
    return Router(with_canary(RULES, canary or CANARY), MODELS, lambda p: p in ok)


def req(turn_id: str, pack: str = "shop", **kw: Any) -> RouteRequest:
    return RouteRequest(step=1, step_kind="first", pack=pack, turn_id=turn_id, **kw)


# -- the canary rule ------------------------------------------------------------------------------


def test_canary_rule_sits_after_public_and_splits_by_turn_id() -> None:
    rules = with_canary(RULES, CANARY)
    assert [r["id"] for r in rules] == ["public", "canary", "default"]
    r = router()
    arms = {}
    for n in range(200):
        route = r.route(req(f"turn-{n}"))
        assert route.rule == "canary"
        want = "candidate" if bucket_of(f"turn-{n}") < 0.5 else "base"
        assert route.arm == want and route.model == ("cand" if want == "candidate" else "base")
        assert route.alternatives == ["base" if want == "candidate" else "cand"]
        arms[want] = arms.get(want, 0) + 1
    assert 70 < arms["candidate"] < 130  # about half, and the same every run
    assert r.route(req("turn-7")).arm == r.route(req("turn-7")).arm


def test_canary_fraction_ends_and_other_packs() -> None:
    none = {**CANARY, "split": {**CANARY["split"], "fraction": 0}}
    every = {**CANARY, "split": {**CANARY["split"], "fraction": 1.0}}
    assert {router(none).route(req(f"t{n}")).arm for n in range(30)} == {"base"}
    assert {router(every).route(req(f"t{n}")).arm for n in range(30)} == {"candidate"}
    assert router().route(req("t1", pack="other")).rule == "default"


def test_canary_skips_eval_turns_pins_and_unusable_arms() -> None:
    r = router()
    assert r.route(req("t1", internal=True)).rule == "default"
    assert r.pinned("fake-small").route(req("t1")).rule == "default"
    far = {"when": {"pack": "shop"}, "split": {"base_alias": "far", "candidate_alias": "far"}}
    assert router(far).route(req("t1")).rule == "default"  # neither arm usable: next rule
    half = {**CANARY, "split": {**CANARY["split"], "candidate_alias": "far", "fraction": 1.0}}
    route = router(half).route(req("t1"))
    assert route.model == "base" and route.arm == "base" and route.alternatives == []


def test_validate_split() -> None:
    validate_split(CANARY, MODELS)
    for bad in (
        {**CANARY, "use": ["base"]},
        {"split": {"base_alias": "nope", "candidate_alias": "cand"}},
        {"split": {"base_alias": "base", "candidate_alias": "cand", "fraction": 1.5}},
        {"split": {"base_alias": "base", "candidate_alias": "cand", "fraction": True}},
    ):
        with pytest.raises(ConfigError):
            validate_split(bad, MODELS)


# -- scripted students ----------------------------------------------------------------------------


def provider_request(messages: list[Message] | None = None) -> ProviderRequest:
    tools = [
        ToolSchema("lookup_order", "", {"type": "object"}, "read"),
        ToolSchema("cancel_order", "", {"type": "object"}, "destructive"),
    ]
    return ProviderRequest(model="x", messages=messages or [Message("user", "hi")], tools=tools)


def test_ask_first_turns_a_write_call_into_a_question() -> None:
    write = {"tool_calls": [{"name": "cancel_order", "arguments": {"order_id": "A-1"}}]}
    read = {"tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "A-1"}}]}
    asked = styled("ask_first", write, provider_request())
    assert "tool_calls" not in asked and "approval" in asked["text"]
    assert styled("ask_first", read, provider_request()) is read


def test_over_call_calls_a_tool_on_a_plain_first_answer() -> None:
    plain = {"text": "Hello."}
    out = styled("over_call", plain, provider_request())
    assert out["tool_calls"][0]["name"] == "lookup_order"
    after_tool = provider_request([Message("user", "hi"), Message("tool", "{}", name="x")])
    assert styled("over_call", plain, after_tool) is plain


# -- models.yaml edits ----------------------------------------------------------------------------


def test_models_edits_are_one_line_each_and_parse() -> None:
    text = (ROOT / "config" / "models.yaml").read_text(encoding="utf-8")
    added = with_alias(text, "tuned-x", {"provider": "fake", "tools": True}, "a note")
    assert yaml.safe_load(added)["models"]["tuned-x"] == {"provider": "fake", "tools": True}
    canaried = edit_canary(added, CANARY)
    assert canary_of(canaried) == CANARY
    assert len(canaried.splitlines()) == len(text.splitlines()) + 4
    assert edit_canary(canaried, None) == added
    moved = edit_canary(canaried, {**CANARY, "split": {**CANARY["split"], "fraction": 0}})
    assert canary_of(moved)["split"]["fraction"] == 0
    assert len(moved.splitlines()) == len(canaried.splitlines())


# -- the gate -------------------------------------------------------------------------------------


def sample(passed: int, cases: int, split: list[str] | None = None) -> dict[str, Any]:
    rows = [{"case_id": f"c{i}", "status": "failed", "tool_split": s} for i, s in
            enumerate(split or [])]  # fmt: skip
    return {"run_id": "r", "status": "done", "totals": {"cases": cases, "passed": passed,
            "skipped": 0}, "cases": rows, "cost": {"usd": 0}, "seconds": 1}  # fmt: skip


def cells(base: dict, cand: dict, arm: dict | None = None) -> dict[str, Any]:
    out = {
        "base": {p: cell_summary([sample(*v)]) for p, v in base.items()},
        "candidate": {p: cell_summary([sample(*v)]) for p, v in cand.items()},
    }
    if arm:
        out["prompt_arm"] = {p: cell_summary([sample(*v)]) for p, v in arm.items()}
    return out


REQ = {"packs": ["t"], "canary_packs": ["c"], "min_gain_cases": 2, "canary_tolerance_cases": 1}


def test_gate_passes_refuses_and_reports_the_prompt_arm() -> None:
    ok = gate_verdict(cells({"t": (4, 24), "c": (12, 22)}, {"t": (10, 24), "c": (11, 22)},
                            {"t": (6, 24), "c": (12, 22)}), REQ)  # fmt: skip
    assert ok["status"] == "passed" and ok["gain"] == 6 and ok["canary_drop"] == {"c": 1}
    assert ok["prompt_arm"] == {"gain": 2, "canary_drop": {"c": 0}, "canary_ok": True,
                                "would_pass": True}  # fmt: skip
    small = gate_verdict(cells({"t": (4, 24), "c": (12, 22)}, {"t": (5, 24), "c": (12, 22)}), REQ)
    assert small["status"] == "failed" and small["reason"] == "below_min_gain"
    forgot = gate_verdict(cells({"t": (4, 24), "c": (12, 22)}, {"t": (9, 24), "c": (9, 22)}), REQ)
    assert forgot["reason"] == "canary_regressed" and forgot["regressed"] == ["c"]


def test_cell_summary_means_samples_and_counts_the_split() -> None:
    c = cell_summary([sample(4, 24, ["write tool not called"] * 3), sample(5, 24, ["none called"])])
    assert c["passed"] == 4.5 and c["min"] == 4 and c["max"] == 5 and c["scored"] == 24
    assert c["tool_split"] == {"none called": 0.5, "write tool not called": 1.5}


def test_canary_gate() -> None:
    cfg = {"min_turns": 3, "max_failure_increase": 0.1}
    arm = {"turns": 4, "failure_rate": 0.25}
    assert not canary_gate({"base": arm, "candidate": {"turns": 2, "failure_rate": 0}}, cfg)[
        "passed"
    ]
    assert canary_gate({"base": arm, "candidate": {"turns": 5, "failure_rate": 0.3}}, cfg)["passed"]
    worse = canary_gate({"base": arm, "candidate": {"turns": 5, "failure_rate": 0.6}}, cfg)
    assert not worse["passed"] and worse["reason"].startswith("candidate_worse")


def test_parse_ref() -> None:
    assert parse_ref("model/x_1@v3", "model") == ("x_1", 3)
    assert parse_ref("x_1", "model") == ("x_1", None)
    with pytest.raises(TuneError):
        parse_ref("model/Bad Name", "model")


# -- curation pieces ------------------------------------------------------------------------------


def test_rewrites_parse_and_keep_identifiers() -> None:
    text = 'Sure:\n```json\n["Cancel QF-20430 please", "QF-20430, cancel it", "Cancel it"]\n```'
    got = parse_rewrites(text, 5)
    assert got == ["Cancel QF-20430 please", "QF-20430, cancel it", "Cancel it"]
    assert keeps_ids("Please cancel order QF-20430.", got[0])
    assert not keeps_ids("Please cancel order QF-20430.", got[2])
    assert parse_rewrites("no list here", 3) == []
    assert rewrite_id("cancel_x", 2) == "cancel_x_p2"


def test_chunks_keep_rewrites_and_after_links_together() -> None:
    from harnesswalk.ops.eval.golden import GoldenCase
    from harnesswalk.ops.tune.curate import chunks

    def mk(i: str, after: str | None = None) -> GoldenCase:
        return GoldenCase(id=i, title="", prompt="", after=after)

    cases = [mk("a"), mk("a_p1"), mk("a_p2"), mk("kb_price"), mk("kb_price_p1"), mk("b"),
             mk("c", "b"), mk("c_p1")]  # fmt: skip
    got = [[c.id for c in chunk] for chunk in chunks(cases, 3)]
    assert got == [["a", "a_p1", "a_p2"], ["kb_price", "kb_price_p1"], ["b", "c", "c_p1"]]


def test_to_chat_shapes() -> None:
    call = {"role": "assistant", "content": "", "tool_calls": [
        {"call_id": "c1", "name": "cancel_order", "arguments": {"order_id": "A"}}]}  # fmt: skip
    assert to_chat(call)["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "cancel_order",
                                                      "arguments": {"order_id": "A"}}}
    ]  # fmt: skip
    tool = to_chat({"role": "tool", "content": "{}", "tool_call_id": "c1", "name": "x"})
    assert tool == {"role": "tool", "content": "{}", "tool_call_id": "c1", "name": "x"}


# -- the loop in process --------------------------------------------------------------------------


def tune_settings(tmp_path: Path, **extra: Any):
    overrides = {"stores.embeddings.provider": "fake", **extra}
    return settings(tmp_path, profile=None, overrides={"dataset": "ecommerce", **overrides})


@pytest.mark.anyio
async def test_fake_loop_end_to_end(tmp_path: Path) -> None:
    h = Harness(tune_settings(tmp_path))
    t = h.tune
    try:
        rec = t.start_curation("dev", {"name": "approvals", "packs": ["ecommerce"],
                                       "teacher": "fake-small", "variants": 1})  # fmt: skip
        await t.wait()
        rec = t.store.get(rec["id"])
        assert rec["status"] == "done" and rec["counts"]["kept"] == 48
        manifest, _ = t.manifest("dataset", "approvals")
        assert manifest["synthetic"] and manifest["scenarios"] == {
            "golden": 24, "paraphrase": 24, "variants": 1, "seed": 7,
            "multi_turn_not_rewritten": 0,
            "rewrite_tokens": {"input_tokens": 0, "output_tokens": 0},
        }  # fmt: skip
        train = Path(manifest["files"]["train"]["path"])
        rows = [json.loads(line) for line in train.read_text().splitlines()]
        arm = t.prompt_arm_text()
        for row in rows:
            system = [m["content"] for m in row["messages"] if m["role"] == "system"]
            assert system and arm[:40] not in system[0]  # the control arm's text is taken out
            assert row["messages"][-1]["role"] == "assistant"
        # Tune artifacts are outside every pack's working set and served set.
        assert {v.kind for v in h.release.store.working_set("ecommerce")}.isdisjoint(
            {"dataset", "adapter", "model"}
        )
        rec = t.start_run("dev", {"dataset": "approvals", "trainer": "fake",
                                  "student": "fake-student"})  # fmt: skip
        await t.wait()
        rec = t.store.get(rec["id"])
        assert rec["status"] == "done" and rec["model"]["version"] == 1
        steps = [s["step"] for s in rec["steps"]]
        assert steps == list(range(1, len(steps) + 1))
        name = rec["request"]["name"]
        rec = t.start_comparison("dev", {"candidate": f"model/{name}", "against": "fake-student",
                                         "packs": ["ecommerce"], "canary_packs": ["sports"],
                                         "samples": 1})  # fmt: skip
        await t.wait()
        rec = t.store.get(rec["id"])
        verdict = rec["verdict"]
        assert verdict["status"] == "passed", verdict
        assert verdict["table"]["candidate"]["ecommerce"] == 24
        assert verdict["table"]["base"]["ecommerce"] < 24
        assert verdict["canary_drop"] == {"sports": 0}
        assert "| ecommerce | target |" in render_comparison(rec)
        assert t.verify()["ok"]
        out = await t.promote("dev", {"candidate": f"model/{name}", "fraction": 0.5})
        assert [r["pack"] for r in out["releases"]] == ["ecommerce"]
        assert out["canary"]["rule"]["split"]["fraction"] == 0.5
        assert h.view("ecommerce").settings.get("models.router.canary")["split"]["fraction"] == 0.5
        with pytest.raises(TuneError) as exc:
            await t.promote("dev", {"candidate": f"model/{name}", "stage": "full"})
        assert exc.value.code == "canary_failed"
        back = await t.rollback("dev")
        assert back["canary"]["rule"]["split"]["fraction"] == 0
    finally:
        await h.aclose()


@pytest.mark.anyio
async def test_one_activity_at_a_time_and_bad_requests(tmp_path: Path) -> None:
    h = Harness(tune_settings(tmp_path))
    t = h.tune
    try:
        with pytest.raises(TuneError) as exc:
            t.start_curation("dev", {"name": "Bad", "packs": ["ecommerce"]})
        assert exc.value.status == 400
        with pytest.raises(TuneError):
            t.start_curation("dev", {"name": "ok", "packs": ["no_such_pack"]})
        t.start_curation("dev", {"name": "ok", "packs": ["ecommerce"], "teacher": "fake-small",
                                 "variants": 0})  # fmt: skip
        with pytest.raises(TuneError) as exc:
            t.start_curation("dev", {"name": "ok2", "packs": ["ecommerce"]})
        assert exc.value.code == "tune_running"
        await t.wait()
        with pytest.raises(TuneError) as exc:
            t.start_run("dev", {"dataset": "ok", "trainer": "local-mlx", "student": "local-4b"})
        assert exc.value.code == "trainer_unavailable"
        with pytest.raises(TuneError) as exc:
            await t.promote("dev", {"candidate": "model/none"})
        assert exc.value.status == 404
    finally:
        await h.aclose()


@pytest.mark.anyio
async def test_diagnose_names_the_tune_lever(tmp_path: Path) -> None:
    h = Harness(tune_settings(tmp_path))
    try:
        run = h.evals.offline.start("dev", "ecommerce", "model", model="fake-student",
                                    case_ids=["cancel_approved_qf20430"])  # fmt: skip
        await h.evals.offline.wait(run["run_id"])
        d = h.evals.diagnose.case("dev", run["run_id"], "cancel_approved_qf20430")
        assert d["layer"] == "model" and d["levers"] == ["prompt_or_skill", "tune"]
        choice = next(e for e in d["evidence"] if e["kind"] == "tool_choice")
        assert choice["split"] in ("none called", "write tool not called")
        assert "tune" in d["suggestion"]
    finally:
        await h.aclose()


# -- HTTP -----------------------------------------------------------------------------------------


def client(h: Harness) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(h)),
        base_url="http://test",
        headers={"X-Harnesswalk-Tenant": "alpha"},
    )


@pytest.mark.anyio
async def test_public_mode_replays_and_refuses(tmp_path: Path) -> None:
    h = Harness(public_settings(tmp_path, "plain_answer"))
    try:
        async with client(h) as c:
            await c.get("/healthz")
            overview = (await c.get("/v1/tune")).json()
            assert overview["replay"] is True and overview["trainers"]
            artifacts = (await c.get("/v1/tune/artifacts")).json()
            assert artifacts["replay"] and {a["kind"] for a in artifacts["artifacts"]} == {
                "dataset", "adapter", "model"
            }  # fmt: skip
            runs = (await c.get("/v1/tune/runs")).json()["runs"]
            one = (await c.get(f"/v1/tune/runs/{runs[0]['id']}")).json()
            assert one["replay"] and one["events"]
            stream = await c.get(f"/v1/tune/runs/{runs[0]['id']}/events")
            assert "event: tune_run_finished" in stream.text
            for path in ("/v1/tune/runs", "/v1/tune/datasets", "/v1/tune/comparisons",
                         "/v1/tune/promote", "/v1/tune/rollback"):  # fmt: skip
                r = await c.post(path, json={})
                assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_in_mode"
    finally:
        await h.aclose()


@pytest.mark.anyio
async def test_http_dev_routes(tmp_path: Path) -> None:
    h = Harness(tune_settings(tmp_path))
    try:
        async with client(h) as c:
            r = await c.post(
                "/v1/tune/datasets",
                json={"name": "a", "packs": ["ecommerce"], "teacher": "fake-small", "variants": 0},
            )
            assert r.status_code == 202 and r.json()["events_url"].endswith("/events")
            await h.tune.wait()
            cur = (await c.get(r.json()["url"])).json()
            assert cur["status"] == "done" and cur["event_counts"]["eval_case_finished"] == 24
            events = await c.get(r.json()["events_url"])
            assert events.text.count("event: eval_case_finished") == 24
            r = await c.post("/v1/tune/runs", json={"dataset": "a", "student": "fake-student"})
            await h.tune.wait()
            run = (await c.get(r.json()["url"])).json()
            assert run["status"] == "done" and run["event_counts"]["tune_run_finished"] == 1
            listing = (await c.get("/v1/tune/artifacts?kind=model")).json()["artifacts"]
            assert listing[0]["summary"]["alias"] == "tuned-a-fakestudent"
            detail = (await c.get("/v1/tune/artifacts/model/a_fakestudent")).json()
            assert detail["manifest"]["adapter"]["name"] == "a_fakestudent"
            r = await c.post("/v1/tune/promote", json={"candidate": "model/a_fakestudent"})
            assert r.status_code == 409 and r.json()["error"]["code"] == "gate_failed"
            assert "gate" in r.json()
            assert (await c.get("/v1/tune/canary")).json()["rule"] is None
    finally:
        await h.aclose()


# -- the CLI --------------------------------------------------------------------------------------


def test_cli_fake_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setenv("HARNESSWALK_DATA_DIR", str(tmp_path / "data"))
    assert main(["tune", "curate", "--pack", "ecommerce", "--teacher", "fake-small",
                 "--variants", "0", "--out", "dataset/demo"]) == 0  # fmt: skip
    assert main(["tune", "run", "--dataset", "demo", "--student", "fake-student",
                 "--name", "demo", "--config", "outcome=forgets"]) == 0  # fmt: skip
    out = tmp_path / "cmp.md"
    code = main(["tune", "compare", "--candidate", "model/demo", "--against", "fake-student",
                 "--packs", "ecommerce", "--canary-packs", "sports", "--samples", "1",
                 "--no-prompt-arm", "--out", str(out)])  # fmt: skip
    assert code == 1  # the forgetting model is refused
    assert "Gate: **failed**" in out.read_text()
    assert main(["tune", "promote", "--candidate", "model/demo"]) == 2
    capsys.readouterr()
    assert main(["tune", "status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["verify"]["ok"] and status["canary"]["rule"] is None
