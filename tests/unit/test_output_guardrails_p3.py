"""Output guardrails P3: citations, PII leaks, schema states and the placeholder restore."""

from pathlib import Path

import pytest

from harnesswalk.control.pii import PiiDetector, Vault
from harnesswalk.execution.output_guardrails import (
    SAFE_CITATION_MESSAGE,
    SAFE_PII_MESSAGE,
    OutputGuardrails,
    citation_report,
)
from tests.unit.support import exits, harness, make_ctx, run_turn

CHUNKS = [{"n": 1, "doc": "returns.md"}, {"n": 2, "doc": "shipping.md"}]


def test_citation_report_counts_markers_invalid_and_uncited_claims() -> None:
    text = (
        "Returns are accepted within thirty days of delivery [1]. Shipping takes three to five"
        " business days in Canada. See also [7]. Anything else?"
    )
    assert citation_report(text, CHUNKS) == {"present": 2, "invalid": 1, "uncited_claims": 1}


def test_missing_citations_repair_in_dev_and_block_when_configured() -> None:
    dev = OutputGuardrails({"citations": {"required": True, "on_missing": "repair"}}, "dev")
    repaired = dev.check("Returns are accepted within thirty days of delivery.", None, CHUNKS)
    assert repaired.action == "repair" and "No sources were cited" in repaired.text
    public = OutputGuardrails({"citations": {"required": True, "block_public": True}}, "public")
    blocked = public.check("Returns are accepted within thirty days of delivery.", None, CHUNKS)
    assert (blocked.action, blocked.text) == ("block", SAFE_CITATION_MESSAGE)
    uncited = public.check(
        "Returns are accepted within thirty days [1]. Shipping takes three to five days here.",
        None,
        CHUNKS,
    )
    assert uncited.action == "block" and uncited.reasons == ["uncited_claims"]
    assert dev.check("No knowledge, no requirement here at all.").citations["required"] is False


def test_pii_leak_is_redacted_or_blocked() -> None:
    g = OutputGuardrails({}, "dev")
    leak = g.check("The customer on file is bob@example.com, card 4111 1111 1111 1111.")
    assert leak.action == "repair" and leak.leaks == {"email": 1, "card": 1}
    assert "bob@example.com" not in leak.text and "[EMAIL]" in leak.text
    strict = OutputGuardrails({"pii": {"action": "block"}}, "dev")
    assert strict.check("Call 416-555-0199.").text == SAFE_PII_MESSAGE
    assert g.check("I sent it to [EMAIL_1].").leaks == {}  # this turn's own placeholder


@pytest.mark.anyio
async def test_restore_follows_restore_modes() -> None:
    d = PiiDetector()
    for mode, restored in (("dev", True), ("public", False), ("private", False)):
        ctx, _ = make_ctx()
        ctx.mode = mode
        ctx.pii_vault = Vault()
        d.redact("ana@example.com", d.find("ana@example.com"), ctx.pii_vault)
        g = OutputGuardrails({}, mode, pii_cfg={"restore_modes": ["dev"]})
        out = await g.run(ctx, "Sent to [EMAIL_1].")
        assert ("ana@example.com" in out.text) is restored
        assert out.restored == (1 if restored else 0)


@pytest.mark.anyio
async def test_schema_state_is_reported(tmp_path: Path) -> None:
    g = OutputGuardrails({}, "dev")
    assert g.check('{"a": 1}', {"type": "object"}).schema == "valid"
    assert g.check("[]", {"type": "object"}).schema == "invalid"
    assert g.check("plain").schema == "none"


@pytest.mark.anyio
async def test_turn_redacts_before_the_model_and_keeps_placeholders_in_the_session(
    tmp_path: Path,
) -> None:
    from tests.unit.support import write_fixture

    fixtures = tmp_path / "fx"
    reply = {
        "text": "Noted, I will write to [EMAIL_1].",
        "expect": {"last_message_contains": "[EMAIL_1]"},
    }
    write_fixture(fixtures, "echo_email", [[reply]])
    h = harness(tmp_path, "echo_email", fixtures=fixtures)
    events = await run_turn(h, "My email is ana@example.com, write to me there.")
    assert events[-1]["summary"]["reply"] == "Noted, I will write to ana@example.com."
    assert exits(events, "input_guardrails")[0]["summary"]["redactions"] == {"email": 1}
    upstream = [e for e in events if e["type"] not in ("message_delta", "turn_end")]
    assert "ana@example.com" not in str(upstream)  # only the reply carries it back (dev)
    row = h.session.db.one("SELECT user_text, reply_text FROM turns")
    assert "ana@example.com" not in row["user_text"] + row["reply_text"]
    assert "[EMAIL_1]" in row["user_text"] and "[EMAIL_1]" in row["reply_text"]
