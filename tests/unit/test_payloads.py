"""Payloads: the typed detail records of the content-bearing hops (tracer.md, "Payloads")."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from harnesswalk.control.http_api import create_app
from harnesswalk.ops import payloads
from tests.unit.support import harness, run_turn, write_fixture

EMAIL = "jo@example.com"
MESSAGE = f"Where is order A-1001? Mail me at {EMAIL}"


def payload_of(h: Any, events: list[dict], node_id: str, at: str = "exit", n: int = -1) -> dict:
    """The payload on the n-th event of a node (node_exit by default)."""
    kind = "node_exit" if at == "exit" else "node_enter"
    picked = [e for e in events if e.get("node_id") == node_id and e["type"] == kind]
    event = picked[n]
    assert event.get("detail_ref"), f"{node_id} {kind} has no detail_ref"
    record = h.tracer.get_detail(event["tenant_id"], event["detail_ref"])
    payload = record["payload"]
    assert payload["node"] == node_id and payload["at"] == at and payload["version"] == 1
    return payload


def joined(sections: list[dict], message: int) -> str:
    return "\n\n".join(s["text"] for s in sections if s["message"] == message)


@pytest.mark.anyio
async def test_gateway_keeps_received_and_redacted_with_the_placeholder_count(
    tmp_path: Path,
) -> None:
    h = harness(tmp_path, "tool_call")
    events = await run_turn(h, MESSAGE)
    receive = payload_of(h, events, "gateway", "exit", 0)
    assert receive["phase"] == "receive" and receive["received"]["text"] == MESSAGE
    reply = payload_of(h, events, "gateway")
    assert reply["phase"] == "reply" and reply["withheld"] is False
    assert reply["received"] == {"text": MESSAGE, "chars": len(MESSAGE), "truncated": False}
    assert reply["redacted"]["text"] == "Where is order A-1001? Mail me at [EMAIL_1]"
    assert reply["placeholders"] == {"count": 1, "kinds": {"email": 1}, "tokens": ["[EMAIL_1]"]}
    assert reply["reply"]["text"] == events[-1]["summary"]["reply"]
    # Events stay small: the summaries carry no text and no new keys.
    for e in events:
        assert EMAIL not in json.dumps(e.get("summary") or {}) or e["type"] == "turn_end"
        assert "has_payload" not in (e.get("summary") or {})


@pytest.mark.anyio
async def test_raw_text_is_withheld_outside_the_restore_modes(tmp_path: Path) -> None:
    h = harness(tmp_path, "tool_call", overrides={"input_guardrails.pii.restore_modes": []})
    events = await run_turn(h, MESSAGE)
    for node_id, at, n in (
        ("gateway", "enter", 0),
        ("gateway", "exit", 0),
        ("gateway", "exit", -1),
        ("input_guardrails", "enter", -1),
        ("input_guardrails", "exit", -1),
    ):
        p = payload_of(h, events, node_id, at, n)
        assert p["withheld"] is True, (node_id, at)
        assert EMAIL not in json.dumps(p), (node_id, at)
    reply = payload_of(h, events, "gateway")
    assert reply["received"] is None and "[EMAIL_1]" in reply["redacted"]["text"]
    # A message the guardrails would not change is kept as typed in every mode.
    plain = await run_turn(h, "Where is order A-1001?")
    assert payload_of(h, plain, "gateway")["received"]["text"] == "Where is order A-1001?"


@pytest.mark.anyio
async def test_input_guardrails_payload_lists_each_check(tmp_path: Path) -> None:
    h = harness(tmp_path, "tool_call")
    events = await run_turn(h, "Hi\x07 there, I am jo@example.com, where is A-1001?")
    p = payload_of(h, events, "input_guardrails")
    assert p["action"] == "redact" and p["blocked"] is False
    by_check = {c["check"]: c for c in p["checks"]}
    assert by_check["control"]["verdict"] == "redact"
    assert by_check["control"]["change"] == "removed 1 control character"
    assert by_check["control"]["looked_at"] == {"stage": "received", "chars": 48}
    pii = by_check["pii"]
    assert pii["verdict"] == "redact" and pii["findings"] == {"email": 1}
    assert pii["looked_at"]["stage"] == "after control" and pii["looked_at"]["chars"] == 47
    assert pii["change"] == "replaced 1 email with placeholders"
    assert "score" in by_check["injection"]
    assert p["output"]["text"] == "Hi there, I am [EMAIL_1], where is A-1001?"
    assert EMAIL not in json.dumps(p["checks"])  # findings are counts, never values


@pytest.mark.anyio
async def test_context_builder_payload_is_the_assembled_context_in_sections(
    tmp_path: Path,
) -> None:
    h = harness(tmp_path, "plain_answer", profile=None)
    question = "How do returns work for a damaged item?"
    await run_turn(h, question, session_id="s1")
    events = await run_turn(h, question, session_id="s1")
    p = payload_of(h, events, "context_builder")
    sections = p["sections"]
    assert [s["index"] for s in sections] == list(range(len(sections)))
    assert {s["kind"] for s in sections} >= {"system", "skill", "history", "retrieved", "user"}
    assert sections[0]["label"] == "System prompt" and sections[0]["role"] == "system"
    assert sections[-1] == {**sections[-1], "kind": "user", "role": "user", "text": question}
    assert p["total_tokens"] == sum(s["tokens"] for s in sections)
    assert p["used_tokens"] > 0 and p["budget_tokens"] == 16000
    assert p["limit_tokens"] == p["budget_tokens"] - p["reserve_output_tokens"]
    knowledge = next(s for s in sections if s["label"] == "Knowledge")
    assert knowledge["role"] == "user" and knowledge["sources"]
    first = knowledge["sources"][0]
    assert first["label"].startswith("[1] ") and first["n"] == 1 and first["doc"]
    # Sections of one message joined by a blank line are that message.
    record = h.tracer.get_detail(
        "t1",
        next(
            e["detail_ref"]
            for e in events
            if e.get("node_id") == "context_builder" and e["type"] == "node_exit"
        ),
    )
    for i, m in enumerate(record["messages"]):
        assert joined(sections, i) == m["content"]
    assert record["items"]  # the keys the eval scorers read are still there
    enter = payload_of(h, events, "context_builder", "enter")
    assert enter["input"]["text"] == question and enter["budget_tokens"] == 16000


@pytest.mark.anyio
async def test_model_layer_request_reuses_the_sections_and_shows_tool_calls(
    tmp_path: Path,
) -> None:
    h = harness(tmp_path, "tool_call")
    events = await run_turn(h, "Where is order A-1001?")
    context = payload_of(h, events, "context_builder")
    first = payload_of(h, events, "model_layer", "exit", 0)
    assert first["request"]["sections"] == context["sections"]
    assert first["request"]["total_tokens"] == context["total_tokens"]
    assert "lookup_order" in first["request"]["tools"]
    assert first["reply"]["finish_reason"] == "tool_calls"
    assert first["reply"]["tool_calls"] == [
        {"call_id": "call_1", "name": "lookup_order", "arguments": {"order_id": "A-1001"}}
    ]
    second = payload_of(h, events, "model_layer", "exit", 1)
    kinds = [s["kind"] for s in second["request"]["sections"]]
    assert kinds[-2:] == ["assistant", "tool"]
    step = second["request"]["sections"][-2]
    assert step["tool_calls"][0]["name"] == "lookup_order"
    assert second["request"]["sections"][-1]["label"] == "lookup_order result"
    assert second["reply"]["text"] == events[-1]["summary"]["reply"]
    assert second["provider"] == "fake" and second["step"] == 2
    enter = payload_of(h, events, "model_layer", "enter", 1)
    assert enter["request"] == second["request"]


@pytest.mark.anyio
async def test_output_guardrails_payload_has_before_after_and_verdicts(tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    write_fixture(fx, "long", [[{"text": "Order A-1001 is on its way. " * 4}]])
    h = harness(
        tmp_path,
        "long",
        fixtures=fx,
        overrides={
            "output_guardrails.max_chars": 40,
            "output_guardrails.checks": ["schema", "content", "length", "citations", "pii"],
        },
    )
    events = await run_turn(h, "Where is my order?")
    p = payload_of(h, events, "output_guardrails")
    assert p["action"] == "repair" and p["repairs"] == ["length"]
    assert p["before"]["chars"] == 112 and p["after"]["text"].endswith("[reply truncated]")
    verdicts = {c["check"]: c["verdict"] for c in p["checks"]}
    assert verdicts == {
        "schema": "skipped",
        "content": "pass",
        "length": "repair",
        "citations": "pass",
        "pii": "pass",
    }
    assert payload_of(h, events, "output_guardrails", "enter")["before"] == p["before"]


@pytest.mark.anyio
async def test_texts_are_capped_with_a_truncated_flag(tmp_path: Path) -> None:
    assert payloads.text("a" * 40_000) == {"text": "a" * 32_000, "chars": 40_000, "truncated": True}
    assert payloads.text(None) == {"text": "", "chars": 0, "truncated": False}
    big = payloads.tool_call({"call_id": "c", "name": "t", "arguments": {"x": "y" * 40_000}})
    assert big["arguments"] is None and big["arguments_text"]["truncated"] is True
    fx = tmp_path / "fx"
    write_fixture(fx, "huge", [[{"text": "b" * 40_000}]])
    h = harness(tmp_path, "huge", fixtures=fx, overrides={"output_guardrails.max_chars": 50_000})
    events = await run_turn(h, "Say a lot.")
    reply = payload_of(h, events, "model_layer")["reply"]
    assert reply["chars"] == 40_000 and reply["truncated"] is True and len(reply["text"]) == 32_000
    after = payload_of(h, events, "output_guardrails")["after"]
    assert after["truncated"] is True and len(after["text"]) == payloads.MAX_CHARS


@pytest.mark.anyio
async def test_no_payloads_when_content_capture_is_off(tmp_path: Path) -> None:
    h = harness(tmp_path, "tool_call", overrides={"trace.capture_content": False})
    events = await run_turn(h, MESSAGE)
    assert not [e for e in events if e.get("node_id") in payloads.NODES and e.get("detail_ref")]


@pytest.mark.anyio
async def test_the_enter_payload_is_stored_before_the_breakpoint(tmp_path: Path) -> None:
    h = harness(tmp_path, "tool_call")
    seen: dict[str, Any] = {}
    loop = asyncio.get_running_loop()

    def on_event(event: dict) -> None:
        if event["type"] == "node_paused" and event.get("node_id") == "model_layer":
            enter = h.tracer.get_turn("t1", event["turn_id"])[-2]
            assert enter["type"] == "node_enter" and enter["node_id"] == "model_layer"
            seen.setdefault("request", h.tracer.get_detail("t1", enter["detail_ref"]))
        if event["type"] == "node_paused":
            loop.call_soon(lambda: h.gateway.resume("t1", event["turn_id"], "step"))

    unsubscribe = h.bus.subscribe(on_event)
    try:
        from harnesswalk.control.gateway import TurnRequest
        from harnesswalk.types import Identity

        state = await h.gateway.start_turn(
            TurnRequest(
                message="Where is order A-1001?",
                identity=Identity("t1", None, ["user"], anonymous=False),
                step_mode=True,
            )
        )
        await h.gateway.wait(state.ctx.turn_id, timeout=10)
    finally:
        unsubscribe()
    request = seen["request"]["payload"]["request"]
    assert request["sections"][-1]["text"] == "Where is order A-1001?"


@pytest.mark.anyio
async def test_the_detail_endpoint_answers_only_the_turns_tenant(tmp_path: Path) -> None:
    h = harness(tmp_path, "tool_call")
    events = await run_turn(h, MESSAGE, tenant="t1")
    turn_id = events[0]["turn_id"]
    exit_ = next(
        e for e in events if e.get("node_id") == "context_builder" and e["type"] == "node_exit"
    )
    app = create_app(h)

    def client(tenant: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://t",
            headers={"X-Harnesswalk-Tenant": tenant},
        )

    url = f"/v1/trace/turns/{turn_id}/details/{exit_['event_id']}"
    async with client("t1") as c:
        r = await c.get(url)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["detail_ref"] == exit_["detail_ref"] and body["event_id"] == exit_["event_id"]
        assert body["detail"]["payload"]["node"] == "context_builder"
        no_detail = next(e for e in events if e["type"] == "edge")
        r = await c.get(f"/v1/trace/turns/{turn_id}/details/{no_detail['event_id']}")
        assert r.status_code == 404 and r.json()["error"]["code"] == "detail_not_found"
    async with client("t2") as c:
        r = await c.get(url)
        assert r.status_code == 404 and r.json()["error"]["code"] == "turn_not_found"
    assert h.tracer.get_detail("t2", exit_["detail_ref"]) is None
