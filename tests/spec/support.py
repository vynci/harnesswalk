"""Shared helpers for the spec checks.

This is the offline half of the conformance runner: the stream rules and matchers from
spec/conformance/README.md, applied to recorded event streams. The P1 runner applies the same
functions to live streams from the HTTP API.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "spec"

POINT_TYPES = {
    "approval_requested",
    "approval_decided",
    "memory_proposed",
    "memory_written",
    "artifact_released",
    "message_delta",
    "node_paused",
    "node_resumed",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@cache
def diagram() -> dict:
    return load_json(SPEC / "diagram" / "diagram.json")


@cache
def event_validator() -> jsonschema.Draft202012Validator:
    schema = load_json(SPEC / "events.schema.json")
    return jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )


def nodes_by_id() -> dict[str, dict]:
    return {n["id"]: n for n in diagram()["nodes"]}


def simple_ids() -> set[str]:
    return {n["id"] for n in diagram()["nodes"] if n["view"] == "simple"}


def edge_ids() -> set[str]:
    return {e["id"] for e in diagram()["edges"]}


def simple_path(events: list[dict]) -> list[str]:
    simple = simple_ids()
    return [e["node_id"] for e in events if e["type"] == "node_enter" and e["node_id"] in simple]


def closing_statuses(events: list[dict]) -> list[str]:
    """Status of the event that closed each simple-path entry, in path order."""
    simple = simple_ids()
    stack: list[tuple[str, int]] = []
    closed: dict[int, str] = {}
    for i, e in enumerate(events):
        if e["type"] == "node_enter":
            stack.append((e["node_id"], i))
        elif e["type"] in ("node_exit", "error") and e.get("node_id"):
            _, opened_at = stack.pop()
            closed[opened_at] = e["status"]
    return [
        closed[i]
        for i, e in enumerate(events)
        if e["type"] == "node_enter" and e["node_id"] in simple
    ]


def check_stream(events: list[dict]) -> list[str]:
    """Return every violation of the stream rules in spec/events.md section 1."""
    problems: list[str] = []
    nodes = nodes_by_id()
    edges = edge_ids()
    validator = event_validator()

    for e in events:
        for err in validator.iter_errors(e):
            problems.append(f"seq {e.get('seq')}: schema: {err.message}")
    if problems:
        return problems

    if not events or events[0]["type"] != "turn_start":
        problems.append("first event is not turn_start")
    if events and events[-1]["type"] != "turn_end":
        problems.append("last event is not turn_end")

    first = events[0]
    for key in ("trace_id", "turn_id", "session_id", "tenant_id"):
        if any(e[key] != first[key] for e in events):
            problems.append(f"{key} changes within the turn")

    ids = [e["event_id"] for e in events]
    if len(ids) != len(set(ids)):
        problems.append("duplicate event_id")

    prev_ts = None
    stack: list[str] = []
    paused: set[str] = set()
    for i, e in enumerate(events):
        if e["seq"] != i:
            problems.append(f"event {i}: seq is {e['seq']}, expected {i}")
        ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        if prev_ts and ts < prev_ts:
            problems.append(f"seq {i}: ts goes backwards")
        prev_ts = ts

        node_id = e.get("node_id")
        if node_id is not None and node_id not in nodes:
            problems.append(f"seq {i}: unknown node_id {node_id}")
            continue
        if e["type"] == "edge" and e["edge_id"] not in edges:
            problems.append(f"seq {i}: unknown edge_id {e['edge_id']}")
        if e["type"] in ("turn_start", "turn_end") and i not in (0, len(events) - 1):
            problems.append(f"seq {i}: {e['type']} in the middle of the turn")
        if e["type"] == "turn_end" and stack:
            problems.append(f"seq {i}: turn_end with open nodes {stack}")

        if e["type"] == "node_enter":
            parent = nodes[node_id].get("parent")
            if parent and parent not in stack:
                problems.append(f"seq {i}: expert node {node_id} entered outside {parent}")
            stack.append(node_id)
        elif e["type"] in ("node_exit", "error") and node_id:
            if not stack or stack[-1] != node_id:
                top = stack[-1] if stack else None
                problems.append(f"seq {i}: {e['type']} {node_id} does not close top node {top}")
            else:
                stack.pop()
                if node_id in paused:
                    # Allowed only when the turn fails or is cancelled (events.md rule 8).
                    if e["type"] != "error":
                        problems.append(f"seq {i}: {node_id} closed while paused")
                    paused.discard(node_id)
        elif e["type"] in POINT_TYPES:
            if node_id not in stack:
                problems.append(f"seq {i}: {e['type']} for {node_id} outside its span")
            if e["type"] == "node_paused":
                paused.add(node_id)
            elif e["type"] == "node_resumed":
                if node_id not in paused:
                    problems.append(f"seq {i}: node_resumed without node_paused")
                paused.discard(node_id)
    if stack:
        problems.append(f"unclosed nodes at end: {stack}")
    return problems


def subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and subset(v, actual[k]) for k, v in expected.items()
        )
    return expected == actual


def matches(matcher: dict, event: dict) -> bool:
    return all(k in event and subset(v, event[k]) for k, v in matcher.items())


def check_expectations(expect: dict, events: list[dict]) -> list[str]:
    """Apply a conformance case's `expect` block (assertions 3 to 8) to one turn's stream."""
    problems: list[str] = []
    path = simple_path(events)
    if path != expect["path"]:
        problems.append(f"path: got {path}, expected {expect['path']}")

    if "statuses" in expect:
        want = expect["statuses"]
        got = closing_statuses(events)
        if isinstance(want, str):
            want = [want] * len(got)
        if got != want:
            problems.append(f"statuses: got {got}, expected {want}")

    cursor = 0
    for m in expect.get("events", []):
        while cursor < len(events) and not matches(m, events[cursor]):
            cursor += 1
        if cursor == len(events):
            problems.append(f"events: no match in order for {m}")
            break
        cursor += 1

    for m in expect.get("absent", []):
        hits = [e["seq"] for e in events if matches(m, e)]
        if hits:
            problems.append(f"absent: {m} matched seq {hits}")

    end = events[-1]
    for key, value in expect.get("cost", {}).items():
        if end.get("cost", {}).get(key) != value:
            problems.append(f"cost: {key} is {end.get('cost', {}).get(key)}, expected {value}")

    reply = end.get("summary", {}).get("reply", "")
    for text in expect.get("reply_contains", []):
        if text not in reply:
            problems.append(f"reply_contains: {text!r} not in reply")
    return problems
