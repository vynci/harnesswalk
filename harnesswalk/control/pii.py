"""PII detection and reversible redaction, shared by the input and output guardrails.

Not a node: `input_guardrails` and `output_guardrails` call it (spec/contracts/guardrail.md).

Kinds, checked in this order so a longer, more specific match wins over a looser one:

    email        name@example.com
    card         13 to 19 digits (spaces or dashes allowed) that pass the Luhn check
    national_id  patterns per pack (US SSN and Canadian SIN by default; SIN is Luhn-checked)
    phone        10 to 15 digits with the usual separators, optional +country code
    name         optional: a capitalised name after a cue such as "my name is"

`Vault` keeps the placeholders of one turn (`[EMAIL_1]` -> the address) in memory only, so the
reply can put the values back when the mode allows it. Nothing in here writes anywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

KINDS = ("email", "card", "national_id", "phone", "name")
LABEL = {"email": "EMAIL", "card": "CARD", "national_id": "ID", "phone": "PHONE", "name": "NAME"}

EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
PHONE = re.compile(
    r"(?<![\w+])(?<!\d[ .-])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?)?\d[\d .-]{6,16}\d"
    r"(?!\w|[ .-]\d)"
)
# Only the cue words ignore case ("My name is", "I'M"); the name itself must be capitalised,
# so "I am looking" or "I'm not sure" are not names. The flag is scoped to the cue group: a
# pattern-wide IGNORECASE made `[A-Z][a-z]+` match any word (P5 integration fix).
NAME_CUES = (
    r"(?<![\w'])(?i:my name is|my name's|i am|i'm|this is|name:|signed,?)\s+"
    r"([A-Z][a-z]+(?:[ -][A-Z][a-z]+)?)\b"
)
PLACEHOLDER = re.compile(r"\[(EMAIL|CARD|ID|PHONE|NAME)_(\d+)\]")

DEFAULT_NATIONAL_IDS: list[dict[str, Any]] = [
    {"name": "us_ssn", "regex": r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])", "luhn": False},
    {"name": "ca_sin", "regex": r"(?<![\d-])\d{3}[ -]\d{3}[ -]\d{3}(?![\d-])", "luhn": True},
]


def luhn_ok(digits: str) -> bool:
    """The Luhn checksum: double every second digit from the right, the sum ends in 0."""
    if not digits.isdigit() or len(digits) < 2:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def digits_of(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


@dataclass
class Finding:
    kind: str
    start: int
    end: int
    value: str


@dataclass
class Vault:
    """One turn's placeholders. Never serialized: it holds the original values."""

    by_value: dict[tuple[str, str], str] = field(default_factory=dict)
    by_placeholder: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def placeholder(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self.by_value:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            token = f"[{LABEL[kind]}_{self.counts[kind]}]"
            self.by_value[key] = token
            self.by_placeholder[token] = value
        return self.by_value[key]

    def restore(self, text: str) -> tuple[str, int]:
        """Put the values back. Returns the text and how many placeholders were replaced."""
        restored = 0

        def put_back(match: re.Match[str]) -> str:
            nonlocal restored
            value = self.by_placeholder.get(match.group(0))
            if value is None:
                return match.group(0)
            restored += 1
            return value

        return PLACEHOLDER.sub(put_back, text), restored

    def __len__(self) -> int:
        return len(self.by_placeholder)

    def __repr__(self) -> str:  # never the values: a vault may end up in a log line
        return f"Vault({len(self)} placeholders)"


class PiiDetector:
    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = cfg or {}
        self.kinds = [k for k in cfg.get("kinds", ["email", "phone", "card", "national_id"])]
        unknown = set(self.kinds) - set(KINDS)
        if unknown:
            raise ValueError(f"unknown PII kinds {sorted(unknown)}")
        patterns = (cfg.get("national_id") or {}).get("patterns") or DEFAULT_NATIONAL_IDS
        self.national = [(re.compile(p["regex"]), bool(p.get("luhn", False))) for p in patterns]
        names = cfg.get("names") or {}
        if names.get("enabled") and "name" not in self.kinds:
            self.kinds.append("name")
        # A configured `cues` pattern is compiled as written: scope any case-insensitivity to
        # its cue words with `(?i:...)`, as NAME_CUES does.
        self.name_re = re.compile(names.get("cues", NAME_CUES))

    def find(self, text: str) -> list[Finding]:
        """Non-overlapping findings, in text order. Placeholders are never findings."""
        taken: list[tuple[int, int]] = [m.span() for m in PLACEHOLDER.finditer(text)]
        found: list[Finding] = []

        def add(kind: str, start: int, end: int) -> None:
            if any(start < e and s < end for s, e in taken):
                return
            taken.append((start, end))
            found.append(Finding(kind, start, end, text[start:end]))

        for kind in KINDS:  # priority order
            if kind not in self.kinds:
                continue
            if kind == "email":
                for m in EMAIL.finditer(text):
                    add(kind, *m.span())
            elif kind == "card":
                for m in CARD.finditer(text):
                    digits = digits_of(m.group(0))
                    if 13 <= len(digits) <= 19 and luhn_ok(digits):
                        add(kind, *m.span())
            elif kind == "national_id":
                for regex, needs_luhn in self.national:
                    for m in regex.finditer(text):
                        if not needs_luhn or luhn_ok(digits_of(m.group(0))):
                            add(kind, *m.span())
            elif kind == "phone":
                for m in PHONE.finditer(text):
                    if 10 <= len(digits_of(m.group(0))) <= 15:
                        add(kind, *m.span())
            elif kind == "name":
                for m in self.name_re.finditer(text):
                    if m.lastindex:
                        add(kind, *m.span(1))
        return sorted(found, key=lambda f: f.start)

    @staticmethod
    def counts(findings: list[Finding]) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in findings:
            out[f.kind] = out.get(f.kind, 0) + 1
        return out

    @staticmethod
    def redact(text: str, findings: list[Finding], vault: Vault | None = None) -> str:
        """Replace each finding with a placeholder: numbered (via the vault) or plain typed."""
        out, last = [], 0
        for f in sorted(findings, key=lambda f: f.start):
            out.append(text[last : f.start])
            out.append(
                vault.placeholder(f.kind, f.value) if vault is not None else f"[{LABEL[f.kind]}]"
            )
            last = f.end
        out.append(text[last:])
        return "".join(out)

    def scrub(self, value: Any) -> Any:
        """Typed, non-reversible redaction of every string inside a JSON-like value."""
        if isinstance(value, str):
            return self.redact(value, self.find(value))
        if isinstance(value, dict):
            return {k: self.scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.scrub(v) for v in value]
        return value
