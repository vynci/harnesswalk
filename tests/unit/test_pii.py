"""PII detection, Luhn, numbered placeholders and their restore (control/pii.py)."""

import pytest

from harnesswalk.control.pii import PiiDetector, Vault, luhn_ok


@pytest.mark.parametrize(
    ("digits", "ok"),
    [
        ("4111111111111111", True),
        ("4111111111111112", False),
        ("5555555555554444", True),
        ("378282246310005", True),
        ("046454286", True),  # a valid Canadian SIN
        ("046454287", False),
        ("1", False),
        ("12a4", False),
    ],
)
def test_luhn(digits: str, ok: bool) -> None:
    assert luhn_ok(digits) is ok


def test_finds_each_kind_and_skips_lookalikes() -> None:
    d = PiiDetector({"kinds": ["email", "phone", "card", "national_id"]})
    text = (
        "Mail ana.lopez@example.com, call +1 (416) 555-0199, card 4111 1111 1111 1111,"
        " not 4111 1111 1111 1112, SSN 123-45-6789, SIN 046 454 286."
        " Order A-1001 shipped 2026-10-24, total 42.00."
    )
    assert d.counts(d.find(text)) == {"email": 1, "phone": 1, "card": 1, "national_id": 2}
    assert d.find("Order A-1001 shipped on 2026-10-24 and is due 2026-10-30.") == []


def test_numbered_placeholders_restore_and_repeat() -> None:
    d, vault = PiiDetector(), Vault()
    text = "Write to a@example.com or b@example.com; again: a@example.com."
    redacted = d.redact(text, d.find(text), vault)
    assert redacted == "Write to [EMAIL_1] or [EMAIL_2]; again: [EMAIL_1]."
    assert vault.restore("Sent to [EMAIL_2] and [EMAIL_1], not [EMAIL_9].") == (
        "Sent to b@example.com and a@example.com, not [EMAIL_9].",
        2,
    )
    assert "a@example.com" not in repr(vault)  # the values never show in a repr or a log line


def test_placeholders_are_never_findings_and_plain_redaction_is_typed() -> None:
    d = PiiDetector()
    assert d.find("send it to [EMAIL_1] please") == []
    text = "card 4111-1111-1111-1111"
    assert d.redact(text, d.find(text)) == "card [CARD]"
    assert d.scrub({"to": "x@example.com", "n": 1, "list": ["4111111111111111"]}) == {
        "to": "[EMAIL]",
        "n": 1,
        "list": ["[CARD]"],
    }


def test_national_id_patterns_per_pack_and_optional_names() -> None:
    d = PiiDetector(
        {
            "kinds": ["national_id"],
            "national_id": {"patterns": [{"name": "uk_nino", "regex": r"\b[A-Z]{2}\d{6}[A-D]\b"}]},
            "names": {"enabled": True},
        }
    )
    found = d.find("My name is Ana Lopez and my NINO is QQ123456C, SSN 123-45-6789.")
    assert d.counts(found) == {"national_id": 1, "name": 1}  # the SSN pattern was replaced
    assert [f.value for f in found] == ["Ana Lopez", "QQ123456C"]


def test_unknown_kind_is_a_config_error() -> None:
    with pytest.raises(ValueError):
        PiiDetector({"kinds": ["email", "dna"]})
