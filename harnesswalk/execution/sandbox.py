"""Sandbox: where a tool call actually runs, with a timeout.

Contract: spec/contracts/sandbox.md. Node: `sandbox` (expert child of `tools`).

P1 implements the `inprocess` level only: a direct call in a worker thread, abandoned when
the timeout fires. That is the level the spec allows for pure dataset functions. Real
isolation (subprocess with limits, one-shot containers) is P3. Side-effecting calls are keyed
by `sha256(tenant_id, turn_id, call_id)` so a resumed turn never repeats a side effect.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any

from harnesswalk.execution.registry import ToolSpec
from harnesswalk.nodes import node, register
from harnesswalk.types import ToolCall, TurnContext

register("sandbox", __name__)


@dataclass
class ExecResult:
    outcome: str  # ok | error | timeout
    content: str
    duration_ms: float
    idempotency_key: str


class Sandbox:
    def __init__(self, level: str = "inprocess", max_timeout_ms: int = 10000) -> None:
        if level != "inprocess":
            raise ValueError("P1 implements the inprocess sandbox level only")
        self.level = level
        self.max_timeout_ms = max_timeout_ms
        self._done: dict[str, ExecResult] = {}  # idempotency key -> first result

    @staticmethod
    def idempotency_key(ctx: TurnContext, call: ToolCall) -> str:
        raw = f"{ctx.tenant_id}\x00{ctx.turn_id}\x00{call.call_id}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def timeout_ms(self, spec: ToolSpec) -> int:
        return min(spec.timeout_ms, self.max_timeout_ms)

    async def execute(self, ctx: TurnContext, spec: ToolSpec, call: ToolCall) -> ExecResult:
        key = self.idempotency_key(ctx, call)
        if spec.action_class != "read" and key in self._done:
            return self._done[key]
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
        result = ExecResult(outcome, content, (time.monotonic() - started) * 1000, key)
        if spec.action_class != "read" and outcome == "ok":
            self._done[key] = result
        return result


async def run_sandbox(
    ctx: TurnContext, sandbox: Sandbox, spec: ToolSpec, call: ToolCall
) -> ExecResult:
    async with node(ctx, "sandbox") as span:
        result = await sandbox.execute(ctx, spec, call)
        span.set(
            tool=spec.name,
            call_id=call.call_id,
            executed=True,
            timeout_ms=sandbox.timeout_ms(spec),
            result_chars=len(result.content),
            outcome=result.outcome,
        )
        span.detail = {"tool": spec.name, "arguments": call.arguments, "result": result.content}
    return result


async def skip_sandbox(ctx: TurnContext, spec: ToolSpec, call: ToolCall, why: str) -> None:
    """Show the sandbox as bypassed when a call is denied, so the diagram tells the truth."""
    async with node(ctx, "sandbox") as span:
        span.skip(tool=spec.name, call_id=call.call_id, executed=False, reason=why)
