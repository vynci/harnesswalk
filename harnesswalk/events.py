"""Harness events: build them, check them, publish them.

`TurnEmitter` is the only way a component reports work (spec/contracts/README.md, Emitter).
It assigns `seq`, keeps `ts` monotonic, enforces span nesting, checks every node and edge id
against the diagram, validates each event against spec/events.schema.json when asked to, and
hands the event to the `EventBus`. The bus fans out to subscribers: the trace store, live SSE
streams, the CLI printer.

Step mode lives here too, because a breakpoint is "pause after every node_enter"
(spec/harness.md section 8), and the emitter is where every node_enter happens.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from typing import Any

import jsonschema

from harnesswalk import diagram
from harnesswalk.paths import spec_dir

SCHEMA_VERSION = "0.1.0"
POINT_TYPES = frozenset(
    {
        "approval_requested",
        "approval_decided",
        "memory_proposed",
        "memory_written",
        "artifact_released",
        "message_delta",
        "node_paused",
        "node_resumed",
    }
)

Event = dict[str, Any]


class StreamError(RuntimeError):
    """A component broke a stream rule (spec/events.md section 1). A programming error."""


def uuid7() -> str:
    """A UUIDv7 string: 48 bits of Unix milliseconds, then randomness. Sorts by time."""
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80 | rand & ((1 << 80) - 1)
    value = (value & ~(0xF << 76)) | (0x7 << 76)  # version 7
    value = (value & ~(0x3 << 62)) | (0x2 << 62)  # RFC 4122 variant
    h = f"{value:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def new_trace_id() -> str:
    """A W3C trace id: 32 lowercase hex characters, never all zero."""
    while True:
        trace_id = secrets.token_hex(16)
        if trace_id != "0" * 32:
            return trace_id


def iso(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


@cache
def _validator() -> jsonschema.Draft202012Validator:
    schema = json.loads((spec_dir() / "events.schema.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def validate_event(event: Event) -> list[str]:
    """Schema problems of one event, empty when it is valid."""
    return [e.message for e in _validator().iter_errors(event)]


class EventBus:
    """In-process fan-out. Subscribers are plain callables, called in subscription order."""

    def __init__(self) -> None:
        self._subscribers: list[Callable[[Event], None]] = []

    def subscribe(self, callback: Callable[[Event], None]) -> Callable[[], None]:
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback)

    def publish(self, event: Event) -> None:
        for callback in list(self._subscribers):
            callback(event)


class NotPaused(Exception):
    """resume was called while the turn is not at a breakpoint."""


class TurnCancelled(asyncio.CancelledError):
    """The caller cancelled the turn (POST /v1/turns/{id}/cancel) while it was paused.

    Raised at a breakpoint or an approval wait. It is a CancelledError on purpose: no
    `except Exception` on the way up can mistake it for a tool or provider failure, and every
    span it crosses closes with `error` code `cancelled` (events.md rule 8).
    """


class Breakpoints:
    """Step mode: pause after every node_enter until someone calls `resume` or `cancel`."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.paused_node: str | None = None
        self.cancelled = False
        self._release = asyncio.Event()

    async def hit(self, emitter: TurnEmitter, node_id: str) -> None:
        if not self.enabled:
            return
        if self.cancelled:  # cancelled while running: the next breakpoint stops the turn
            raise TurnCancelled()
        emitter.point("node_paused", node_id, "waiting", {"reason": "breakpoint"})
        self.paused_node = node_id
        self._release.clear()
        started = time.monotonic()
        try:
            await self._release.wait()
        finally:
            emitter.paused_s += time.monotonic() - started
            self.paused_node = None
        if self.cancelled:  # no node_resumed: the paused span closes with error instead
            raise TurnCancelled()
        emitter.point("node_resumed", node_id, "active", {"reason": "breakpoint"})

    def cancel(self) -> bool:
        """Stop at the current breakpoint, or at the next one. True when it was paused."""
        was_paused = self.paused_node is not None
        self.cancelled = True
        self.paused_node = None
        self._release.set()
        return was_paused

    def resume(self, mode: str) -> str:
        """Release the current breakpoint. `continue` clears all breakpoints for the turn."""
        if self.paused_node is None:
            raise NotPaused()
        node_id = self.paused_node
        if mode == "continue":
            self.enabled = False
        self.paused_node = None  # a second resume before the turn wakes is a 409
        self._release.set()
        return node_id


class Span:
    """One open node. Components fill `summary` while they work; the emitter closes it."""

    def __init__(self, node_id: str, seq: int) -> None:
        self.node_id = node_id
        self.enter_seq = seq
        self.started = time.monotonic()
        self.summary: dict[str, Any] = {}
        self.status = "done"
        self.cost: dict[str, Any] | None = None
        self.detail: Any = None
        self.closed = False

    def set(self, **summary: Any) -> None:
        self.summary.update(summary)

    def skip(self, **summary: Any) -> None:
        """Mark the node as `skipped`: it ran but chose to do nothing."""
        self.status = "skipped"
        self.summary.update(summary)


DetailSink = Callable[[str, Any], str]


