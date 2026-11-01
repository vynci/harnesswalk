"""Deterministic scorers: what a case turn's events and reply must show.

Contract: spec/contracts/evaluator.md (P4 additions), names fixed by http_api.md. Each scorer
reads only the stored event stream and the reply text, never the model's reasoning, and
returns `{value, pass, detail}`:

    path          the expected simple-view nodes appear in this order (a subsequence)
    citations     every expected {doc, chunk?} is in turn_end.summary.citations
    tool_calls    the tool names the turn called, in order, equal the expected list
    schema        the reply is JSON valid against the case's output_schema
    pii_leak      no personal data in the reply except values the user typed
    answer_match  substring (all of them), exact, or regex
    checks        the case's `checks` (P5): the router rule of each model call, in order, and
                  a substring each named store put in context (from the context builder's
                  stored detail)
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import jsonschema

from harnesswalk import diagram
from harnesswalk.events import Event
from harnesswalk.ops.eval.golden import GoldenCase

DETERMINISTIC = (
    "path",
    "citations",
    "tool_calls",
    "schema",
    "pii_leak",
    "answer_match",
    "checks",
)
JUDGED = ("faithfulness", "relevance")


def simple_path(events: list[Event]) -> list[str]:
    simple = {n["id"] for n in diagram.diagram()["nodes"] if n["view"] == "simple"}
    return [e["node_id"] for e in events if e["type"] == "node_enter" and e["node_id"] in simple]


def actual_of(events: list[Event]) -> dict[str, Any]:
    """What the turn did, in the shape of a run detail's `actual`."""
    end = next((e for e in reversed(events) if e["type"] == "turn_end"), None)
    summary = (end or {}).get("summary") or {}
    tool_calls = [
        (e.get("summary") or {}).get("tool")
        for e in events
        if e["type"] == "node_enter" and e.get("node_id") == "tools"
    ]
    return {
        "reply": summary.get("reply"),
        "exit": summary.get("exit"),
        "status": (end or {}).get("status"),
        "path": simple_path(events),
        "citations": summary.get("citations") or [],
        "tool_calls": [t for t in tool_calls if t],
        "cost": (end or {}).get("cost"),
        # Why a turn went wrong, so a failed case explains itself (fixture_mismatch after an
        # edited skill, provider_error, ...).
        "errors": [
            {
                "node_id": e.get("node_id"),
                "code": e["error"]["code"],
                "message": e["error"]["message"][:200],
            }
            for e in events
            if e["type"] == "error"
        ],
    }


def _result(value: float, passed: bool, detail: str) -> dict[str, Any]:
    return {"value": round(float(value), 4), "pass": bool(passed), "detail": detail[:500]}


def score_path(expected: list[str], actual: list[str]) -> dict[str, Any]:
    i = 0
    for node in actual:
        if i < len(expected) and node == expected[i]:
            i += 1
    found = i / len(expected) if expected else 1.0
    if i == len(expected):
        return _result(1.0, True, "every expected node, in order")
    return _result(found, False, f"missing {expected[i]!r} after {expected[:i]} in {actual}")


