"""Ops events: harness events that belong to no turn (spec/events.md section 10, P4).

An eval run's progress, feedback on a finished turn, an edited artifact, a release. They use
the turn event envelope and schema, but live on streams of their own: one stream per eval run
(`eval:<run_id>`) and one `ops` stream per tenant. `seq` counts from 0 per stream. `turn_id`
and `session_id` are null except on `feedback_recorded`, which names its turn.

They are stored in the trace database (`ops_events`), validated against the schema like turn
events, and fanned out to live readers (the eval run SSE) and to plain subscribers (the
OpenTelemetry exporter).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.events import (
    SCHEMA_VERSION,
    Event,
    StreamError,
    iso,
    new_trace_id,
    uuid7,
    validate_event,
)

OPS_TYPES = frozenset(
    {
        "eval_run_started",
        "eval_case_finished",
        "eval_run_finished",
        "feedback_recorded",
        "artifact_changed",
        "artifact_released",
    }
)
OPS_STREAM = "ops"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_events (
    tenant_id TEXT NOT NULL,
    stream TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    type TEXT NOT NULL,
    ts TEXT NOT NULL,
    turn_id TEXT,
    body TEXT NOT NULL,
    PRIMARY KEY (tenant_id, stream, seq)
);
CREATE INDEX IF NOT EXISTS ops_events_by_turn ON ops_events (tenant_id, turn_id, type);
"""


class OpsEvents:
    def __init__(self, db: Database, *, validate: bool = True) -> None:
        self.db = db
        db.executescript(SCHEMA)
        self.validate = validate
        self._live: dict[tuple[str, str], list[asyncio.Queue[Event]]] = {}
        self._subscribers: list[Callable[[Event], None]] = []

    def subscribe(self, callback: Callable[[Event], None]) -> Callable[[], None]:
        self._subscribers.append(callback)
        return lambda: self._subscribers.remove(callback)

    def emit(
        self,
        tenant_id: str,
        stream: str,
        type_: str,
        node_id: str,
        status: str,
        summary: dict[str, Any],
        *,
        trace_id: str | None = None,
        turn_id: str | None = None,
        session_id: str | None = None,
    ) -> Event:
        if type_ not in OPS_TYPES:
            raise StreamError(f"{type_} is not an ops event")
        event: Event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": uuid7(),
            "seq": 0,
            "ts": iso(time.time()),
            "trace_id": trace_id or new_trace_id(),
            "turn_id": turn_id,
            "session_id": session_id,
            "tenant_id": tenant_id,
            "type": type_,
            "node_id": node_id,
            "status": status,
            "summary": summary,
        }
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 FROM ops_events"
                " WHERE tenant_id = ? AND stream = ?",
                (tenant_id, stream),
            ).fetchone()
            event["seq"] = int(row[0])
            if self.validate:
                problems = validate_event(event)
                if problems:
                    raise StreamError(f"invalid {type_} event: {problems}")
            conn.execute(
                "INSERT INTO ops_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant_id,
                    stream,
                    event["seq"],
                    event["event_id"],
                    type_,
                    event["ts"],
                    turn_id,
                    json.dumps(event, separators=(",", ":"), ensure_ascii=False),
                ),
            )
        for queue in self._live.get((tenant_id, stream), []):
            queue.put_nowait(event)
        for callback in list(self._subscribers):
            # An exporter never breaks the operation that emitted the event.
            with contextlib.suppress(Exception):
                callback(event)
        return event

    def list(
        self,
        tenant_id: str,
        stream: str = OPS_STREAM,
        *,
        after: int = -1,
        limit: int = 500,
        type_: str | None = None,
    ) -> list[Event]:
        sql = "SELECT body FROM ops_events WHERE tenant_id = ? AND stream = ? AND seq > ?"
        params: list[Any] = [tenant_id, stream, after]
        if type_:
            sql += " AND type = ?"
            params.append(type_)
        rows = self.db.query(sql + " ORDER BY seq LIMIT ?", (*params, limit))
        return [json.loads(r["body"]) for r in rows]

    def for_turn(self, tenant_id: str, turn_id: str, type_: str) -> list[Event]:
        rows = self.db.query(
            "SELECT body FROM ops_events WHERE tenant_id = ? AND turn_id = ? AND type = ?"
            " ORDER BY ts",
            (tenant_id, turn_id, type_),
        )
        return [json.loads(r["body"]) for r in rows]

    async def follow(
        self,
        tenant_id: str,
        stream: str,
        *,
        after: int = -1,
        until: str,
        live: bool = True,
        keepalive_s: float = 15.0,
    ) -> AsyncIterator[Event | None]:
        """Stored events with seq > after, then live ones, until an event of type `until`.

        Yields None when nothing happened for `keepalive_s` (the SSE layer sends a comment).
        """
        key = (tenant_id, stream)
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._live.setdefault(key, []).append(queue)
        try:
            last = after
            for event in self.list(tenant_id, stream, after=after, limit=100000):
                last = event["seq"]
                yield event
                if event["type"] == until:
                    return
            if not live or self.list(tenant_id, stream, after=-1, limit=100000, type_=until):
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), keepalive_s)
                except TimeoutError:
                    yield None
                    continue
                if event["seq"] > last:
                    last = event["seq"]
                    yield event
                if event["type"] == until:
                    return
        finally:
            self._live[key].remove(queue)
            if not self._live[key]:
                del self._live[key]
