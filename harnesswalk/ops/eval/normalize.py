"""Model-grade answer matching: the normalisation the `model` suite applies before
`answer_match` compares a reply with a case's expectation.

Contract: spec/contracts/evaluator.md ("Grading modes"). The `golden` suite never uses this: a
fixture reply is scripted, so its expectations match it byte for byte. A real model words a
right answer its own way ("$6" for "6 dollars", "October 14, 2026" for "2026-10-14", "2-year"
for "2 year", "**shipped**" for "shipped"), and exact substrings would grade its phrasing
instead of its answer.

`normalize` applies, in this order, to the expectation and the reply alike:

1. Unicode NFKC, then case folding.
2. Markdown emphasis and code marks (`*`, `_`, backtick) removed.
3. Thousands separators dropped inside numbers (`1,234` is `1234`).
4. Dates to `YYYY-MM-DD` (`October 14, 2026`, `Oct 14 2026`, `14 October 2026`, `2026-10-14`);
   a date without a year to `MM-DD` (`24 October`, `October 24th`; not `may`, too often the
   verb), so it matches inside a full date.
5. Times on a 12-hour clock to 24 hours (`2:30 pm` is `14:30`, `4 pm` is `16:00`).
6. Amounts to `usd 6`, `eur 6`, `gbp 6` (`$6`, `$6.00`, `6 dollars`, `USD 6.00`, `6 usd`,
   `€6`, `6 euros`, `£6`), the amount without trailing zeros.
7. Hyphens and dashes between words or numbers read as spaces (`2-year` is `2 year`), and
   whitespace collapsed to one space.

An expectation that is a number after this (`129.00`, `42.5`) matches when the reply holds that
number, compared numerically, so `129.00` matches `$129`. Deterministic, no model calls.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}  # fmt: skip
for _name, _n in list(MONTHS.items()):
    MONTHS[_name[:3]] = _n
MONTHS["sept"] = 9
_MONTH = r"(?P<month>" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?"
_DAY = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?P<year>\d{4})"
DATE_PATTERNS = [
    re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b"),
    re.compile(rf"\b{_MONTH}\s+{_DAY},?\s+{_YEAR}\b"),
    re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH},?\s+{_YEAR}\b"),
]
# Without a year, "may" is too often the verb ("you may 3 times"), so it is left alone.
_PARTIAL_MONTH = _MONTH.replace("|may|", "|")
PARTIAL_DATE_PATTERNS = [
    re.compile(rf"\b{_PARTIAL_MONTH}\s+{_DAY}\b(?!:)"),
    re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_PARTIAL_MONTH}\b"),
]
TIME_12H = re.compile(r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?![a-z])")
_AMOUNT = r"(?P<amount>\d+(?:\.\d+)?)"
CURRENCY_PATTERNS = [
    (re.compile(rf"(?:us\$|\$|\busd\s*)\s*{_AMOUNT}"), "usd"),
    (re.compile(rf"\b{_AMOUNT}\s*(?:usd|us dollars?|dollars?|bucks)\b"), "usd"),
    (re.compile(rf"(?:€|\beur\s*)\s*{_AMOUNT}"), "eur"),
    (re.compile(rf"\b{_AMOUNT}\s*(?:eur|euros?)\b"), "eur"),
    (re.compile(rf"(?:£|\bgbp\s*)\s*{_AMOUNT}"), "gbp"),
    (re.compile(rf"\b{_AMOUNT}\s*(?:gbp|pounds?)\b"), "gbp"),
]
NUMBER = re.compile(r"\d+(?:\.\d+)?")
PLAIN_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")


def _amount(text: str) -> str:
    value = float(text)
    return str(int(value)) if value == int(value) else f"{value:g}"


def _date(match: re.Match[str], with_year: bool) -> str:
    month = match.group("month").rstrip(".")
    m = int(month) if month.isdigit() else MONTHS[month]
    d = int(match.group("day"))
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return match.group(0)
    return f"{int(match.group('year')):04d}-{m:02d}-{d:02d}" if with_year else f"{m:02d}-{d:02d}"


def _time(match: re.Match[str]) -> str:
    h, minutes = int(match.group("h")), int(match.group("m") or 0)
    if not (1 <= h <= 12 and 0 <= minutes < 60):
        return match.group(0)
    pm = match.group("ap").startswith("p")
    h = (h % 12) + (12 if pm else 0)
    return f"{h:02d}:{minutes:02d}"


def normalize(text: str) -> str:
    """The canonical form of `text` for model-grade matching (see the module docstring)."""
    s = unicodedata.normalize("NFKC", text or "").casefold()
    s = re.sub(r"[*_`]", "", s)
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)
    for pattern in DATE_PATTERNS:
        s = pattern.sub(lambda m: _date(m, True), s)
    for pattern in PARTIAL_DATE_PATTERNS:
        s = pattern.sub(lambda m: _date(m, False), s)
    s = TIME_12H.sub(_time, s)
    for pattern, code in CURRENCY_PATTERNS:
        s = pattern.sub(lambda m, code=code: f"{code} {_amount(m.group('amount'))}", s)
    s = re.sub(r"(?<=[\w])\s*[-‐-―]\s*(?=[\w])", " ", s)
    s = re.sub(r"[‐-―]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def contains(needle: str, reply_normalized: str, reply_numbers: list[float]) -> bool:
    """Whether the normalized reply holds `needle`: as a substring of the canonical form, or,
    when the needle is a plain number, as a number."""
    want = normalize(needle)
    if want and want in reply_normalized:
        return True
    if PLAIN_NUMBER.match(want):
        return any(abs(n - float(want)) < 1e-9 for n in reply_numbers)
    return False


def answer_match(expect: dict[str, Any], reply: str | None) -> tuple[bool, float, str]:
    """`answer_match` for the `model` suite: (pass, value, detail)."""
    text = normalize(reply or "")
    numbers = [float(n) for n in NUMBER.findall(text)]
    if "answer_exact" in expect:
        ok = text == normalize(str(expect["answer_exact"]))
        return ok, 1.0 if ok else 0.0, "normalized exact match" if ok else "not the exact answer"
    if "answer_regex" in expect:
        ok = re.search(expect["answer_regex"], reply or "", re.I) is not None
        return ok, 1.0 if ok else 0.0, f"regex {expect['answer_regex']!r} (case-insensitive)"
    wanted = list(expect.get("answer_contains") or [])
    missing = [w for w in wanted if not contains(w, text, numbers)]
    if not missing:
        return True, 1.0, f"contains {wanted} (normalized)"
    return False, 1 - len(missing) / len(wanted), f"missing {missing} (normalized)"
