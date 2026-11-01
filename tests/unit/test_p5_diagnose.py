"""P5 diagnose: a failed case or a bad turn mapped to the layer at fault, from the trace.
Fake provider only; the rules are also checked on small synthetic streams."""

from pathlib import Path

import pytest

from harnesswalk.ops.eval.diagnose import artifact_changes, decide, suggest, trace_facts
from harnesswalk.runtime import Harness
from tests.spec.support import event_validator
from tests.unit.support import run_turn, settings
from tests.unit.test_http_api import client


def starter(tmp_path: Path, **overrides) -> Harness:
    return Harness(settings(tmp_path, "plain_answer", profile=None, overrides=overrides))


def exit_(node_id: str, **summary) -> dict:
    return {"type": "node_exit", "node_id": node_id, "summary": summary}


def error(node_id: str, code: str) -> dict:
    return {"type": "error", "node_id": node_id, "error": {"code": code, "message": "boom"}}


CHANGED = artifact_changes(
    [{"kind": "skill", "name": "returns", "version": 2, "hash": "b"}],
    [{"kind": "skill", "name": "returns", "version": 1, "hash": "a"}],
    "run_0001",
)


@pytest.mark.parametrize(
    ("events", "extra", "layer"),
    [
        ([exit_("input_guardrails", action="block", reason="too long")], [], "guardrail"),
        ([exit_("policy", tool="cancel_order", decision="deny", rule="r")], [], "guardrail"),
        ([exit_("loop", exit_reason="budget_tokens", steps=3)], CHANGED, "budget"),
        ([exit_("tools", tool="lookup_order", outcome="error")], CHANGED, "tool"),
        ([error("sandbox", "timeout")], [], "tool"),
        ([error("model_layer", "fixture_mismatch")], CHANGED, "prompt_or_skill"),
        ([error("model_layer", "provider_error")], [], "model"),
        ([exit_("semantic", hits=0), exit_("knowledge", hits=0)], [], "retrieval"),
        ([exit_("semantic", hits=1)], [], "unknown"),
        ([], [], "unknown"),
    ],
)
def test_the_rules_in_order(events: list, extra: list, layer: str) -> None:
    evidence = trace_facts(events) + extra
    assert decide(evidence) == layer
    assert suggest(layer, evidence, {"release_id": "r1"})


def test_citations_point_at_retrieval_and_artifact_kinds_map_to_layers() -> None:
    expected = {"citations": [{"doc": "returns_policy"}]}
    facts = trace_facts([exit_("knowledge", hits=2, docs=["shipping"])], expected)
    assert [f["kind"] for f in facts] == ["citation_missing"]
    assert decide(facts) == "retrieval"
    assert trace_facts([exit_("knowledge", hits=0)], expected)[0]["kind"] == "retrieval_empty"
    for kind, layer in (("tools", "tool"), ("policy", "guardrail"), ("context", "retrieval")):
        changed = artifact_changes(
            [{"kind": kind, "name": kind, "version": 2, "hash": "b"}],
            [{"kind": kind, "name": kind, "version": 1, "hash": "a"}],
            "run_0001",
        )
        assert decide(changed) == layer
    both = artifact_changes(
        [
            {"kind": "models", "name": "models", "version": 2, "hash": "b"},
            {"kind": "prompt", "name": "system_prompt", "version": 3, "hash": "d"},
        ],
        [
            {"kind": "models", "name": "models", "version": 1, "hash": "a"},
            {"kind": "prompt", "name": "system_prompt", "version": 2, "hash": "c"},
        ],
        "run_0001",
    )
    assert [c["artifact"] for c in both] == ["prompt/system_prompt", "models/models"]
    assert decide(both) == "prompt_or_skill"
    assert artifact_changes(None, [], "run_0001") == []


