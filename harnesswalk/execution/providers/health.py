"""Provider health: which providers the router may pick right now.

Part of the `providers` node (spec/contracts/router.md, P3 additions). Two signals:

- a probe per provider every `router.health_check_s` (a cheap list-models call; `fake` is
  always healthy), run by the server in the background;
- a passive signal: a retryable failure marks the provider unhealthy until the next probe,
  or for `router.health_check_s` when no probe refreshes it (a process that runs no probe
  loop, such as the in-process eval and scorecard harness, would otherwise keep a provider
  down for good after one timeout). A probe result stands until the next probe.

A provider never probed counts as healthy, so a fresh process routes as configured.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import Any


class ProviderHealth:
    def __init__(
        self,
        providers: dict[str, Any],
        interval_s: float = 30.0,
        *,
        keyless_ok: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.providers = providers
        self.interval_s = interval_s
        self.clock = clock
        # Public mode (P6): a hosted provider built without the site's key serves BYO-key
        # turns only, so it is not probed and counts as healthy (control/public.py keeps
        # site-paid turns off it).
        self.keyless_ok = keyless_ok
        # name -> {ok, checked_at, reason, passive}; `passive`: set by mark_failed, not a probe
        self.state: dict[str, dict[str, Any]] = {}

    def ok(self, name: str) -> bool:
        entry = self.state.get(name)
        if entry is None or entry["ok"]:
            return True
        # A passive mark no probe refreshed expires after one interval.
        return bool(entry.get("passive")) and self.clock() - entry["checked_at"] >= self.interval_s

    def mark_failed(self, name: str, reason: str) -> None:
        self.state[name] = {
            "ok": False,
            "checked_at": self.clock(),
            "reason": reason[:120],
            "passive": True,
        }

    async def probe(self, name: str) -> bool:
        provider = self.providers.get(name)
        check = getattr(provider, "health", None)
        has_key = getattr(provider, "site_key_present", None)
        if self.keyless_ok and has_key is not None and not has_key():
            ok, reason = True, "no site key"
        elif check is None:
            ok, reason = True, "no probe"
        else:
            try:
                ok = bool(await asyncio.wait_for(check(), 10))
                reason = "ok" if ok else "probe failed"
            except Exception as exc:  # a probe failure is a state, never an error
                ok, reason = False, type(exc).__name__
        self.state[name] = {"ok": ok, "checked_at": self.clock(), "reason": reason}
        return ok

    async def probe_all(self) -> dict[str, bool]:
        return {name: await self.probe(name) for name in list(self.providers)}

    async def run(self) -> None:
        """Probe every provider forever, every `interval_s`. Cancel the task to stop."""
        while True:
            with contextlib.suppress(Exception):
                await self.probe_all()
            await asyncio.sleep(self.interval_s)

    def report(self) -> dict[str, str]:
        return {
            name: ("ok" if self.ok(name) else "unhealthy")
            + ("" if name in self.state else " (not probed)")
            for name in self.providers
        }
