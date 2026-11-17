"""Personal data: emails, phone numbers, Luhn-valid card numbers and national ids.

Used by the input guardrails (redaction with numbered placeholders), the output guardrails
(leaks), the memory writer (proposals are redacted before they are stored) and the golden
scorer `pii_leak`. Regular expressions, deterministic; a pack adds its own national id
patterns in `packs/<pack>/guardrails.yaml`.
"""

from __future__ import annotations

import re
from typing import Any

PII = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "card": re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"),
    "national_id": re.compile(r"(?<!\d)\d{3}[- ]\d{2}[- ]\d{4}(?!\d)|(?<!\d)\d{3}[- ]\d{3}[- ]\d{3}(?!\d)"),
    "phone": re.compile(r"(?<![\d-])(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?![\d-])"),
}  # fmt: skip


def luhn(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()][::-1]
    total = sum(n if i % 2 == 0 else (n * 2 - 9 if n > 4 else n * 2) for i, n in enumerate(nums))
    return 13 <= len(nums) <= 19 and total % 10 == 0


def find_pii(text: str, extra: list[dict[str, Any]] | None = None) -> list[tuple[str, str]]:
    """(kind, value) in text order. A card must pass Luhn; pack patterns add national ids."""
    found: list[tuple[int, str, str]] = []
    patterns = list(PII.items()) + [("national_id", re.compile(p["regex"])) for p in extra or []]
    for kind, rx in patterns:
        for m in rx.finditer(text):
            if kind == "card" and not luhn(m.group()):
                continue
            if any(s <= m.start() < e for s, _, e in [(f[0], 0, f[0] + len(f[2])) for f in found]):
                continue
            found.append((m.start(), kind, m.group()))
    return [(k, v) for _, k, v in sorted(found)]


def redact(text: str, mapping: dict[str, str], extra: list[dict[str, Any]] | None = None):
    """Numbered placeholders; the same value keeps its placeholder within the turn."""
    counts: dict[str, int] = {}
    for kind, value in find_pii(text, extra):
        if value not in mapping:
            n = sum(1 for p in mapping.values() if p.startswith(f"[{kind.upper()}_")) + 1
            mapping[value] = f"[{kind.upper()}_{n}]"
        text = text.replace(value, mapping[value])
        counts[kind] = counts.get(kind, 0) + 1
    return text, counts