async def two_runs_with_a_skill_edit(h: Harness, c) -> tuple[str, str]:
    first = (await c.post("/v1/evals/runs", json={"pack": "_starter", "suite": "golden"})).json()
    await h.evals.offline.wait(first["run_id"])
    body = (await c.get("/v1/artifacts/skill/returns")).json()["body"]
    r = await c.put(
        "/v1/artifacts/skill/returns", json={"body": body.replace("30 days", "14 days")}
    )
    assert r.status_code == 200
    second = (await c.post("/v1/evals/runs", json={"pack": "_starter", "suite": "golden"})).json()
    await h.evals.offline.wait(second["run_id"])
    return first["run_id"], second["run_id"]


@pytest.mark.anyio
async def test_a_regression_is_diagnosed_on_its_own(tmp_path: Path) -> None:
    h = starter(tmp_path)
    async with client(h, "dev") as c:
        run1, run2 = await two_runs_with_a_skill_edit(h, c)
        run = (await c.get(f"/v1/evals/runs/{run2}")).json()
        assert run["regression_cases"] == ["returns_window"]
        assert run["diagnoses"] == {"returns_window": "prompt_or_skill"}
        case = next(x for x in run["cases"] if x["case_id"] == "returns_window")
        d = case["diagnosis"]
        assert d["automatic"] is True and d["target"] == "case" and d["run_id"] == run2
        assert d["baseline"]["run_id"] == run1 and d["baseline"]["release_id"] == "r1"
        changed = [e for e in d["evidence"] if e["kind"] == "artifact_changed"]
        assert changed == [
            {
                "kind": "artifact_changed", "artifact": "skill/returns", "artifact_kind": "skill",
                "from_version": 1, "to_version": 2, "baseline_run_id": run1,
                "fact": f"skill/returns is v2 here; {run1}, the last pass, ran v1",
            }
        ]  # fmt: skip
        assert any(e["kind"] == "model_error" for e in d["evidence"])  # fixture_mismatch
        assert "skill/returns" in d["suggestion"] and "r1" in d["suggestion"]
        passed = next(x for x in run["cases"] if x["case_id"] == "support_hours")
        assert passed["diagnosis"] is None
        listed = (await c.get("/v1/evals/runs")).json()["runs"][0]
        assert listed["diagnoses"] == {"returns_window": "prompt_or_skill"}

        # Asked for: the same answer, not automatic; a passed case has nothing to diagnose.
        r = await c.post("/v1/diagnose", json={"case_id": "returns_window"})
        assert r.status_code == 200 and r.json()["run_id"] == run2
        assert r.json()["layer"] == "prompt_or_skill" and r.json()["automatic"] is False
        r = await c.post("/v1/diagnose", json={"run_id": run2, "case_id": "support_hours"})
        assert r.status_code == 422 and r.json()["error"]["code"] == "nothing_to_diagnose"
        turn_id = case["turn_id"]
        by_turn = await c.post("/v1/diagnose", json={"turn_id": turn_id})
        assert by_turn.status_code == 200 and by_turn.json()["case_id"] == "returns_window"

        events = h.ops.list("dev", type_="diagnose_finished")
        assert [e["summary"]["automatic"] for e in events] == [True, False, False]
        assert events[0]["summary"]["layer"] == "prompt_or_skill"
        assert events[0]["turn_id"] is None and events[0]["summary"]["case_id"] == "returns_window"
        validator = event_validator()
        assert all(not list(validator.iter_errors(e)) for e in events)

        node = (await c.get("/v1/nodes/diagnose")).json()
        assert node["implemented"] is True
        assert [d["diagnosis_id"] for d in node["inspector"]["last_diagnoses"]][:1] == [
            events[-1]["summary"]["diagnosis_id"]
        ]
    await h.aclose()


