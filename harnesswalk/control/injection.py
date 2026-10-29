"""Prompt-injection heuristics: a score in 0..1 and which signals fired. Deterministic.

Not a node: `input_guardrails` calls it (spec/contracts/guardrail.md, P3 additions).

Each category has a weight. The score is a noisy-or over the categories that fired at least
once: `1 - prod(1 - weight)`. One strong signal (an instruction override) reaches the default
block threshold of 0.8 on its own; weaker ones need company.

Encoded payloads (base64, hex, percent or unicode escapes) count a little by themselves, and
their decoded text is scanned again: an override hidden in base64 scores as `encoded_override`.

These are heuristics: they catch the common, lazy attacks and they miss clever ones. That is
the lesson of the node. The optional classifier hook in `input_guardrails.py` (off by
default) is how a deployment adds a model's opinion on top.
"""

from __future__ import annotations

import base64
import binascii
import re
import urllib.parse
from dataclasses import dataclass, field

_I = re.IGNORECASE

SIGNALS: dict[str, tuple[float, list[re.Pattern[str]]]] = {
    "override": (
        0.8,
        [
            re.compile(
                r"\b(ignore|disregard|forget|override|bypass|skip)\b[^.\n]{0,40}"
                r"\b(previous|prior|above|earlier|all|any|your|the|these)\b[^.\n]{0,30}"
                r"\b(instructions?|rules|prompts?|guidelines|directions|polic(y|ies))\b",
                _I,
            ),
            re.compile(r"\bnew (system )?instructions?\s*:", _I),
            re.compile(r"\byou (must|will) now (only )?(obey|follow|do)\b", _I),
        ],
    ),
    "exfiltration": (
        0.5,
        [
            re.compile(
                r"\b(reveal|print|show|repeat|output|tell me|leak|dump)\b[^.\n]{0,30}"
                r"\b(system prompt|hidden (prompt|instructions)|initial instructions|"
                r"developer (message|prompt)|your (prompt|instructions))\b",
                _I,
            ),
        ],
    ),
    "role_escalation": (
        0.45,
        [
            re.compile(r"\byou are (now|no longer)\b", _I),
            re.compile(r"\bfrom now on,? you\b", _I),
            re.compile(r"\bpretend (to be|you are|you're)\b", _I),
            re.compile(
                r"\bact as (an?|the|my)?\s*(admin|administrator|developer|root|system|"
                r"unrestricted|jailbroken|evil)\b",
                _I,
            ),
            re.compile(r"\b(developer|god|dan|jailbreak) mode\b", _I),
        ],
    ),
    "jailbreak": (
        0.5,
        [
            re.compile(
                r"\b(without|no|free of|ignore) (any )?(restrictions|filters|limits|rules|"
                r"guardrails|safety)\b",
                _I,
            ),
            re.compile(r"\bdo anything now\b", _I),
        ],
    ),
    "role_tag": (
        0.45,
        [
            re.compile(r"(^|\n)\s*(system|assistant|developer)\s*:", _I),
            re.compile(r"<\|?(im_start|im_end|system|endoftext)\|?>", _I),
            re.compile(r"\[/?(INST|SYS)\]|<</?SYS>>|###\s*(system|instruction)", _I),
        ],
    ),
}
ENCODED_WEIGHT = 0.25
ENCODED_OVERRIDE_WEIGHT = 0.8

BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
HEX_RUN = re.compile(r"\b(?:[0-9a-fA-F]{2}){16,}\b")
PERCENT_RUN = re.compile(r"(?:%[0-9a-fA-F]{2}){8,}")
UNICODE_RUN = re.compile(r"(?:\\u[0-9a-fA-F]{4}){6,}")


@dataclass
class InjectionReport:
    score: float
    signals: dict[str, int] = field(default_factory=dict)


def _decode_runs(text: str) -> list[str]:
    """Decoded text of every encoded-looking run that decodes to mostly printable text."""
    out: list[str] = []
    for m in BASE64_RUN.finditer(text):
        raw = m.group(0)
        try:
            decoded = base64.b64decode(raw + "=" * (-len(raw) % 4), validate=True)
        except (binascii.Error, ValueError):
            continue
        out.append(decoded.decode("utf-8", "ignore"))
    for m in HEX_RUN.finditer(text):
        out.append(bytes.fromhex(m.group(0)).decode("utf-8", "ignore"))
    for m in PERCENT_RUN.finditer(text):
        out.append(urllib.parse.unquote(m.group(0)))
    for m in UNICODE_RUN.finditer(text):
        out.append(m.group(0).encode().decode("unicode_escape", "ignore"))
    printable = []
    for s in out:
        if s and sum(ch.isprintable() or ch.isspace() for ch in s) / len(s) > 0.9:
            printable.append(s)
    return printable


def _hits(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for category, (_, patterns) in SIGNALS.items():
        n = sum(len(p.findall(text)) for p in patterns)
        if n:
            counts[category] = n
    return counts


def score(text: str) -> InjectionReport:
    signals = _hits(text)
    weights = [SIGNALS[c][0] for c in signals]
    decoded = _decode_runs(text)
    if decoded:
        signals["encoded"] = len(decoded)
        weights.append(ENCODED_WEIGHT)
        hidden = sum(_hits(s).get("override", 0) + _hits(s).get("exfiltration", 0) for s in decoded)
        if hidden:
            signals["encoded_override"] = hidden
            weights.append(ENCODED_OVERRIDE_WEIGHT)
    remaining = 1.0
    for w in weights:
        remaining *= 1 - w
    return InjectionReport(round(1 - remaining, 3), signals)
