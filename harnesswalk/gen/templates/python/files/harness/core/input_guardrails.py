"""Input guardrails: deterministic checks before the model sees anything.

Contract: spec/contracts/guardrail.md. Node: `input_guardrails` (on from the `guarded` rung).
Checks, in order: `control` (strip control and bidi characters), `size` (block), `pii`
(numbered placeholders such as [EMAIL_1], restored in the reply in dev) and `injection` (a
heuristic score; dev warns, a deployment would block).

Lesson: code, not a prompt, sits between the user and the model. PII never reaches the model,
the trace or memory, and the turn can be refused before it costs anything. Remove it only for
a tool nobody outside your team can reach.
"""

from __future__ import annotations

import re
from typing import Any

from harness.core.pii import find_pii, redact  # noqa: F401 - the memory writer imports them here

INJECTION = {  # category: (weight, pattern); score = 1 - prod(1 - weight) over the fired ones
    "override": (0.6, r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|above|all)\b.{0,20}"
                      r"\b(instructions?|rules|prompt)"),
    "extraction": (0.45, r"\b(reveal|print|show|repeat)\b.{0,30}\b(system prompt|instructions)"),
    "roleplay": (0.35, r"\b(you are now|pretend to be|act as|developer mode|jailbreak)\b"),
    "markup": (0.5, r"<\|?(system|im_start)\|?>|\[INST\]|^#+\s*system\s*:"),
    "encoded": (0.3, r"[A-Za-z0-9+/]{60,}={0,2}"),
}  # fmt: skip
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069]")


def injection_score(text: str) -> tuple[float, dict[str, int]]:
    score, signals = 0.0, {}
    for name, (weight, pattern) in INJECTION.items():
        hits = len(re.findall(pattern, text, re.IGNORECASE | re.MULTILINE))
        if hits:
            signals[name] = hits
            score = 1 - (1 - score) * (1 - weight)
    return round(score, 4), signals


def run(h: Any, turn: Any, text: str) -> tuple[str, bool]:
    """Returns (the text to pass on, blocked)."""
    cfg = h.settings.get("input_guardrails", {})
    checks = cfg.get("checks") or ["control", "size", "pii", "injection"]
    with turn.span("input_guardrails") as span:
        action, reason, counts, warnings = "pass", None, {}, []
        score, signals = injection_score(text)
        if "control" in checks and CONTROL.search(text):
            text, action = CONTROL.sub("", text), "redact"
        if "size" in checks and len(text) > int((cfg.get("size") or {}).get("max_chars", 8000)):
            action, reason = "block", "size"
        if action != "block" and "pii" in checks:
            mapping = turn.data.setdefault("pii_map", {})
            text, counts = redact(text, mapping, h.pack_patterns(turn))
            action = "redact" if counts else action
        threshold = float((cfg.get("injection") or {}).get("block_threshold", 0.8))
        if action != "block" and "injection" in checks and score >= threshold:
            mode_action = ((cfg.get("injection") or {}).get("action") or {}).get("dev", "warn")
            if mode_action == "block":
                action, reason = "block", "injection"
            else:
                warnings.append("injection")
        span.set(action=action, checks=checks, redactions=counts, injection_score=score,
                 injection_signals=signals, warnings=warnings or None, reason=reason)  # fmt: skip
    return text, action == "block"
