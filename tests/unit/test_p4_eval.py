"""Eval (P4): the golden set, scorers, runs and their diff, the judge seam, online scores and
feedback. Fake provider only; the judge is a deterministic stand-in."""

import json
from pathlib import Path

import pytest

from harnesswalk.control.gateway import TurnRequest
from harnesswalk.control.pii import PiiDetector
from harnesswalk.ops.eval.golden import GoldenError, load_golden, normalize
from harnesswalk.ops.eval.judge import Verdict, parse
from harnesswalk.ops.eval.scorers import (
    score_answer,
    score_citations,
    score_path,
    score_pii,
    score_schema,
    score_tool_calls,
)
from harnesswalk.runtime import Harness
from harnesswalk.types import Identity, Usage
from tests.spec.support import ROOT, event_validator
from tests.unit.support import run_turn, settings
from tests.unit.test_http_api import client, parse_sse

GOLDEN = ROOT / "datasets" / "_starter" / "golden.jsonl"


def starter(tmp_path: Path, **overrides) -> Harness:
    """The starter pack as shipped (no conformance profile), on the fake provider."""
    return Harness(settings(tmp_path, "plain_answer", profile=None, overrides=overrides))


async def finished(h: Harness, run_id: str) -> dict:
    await h.evals.offline.wait(run_id)
    run = h.evals.store.run("dev", run_id)
    run["cases"] = h.evals.store.cases(run_id)
    return run


# -- the golden set and the scorers ------------------------------------------------------------


def test_the_starter_golden_set_covers_every_scorer() -> None:
    cases = load_golden(GOLDEN)
    assert len(cases) >= 12
    expects = [c.expect for c in cases]
    assert any("path" in e for e in expects) and any("citations" in e for e in expects)
    assert any("tool_calls" in e for e in expects) and any(c.output_schema for c in cases)
    assert any("answer_regex" in e for e in expects) and any(
        "answer_contains" in e for e in expects
    )
    assert {"pii"} <= {t for c in cases for t in c.tags}
    assert all(c.fixture and c.rubric for c in cases)


def test_golden_cases_are_checked() -> None:
    with pytest.raises(GoldenError):
        normalize({"id": "Bad Id", "prompt": "x"}, "t")
    with pytest.raises(GoldenError):
        normalize({"id": "ok", "prompt": "x", "expect": {"nope": 1}}, "t")
    with pytest.raises(GoldenError):
        normalize({"id": "ok", "prompt": "x", "approval": "maybe"}, "t")
    case = normalize(
        {"id": "old", "input": "hi", "expect": {"path_includes": ["loop"], "must_cite": True}}, "t"
    )
    assert case.prompt == "hi" and case.expect == {"path": ["loop"], "citations": [{}]}


def test_scorers() -> None:
    assert score_path(["gateway", "loop"], ["gateway", "memory", "loop"])["pass"]
    miss = score_path(["loop", "gateway"], ["gateway", "loop"])
    assert not miss["pass"] and miss["value"] == 0.5
    cited = [{"n": 1, "doc": "returns_policy", "chunk": 1}]
    assert score_citations([{"doc": "returns_policy"}], cited)["pass"]
    assert not score_citations([{"doc": "returns_policy", "chunk": 2}], cited)["pass"]
    assert score_citations([{}], cited)["pass"] and not score_citations([{}], [])["pass"]
    assert score_tool_calls([], [])["pass"] and not score_tool_calls(["a"], ["a", "a"])["pass"]
    schema = {"type": "object", "required": ["id"]}
    assert score_schema(schema, '{"id": 1}')["pass"]
    assert not score_schema(schema, "{}")["pass"] and not score_schema(schema, "nope")["pass"]
    pii = PiiDetector()
    assert score_pii(pii, "I am sam@example.com", "Sent to sam@example.com")["pass"]
    leak = score_pii(pii, "Who?", "Write to jane@example.org")
    assert not leak["pass"] and leak["value"] == 1
    assert score_answer({"answer_contains": ["a", "b"]}, "a b")["pass"]
    assert score_answer({"answer_exact": "yes"}, " yes ")["pass"]
    assert not score_answer({"answer_regex": r"^\d+$"}, "x1")["pass"]


