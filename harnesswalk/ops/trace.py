"""Trace: persist every event of every turn, serve it back, compute the cost receipt.

Contract: spec/contracts/tracer.md. Node: `trace` (LLM Ops, kind store).

The tracer subscribes to the event bus, so every event is stored the moment it is emitted,
exactly as emitted. Readers ask by `tenant_id` and `turn_id`; there is no read without a
tenant. Live readers get stored events first, then new ones as they arrive, until `turn_end`.

The `trace` node itself runs once at the end of a chat turn, after the gateway reply and before
`turn_end`: it confirms the turn is persisted and computes the receipt (its `cost` child).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.events import Event
from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext, Usage

register("trace", __name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    tenant_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    type TEXT NOT NULL,
    node_id TEXT,
    ts TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (tenant_id, turn_id, seq)
);
CREATE INDEX IF NOT EXISTS events_by_node ON events (tenant_id, node_id, type);
CREATE INDEX IF NOT EXISTS events_by_session ON events (tenant_id, session_id, type);
CREATE TABLE IF NOT EXISTS details (
    tenant_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (tenant_id, trace_id, event_id)
);
"""


class TraceRejected(Exception):
    """An event does not belong to the turn it claims (tenant, turn or trace id differ)."""


class Tracer:
    def __init__(self, path: str, *, buffer_events: int = 10000, readonly: bool = False) -> None:
        self.db = Database(path, SCHEMA, readonly=readonly)
        self.store = "sqlite"
        self.buffer_events = buffer_events
        self._buffer: list[Event] = []
        self._turn_ids: dict[str, tuple[str, str]] = {}  # turn_id -> (tenant_id, trace_id)
        self._live: dict[str, list[asyncio.Queue[Event]]] = {}

    # -- writing ----------------------------------------------------------------------------

    def record(self, event: Event) -> None:
        """Called by the bus for every event. Stores it exactly as emitted."""
        turn_id = event["turn_id"]
        ids = (event["tenant_id"], event["trace_id"])
        if event["type"] == "turn_start":
            self._turn_ids[turn_id] = ids
        elif self._turn_ids.get(turn_id, ids) != ids:
            raise TraceRejected(f"event {event['event_id']} does not match turn {turn_id}")
        try:
            self._flush_buffer()
            self._insert(event)
        except sqlite3.Error:
            # Stream events are never dropped; detail records are what overflow sacrifices.
            self._buffer.append(event)
        for queue in self._live.get(turn_id, []):
            queue.put_nowait(event)
        if event["type"] == "turn_end":
            self._turn_ids.pop(turn_id, None)

    def _insert(self, event: Event) -> None:
        self.db.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event["tenant_id"],
                event["turn_id"],
                event["seq"],
                event["event_id"],
                event["trace_id"],
                event["session_id"],
                event["type"],
                event.get("node_id"),
                event["ts"],
                json.dumps(event, separators=(",", ":"), ensure_ascii=False),
            ),
        )

    def _flush_buffer(self) -> None:
        while self._buffer:
            self._insert(self._buffer[0])
            self._buffer.pop(0)

    @property
    def persisted(self) -> bool:
        return not self._buffer

    def put_detail(self, tenant_id: str, trace_id: str, event_id: str, record: Any) -> str | None:
        """Store a full record (assembled context, prompt, tool result) and return its ref."""
        if len(self._buffer) >= self.buffer_events:
            return None  # the store is down and the buffer is full: drop details first
        body = json.dumps(record, default=str, ensure_ascii=False)
        try:
            self.db.execute(
                "INSERT OR REPLACE INTO details VALUES (?, ?, ?, ?)",
                (tenant_id, trace_id, event_id, body),
            )
        except sqlite3.Error:
            return None
        return f"trace://{trace_id}/{event_id}"

    # -- reading ----------------------------------------------------------------------------

    def get_turn(self, tenant_id: str, turn_id: str, after_seq: int = -1) -> list[Event]:
        rows = self.db.query(
            "SELECT body FROM events WHERE tenant_id = ? AND turn_id = ? AND seq > ? ORDER BY seq",
            (tenant_id, turn_id, after_seq),
        )
        events = [json.loads(r["body"]) for r in rows]
        buffered = [
            e
            for e in self._buffer
            if e["tenant_id"] == tenant_id and e["turn_id"] == turn_id and e["seq"] > after_seq
        ]
        return sorted(events + buffered, key=lambda e: e["seq"]) if buffered else events

    def turn_exists(self, tenant_id: str, turn_id: str) -> bool:
        row = self.db.one(
            "SELECT 1 FROM events WHERE tenant_id = ? AND turn_id = ? LIMIT 1",
            (tenant_id, turn_id),
        )
        return row is not None

    def turn_ended(self, tenant_id: str, turn_id: str) -> bool:
        """Whether the turn's turn_end is stored (or buffered while the store is down)."""
        if any(
            e["type"] == "turn_end" and e["tenant_id"] == tenant_id and e["turn_id"] == turn_id
            for e in self._buffer
        ):
            return True
        row = self.db.one(
            "SELECT 1 FROM events WHERE tenant_id = ? AND turn_id = ? AND type = 'turn_end'",
            (tenant_id, turn_id),
        )
        return row is not None

    def get_detail(self, tenant_id: str, detail_ref: str) -> Any:
        trace_id, _, event_id = detail_ref.removeprefix("trace://").partition("/")
        row = self.db.one(
            "SELECT body FROM details WHERE tenant_id = ? AND trace_id = ? AND event_id = ?",
            (tenant_id, trace_id, event_id),
        )
        return json.loads(row["body"]) if row else None

    def recent_exits(self, tenant_id: str, node_id: str, limit: int = 5) -> list[Event]:
        rows = self.db.query(
            "SELECT body FROM events WHERE tenant_id = ? AND node_id = ? AND type = 'node_exit'"
            " ORDER BY ts DESC, seq DESC LIMIT ?",
            (tenant_id, node_id, limit),
        )
        return [json.loads(r["body"]) for r in rows]

    def list_turns(self, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT turn_id, trace_id, session_id, ts FROM events"
            " WHERE tenant_id = ? AND type = 'turn_start' ORDER BY ts DESC LIMIT ?",
            (tenant_id, limit),
        )
        return [dict(r) for r in rows]

    def session_turn_ids(self, tenant_id: str, session_id: str, limit: int = 50) -> list[str]:
        """Turn ids of one session, newest first (by turn index, then start time)."""
        rows = self.db.query(
            "SELECT turn_id FROM events WHERE tenant_id = ? AND session_id = ?"
            " AND type = 'turn_start'"
            " ORDER BY CAST(json_extract(body, '$.summary.turn_index') AS INTEGER) DESC,"
            " ts DESC, turn_id DESC LIMIT ?",
            (tenant_id, session_id, limit),
        )
        return [r["turn_id"] for r in rows]

    async def subscribe(
        self, tenant_id: str, turn_id: str, after_seq: int = -1, keepalive_s: float = 15.0
    ) -> AsyncIterator[Event | None]:
        """Stored events with seq > after_seq, then live ones, until turn_end.

        The stream closes at turn_end even when the cursor is past it (turn_end itself is then
        not sent). Yields None when nothing happened for `keepalive_s` (the SSE layer sends a
        comment).
        """
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._live.setdefault(turn_id, []).append(queue)
        try:
            last = after_seq
            for event in self.get_turn(tenant_id, turn_id, after_seq):
                last = event["seq"]
                yield event
                if event["type"] == "turn_end":
                    return
            if self.turn_ended(tenant_id, turn_id):  # it ended at or before the cursor
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), keepalive_s)
                except TimeoutError:
                    yield None
                    continue
                if event["tenant_id"] != tenant_id:
                    continue
                if event["seq"] > last:
                    last = event["seq"]
                    yield event
                if event["type"] == "turn_end":
                    return
        finally:
            self._live[turn_id].remove(queue)
            if not self._live[turn_id]:
                del self._live[turn_id]

    async def replay(self, tenant_id: str, turn_id: str) -> AsyncIterator[Event]:
        """Re-emit a stored turn without calling any node."""
        for event in self.get_turn(tenant_id, turn_id):
            yield event

    def receipt(self, tenant_id: str, turn_id: str) -> dict[str, Any]:
        """Sum cost from node exits only, never from turn_end (tracer.md invariant)."""
        return receipt_of(turn_id, self.get_turn(tenant_id, turn_id))

    def close(self) -> None:
        self.db.close()


