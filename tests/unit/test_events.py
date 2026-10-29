"""The emitter builds schema-valid events and refuses to break the stream rules."""

import re

import pytest

from harnesswalk.events import (
    Breakpoints,
    NotPaused,
    StreamError,
    iso,
    new_trace_id,
    uuid7,
    validate_event,
)
from harnesswalk.nodes import node
from tests.spec.support import check_stream
from tests.unit.support import make_ctx


@pytest.mark.anyio
async def test_spans_produce_a_valid_stream() -> None:
    ctx, got = make_ctx()
    async with node(ctx, "gateway") as span:
        span.set(phase="receive")
    ctx.emit.edge("gateway_to_input_guardrails")
    async with (
        node(ctx, "context_builder", budget_tokens=100),
        node(ctx, "memory"),
        node(ctx, "procedural") as span,
    ):
        span.set(tokens=3)
    ctx.emit.turn_end(
        "done",
        {"exit": "answered", "reply": "hi"},
        cost={"input_tokens": 0, "output_tokens": 0, "usd": 0},
    )
    events = got.events
    assert [e["seq"] for e in events] == list(range(len(events)))
    assert all(validate_event(e) == [] for e in events)
    assert check_stream(events) == []
    enter = next(
        e for e in events if e["type"] == "node_enter" and e["node_id"] == "context_builder"
    )
    assert enter["summary"] == {"budget_tokens": 100}
    exit_ = next(e for e in events if e["type"] == "node_exit" and e["node_id"] == "gateway")
    assert exit_["summary"] == {"phase": "receive"} and exit_["duration_ms"] >= 0


@pytest.mark.anyio
async def test_exception_closes_the_span_with_error() -> None:
    ctx, got = make_ctx()
    with pytest.raises(RuntimeError):
        async with node(ctx, "loop"):
            raise RuntimeError("boom")
    err = got.events[-1]
    assert err["type"] == "error" and err["node_id"] == "loop"
    assert err["error"]["code"] == "internal" and validate_event(err) == []


@pytest.mark.anyio
async def test_rules_are_enforced() -> None:
    ctx, _ = make_ctx()
    with pytest.raises(StreamError):
        await ctx.emit.enter("not_a_node")
    with pytest.raises(StreamError):
        await ctx.emit.enter("procedural")  # expert node outside its parent
    with pytest.raises(StreamError):
        ctx.emit.edge("no_such_edge")
    with pytest.raises(StreamError):
        ctx.emit.point("node_paused", "loop", "waiting", {"reason": "breakpoint"})  # loop not open
    outer = await ctx.emit.enter("loop")
    await ctx.emit.enter("model_layer")
    with pytest.raises(StreamError):
        ctx.emit.exit(outer)  # closes out of order
    with pytest.raises(StreamError):
        ctx.emit.turn_end("done", {})  # spans still open


@pytest.mark.anyio
async def test_message_delta_only_in_gateway() -> None:
    ctx, got = make_ctx()
    async with node(ctx, "gateway"):
        ctx.emit.point("message_delta", "gateway", "active", {"delta": "Hel", "index": 0})
    assert got.events[-2]["type"] == "message_delta"
    async with node(ctx, "loop"):
        with pytest.raises(StreamError):
            ctx.emit.point("message_delta", "loop", "active", {"delta": "x", "index": 0})


@pytest.mark.anyio
async def test_breakpoint_pauses_until_resumed() -> None:
    import asyncio

    ctx, got = make_ctx(step_mode=True)
    bp: Breakpoints = ctx.emit.breakpoints

    async def body() -> None:
        async with node(ctx, "gateway"):
            pass

    task = asyncio.create_task(body())
    await asyncio.sleep(0.01)
    assert bp.paused_node == "gateway"
    assert got.events[-1]["type"] == "node_paused"
    assert bp.resume("step") == "gateway"
    await task
    types = [e["type"] for e in got.events[1:]]
    assert types == ["node_enter", "node_paused", "node_resumed", "node_exit"]
    with pytest.raises(NotPaused):
        bp.resume("step")  # not paused any more


def test_ids_and_timestamps() -> None:
    a, b = uuid7(), uuid7()
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", a)
    assert a[:13] <= b[:13]
    assert re.match(r"^[0-9a-f]{32}$", new_trace_id())
    assert iso(0).endswith("Z") and iso(0).startswith("1970-01-01T00:00:00.000")
