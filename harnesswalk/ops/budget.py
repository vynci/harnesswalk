"""The shared hosted budget: dollars spent on priced models per UTC day (P6).

Not a node. The model layer charges it after every priced call a BYO key did not pay for, and
in public mode checks it before every call: once `spent_usd >= public.hosted_budget_usd_per_day`
the router stops offering priced aliases until the next UTC date (spec/contracts/http_api.md,
"P6 additions", Budget ledger). No cron: the row is per date, so a new day starts at zero.

One row per UTC date (`spent_usd`, `calls`, `updated_at`) in `budgets.db`, next to the
per-tenant ledger (control/budgets.py), so it survives restarts and redeploys with the data
volume. The clock is injectable for tests. Public mode never exposes the dollars: capabilities
carry `spent_fraction` and `resets_at` only; `GET /v1/ops/budget` (dev mode) shows the row.

Concurrent turns (P6 review): in public mode a priced call first `reserve()`s its worst case (the
input estimate plus `max_output_tokens` at the alias price) and `settle()`s the real amount after.
A reservation is refused while today's spend plus the open reservations has reached the limit,
so however many turns run at once the day overshoots by at most one call's worst case. The
holds live in this process (one API process per box); a restart drops them with the calls.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.events import iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS hosted_daily (
    date TEXT PRIMARY KEY,
    spent_usd REAL NOT NULL,
    calls INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utc_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d")


def next_midnight(ts: float) -> float:
    day = datetime.fromtimestamp(ts, tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return (day + timedelta(days=1)).timestamp()


class HostedBudget:
    def __init__(
        self,
        db: Database,
        limit_usd: Callable[[], float],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db = db
        self._limit = limit_usd  # read on every check, so a reload or an env override applies
        self.clock = clock
        self._held: dict[int, float] = {}  # reservation -> worst-case dollars of a call in flight
        self._next_hold = 0
        db.executescript(SCHEMA)

    @property
    def limit_usd(self) -> float:
        return float(self._limit())

    def today(self) -> dict[str, Any]:
        """Today's row (zeros before the first charge), plus the limit and the reset time."""
        now = self.clock()
        date = utc_date(now)
        row = self.db.one("SELECT * FROM hosted_daily WHERE date = ?", (date,))
        spent = float(row["spent_usd"]) if row else 0.0
        return {
            "date": date,
            "spent_usd": round(spent, 6),
            "calls": int(row["calls"]) if row else 0,
            "updated_at": row["updated_at"] if row else None,
            "limit_usd": self.limit_usd,
            "exhausted": spent >= self.limit_usd,
            "resets_at": iso(next_midnight(now)).split(".")[0] + "Z",
        }

    def spent(self) -> float:
        row = self.db.one(
            "SELECT spent_usd FROM hosted_daily WHERE date = ?", (utc_date(self.clock()),)
        )
        return float(row["spent_usd"]) if row else 0.0

    def ok(self) -> bool:
        """Whether a priced call may start now. A failing ledger closes the door (fail closed)."""
        try:
            return self.spent() < self.limit_usd
        except Exception:
            return False

    def held_usd(self) -> float:
        """Dollars reserved by priced calls in flight."""
        return sum(self._held.values())

    def reserve(self, usd: float) -> int | None:
        """Hold a priced call's worst case before it is sent; None when the day is committed.

        Synchronous on purpose: on the event loop nothing runs between the check and the hold,
        so two turns cannot both see the last dollars free. Fails closed like `ok()`."""
        try:
            committed = self.spent() + self.held_usd()
        except Exception:
            return None
        if committed >= self.limit_usd:
            return None
        self._next_hold += 1
        self._held[self._next_hold] = max(0.0, float(usd))
        return self._next_hold

    def settle(self, hold: int | None, usd: float) -> None:
        """The call answered: drop its hold and charge what it really cost."""
        if hold is not None:
            self._held.pop(hold, None)
        self.charge(usd)

    def release(self, hold: int | None) -> None:
        """The call failed or was cancelled before it cost anything: drop its hold."""
        if hold is not None:
            self._held.pop(hold, None)

    def charge(self, usd: float) -> None:
        """Add one priced call's dollars to today's row."""
        if usd <= 0:
            return
        now = self.clock()
        self.db.execute(
            "INSERT INTO hosted_daily VALUES (?, ?, 1, ?) ON CONFLICT(date) DO UPDATE SET"
            " spent_usd = spent_usd + excluded.spent_usd, calls = calls + 1,"
            " updated_at = excluded.updated_at",
            (utc_date(now), float(usd), iso(now)),
        )

    def public_view(self) -> dict[str, Any]:
        """What capabilities may say: a status, the reset time and a fraction, no dollars."""
        today = self.today()
        limit = today["limit_usd"]
        fraction = 1.0 if limit <= 0 else min(1.0, today["spent_usd"] / limit)
        return {
            "status": "exhausted" if today["exhausted"] else "ok",
            "resets_at": today["resets_at"],
            "spent_fraction": round(fraction, 6),
        }
