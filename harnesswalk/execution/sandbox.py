"""Sandbox: where a tool call actually runs, isolated and time-limited.

Contract: spec/contracts/sandbox.md. Node: `sandbox` (expert child of `tools`).

Two levels:

- `inprocess`: a direct call in a worker thread, abandoned when the timeout fires. For tests,
  for public mode (pure dataset functions only) and for tools that read harness memory
  (`read_skill` declares it).
- `subprocess` (the default in dev and private): one child process per call
  (`sandbox_child.py`) in a work directory per turn, with a scrubbed environment, its own
  process group, a wall-clock timeout, an output cap, a memory cap and resource limits. No
  network unless the tool's class is `external` and policy allowed the call: a Python audit
  hook in the child refuses sockets, process spawning and writes outside the work directory,
  and an OS jail adds a second wall where one exists: `sandbox-exec` on macOS cuts the
  network and every file write outside the work directory (P3 review); `unshare` on Linux
  cuts the network.

Side-effecting calls are keyed by `sha256(tenant_id, turn_id, call_id)` so a resumed turn never
repeats a side effect in this process.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from harnesswalk.execution.registry import ToolSpec
from harnesswalk.nodes import node, register
from harnesswalk.types import ToolCall, TurnContext

register("sandbox", __name__)

LEVELS = ("inprocess", "subprocess")
MACOS_PROFILE = "(version 1)(allow default)(deny network*)"
# The macOS jail of a child: no file writes outside the work directory (passed as the WORK
# parameter, so no path is ever quoted into the profile), and no network unless allowed.
MACOS_WRITE_WALL = (
    '(deny file-write*)(allow file-write* (subpath (param "WORK"))'
    ' (literal "/dev/null") (literal "/dev/zero") (regex #"^/dev/fd/")'
    ' (literal "/dev/dtracehelper"))'
)


def jail_prefix(base: list[str], workdir: Path, network: bool) -> list[str]:
    """The jail command for one child. On macOS the profile adds the write wall."""
    if base[0] == "sandbox-exec":
        profile = "(version 1)(allow default)" + ("" if network else "(deny network*)")
        return ["sandbox-exec", "-D", f"WORK={workdir}", "-p", profile + MACOS_WRITE_WALL]
    return [] if network else base


SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")
ENVELOPE_BYTES = 4096  # room for the JSON around the content
STDERR_CAP = 16 * 1024


@dataclass
class ExecResult:
    outcome: str  # ok | error | timeout
    content: str
    duration_ms: float
    idempotency_key: str
    level: str = "inprocess"
    network: str = "n/a"  # blocked | allowed | n/a (in-process: by convention)
    jail: str = "none"


@cache
def os_jail() -> list[str] | None:
    """The command prefix that cuts the network for a child, or None when none works here."""
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        probe = ["sandbox-exec", "-p", MACOS_PROFILE, "/usr/bin/true"]
        prefix = ["sandbox-exec", "-p", MACOS_PROFILE]
    elif sys.platform.startswith("linux") and shutil.which("unshare"):
        probe = ["unshare", "--user", "--map-root-user", "--net", "true"]
        prefix = ["unshare", "--user", "--map-root-user", "--net"]
    else:
        return None
    try:
        ok = subprocess.run(probe, capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    return prefix if ok else None


class Sandbox:
    def __init__(
        self,
        level: str = "inprocess",
        max_timeout_ms: int = 10000,
        *,
        mode: str = "dev",
        subprocess_cfg: dict[str, Any] | None = None,
        workdir: Path | None = None,
    ) -> None:
        if level not in LEVELS:
            raise ValueError(f"sandbox.level must be one of {LEVELS} (container is a later phase)")
        self.mode = mode
        # Public mode runs pure dataset functions in-process and nothing else (sandbox.md).
        self.level = "inprocess" if mode == "public" else level
        self.max_timeout_ms = max_timeout_ms
        cfg = subprocess_cfg or {}
        self.memory_mb = int(cfg.get("memory_mb", 256))
        self.output_cap = int(cfg.get("output_cap_bytes", 65536))
        self.use_jail = cfg.get("os_jail", "auto") != "off"
        self.workdir = Path(workdir) if workdir else Path("data/sandbox")
        self._done: dict[str, ExecResult] = {}  # idempotency key -> first result

    @staticmethod
    def idempotency_key(ctx: TurnContext, call: ToolCall) -> str:
        raw = f"{ctx.tenant_id}\x00{ctx.turn_id}\x00{call.call_id}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def timeout_ms(self, spec: ToolSpec) -> int:
        return min(spec.timeout_ms, self.max_timeout_ms)

    def level_for(self, spec: ToolSpec) -> str:
        if self.mode == "public":
            return "inprocess"
        level = spec.sandbox or self.level
        if level == "subprocess" and not spec.entrypoint:
            return "inprocess"  # nothing a child could import (a closure registered in code)
        return level

    def turn_dir(self, ctx: TurnContext) -> Path:
        return self.workdir / SAFE_NAME.sub("_", ctx.tenant_id) / SAFE_NAME.sub("_", ctx.turn_id)

    def end_turn(self, ctx: TurnContext) -> None:
        """Remove the turn's work directory (called when the turn ends)."""
        with contextlib.suppress(OSError):
            shutil.rmtree(self.turn_dir(ctx))

    async def execute(
        self, ctx: TurnContext, spec: ToolSpec, call: ToolCall, *, network: bool = False
    ) -> ExecResult:
        key = self.idempotency_key(ctx, call)
        if spec.action_class != "read" and key in self._done:
            return self._done[key]
        if self.level_for(spec) == "subprocess":
            result = await self._subprocess(ctx, spec, call, key, network)
        else:
            result = await self._inprocess(spec, call, key)
        if spec.action_class != "read" and result.outcome == "ok":
            self._done[key] = result
        return result

    async def _inprocess(self, spec: ToolSpec, call: ToolCall, key: str) -> ExecResult:
        started = time.monotonic()
        try:
            raw: Any = await asyncio.wait_for(
                asyncio.to_thread(spec.impl, call.arguments), self.timeout_ms(spec) / 1000
            )
            outcome, content = "ok", raw if isinstance(raw, str) else str(raw)
        except TimeoutError:
            outcome, content = "timeout", f"{spec.name} timed out after {self.timeout_ms(spec)} ms"
        except Exception as exc:  # a tool error is a result, not a harness error
            outcome, content = "error", f"{spec.name} failed: {type(exc).__name__}: {exc}"[:300]
        return ExecResult(outcome, content, (time.monotonic() - started) * 1000, key)

    async def _subprocess(
        self, ctx: TurnContext, spec: ToolSpec, call: ToolCall, key: str, network: bool
    ) -> ExecResult:
        workdir = self.turn_dir(ctx)
        workdir.mkdir(parents=True, exist_ok=True)
        timeout_s = self.timeout_ms(spec) / 1000
        base_jail = os_jail() if self.use_jail else None
        jail = jail_prefix(base_jail, workdir.resolve(), network) if base_jail else None
        cmd = [
            *(jail or []),
            sys.executable,
            "-I",
            "-B",
            "-m",
            "harnesswalk.execution.sandbox_child",
        ]
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "HOME": str(workdir),
            "TMPDIR": str(workdir),
        }
        request = {
            "entrypoint": spec.entrypoint,
            "data": spec.entry_data,
            "arguments": call.arguments,
            "memory_mb": self.memory_mb,
            "cpu_s": max(1, int(timeout_s) + 1),
            "network": network,
            "workdir": str(workdir.resolve()),
        }
        started = time.monotonic()
        base = {
            "idempotency_key": key,
            "level": "subprocess",
            "network": "allowed" if network else "blocked",
            "jail": (jail[0] if jail else "none"),
        }

        def done(outcome: str, content: str) -> ExecResult:
            return ExecResult(outcome, content, (time.monotonic() - started) * 1000, **base)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workdir),
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            return done("error", f"the sandbox could not start: {type(exc).__name__}")
        cap = self.output_cap + ENVELOPE_BYTES

        async def read_capped(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
            buf = bytearray()
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return bytes(buf), False
                buf.extend(chunk)
                if len(buf) > limit:
                    return bytes(buf[:limit]), True

        pending: list[asyncio.Task] = []

        async def run() -> tuple[bytes, bool, bytes]:
            assert proc.stdin and proc.stdout and proc.stderr
            proc.stdin.write(json.dumps(request).encode())
            await proc.stdin.drain()
            proc.stdin.close()
            err_task = asyncio.create_task(read_capped(proc.stderr, STDERR_CAP))
            pending.append(err_task)
            out, overflow = await read_capped(proc.stdout, cap)
            if overflow:
                return out, True, b""
            err, _ = await err_task
            await proc.wait()
            return out, False, err

        try:
            out, overflow, err = await asyncio.wait_for(run(), timeout_s)
        except TimeoutError:
            self._kill(proc)
            await proc.wait()
            return done("timeout", f"{spec.name} timed out after {self.timeout_ms(spec)} ms")
        finally:
            if proc.returncode is None:
                self._kill(proc)
                with contextlib.suppress(Exception):
                    await proc.wait()
            for task in pending:
                task.cancel()
        if overflow:
            return done(
                "error", f"{spec.name} produced more than the {self.output_cap} byte output cap"
            )
        if proc.returncode in (137, -signal.SIGKILL) and b"memory over" in err:
            return done("error", f"{spec.name} went over the {self.memory_mb} MB memory cap")
        try:
            result = json.loads(out.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return done("error", f"{spec.name} failed: the sandbox exited {proc.returncode}")
        content = str(result.get("content", ""))
        if len(content.encode("utf-8")) > self.output_cap:
            return done(
                "error", f"{spec.name} produced more than the {self.output_cap} byte output cap"
            )
        if result.get("outcome") == "ok":
            return done("ok", content)
        return done("error", f"{spec.name} failed: {content}"[:300])

    @staticmethod
    def _kill(proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(proc.pid, signal.SIGKILL)


async def run_sandbox(
    ctx: TurnContext, sandbox: Sandbox, spec: ToolSpec, call: ToolCall, *, network: bool = False
) -> ExecResult:
    async with node(ctx, "sandbox") as span:
        result = await sandbox.execute(ctx, spec, call, network=network)
        span.set(
            tool=spec.name,
            call_id=call.call_id,
            executed=True,
            timeout_ms=sandbox.timeout_ms(spec),
            result_chars=len(result.content),
            outcome=result.outcome,
            level=result.level,
            network=result.network,
            jail=result.jail,
        )
        span.detail = {"tool": spec.name, "arguments": call.arguments, "result": result.content}
    return result


async def skip_sandbox(ctx: TurnContext, spec: ToolSpec, call: ToolCall, why: str) -> None:
    """Show the sandbox as bypassed when a call is denied, so the diagram tells the truth."""
    async with node(ctx, "sandbox") as span:
        span.skip(tool=spec.name, call_id=call.call_id, executed=False, reason=why)