def test_judge_replies_are_parsed() -> None:
    got = parse(
        'Sure: ```json\n{"faithfulness": {"score": 0.9, "reason": "ok"}, "relevance": 1}```'
    )
    assert got["faithfulness"]["score"] == 0.9 and got["relevance"]["score"] == 1.0
    assert (
        parse('{"faithfulness": {"score": 7}, "relevance": {"score": -1}}')["faithfulness"]["score"]
        == 1.0
    )
    with pytest.raises(ValueError):
        parse("no json here")


# -- runs ---------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_golden_run_over_http_passes_and_streams_progress(tmp_path: Path) -> None:
    h = starter(tmp_path)
    async with client(h, "dev") as c:
        r = await c.post("/v1/evals/runs", json={"pack": "_starter", "suite": "golden"})
        assert r.status_code == 202, r.text
        run_id = r.json()["run_id"]
        assert r.json()["events_url"] == f"/v1/evals/runs/{run_id}/events"
        stream = await c.get(f"/v1/evals/runs/{run_id}/events")  # follows until the run ends
        events = parse_sse(stream.text)
        types = [e["type"] for e in events]
        assert types[0] == "eval_run_started" and types[-1] == "eval_run_finished"
        assert types.count("eval_case_finished") == 16
        assert [e["seq"] for e in events] == list(range(len(events)))
        assert all(e["run_id"] == run_id and e["turn_id"] is None for e in events)
        assert events[0]["summary"]["total"] == 16 and events[1]["summary"]["total"] == 16
        validator = event_validator()  # the stored event is the schema's; SSE adds run_id
        assert all(not list(validator.iter_errors({k: v for k, v in e.items() if k != "run_id"}))
                   for e in events)  # fmt: skip

        run = (await c.get(f"/v1/evals/runs/{run_id}")).json()
        assert run["status"] == "done" and run["suite"] == "golden"
        assert run["totals"] == {"cases": 16, "passed": 16, "failed": 0, "skipped": 0}
        assert run["regressions"] == 0 and run["previous_run_id"] is None
        assert run["release_id"] == "r1" and run["working_set"] is True
        by_id = {case["case_id"]: case for case in run["cases"]}
        cited = by_id["trailhead_cited"]
        assert cited["scores"]["citations"]["pass"] and cited["actual"]["citations"]
        assert set(by_id["structured_status"]["scores"]) == {"tool_calls", "schema", "pii_leak"}
        assert by_id["cancel_needs_approval"]["actual"]["path"].count("approval") == 1

        # A case turn ran in the run's scratch tenant, not the caller's, and is served here.
        turn_id = by_id["support_hours"]["turn_id"]
        assert (await c.get(f"/v1/turns/{turn_id}")).status_code == 404
        case_stream = await c.get(by_id["support_hours"]["events_url"])
        case_events = parse_sse(case_stream.text)
        assert case_events[0]["summary"]["channel"] == "eval"
        assert case_events[0]["summary"]["eval_run_id"] == run_id
        assert case_events[-1]["type"] == "turn_end"
        listed = (await c.get("/v1/evals/runs", params={"pack": "_starter"})).json()["runs"]
        assert [x["run_id"] for x in listed] == [run_id]
        golden = (await c.get("/v1/evals/golden")).json()
        assert golden["path"] == "datasets/_starter/golden.jsonl" and len(golden["cases"]) == 16
    await h.aclose()


