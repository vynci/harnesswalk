"""Session memory: the turns of a conversation, its pack, and the loop's checkpoints.

Contract: spec/contracts/store.md (`session`) and loop.md (checkpoints). Always on.

Lesson: the model is stateless; the session is what makes turn two follow turn one. The loop
checkpoints here after every step and before an approval pause, which is what lets a restarted
process pick up the same turn where it stopped.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from harness.core.db import DB
from harness.core.events import iso


class SessionStore:
    def __init__(self, db: DB) -> None:
        self.db = db

    def create(self, tenant: str, pack: str, session: str | None = None) -> dict[str, Any]:
        row = {"session": session or "sess_" + uuid.uuid4().hex[:16], "tenant": tenant, "pack": pack,
               "created_at": iso(time.time())}  # fmt: skip
        self.db.insert("sessions", row)
        return row

    def get(self, tenant: str, session: str) -> dict[str, Any] | None:
        return self.db.one("sessions", tenant=tenant, session=session)

    def history(self, tenant: str, session: str) -> list[dict[str, Any]]:
        """Finished turns that are session history: answered, not blocked or failed."""
        return [t for t in self.db.rows("history", tenant=tenant, session=session)
                if t.get("user") is not None]  # fmt: skip

    def record(self, tenant: str, session: str, turn_id: str, user: str | None, reply: str,
               exit_: str) -> None:  # fmt: skip
        self.db.insert("history", {"tenant": tenant, "session": session, "turn": turn_id,
                                   "user": user, "reply": reply, "exit": exit_})  # fmt: skip

    def turns(self, tenant: str, session: str) -> list[dict[str, Any]]:
        return self.db.rows("turns", tenant=tenant, session=session)

    def save_checkpoint(self, turn_id: str, data: dict[str, Any]) -> None:
        if self.db.one("checkpoints", turn=turn_id):
            self.db.update("checkpoints", {"turn": turn_id}, {"data": data, "at": iso(time.time())})
        else:
            self.db.insert("checkpoints", {"turn": turn_id, "data": data, "at": iso(time.time()),
                                           "restored": False})  # fmt: skip

    def checkpoint(self, turn_id: str) -> dict[str, Any] | None:
        return self.db.one("checkpoints", turn=turn_id)
