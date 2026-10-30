"""Turn ownership on a shared data directory: `data/owner.lock` (P4).

Contract: spec/contracts/http_api.md ("Step-over and checkpoints"). Recovery at start-up closes
every unfinished turn that is not waiting on an approval, as cut off by a crash. That is right
after a crash and wrong when another `harnesswalk serve` is still running those turns on the
same data directory. So `serve` holds a lease: a small JSON file with its pid, host and a
heartbeat it rewrites every `release.lease.heartbeat_s` seconds.

- A second `serve` refuses to start while the lock names a live process with a fresh heartbeat.
- `--takeover` writes its own lock anyway, then waits for the holder to stop: the holder sees a
  foreign pid on its next heartbeat and shuts itself down (SIGTERM to itself, a clean uvicorn
  stop that detaches approval waits for recovery).
- A lock whose pid is gone, or whose heartbeat is older than three periods, is stale and is
  simply replaced (a crash, a SIGKILL).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("harnesswalk.lease")


class LeaseHeld(Exception):
    """Another live process owns the data directory."""

    def __init__(self, holder: dict[str, Any]) -> None:
        super().__init__(
            f"pid {holder.get('pid')} on {holder.get('host')} holds the data directory"
        )
        self.holder = holder


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Lease:
    def __init__(self, data_dir: Path, *, heartbeat_s: float = 5.0) -> None:
        self.path = Path(data_dir) / "owner.lock"
        self.heartbeat_s = heartbeat_s
        self.pid = os.getpid()
        self.host = socket.gethostname()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lost = False

    def read(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _fresh(self, holder: dict[str, Any]) -> bool:
        if holder.get("host") == self.host and not _alive(int(holder.get("pid", 0))):
            return False
        return time.time() - float(holder.get("heartbeat", 0)) < 3 * self.heartbeat_s

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"owner.lock.{self.pid}.tmp")
        body = {
            "pid": self.pid,
            "host": self.host,
            "heartbeat": time.time(),
            "started": self.started,
        }
        tmp.write_text(json.dumps(body), encoding="utf-8")
        tmp.replace(self.path)

    def acquire(self, *, takeover: bool = False, wait_s: float = 20.0) -> dict[str, Any] | None:
        """Take the lease. Returns the previous holder when one was taken over."""
        self.started = time.time()
        holder = self.read()
        live = holder is not None and holder.get("pid") != self.pid and self._fresh(holder)
        if live and not takeover:
            raise LeaseHeld(holder)  # type: ignore[arg-type]
        self._write()
        if live and takeover:
            self._wait_for_exit(holder, wait_s)  # type: ignore[arg-type]
        self._thread = threading.Thread(target=self._beat, name="harnesswalk-lease", daemon=True)
        self._thread.start()
        return holder if live else None

    def _wait_for_exit(self, holder: dict[str, Any], wait_s: float) -> None:
        pid = int(holder.get("pid", 0))
        if holder.get("host") != self.host:
            time.sleep(min(wait_s, 3 * self.heartbeat_s))  # cannot see it: give it its beats
            return
        deadline = time.monotonic() + wait_s
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
            self._write()  # keep our claim fresh while we wait

    def _beat(self) -> None:
        while not self._stop.wait(self.heartbeat_s):
            holder = self.read()
            if holder is not None and holder.get("pid") != self.pid:
                self.lost = True
                log.error(
                    "another harnesswalk serve (pid %s) took over this data directory;"
                    " shutting down",
                    holder.get("pid"),
                )
                os.kill(self.pid, signal.SIGTERM)
                return
            try:
                self._write()
            except OSError as exc:
                log.warning("could not refresh %s: %s", self.path, exc)

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
        holder = self.read()
        if holder is not None and holder.get("pid") == self.pid and not self.lost:
            with contextlib.suppress(OSError):
                self.path.unlink()
