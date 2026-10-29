"""Input and output guardrails are deterministic code with counts-only summaries."""

from pathlib import Path

import pytest

from harnesswalk.control.input_guardrails import InputGuardrails, strip_control
from harnesswalk.execution.output_guardrails import SAFE_SCHEMA_MESSAGE, OutputGuardrails
from tests.unit.support import exits, harness, path_of, run_turn


def test_control_characters_are_stripped() -> None:
    text, removed = strip_control("hi\x00 there\x1b[31m‮!\tok\n")
    assert text == "hi there[31m!\tok\n" and removed == 3
    result = InputGuardrails({"checks": ["control", "size"]}, "dev").check("a\x07b")
    assert (result.action, result.text) == ("redact", "ab")
    assert result.results[0].findings == {"control": 1}


def test_size_blocks_and_stops_later_checks() -> None:
    g = InputGuardrails(
        {"checks": ["control", "size", "pii", "injection"], "size": {"max_chars": 5}}, "dev"
    )
    result = g.check("123456")
    assert result.action == "block" and "6 characters" in result.reason
    assert [r.check for r in result.results] == ["control", "size"]
    ok = g.check("1234")
    assert ok.action == "pass" and ok.pending == []
    assert [r.check for r in ok.results] == ["control", "size", "pii", "injection"]


def test_output_checks() -> None:
    g = OutputGuardrails({"max_chars": 10, "content": {"blocklist": ["SECRET"]}}, "dev")
    assert g.check("short").action == "pass"
    long = g.check("x" * 50)
    assert long.action == "repair" and long.text.endswith("[reply truncated]")
    assert g.check("the SECRET").action == "block"
    schema = {"type": "object", "required": ["a"]}
    assert g.check('{"a": 1}', output_schema=schema).action == "pass"
    bad = g.check("not json", output_schema=schema)
    assert (bad.action, bad.text) == ("block", SAFE_SCHEMA_MESSAGE)
    cites = OutputGuardrails({"citations": {"required": True, "on_missing": "repair"}}, "dev")
    missing = cites.check("The return window is thirty days long.", knowledge_chunks=[{"n": 1}])
    assert missing.action == "repair" and missing.citations["required"] is True
    assert missing.citations["present"] == 0


@pytest.mark.anyio
async def test_block_skips_the_model_and_never_echoes_input(tmp_path: Path) -> None:
    h = harness(tmp_path, "plain_answer", overrides={"input_guardrails.size.max_chars": 20})
    secret = "my card is 4111 1111 1111 1111 and this is long"
    events = await run_turn(h, secret)
    assert path_of(events) == ["gateway", "input_guardrails", "gateway", "trace"]
    assert events[-1]["summary"]["exit"] == "blocked" and events[-1]["status"] == "done"
    assert "4111" not in str(events)


@pytest.mark.anyio
async def test_disabled_guardrails_are_skipped(tmp_path: Path) -> None:
    h = harness(tmp_path, "plain_answer")
    events = await run_turn(
        h, "What are your support hours?", disabled=["input_guardrails", "output_guardrails"]
    )
    for node_id in ("input_guardrails", "output_guardrails"):
        ex = exits(events, node_id)[0]
        assert ex["status"] == "skipped" and ex["summary"]["disabled"] is True
