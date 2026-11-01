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

CHUNKS = [
    {"n": 1, "doc": "returns", "text": "Items can be returned within thirty days of delivery."},
    {"n": 2, "doc": "shipping", "text": "Shipping takes three to five business days in Canada."},
]


def test_citation_report_counts_markers_invalid_and_uncited_claims() -> None:
    text = (
        "Returns are accepted within thirty days of delivery [1]. Shipping takes three to five"
        " business days in Canada. See also [7]. Anything else?"
    )
    assert citation_report(text, CHUNKS) == {
        "present": 2,
        "invalid": 1,
        "drawn_claims": 2,
        "uncited_claims": 1,
    }
    # Without chunk text every claim sentence counts as drawn (the strict reading).
    bare = [{"n": 1}]
    assert (
        citation_report("Our store opens at nine every single morning.", bare)["drawn_claims"] == 1
    )


def test_knowledge_in_context_but_not_used_is_not_penalised() -> None:
    g = OutputGuardrails({"citations": {"required": True, "on_missing": "block"}}, "dev")
    unrelated = g.check(
        "Our support team answers chat messages every weekday morning.", None, CHUNKS
    )
    assert unrelated.action == "pass" and unrelated.citations["required"] is False
    drawn = g.check("Items can be returned within thirty days of delivery.", None, CHUNKS)
    assert drawn.action == "block" and drawn.citations["required"] is True


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


HOURS = [{"n": 1, "doc": "sites", "text": "The riverside site is open Monday to Friday and closed"
          " on Saturday, with free parking on Willowbank Row and no walk-ins."}]  # fmt: skip
LOOKUP_REPLY = (
    "Riverside is open Monday to Friday 08:00 to 18:00 and closed on Saturday, with free"
    " parking on Willowbank Row."
)


def test_a_lookup_answer_needs_no_citation() -> None:
    """P5 integration: a tool's answer that shares words with a document is not a claim drawn
    from the document. Before, lookups got "(No sources were cited for this answer.)"."""
    g = OutputGuardrails({"citations": {"required": True, "on_missing": "repair"}}, "dev")
    alone = g.check(LOOKUP_REPLY, None, HOURS)
    assert alone.action == "repair" and "No sources were cited" in alone.text
    tool = '{"site": "riverside", "weekdays": "08:00-18:00", "saturday": "closed"}'
    looked_up = g.check(LOOKUP_REPLY, None, HOURS, observations=[tool])
    assert looked_up.action == "pass" and "No sources were cited" not in looked_up.text
    assert looked_up.citations["required"] is False and looked_up.citations["from_tools"]
    # A cited answer on a turn with tools is still checked (a mixed answer).
    cited = g.check(LOOKUP_REPLY + " [3]", None, HOURS, observations=[tool])
    assert cited.citations["required"] is True and cited.citations["invalid"] == 1


def test_a_fact_recalled_from_memory_explains_a_sentence() -> None:
    fact = "Investor prefers notices by portal plus courier copy for the riverside site"
    reply = "You asked for notices by portal plus a courier copy for the riverside site."
    chunk = [{"n": 1, "doc": "notices", "text": "Notices go by portal, with a courier copy on"
              " request, to the riverside site and every other site."}]  # fmt: skip
    assert citation_report(reply, chunk)["drawn_claims"] == 1
    assert citation_report(reply, chunk, [fact])["drawn_claims"] == 0


@pytest.mark.anyio
async def test_a_lookup_turn_on_the_healthcare_pack_gets_no_notice(tmp_path: Path) -> None:
    from harnesswalk.config.loader import load_settings
    from harnesswalk.runtime import Harness
    from tests.spec.support import ROOT

    settings = load_settings(
        ROOT,
        provider="fake",
        fixture="tool_clinic_hours",
        overrides={"dataset": "healthcare", "sandbox.level": "inprocess"},
        data_dir=tmp_path / "d",
        env={},
    )
    h = Harness(settings)
    try:
        events = await run_turn(h, "What are the opening hours at the riverside site?")
        end = next(e for e in events if e["type"] == "turn_end")
        assert "No sources were cited" not in end["summary"]["reply"]
        og = next(
            e for e in events if e["type"] == "node_exit" and e["node_id"] == "output_guardrails"
        )
        assert og["summary"]["action"] == "pass"
        assert og["summary"]["citations"]["required"] is False
    finally:
        await h.aclose()
