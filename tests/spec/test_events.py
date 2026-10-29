"""spec/events.schema.json and the example streams in spec/examples/."""

from pathlib import Path

import jsonschema
import pytest

from tests.spec.support import (
    SPEC,
    check_stream,
    diagram,
    event_validator,
    load_json,
    load_jsonl,
    simple_path,
)

EXAMPLES = sorted((SPEC / "examples").glob("*.jsonl"))


def test_event_schema_is_valid_2020_12() -> None:
    schema = load_json(SPEC / "events.schema.json")
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    jsonschema.Draft202012Validator.check_schema(schema)


def test_three_examples_exist() -> None:
    assert {p.stem for p in EXAMPLES} == {"plain_answer", "tool_call", "approval_pause"}


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_every_example_line_validates(path: Path) -> None:
    validator = event_validator()
    for event in load_jsonl(path):
        errors = [e.message for e in validator.iter_errors(event)]
        assert not errors, (event["seq"], errors)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_obeys_stream_rules(path: Path) -> None:
    assert check_stream(load_jsonl(path)) == []


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_path_matches_diagram(path: Path) -> None:
    assert simple_path(load_jsonl(path)) == diagram()["paths"][path.stem]


def _base(**overrides) -> dict:
    event = {
        "schema_version": "0.1.0",
        "event_id": "evt_1",
        "seq": 1,
        "ts": "2026-10-29T14:00:00.000Z",
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "turn_id": "turn_1",
        "session_id": "sess_1",
        "tenant_id": "tenant_1",
        "type": "node_enter",
        "node_id": "gateway",
        "status": "active",
    }
    event.update(overrides)
    return {k: v for k, v in event.items() if v is not None}


@pytest.mark.parametrize(
    "event",
    [
        _base(node_id="not_a_node"),
        _base(status="done"),
        _base(type="node_exit", status="done"),  # no duration_ms
        _base(type="node_paused", status="waiting", summary={"reason": "tired"}),
        _base(type="edge", node_id=None, status="done"),  # no edge_id
        _base(type="turn_start", node_id=None, seq=3),
        _base(type="error", status="error"),  # no error object
        _base(trace_id="0" * 32),
        _base(ts="2026-10-29 14:00:00"),
        _base(tenant_id=None),
        _base(extra_field=True),
    ],
    ids=[
        "unknown-node", "enter-not-active", "exit-no-duration", "bad-pause-reason",
        "edge-no-id", "turn-start-not-seq0", "error-no-object", "zero-trace-id",
        "not-rfc3339", "no-tenant", "unknown-field",
    ],
)  # fmt: skip
def test_schema_rejects_bad_events(event: dict) -> None:
    assert list(event_validator().iter_errors(event)), event


def test_stream_rules_catch_bad_nesting() -> None:
    events = load_jsonl(SPEC / "examples" / "plain_answer.jsonl")
    exits = [i for i, e in enumerate(events) if e["type"] == "node_exit"]
    del events[exits[0]]  # drop the first node_exit (gateway receive)
    for i, e in enumerate(events):
        e["seq"] = i
    assert check_stream(events)