@pytest.mark.anyio
async def test_a_changed_skill_is_a_listed_regression(tmp_path: Path) -> None:
    h = starter(tmp_path)
    async with client(h, "dev") as c:
        first = (
            await c.post("/v1/evals/runs", json={"pack": "_starter", "suite": "golden"})
        ).json()
        assert (await finished(h, first["run_id"]))["totals"]["passed"] == 16
        skill = (await c.get("/v1/artifacts/skill/returns")).json()
        body = skill["body"].replace("within 30 days", "within 14 days")
        r = await c.put("/v1/artifacts/skill/returns", json={"body": body, "note": "14 days"})
        assert r.status_code == 200 and r.json()["version"] == 2
        second = (
            await c.post("/v1/evals/runs", json={"pack": "_starter", "suite": "golden"})
        ).json()
        run = await finished(h, second["run_id"])
        detail = (await c.get(f"/v1/evals/runs/{second['run_id']}")).json()
    assert run["previous_run_id"] == first["run_id"]
    assert detail["regressions"] == 1 and detail["regression_cases"] == ["returns_window"]
    assert detail["diff"] == {
        "fixed": [],
        "regressed": ["returns_window"],
        "new": [],
        "removed": [],
    }
    case = next(x for x in detail["cases"] if x["case_id"] == "returns_window")
    assert case["status"] == "failed" and case["previous_status"] == "passed" and case["regression"]
    assert case["actual"]["errors"][0]["code"] == "fixture_mismatch"
    assert detail["working_set"] is True and detail["artifacts_hash"] != first.get("hash")
    await h.aclose()


@pytest.mark.anyio
async def test_editing_lookup_order_in_the_order_status_skill_regresses(tmp_path: Path) -> None:
    h = starter(tmp_path)
    first = h.evals.offline.start("dev", "_starter", "golden")
    assert (await finished(h, first["run_id"]))["totals"]["failed"] == 0
    from harnesswalk.ops import release as rel

    body = h.release.artifact("skill", "order_status")["body"].replace("lookup_order", "find_order")
    rel.apply_edit(h, "dev", "skill", "order_status", body)
    second = h.evals.offline.start("dev", "_starter", "golden")
    run = await finished(h, second["run_id"])
    assert "order_status_skill" in run["regression_cases"]
    await h.aclose()


@pytest.mark.anyio
async def test_run_requests_are_checked(tmp_path: Path) -> None:
    h = starter(tmp_path)
    async with client(h, "dev") as c:

        async def start(**body) -> tuple[int, str]:
            r = await c.post("/v1/evals/runs", json=body)
            return r.status_code, r.json().get("error", {}).get("code", "")

        assert await start(pack="other", suite="golden") == (422, "invalid_request")
        assert await start(pack="_starter", suite="vibes") == (400, "invalid_request")
        assert await start(pack="_starter", suite="golden", cases=["nope"]) == (
            400,
            "invalid_request",
        )
        assert (await start(pack="_starter", suite="golden", model="local-8b"))[0] == 400
        assert await start(pack="_starter", suite="judge") == (422, "judge_unavailable")
        assert (await c.get("/v1/evals/runs/run_9999")).status_code == 404
        caps = (await c.get("/v1/capabilities")).json()
        assert caps["features"]["evals"] and not caps["features"]["judge"]
    await h.aclose()


@pytest.mark.anyio
async def test_judge_suite_with_a_stand_in_judge(tmp_path: Path) -> None:
    h = starter(tmp_path)
    seen: list[tuple[str, str]] = []

    async def judge(question: str, reply: str, context: str, rubric: str | None) -> Verdict:
        seen.append((question, context))
        good = "9am" in reply
        scores = {
            "faithfulness": {"value": 1.0, "pass": True, "detail": "supported"},
            "relevance": {"value": 1.0 if good else 0.2, "pass": good, "detail": rubric or ""},
        }
        return Verdict(scores, Usage(100, 10, 0, 0.0001), "stand-in")

    h.evals.judge.override = judge
    run = h.evals.offline.start(
        "dev", "_starter", "judge", case_ids=["support_hours", "calculator"]
    )
    done = await finished(h, run["run_id"])
    assert run["judge_model"] == "override"
    by_id = {c["case_id"]: c for c in done["cases"]}
    assert set(by_id["support_hours"]["scores"]) == {"pii_leak", "faithfulness", "relevance"}
    assert by_id["support_hours"]["status"] == "passed"
    assert by_id["calculator"]["status"] == "failed"
    assert done["cost"] == {"input_tokens": 200, "output_tokens": 20, "usd": 0.0002}
    assert "Support hours are 9am to 5pm" in seen[0][1]  # the trace's context reached the judge
    await h.aclose()


