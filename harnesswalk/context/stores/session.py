"""Session memory: the turns of a conversation, its running summary, and loop checkpoints.

Contract: spec/contracts/store.md. Node: `session` (expert child of `memory`).

Ranking is recency only: the summary first, then recent turns verbatim. Writers are checked
in code: turns are written by the `gateway`, checkpoints and the summary by the `loop` side.
Every method takes the tenant from the turn context and filters by it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.context.tokens import estimate
from harnesswalk.events import iso
from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("session", __name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    dataset TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turns (
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    user_text TEXT,
    reply_text TEXT,
    exit TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (session_id, turn_index)
);
CREATE TABLE IF NOT EXISTS summaries (
    session_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    through_index INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoints (
    turn_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    tenant_id TEXT NOT NULL,
    body TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (turn_id, step)
);
CREATE TABLE IF NOT EXISTS restored_turns (
    turn_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    ts TEXT NOT NULL
);
"""

WRITERS = {"turns": {"gateway"}, "summary": {"loop"}, "checkpoint": {"loop"}}


class SessionForbidden(Exception):
    """The session exists but belongs to another tenant."""


@dataclass
class Turn:
    index: int
    user: str
    assistant: str


def _check_writer(what: str, writer: str) -> None:
    if writer not in WRITERS[what]:
        raise PermissionError(f"{writer} may not write session {what}")