@pytest.mark.anyio
async def test_a_turn_with_bad_feedback(tmp_path: Path) -> None:
    h = starter(tmp_path)
    async with client(h, "dev") as c:
        run1, _ = await two_runs_with_a_skill_edit(h, c)
        events = await run_turn(h, "What are your support hours?", tenant="dev")
        turn_id = events[0]["turn_id"]
        r = await c.post("/v1/diagnose", json={"turn_id": turn_id})
        assert r.status_code == 422  # answered, no bad feedback
        await c.post(f"/v1/turns/{turn_id}/feedback", json={"rating": "bad", "note": "wrong"})
        r = await c.post("/v1/diagnose", json={"turn_id": turn_id})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["target"] == "turn" and d["pack"] == "_starter"
        assert d["layer"] == "prompt_or_skill" and d["baseline"]["run_id"] == run1
        feedback = {k: v for k, v in d["evidence"][-1].items() if k != "fact"}
        assert feedback == {"kind": "feedback", "rating": "bad", "note_chars": 5}
        summary = (await c.get(f"/v1/turns/{turn_id}")).json()
        assert summary["diagnosis"]["diagnosis_id"] == d["diagnosis_id"]
        last = h.ops.list("dev", type_="diagnose_finished")[-1]
        assert last["turn_id"] == turn_id and last["session_id"] == events[0]["session_id"]
        assert last["summary"]["target"] == "turn"
    await h.aclose()


@pytest.mark.anyio
async def test_diagnose_requests_are_checked(tmp_path: Path) -> None:
    h = starter(tmp_path, **{"evals.diagnose.auto": False})
    async with client(h, "dev") as c:
        _, run2 = await two_runs_with_a_skill_edit(h, c)
        assert (await c.get(f"/v1/evals/runs/{run2}")).json()["diagnoses"] == {}
        for body in ({}, {"turn_id": "t", "case_id": "c"}, {"turn_id": "t", "run_id": "r"}):
            assert (await c.post("/v1/diagnose", json=body)).status_code == 400
        assert (await c.post("/v1/diagnose", json={"case_id": 3})).status_code == 400
        r = await c.post("/v1/diagnose", json={"run_id": "run_9999", "case_id": "x"})
        assert r.status_code == 404 and r.json()["error"]["code"] == "run_not_found"
        r = await c.post("/v1/diagnose", json={"run_id": run2, "case_id": "x"})
        assert r.json()["error"]["code"] == "case_not_found"
        r = await c.post("/v1/diagnose", json={"case_id": "never_failed"})
        assert r.status_code == 404
        r = await c.post("/v1/diagnose", json={"turn_id": "turn_nope"})
        assert r.status_code == 404 and r.json()["error"]["code"] == "turn_not_found"
    await h.aclose()


@pytest.mark.anyio
async def test_diagnose_is_not_offered_in_public_mode(tmp_path: Path) -> None:
    h = Harness(settings(tmp_path, "plain_answer", mode="public"))
    async with client(h) as c:
        r = await c.post("/v1/diagnose", json={"turn_id": "x"})
        assert r.status_code == 403
        assert (await c.get("/v1/capabilities")).json()["features"]["diagnose"] is False
    await h.aclose()


@pytest.mark.anyio
async def test_case_turns_in_the_callers_tenant(tmp_path: Path) -> None:
    h = starter(tmp_path, **{"evals.scratch_tenant": False})
    async with client(h, "dev") as c:
        r = await c.post(
            "/v1/evals/runs",
            json={"pack": "_starter", "suite": "golden", "cases": ["support_hours"]},
        )
        await h.evals.offline.wait(r.json()["run_id"])
        run = (await c.get(f"/v1/evals/runs/{r.json()['run_id']}")).json()
        assert run["case_tenant"] == "dev" and run["cases"][0]["status"] == "passed"
        turn_id = run["cases"][0]["turn_id"]
        assert h.tracer.turn_exists("dev", turn_id)
        stream = await c.get(run["cases"][0]["events_url"])
        assert stream.status_code == 200 and "turn_end" in stream.text
        listed = (await c.get("/v1/trace/turns", params={"kind": "eval"})).json()["turns"]
        assert [(t["turn_id"], t["run_id"], t["case_id"]) for t in listed] == [
            (turn_id, run["run_id"], "support_hours")
        ]
    await h.aclose()