def test_eval_cli_runs_the_golden_suite(tmp_path: Path, monkeypatch, capsys) -> None:
    from harnesswalk.__main__ import main

    monkeypatch.setenv("HARNESSWALK_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HARNESSWALK_PROFILE", raising=False)
    assert main(["eval", "_starter", "--suite", "golden", "--case", "support_hours"]) == 0
    out = capsys.readouterr().out
    assert "PASSED  support_hours" in out and "1 passed, 0 failed" in out
    # Hosted judges are off unless enabled, and Ollama is never asked in tests.
    code = main(["eval", "_starter", "--suite", "judge", "--provider", "fake"])
    assert code == 2 and "judge_unavailable" in capsys.readouterr().err


# -- online -------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sampled_turns_are_judged_and_scored(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer", overrides={"evals.online.sample_rate": 1.0}))

    async def judge(question, reply, context, rubric) -> Verdict:
        scores = {
            "faithfulness": {"value": 0.9, "pass": True, "detail": ""},
            "relevance": {"value": 0.4, "pass": False, "detail": ""},
        }
        return Verdict(scores, Usage(), "stand-in")

    h.evals.judge.override = judge
    async with client(h) as c:
        events = await run_turn(h, "What are your support hours?")
        await h.evals.online.drain()
        turn_id = events[0]["turn_id"]
        summary = (await c.get(f"/v1/turns/{turn_id}")).json()
        assert summary["scores"] == {"faithfulness": 0.9, "relevance": 0.4}
        receipt = (await c.get(f"/v1/trace/turns/{turn_id}/receipt")).json()
        assert receipt["scores"] == {
            "output_guardrails": {"faithfulness": 0.9},
            "loop": {"relevance": 0.4},
        }

        # A BYO-key turn is never judged.
        state = await h.gateway.start_turn(
            TurnRequest(
                message="Hello", identity=Identity("t1", None, ["user"], False), api_key="k"
            )
        )
        await h.gateway.wait(state.ctx.turn_id, 5)
        await h.evals.online.drain()
        assert h.evals.online.scores_for("t1", state.ctx.turn_id) is None
    await h.aclose()


@pytest.mark.anyio
async def test_unsampled_by_default_without_a_judge(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    assert h.evals.online.sample_rate == 0.2
    events = await run_turn(h, "Hi")
    await h.evals.online.drain()
    assert h.evals.online.scores_for("t1", events[0]["turn_id"]) is None
    await h.aclose()


@pytest.mark.anyio
async def test_feedback_is_stored_redacted_and_emitted(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    async with client(h) as c, client(h, "other") as other:
        turn_id = (await run_turn(h, "Hi"))[0]["turn_id"]
        url = f"/v1/turns/{turn_id}/feedback"
        r = await c.post(url, json={"rating": "bad", "note": "Wrong, mail me at sam@example.com"})
        assert r.status_code == 200
        record = r.json()
        assert record["rating"] == "bad" and "sam@example.com" not in record["note"]
        assert (await c.post(url, json={"rating": "good"})).json()["rating"] == "good"
        summary = (await c.get(f"/v1/turns/{turn_id}")).json()
        assert summary["feedback"]["rating"] == "good"
        assert (await c.post(url, json={"rating": "meh"})).status_code == 400
        assert (await c.post(url, json={"rating": "good", "note": "x" * 2001})).status_code == 400
        assert (await other.post(url, json={"rating": "good"})).status_code == 404
        ops = (await c.get("/v1/trace/ops-events", params={"type": "feedback_recorded"})).json()
        first = ops["events"][0]
        assert first["turn_id"] == turn_id and first["session_id"] == summary["session_id"]
        assert first["summary"] == {"rating": "bad", "note_chars": len(record["note"])}
        assert not list(event_validator().iter_errors(first))
        assert json.dumps(ops).count("sam@example.com") == 0
    await h.aclose()
