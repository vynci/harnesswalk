"""Trace: every event of every turn, stored exactly as emitted, and the cost receipt.

Contract: spec/contracts/tracer.md. Node: `trace` (always on), expert child `cost`.

Lesson: you cannot debug, replay, evaluate or price a turn you did not keep. The store is
written event by event, so a turn that waits on an approval survives a restart, and the event
stream a viewer reads after the fact is the same one it would have read live.
"""

from __future__ import annotations

from typing import Any

from harness.core.db import DB


class TraceStore:
    def __init__(self, db: DB) -> None:
        self.db = db

    def store(self, event: dict[str, Any]) -> None:
        self.db.insert("events", {"turn": event["turn_id"], "tenant": event["tenant_id"],
                                  "event": event})  # fmt: skip

    def events(self, turn_id: str) -> list[dict[str, Any]]:
        return sorted((r["event"] for r in self.db.rows("events", turn=turn_id)),
                      key=lambda e: e["seq"])  # fmt: skip


def total_cost(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The turn's cost: the sum over the node exits that carry one (events.md rule 13)."""
    total = {"input_tokens": 0, "output_tokens": 0, "usd": 0.0}
    for e in events:
        if e["type"] == "node_exit" and e.get("cost"):
            for k in total:
                total[k] += e["cost"][k]
    total["usd"] = round(total["usd"], 6)
    return total


def run_trace(turn: Any, backend: str) -> None:
    """Entered once at the end of every chat turn, after the reply, before turn_end."""
    with turn.span("trace") as span:
        with turn.span("cost") as cost:
            cost.set(**total_cost(turn.events))
        span.set(events=len(turn.events), persisted=True, store=backend)