def receipt_of(turn_id: str, events: list[Event]) -> dict[str, Any]:
    """The cost receipt of a list of events: node exits only, never turn_end."""
    total = Usage()
    by_node: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    latency: dict[str, float] = {}
    for event in events:
        if event["type"] not in ("node_exit", "error") or "node_id" not in event:
            continue
        node_id = event["node_id"]
        latency[node_id] = latency.get(node_id, 0.0) + event.get("duration_ms", 0.0)
        cost = event.get("cost")
        if not cost:
            continue
        usage = Usage(cost["input_tokens"], cost["output_tokens"], 0, cost["usd"])
        total.add(usage)
        node_usage = by_node.setdefault(node_id, Usage().cost())
        for key in ("input_tokens", "output_tokens", "usd"):
            node_usage[key] += cost[key]
        model = event.get("summary", {}).get("model")
        if model:
            model_usage = by_model.setdefault(model, Usage().cost())
            for key in ("input_tokens", "output_tokens", "usd"):
                model_usage[key] += cost[key]
    return {
        "turn_id": turn_id,
        "total": total.cost(),
        "by_node": by_node,
        "by_model": by_model,
        "latency_ms": {k: round(v, 3) for k, v in latency.items()},
    }


async def run_trace_node(ctx: TurnContext, tracer: Tracer) -> dict[str, Any]:
    """The `trace` span at the end of a chat turn. Returns the receipt."""
    from harnesswalk.ops.cost import run_cost_node

    async with node(ctx, "trace") as span:
        receipt = tracer.receipt(ctx.tenant_id, ctx.turn_id)
        await run_cost_node(ctx, receipt)
        span.set(events=span.enter_seq, persisted=tracer.persisted, store=tracer.store)
    return receipt
