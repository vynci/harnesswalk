"""Output guardrails: check the answer before it leaves.

Contract: spec/contracts/guardrail.md. Node: `output_guardrails` (on from the `guarded` rung).
Checks: `schema` (a requested JSON shape, repaired out of prose or a code fence, else
blocked), `content` (a literal blocklist), `length`, `citations` (an answer drawn from the
numbered passages must cite one) and `pii` (personal data the user did not type is redacted).
In dev the user's own values come back in place of their placeholders.

Lesson: a malformed, uncited or leaking answer should never be the user's problem to spot.
"""

from __future__ import annotations

import json
import re
from typing import Any

from harness.core.pii import find_pii
from harness.core.tools import check_schema

MARKER = re.compile(r"\[(\d{1,2})\]")
FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.S)
WORD = re.compile(r"[a-z0-9]+")
SAFE = "I could not produce an answer in the required format."


def extract_json(text: str, schema: dict[str, Any]) -> tuple[Any, bool] | None:
    """(value, repaired) for the first JSON value in text valid against schema."""
    try:
        value = json.loads(text)
        if not check_schema(schema, value):
            return value, False
    except ValueError:
        pass
    candidates = FENCE.findall(text)
    candidates += [m.group() for m in re.finditer(r"\{.*\}|\[.*\]", text, re.S)]
    for c in candidates:
        try:
            value = json.loads(c)
        except ValueError:
            continue
        if not check_schema(schema, value):
            return value, True
    return None


def drawn(sentence: str, chunk: str) -> bool:
    words = set(WORD.findall(sentence.lower()))
    shared = words & set(WORD.findall(chunk.lower()))
    return len(words) >= 6 and len({w for w in shared if len(w) > 3}) >= 3


def run(h: Any, turn: Any, text: str, output_schema: dict[str, Any] | None) -> tuple[str, bool]:
    """Returns (the reply, blocked)."""
    cfg = h.settings.get("output_guardrails", {})
    checks = cfg.get("checks") or ["schema", "content", "citations"]
    numbered = turn.data.get("citations") or []
    with turn.span("output_guardrails") as span:
        action, reasons, schema_state = "pass", [], "none"
        if output_schema and "schema" in checks:
            found = extract_json(text, output_schema)
            if found is None:
                action, text, schema_state = "block", SAFE, "invalid"
            else:
                schema_state = "valid"
                if found[1]:
                    action, text = "repair", json.dumps(found[0])
                    reasons.append("schema")
        for literal in (cfg.get("content") or {}).get("blocklist") or []:
            if action != "block" and literal.lower() in text.lower():
                action, text = "block", "I cannot answer that."
                reasons.append("content")
        if action != "block" and len(text) > int(cfg.get("max_chars", 12000)):
            action, text = "repair", text[: int(cfg.get("max_chars", 12000))]
            reasons.append("length")
        markers = [int(n) for n in MARKER.findall(text)]
        invalid = [n for n in markers if not any(c["n"] == n for c in numbered)]
        claims = [s for s in re.split(r"(?<=[.!?])\s+", text)
                  if any(drawn(s, c["text"]) for c in numbered)]  # fmt: skip
        required = bool(numbered) and bool((cfg.get("citations") or {}).get("required", True))
        uncited = [s for s in claims if not MARKER.search(s)]
        if (action != "block" and "citations" in checks and required and not markers and claims
                and not turn.data.get("executed")):  # fmt: skip
            best = next(c for c in numbered if any(drawn(s, c["text"]) for s in claims))
            text += f"\n\nSource: [{best['n']}] {best['citation']}"
            action = "repair" if action == "pass" else action
            reasons.append("citations")
        leaks: dict[str, int] = {}
        if "pii" in checks:
            for kind, value in find_pii(text):
                leaks[kind] = leaks.get(kind, 0) + 1
                text = text.replace(value, f"[{kind.upper()}]")
            if leaks and action == "pass":
                action = "repair"
        restored = 0
        for value, placeholder in (turn.data.get("pii_map") or {}).items():
            if placeholder in text:  # dev: the user's own values come back (guardrail.md, P3)
                text, restored = text.replace(placeholder, value), restored + 1
        span.set(action=action, checks=checks, schema=schema_state, restored=restored,
                 citations={"required": required, "present": len(markers), "invalid": len(invalid),
                            "drawn_claims": len(claims), "uncited_claims": len(uncited)},
                 leaks=leaks or None, reasons=reasons or None)  # fmt: skip
    return text, action == "block"