def score_citations(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> dict:
    missing = []
    for want in expected:
        hit = any(
            all(c.get(k) == v for k, v in want.items() if k in ("doc", "chunk")) for c in actual
        )
        if not hit:
            missing.append(want or {"any": True})
    if not missing:
        cited = sorted({c.get("doc") for c in actual if c.get("doc")})
        return _result(1.0, True, f"cites {', '.join(cited) or 'something'}")
    got = [f"{c.get('doc')}#{c.get('chunk')}" for c in actual]
    return _result(
        1 - len(missing) / max(1, len(expected)), False, f"missing {missing}, cited {got}"
    )


def score_tool_calls(expected: list[str], actual: list[str]) -> dict[str, Any]:
    if list(expected) == list(actual):
        return _result(1.0, True, f"called {actual or 'no tool'}")
    return _result(0.0, False, f"expected {expected}, called {actual}")


def score_schema(schema: dict[str, Any], reply: str | None) -> dict[str, Any]:
    try:
        jsonschema.validate(json.loads(reply or ""), schema)
    except json.JSONDecodeError:
        return _result(0.0, False, "the reply is not JSON")
    except jsonschema.ValidationError as exc:
        return _result(0.0, False, f"not valid: {exc.message}")
    return _result(1.0, True, "valid against output_schema")


def score_pii(detector: Any, prompt: str, reply: str | None) -> dict[str, Any]:
    text = reply or ""
    leaks = []
    for finding in detector.find(text):
        value = text[finding.start : finding.end]
        if value and value not in prompt:  # the user's own value, restored on purpose in dev
            leaks.append(finding.kind)
    if not leaks:
        return _result(0, True, "no personal data the user did not give")
    return _result(len(leaks), False, f"{len(leaks)} leak(s): {sorted(set(leaks))}")


def score_answer(expect: dict[str, Any], reply: str | None) -> dict[str, Any]:
    text = reply or ""
    if "answer_exact" in expect:
        ok = text.strip() == str(expect["answer_exact"]).strip()
        return _result(1.0 if ok else 0.0, ok, "exact match" if ok else "not the exact answer")
    if "answer_regex" in expect:
        ok = re.search(expect["answer_regex"], text) is not None
        return _result(1.0 if ok else 0.0, ok, f"regex {expect['answer_regex']!r}")
    wanted = list(expect.get("answer_contains") or [])
    missing = [w for w in wanted if w not in text]
    if not missing:
        return _result(1.0, True, f"contains {wanted}")
    return _result(1 - len(missing) / len(wanted), False, f"missing {missing}")


def score_checks(
    checks: dict[str, Any], events: list[Event], get_detail: Callable[[str], Any]
) -> dict[str, Any]:
    """`router_rules` and `retrieved` against the case turn's stored events and details."""
    problems = []
    want_rules = checks.get("router_rules")
    if want_rules is not None:
        got = [
            (e.get("summary") or {}).get("rule")
            for e in events
            if e["type"] == "node_exit" and e.get("node_id") == "router"
        ]
        if got != list(want_rules):
            problems.append(f"router rules {got}, expected {list(want_rules)}")
    retrieved = checks.get("retrieved") or {}
    if retrieved:
        detail: dict[str, Any] = {}
        for e in events:
            if e["type"] == "node_exit" and e.get("node_id") == "context_builder":
                detail = (get_detail(e["detail_ref"]) if e.get("detail_ref") else None) or {}
        items = detail.get("items") or {}
        for store, needle in retrieved.items():
            texts = [h.get("text", "") for h in items.get(store) or []]
            if not any(needle in t for t in texts):
                problems.append(f"{store} context has no {needle!r} (got {texts[:3]})")
    total = (want_rules is not None) + len(retrieved)
    if not problems:
        return _result(1.0, True, "every check holds")
    return _result(1 - len(problems) / max(1, total), False, "; ".join(problems))


def deterministic(
    case: GoldenCase,
    actual: dict[str, Any],
    detector: Any,
    names: tuple[str, ...],
    *,
    events: list[Event] | None = None,
    get_detail: Callable[[str], Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """The applicable deterministic scorers among `names`, by name. `checks` needs the case
    turn's `events` and a `get_detail` for its stored details."""
    e = case.expect
    out: dict[str, dict[str, Any]] = {}
    if "path" in names and e.get("path"):
        out["path"] = score_path(e["path"], actual["path"])
    if "citations" in names and "citations" in e:
        out["citations"] = score_citations(e["citations"], actual["citations"])
    if "tool_calls" in names and "tool_calls" in e:
        out["tool_calls"] = score_tool_calls(e["tool_calls"], actual["tool_calls"])
    if "schema" in names and case.output_schema is not None:
        out["schema"] = score_schema(case.output_schema, actual["reply"])
    if "pii_leak" in names:
        out["pii_leak"] = score_pii(detector, case.prompt, actual["reply"])
    if "answer_match" in names and {"answer_contains", "answer_exact", "answer_regex"} & set(e):
        out["answer_match"] = score_answer(e, actual["reply"])
    if "checks" in names and case.checks and events is not None:
        out["checks"] = score_checks(case.checks, events, get_detail or (lambda _ref: None))
    return out
