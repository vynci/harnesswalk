"""spec/conformance: cases, fixtures and profile are well formed and agree with each other."""

from pathlib import Path

import jsonschema
import pytest

from tests.spec.support import (
    SPEC,
    check_expectations,
    check_stream,
    load_json,
    load_jsonl,
    load_yaml,
    simple_ids,
)

CONF = SPEC / "conformance"
CASES = sorted((CONF / "cases").glob("*.yaml"))
FIXTURES = sorted((CONF / "fixtures").glob("*.yaml"))
REQUIRED_CASES = {
    "plain_answer",
    "tool_call",
    "approval_approve",
    "approval_deny",
    "loop_max_steps",
}

# Recorded example stream -> the case it must satisfy.
EXAMPLE_FOR_CASE = {
    "plain_answer": "plain_answer",
    "tool_call": "tool_call",
    "approval_approve": "approval_pause",
}


def _tools() -> dict[str, dict]:
    return {t["name"]: t for t in load_yaml(CONF / "tools.yaml")}


def test_required_cases_present() -> None:
    assert {p.stem for p in CASES} >= REQUIRED_CASES


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.stem)
def test_case_is_valid(path: Path) -> None:
    schema = load_json(CONF / "case.schema.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    case = load_yaml(path)
    jsonschema.Draft202012Validator(schema).validate(case)
    assert case["id"] == path.stem
    assert (CONF / "fixtures" / f"{case['fixture']}.yaml").exists()
    overridable = set(load_yaml(CONF / "profile.yaml")["overridable"])
    assert set(case.get("config", {})) <= overridable
    for turn in case["turns"]:
        assert set(turn["expect"]["path"]) <= simple_ids()
        statuses = turn["expect"].get("statuses")
        if isinstance(statuses, list):
            assert len(statuses) == len(turn["expect"]["path"])


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_is_valid(path: Path) -> None:
    schema = load_json(CONF / "fixture.schema.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    fixture = load_yaml(path)
    jsonschema.Draft202012Validator(schema).validate(fixture)
    assert fixture["fixture"] == path.stem
    tools = _tools()
    for turn in fixture["turns"]:
        for response in turn["responses"]:
            for call in response.get("tool_calls", []):
                assert call["name"] in tools, call["name"]
                jsonschema.validate(call["arguments"], tools[call["name"]]["parameters"])


def test_fixture_tools_answer_every_scripted_call() -> None:
    tools = _tools()
    for path in FIXTURES:
        for turn in load_yaml(path)["turns"]:
            for response in turn["responses"]:
                for call in response.get("tool_calls", []):
                    key = next(iter(call["arguments"].values()))
                    assert key in tools[call["name"]]["table"], (path.stem, call)


def test_profile_tools_are_allowlisted() -> None:
    profile = load_yaml(CONF / "profile.yaml")
    assert set(profile["policy"]["agent"]["allow_tools"]) == set(_tools())
    assert profile["mode"] == "dev"


@pytest.mark.parametrize("case_id", sorted(EXAMPLE_FOR_CASE))
def test_recorded_example_satisfies_case(case_id: str) -> None:
    case = load_yaml(CONF / "cases" / f"{case_id}.yaml")
    events = load_jsonl(SPEC / "examples" / f"{EXAMPLE_FOR_CASE[case_id]}.jsonl")
    assert check_stream(events) == []
    assert check_expectations(case["turns"][0]["expect"], events) == []