class TurnEmitter:
    """Builds every event of one turn, in order, and publishes it to the bus."""

    def __init__(
        self,
        *,
        trace_id: str,
        turn_id: str,
        session_id: str,
        tenant_id: str,
        bus: EventBus,
        validate: bool = True,
        breakpoints: Breakpoints | None = None,
        detail_sink: DetailSink | None = None,
    ) -> None:
        self.ids = {
            "trace_id": trace_id,
            "turn_id": turn_id,
            "session_id": session_id,
            "tenant_id": tenant_id,
        }
        self.bus = bus
        self.validate = validate
        self.breakpoints = breakpoints or Breakpoints(False)
        self.detail_sink = detail_sink
        self.seq = 0
        self.stack: list[Span] = []
        self.paused_s = 0.0
        self.started = time.monotonic()
        self.ended = False
        self._last_ts = 0.0

    # -- building ---------------------------------------------------------------------------

    def _emit(self, type_: str, status: str, **fields: Any) -> Event:
        if self.ended:
            raise StreamError(f"{type_} after turn_end")
        if self.seq == 0 and type_ != "turn_start":
            raise StreamError("the first event of a turn must be turn_start")
        self._last_ts = max(self._last_ts, time.time())
        event: Event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": uuid7(),
            "seq": self.seq,
            "ts": iso(self._last_ts),
            **self.ids,
            "type": type_,
            "status": status,
        }
        detail = fields.pop("detail", None)
        for key, value in fields.items():
            if value is not None:
                event[key] = value
        if detail is not None and self.detail_sink is not None:
            ref = self.detail_sink(event["event_id"], detail)  # None: detail dropped, event kept
            if ref:
                event["detail_ref"] = ref
        if self.validate:
            problems = validate_event(event)
            if problems:
                raise StreamError(f"invalid {type_} event: {problems}")
        self.seq += 1
        self.bus.publish(event)
        return event

    @staticmethod
    def _check_node(node_id: str) -> None:
        if node_id not in diagram.nodes_by_id():
            raise StreamError(f"unknown node_id {node_id!r}: not in spec/diagram/diagram.json")

    # -- turn -------------------------------------------------------------------------------

    def turn_start(self, summary: dict[str, Any]) -> Event:
        return self._emit("turn_start", "active", summary=summary)

    def turn_end(
        self,
        status: str,
        summary: dict[str, Any],
        cost: dict[str, Any] | None = None,
        detail: Any = None,
    ) -> Event:
        if self.stack:
            raise StreamError(f"turn_end with open nodes {[s.node_id for s in self.stack]}")
        event = self._emit(
            "turn_end",
            status,
            summary=summary,
            cost=cost,
            duration_ms=self._ms(self.started),
            detail=detail,
        )
        self.ended = True
        return event

    def turn_error(self, code: str, message: str) -> Event:
        """A turn-level failure (no node). It is followed by turn_end with status error."""
        return self._emit("error", "error", error={"code": code, "message": message[:1000]})

    # -- spans ------------------------------------------------------------------------------

    async def enter(self, node_id: str, summary: dict[str, Any] | None = None) -> Span:
        self._check_node(node_id)
        parent = diagram.parent_of(node_id)
        if parent and parent not in [s.node_id for s in self.stack]:
            raise StreamError(f"expert node {node_id} entered outside its parent {parent}")
        span = Span(node_id, self.seq)
        self._emit("node_enter", "active", node_id=node_id, summary=summary or None)
        self.stack.append(span)
        try:
            await self.breakpoints.hit(self, node_id)
        except BaseException as exc:  # cancelled while paused: close this span first
            cancelled = isinstance(exc, asyncio.CancelledError)
            self.fail(
                span,
                "cancelled" if cancelled else "internal",
                "The turn was cancelled." if cancelled else "The turn stopped while paused.",
            )
            raise
        return span

    def _check_top(self, span: Span) -> None:
        if not self.stack or self.stack[-1] is not span:
            top = self.stack[-1].node_id if self.stack else None
            raise StreamError(f"closing {span.node_id} but the innermost open node is {top}")

    def _pop(self, span: Span) -> None:
        """Pop only after the closing event is out, so a failed emit never strands a span."""
        self.stack.pop()
        span.closed = True

    def exit(
        self,
        span: Span,
        status: str | None = None,
        summary: dict[str, Any] | None = None,
        cost: dict[str, Any] | None = None,
        detail: Any = None,
    ) -> Event:
        self._check_top(span)
        if summary:
            span.summary.update(summary)
        event = self._emit(
            "node_exit",
            status or span.status,
            node_id=span.node_id,
            duration_ms=self._ms(span.started),
            summary=span.summary or None,
            cost=cost or span.cost,
            detail=detail if detail is not None else span.detail,
        )
        self._pop(span)
        return event

    def fail(
        self, span: Span, code: str, message: str, summary: dict[str, Any] | None = None
    ) -> Event:
        """Emit `error` for this node, which closes its span."""
        self._check_top(span)
        if summary:
            span.summary.update(summary)
        event = self._emit(
            "error",
            "error",
            node_id=span.node_id,
            duration_ms=self._ms(span.started),
            summary=span.summary or None,
            cost=span.cost,
            error={"code": code, "message": message[:1000]},
        )
        self._pop(span)
        return event

    # -- point events and edges ---------------------------------------------------------------

    def point(self, type_: str, node_id: str, status: str, summary: dict[str, Any]) -> Event:
        if type_ not in POINT_TYPES:
            raise StreamError(f"{type_} is not a point event")
        if node_id not in [s.node_id for s in self.stack]:
            raise StreamError(f"{type_} for {node_id} outside its span")
        return self._emit(type_, status, node_id=node_id, summary=summary)

    def edge(self, edge_id: str) -> Event:
        if edge_id not in diagram.edge_ids():
            raise StreamError(f"unknown edge_id {edge_id!r}: not in spec/diagram/diagram.json")
        return self._emit("edge", "done", edge_id=edge_id)

    # -- helpers ------------------------------------------------------------------------------

    @property
    def open_nodes(self) -> list[str]:
        return [s.node_id for s in self.stack]

    @staticmethod
    def _ms(started: float) -> float:
        return round((time.monotonic() - started) * 1000, 3)
