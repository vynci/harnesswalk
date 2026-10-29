"""Per-tenant budgets: limits from config/policy.yaml and a rolling one-hour usage ledger.

Not a node: `policy` reads it for tool-call budgets and the gateway turns it into the loop's
token and dollar limits (spec/contracts/policy.md, P3 additions, Budgets).

    budgets:
      default: { tokens_per_turn, tokens_per_hour, usd_per_turn, usd_per_hour,
                 tool_calls_per_turn, tool_calls_per_hour }
      tenants: { <tenant>: { ...any of the same keys } }

A missing key is no limit. The ledger gets one row per finished turn (tokens, dollars and the
tool calls that executed); the hourly usage is the sum over the last 3600 seconds.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database

KEYS = (
    "tokens_per_turn",
    "tokens_per_hour",
    "usd_per_turn",
    "usd_per_hour",
    "tool_calls_per_turn",
    "tool_calls_per_hour",
)
WINDOW_S = 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    tenant_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    ts REAL NOT NULL,
    tokens INTEGER NOT NULL,
    usd REAL NOT NULL,
    tool_calls INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, turn_id)
);
CREATE INDEX IF NOT EXISTS ledger_by_time ON ledger (tenant_id, ts);
"""


@dataclass
class HourUsage:
    tokens: int = 0
    usd: float = 0.0
    tool_calls: int = 0


@dataclass
class TurnBudget:
    """One turn's view: the tenant's limits, the hour's usage when the turn started, and the
    tool calls this turn has run so far. `policy` reads it; `tools` counts into it."""

    limits: dict[str, float]
    hour: HourUsage = field(default_factory=HourUsage)
    tool_calls: int = 0

    def tool_call_limit(self) -> tuple[str, float, int] | None:
        """The tool-call budget this turn has spent, as (name, limit, used), or None."""
        per_turn = self.limits.get("tool_calls_per_turn")
        if per_turn is not None and self.tool_calls >= per_turn:
            return "tool_calls_per_turn", per_turn, self.tool_calls
        per_hour = self.limits.get("tool_calls_per_hour")
        used = self.hour.tool_calls + self.tool_calls
        if per_hour is not None and used >= per_hour:
            return "tool_calls_per_hour", per_hour, used
        return None

    def loop_limit(self, kind: str, configured: float | None) -> tuple[float | None, str]:
        """The smallest of the loop's own budget, the per-turn budget and what the hour has left.

        `kind` is `tokens` or `usd`. Returns the limit (None for none) and where it came from.
        """
        candidates: list[tuple[float, str]] = []
        if configured is not None:
            candidates.append((configured, f"loop.budget_{kind}"))
        per_turn = self.limits.get(f"{kind}_per_turn")
        if per_turn is not None:
            candidates.append((per_turn, f"policy.budgets.{kind}_per_turn"))
        per_hour = self.limits.get(f"{kind}_per_hour")
        if per_hour is not None:
            used = self.hour.tokens if kind == "tokens" else self.hour.usd
            candidates.append((max(0.0, per_hour - used), f"policy.budgets.{kind}_per_hour"))
        if not candidates:
            return None, ""
        value, source = min(candidates, key=lambda c: c[0])
        return value, source


class Budgets:
    def __init__(self, cfg: dict[str, Any] | None, path: str) -> None:
        cfg = cfg or {}
        self.default = self._clean(cfg.get("default") or {})
        self.tenants = {t: self._clean(v or {}) for t, v in (cfg.get("tenants") or {}).items()}
        self.db = Database(path, SCHEMA)

    @staticmethod
    def _clean(raw: dict[str, Any]) -> dict[str, float]:
        unknown = set(raw) - set(KEYS)
        if unknown:
            raise ValueError(f"unknown budget keys {sorted(unknown)}")
        return {k: float(v) for k, v in raw.items() if v is not None}

    def limits(self, tenant_id: str) -> dict[str, float]:
        return {**self.default, **self.tenants.get(tenant_id, {})}

    def hour_usage(self, tenant_id: str, now: float | None = None) -> HourUsage:
        since = (now or time.time()) - WINDOW_S
        row = self.db.one(
            "SELECT COALESCE(SUM(tokens), 0) AS t, COALESCE(SUM(usd), 0) AS u,"
            " COALESCE(SUM(tool_calls), 0) AS c FROM ledger WHERE tenant_id = ? AND ts >= ?",
            (tenant_id, since),
        )
        return HourUsage(int(row["t"]), float(row["u"]), int(row["c"])) if row else HourUsage()

    def for_turn(self, tenant_id: str) -> TurnBudget:
        try:
            hour = self.hour_usage(tenant_id)
        except Exception:  # the ledger is down: limits still apply per turn
            hour = HourUsage()
        return TurnBudget(self.limits(tenant_id), hour)

    def record(
        self, tenant_id: str, turn_id: str, tokens: int, usd: float, tool_calls: int
    ) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO ledger VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, turn_id, time.time(), int(tokens), float(usd), int(tool_calls)),
        )
