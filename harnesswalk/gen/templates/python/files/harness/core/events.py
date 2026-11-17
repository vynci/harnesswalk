"""The event stream: one turn, its spans, the off_node seam and step mode.

Contract: spec/events.md (rules 1 to 14) and spec/events.schema.json, shipped as
`diagram.json` next to this file. Every node reports its work through `Turn.span`, so the same
stream drives the diagram, the trace store, step mode and replay.

Lesson: the stream is the product. A node that is switched off still opens its span where it
would run and exits `skipped` with `disabled: true` (`off_node`), so the walk keeps its shape
and the diagram dims the node honestly instead of hiding it.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.2.0"
DIAGRAM = json.loads(Path(__file__).with_name("diagram.json").read_text(encoding="utf-8"))
NODES = {n["id"]: n for n in DIAGRAM["nodes"]}
SIMPLE = {i for i, n in NODES.items() if n["view"] == "simple"}


class Cancelled(Exception):
    """The caller cancelled the turn while it was paused, or before a pause."""


class NodeError(Exception):
    """A node failed (events.md section 4): the walk closes the open spans with this code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def compact(**kw: Any) -> dict[str, Any]:
    return {k: v for k, v in kw.items() if v is not None}


class Span:
    def __init__(self, node_id: str, t0: float) -> None:
        self.node_id, self.t0 = node_id, t0
        self.summary: dict[str, Any] = {}
        self.status = "done"
        self.cost: dict[str, Any] | None = None

    def set(self, **kw: Any) -> Span:
        self.summary.update(compact(**kw))
        return self

    def skip(self, **kw: Any) -> Span:
        self.status = "skipped"
        return self.set(**kw)


class Turn:
    """One turn's stream and control state (pauses, cancel, step mode)."""

    def __init__(self, sink: Any, ids: dict[str, str], *, step_mode: bool, off: set[str]) -> None:
        self.sink = sink  # the trace store: every event is durable as it is emitted
        self.ids = ids  # trace_id, turn_id, session_id, tenant_id
        self.off = set(off)  # nodes switched off for this turn (profile and request)
        self.events: list[dict[str, Any]] = []
        self.stack: list[Span] = []
        self.cond = threading.Condition()
        self.breakpoints = "all" if step_mode else "none"  # all | simple (step over) | none
        self.resume_mode: str | None = None
        self.paused_at: str | None = None
        self.pause_reason: str | None = None
        self.cancel_requested = False
        self.past_reply = False  # a cancel after the reply span closed is not honoured
        self.done = False
        self.last_ts = 0.0
        self.paused_s = 0.0  # time spent paused: the loop's timeout does not count it
        self.data: dict[str, Any] = {}  # what nodes share during the turn

    # -- emitting --------------------------------------------------------------------------
    def emit(self, type_: str, node_id: str | None = None, status: str = "done", **f: Any):
        with self.cond:
            self.last_ts = max(time.time(), self.last_ts)
            event = {
                "schema_version": SCHEMA_VERSION,
                "event_id": uuid.uuid4().hex,
                "seq": len(self.events),
                "ts": iso(self.last_ts),
                **self.ids,
                "type": type_,
                "status": status,
            }
            if node_id:
                event["node_id"] = node_id
            event.update({k: v for k, v in f.items() if v is not None})
            self.sink.store(event)
            self.events.append(event)
            self.cond.notify_all()
        return event

    def is_off(self, node_id: str) -> bool:
        return node_id in self.off

    @contextmanager
    def span(self, node_id: str, resume: bool = False, **enter: Any):
        """Open a node's span; on a clean exit close it with its summary. An exception leaves
        it open on purpose: the walk closes every open span with `error` (rule 8 and 10).
        `resume` re-enters a span a restart left open (the stored stream already has it)."""
        if resume:
            span = next(s for s in self.stack if s.node_id == node_id)
        else:
            self.emit("node_enter", node_id, "active", summary=compact(**enter) or None)
            span = Span(node_id, time.monotonic())
            self.stack.append(span)
            self.breakpoint(node_id)
        yield span
        self.stack.remove(span)
        ms = round((time.monotonic() - span.t0) * 1000, 3)
        self.emit("node_exit", node_id, span.status, summary=span.summary, cost=span.cost,
                  duration_ms=ms)  # fmt: skip

    def close_open(self, code: str, message: str) -> None:
        while self.stack:
            span = self.stack.pop()
            ms = round((time.monotonic() - span.t0) * 1000, 3)
            self.emit("error", span.node_id, "error", error={"code": code, "message": message},
                      duration_ms=ms)  # fmt: skip

    # -- pausing ---------------------------------------------------------------------------
    def breakpoint(self, node_id: str) -> None:
        if self.breakpoints == "none" or (self.breakpoints == "simple" and node_id not in SIMPLE):
            return
        self.breakpoints = "all"
        mode = self.pause(node_id, "breakpoint")
        self.resumed(node_id, "breakpoint", mode)

    def pause(self, node_id: str, reason: str, approval_id: str | None = None,
              timeout: float | None = None, ready: Any = None, announce: bool = True) -> str:  # fmt: skip
        """Emit node_paused and wait for a resume, or for `ready()` (an approval decided before
        the wait began). Returns the mode, `timeout` when the wait ran out; raises Cancelled."""
        if self.cancel_requested and not self.past_reply:
            raise Cancelled
        with self.cond:
            self.paused_at, self.resume_mode, self.pause_reason = node_id, None, reason
        if announce:  # a turn re-attached after a restart is already paused in its stream
            self.emit("node_paused", node_id, "waiting", summary=compact(reason=reason,
                      approval_id=approval_id))  # fmt: skip
        t0 = time.monotonic()
        deadline = None if timeout is None else t0 + timeout
        with self.cond:
            while self.resume_mode is None:
                if self.cancel_requested and not self.past_reply:
                    self.paused_at = None
                    raise Cancelled
                left = None if deadline is None else deadline - time.monotonic()
                if (ready and ready()) or (left is not None and left <= 0):
                    self.resume_mode = "approval" if ready and ready() else "timeout"
                    break
                self.cond.wait(min(left, 0.5) if left is not None else 0.5)
            mode, self.paused_at = self.resume_mode, None
        self.paused_s += time.monotonic() - t0
        return mode

    def resumed(self, node_id: str, reason: str, mode: str, approval_id: str | None = None):
        if mode == "continue":
            self.breakpoints = "none"
        elif mode == "step_over":
            self.breakpoints = "simple"
        self.emit("node_resumed", node_id, "active", summary=compact(reason=reason,
                  approval_id=approval_id, mode=mode))  # fmt: skip

    def resume(self, mode: str) -> None:
        with self.cond:
            self.resume_mode = mode
            self.cond.notify_all()

    def cancel(self) -> None:
        with self.cond:
            self.cancel_requested = True
            if self.past_reply and self.paused_at:
                self.resume_mode = "continue"  # too late to stop: release the breakpoint
            self.cond.notify_all()


def off_node(turn: Turn, node_id: str, **summary: Any) -> dict[str, Any]:
    """The seam for a switched-off node (harness.md section 10.3): its span opens where the
    node would run and exits `skipped` with `disabled: true`. One helper, no null objects."""
    with turn.span(node_id) as span:
        span.skip(disabled=True, **summary)
    return span.summary
