"""Turn summaries: what `GET /v1/turns/{id}` and `GET /v1/sessions/{id}/turns` return.

A summary is read off the turn's stored events (events.md rule 1: the stream is the source of
truth), so it is the same for a live turn, a finished one, and one read after a restart. The
only thing taken from elsewhere is the user's message, which the stream never carries (rule
11); it comes from the session store once the turn has ended.

    status   running | paused (a node_paused without its node_resumed) | done (turn_end stored)
"""

from __future__ import annotations

from typing import Any

from harnesswalk.context.stores.session import SessionStore
from harnesswalk.events import Event
from harnesswalk.ops.trace import Tracer, receipt_of

MAX_SESSION_TURNS = 200


def summarize(events: list[Event], message: str | None = None) -> dict[str, Any] | None:
    """The turn summary of one turn's events, in seq order. None when there are none."""
    if not events:
        return None
    start, end = events[0], None
    paused_at: str | None = None
    pending: str | None = None
    for event in events:
        kind, node_id = event["type"], event.get("node_id")
        summary = event.get("summary") or {}
        if kind == "node_paused":
            paused_at = node_id
        elif kind == "node_resumed" or (kind in ("node_exit", "error") and node_id == paused_at):
            paused_at = None
        elif kind == "approval_requested":
            pending = summary.get("approval_id")
        elif kind == "approval_decided" and summary.get("approval_id") == pending:
            pending = None
        elif kind == "turn_end":
            end = event
    if end is not None:
        status, paused_at, pending = "done", None, None
        cost = end.get("cost") or receipt_of(start["turn_id"], events)["total"]
    else:
        status = "paused" if paused_at else "running"
        cost = receipt_of(start["turn_id"], events)["total"]
    end_summary = (end or {}).get("summary") or {}
    return {
        "turn_id": start["turn_id"],
        "session_id": start["session_id"],
        "tenant_id": start["tenant_id"],
        "trace_id": start["trace_id"],
        "turn_index": (start.get("summary") or {}).get("turn_index"),
        "pack": (start.get("summary") or {}).get("pack")
        or (start.get("summary") or {}).get("dataset"),
        "status": status,
        "exit": end_summary.get("exit") if end else None,
        "reply": end_summary.get("reply") if end else None,
        "message": message if end else None,
        "started_at": start["ts"],
        "ended_at": end["ts"] if end else None,
        "cost": cost,
        "event_count": len(events),
        "paused_at_node": paused_at,
        "pending_approval_id": pending,
    }


def turn_summary(
    tracer: Tracer, session: SessionStore, tenant_id: str, turn_id: str
) -> dict[str, Any] | None:
    """One turn of this tenant, or None (unknown, or another tenant's)."""
    events = tracer.get_turn(tenant_id, turn_id)
    if not events:
        return None
    texts = session.user_texts(tenant_id, [turn_id])
    return summarize(events, texts.get(turn_id))


def session_turns(
    tracer: Tracer, session: SessionStore, tenant_id: str, session_id: str, limit: int = 50
) -> list[dict[str, Any]] | None:
    """The session's turns, newest first. None when the session is not this tenant's."""
    if session.owner(session_id) != tenant_id:
        return None
    ids = tracer.session_turn_ids(tenant_id, session_id, max(1, min(limit, MAX_SESSION_TURNS)))
    texts = session.user_texts(tenant_id, ids)
    out = []
    for turn_id in ids:
        summary = summarize(tracer.get_turn(tenant_id, turn_id), texts.get(turn_id))
        if summary is not None:
            out.append(summary)
    return out