class SessionStore:
    kind = "session"

    def __init__(self, path: str, *, readonly: bool = False) -> None:
        self.db = Database(path, SCHEMA, readonly=readonly)

    def ensure_session(self, tenant_id: str, session_id: str, dataset: str) -> str:
        """Create the session, or check it belongs to this tenant. Returns its dataset."""
        row = self.db.one(
            "SELECT tenant_id, dataset FROM sessions WHERE session_id = ?", (session_id,)
        )
        if row is None:
            self.db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                (session_id, tenant_id, dataset, iso(time.time())),
            )
            return dataset
        if row["tenant_id"] != tenant_id:
            raise SessionForbidden(session_id)
        return row["dataset"]

    def dataset_of(self, tenant_id: str, session_id: str) -> str | None:
        """The pack a session of this tenant runs on (P5), or None when there is none."""
        row = self.db.one(
            "SELECT dataset FROM sessions WHERE session_id = ? AND tenant_id = ?",
            (session_id, tenant_id),
        )
        return row["dataset"] if row else None

    def info(self, tenant_id: str, session_id: str) -> dict[str, Any] | None:
        """One session of this tenant: its pack, when it began, how many turns it has (P5)."""
        row = self.db.one(
            "SELECT session_id, tenant_id, dataset, created_at FROM sessions"
            " WHERE session_id = ? AND tenant_id = ?",
            (session_id, tenant_id),
        )
        if row is None:
            return None
        return {
            "session_id": row["session_id"],
            "pack": row["dataset"],
            "tenant_id": row["tenant_id"],
            "created_at": row["created_at"],
            "turns": self.next_turn_index(tenant_id, session_id),
        }

    def owner(self, session_id: str) -> str | None:
        """The tenant a session belongs to, or None when there is no such session."""
        row = self.db.one("SELECT tenant_id FROM sessions WHERE session_id = ?", (session_id,))
        return row["tenant_id"] if row else None

    def user_texts(self, tenant_id: str, turn_ids: list[str]) -> dict[str, str | None]:
        """The stored user text of finished turns (None for blocked or failed input)."""
        if not turn_ids:
            return {}
        marks = ", ".join("?" for _ in turn_ids)
        rows = self.db.query(
            f"SELECT turn_id, user_text FROM turns WHERE tenant_id = ? AND turn_id IN ({marks})",
            (tenant_id, *turn_ids),
        )
        return {r["turn_id"]: r["user_text"] for r in rows}

    def next_turn_index(self, tenant_id: str, session_id: str) -> int:
        row = self.db.one(
            "SELECT COALESCE(MAX(turn_index), -1) + 1 AS n FROM turns"
            " WHERE tenant_id = ? AND session_id = ?",
            (tenant_id, session_id),
        )
        return int(row["n"]) if row else 0

    def turn_at(self, tenant_id: str, session_id: str, turn_index: int) -> str | None:
        """The turn id recorded at this index of the session, or None."""
        row = self.db.one(
            "SELECT turn_id FROM turns WHERE tenant_id = ? AND session_id = ? AND turn_index = ?",
            (tenant_id, session_id, turn_index),
        )
        return row["turn_id"] if row else None

    def append_turn(
        self, ctx: TurnContext, writer: str, user: str | None, reply: str, exit_: str
    ) -> None:
        _check_writer("turns", writer)
        self.db.execute(
            "INSERT OR REPLACE INTO turns VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ctx.session_id,
                ctx.turn_index,
                ctx.tenant_id,
                ctx.turn_id,
                user,
                reply,
                exit_,
                iso(time.time()),
            ),
        )

    def turns(self, ctx: TurnContext, after_index: int = -1) -> list[Turn]:
        """Answered turns after `after_index`, oldest first. Blocked turns are not history."""
        rows = self.db.query(
            "SELECT turn_index, user_text, reply_text FROM turns"
            " WHERE tenant_id = ? AND session_id = ?"
            " AND turn_index > ? AND turn_index < ? AND user_text IS NOT NULL ORDER BY turn_index",
            (ctx.tenant_id, ctx.session_id, after_index, ctx.turn_index),
        )
        return [Turn(r["turn_index"], r["user_text"], r["reply_text"] or "") for r in rows]

    def summary(self, ctx: TurnContext) -> tuple[str, int]:
        row = self.db.one(
            "SELECT summary, through_index FROM summaries WHERE tenant_id = ? AND session_id = ?",
            (ctx.tenant_id, ctx.session_id),
        )
        return (row["summary"], row["through_index"]) if row else ("", -1)

    def write_summary(
        self, ctx: TurnContext, writer: str, summary: str, through_index: int
    ) -> None:
        _check_writer("summary", writer)
        self.db.execute(
            "INSERT OR REPLACE INTO summaries VALUES (?, ?, ?, ?, ?)",
            (ctx.session_id, ctx.tenant_id, summary, through_index, iso(time.time())),
        )

    def checkpoint(self, ctx: TurnContext, writer: str, step: int, body: dict[str, Any]) -> None:
        _check_writer("checkpoint", writer)
        self.db.execute(
            "INSERT OR REPLACE INTO checkpoints VALUES (?, ?, ?, ?, ?)",
            (ctx.turn_id, step, ctx.tenant_id, json.dumps(body, default=str), iso(time.time())),
        )

    def last_checkpoint(self, tenant_id: str, turn_id: str) -> dict[str, Any] | None:
        row = self.db.one(
            "SELECT step, body FROM checkpoints WHERE tenant_id = ? AND turn_id = ?"
            " ORDER BY step DESC LIMIT 1",
            (tenant_id, turn_id),
        )
        return {"step": row["step"], **json.loads(row["body"])} if row else None

    def mark_restored(self, tenant_id: str, turn_id: str) -> None:
        """The turn was re-attached from its checkpoint after a restart (P4)."""
        self.db.execute(
            "INSERT OR REPLACE INTO restored_turns VALUES (?, ?, ?)",
            (turn_id, tenant_id, iso(time.time())),
        )

    def checkpoint_info(self, tenant_id: str, turn_id: str) -> dict[str, Any] | None:
        """`{step, at, restored}` of the turn's last checkpoint, for the turn summary (P4)."""
        row = self.db.one(
            "SELECT step, ts FROM checkpoints WHERE tenant_id = ? AND turn_id = ?"
            " ORDER BY step DESC LIMIT 1",
            (tenant_id, turn_id),
        )
        if row is None:
            return None
        restored = self.db.one(
            "SELECT 1 FROM restored_turns WHERE tenant_id = ? AND turn_id = ?",
            (tenant_id, turn_id),
        )
        return {"step": row["step"], "at": row["ts"], "restored": restored is not None}

    def count(self, tenant_id: str) -> dict[str, int]:
        sessions = self.db.one(
            "SELECT COUNT(*) AS n FROM sessions WHERE tenant_id = ?", (tenant_id,)
        )
        turns = self.db.one("SELECT COUNT(*) AS n FROM turns WHERE tenant_id = ?", (tenant_id,))
        return {"sessions": sessions["n"], "turns": turns["n"]}


@dataclass
class SessionView:
    summary: str
    summary_through: int
    turns: list[Turn]


async def run_session(ctx: TurnContext, store: SessionStore, estimator: str) -> SessionView:
    async with node(ctx, "session") as span:
        summary, through = store.summary(ctx)
        turns = store.turns(ctx, after_index=through)
        tokens = estimate(summary, estimator) + sum(
            estimate(t.user, estimator) + estimate(t.assistant, estimator) for t in turns
        )
        span.set(turns=len(turns), summary_tokens=estimate(summary, estimator), tokens=tokens)
    return SessionView(summary, through, turns)
