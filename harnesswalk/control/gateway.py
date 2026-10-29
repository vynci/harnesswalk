"""Gateway: the front door and the exit. It turns a request into a turn and runs it.

Contract: spec/contracts/gateway.md (HTTP surface in http_api.md, served by http_api.py).
Node: `gateway` (control plane, kind code), entered twice per chat turn: `phase: receive`
first, `phase: reply` last.

`start_turn` resolves the session, allocates `turn_id` and `trace_id`, emits `turn_start`
(seq 0) and schedules `run_turn`, which walks the request through the harness:

    gateway (receive) -> input_guardrails -> context_builder (memory) -> loop
        (model_layer, tools ...) -> output_guardrails -> gateway (reply)
        -> [memory_writer] -> trace -> turn_end

When input guardrails block, the walk is gateway, input_guardrails, gateway, trace.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from harnesswalk import diagram
from harnesswalk.context.memory_writer import run_memory_writer
from harnesswalk.context.stores.session import SessionForbidden
from harnesswalk.events import Breakpoints, TurnEmitter, new_trace_id, uuid7
from harnesswalk.execution.loop import Limits
from harnesswalk.nodes import node, register
from harnesswalk.ops.trace import run_trace_node
from harnesswalk.types import HarnessError, Identity, TurnContext

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

register("gateway", __name__)

REPLY_MAX_BYTES = 16 * 1024
EXIT_FOR_REASON = {
    "answer": "answered",
    "max_steps": "limit",
    "stuck": "limit",
    "budget_tokens": "limit",
    "budget_usd": "limit",
    "timeout": "limit",
    "interrupted": "cancelled",
}


class GatewayError(Exception):
    """A request the gateway refuses before any turn exists. Maps to an HTTP status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class TurnRequest:
    message: str
    identity: Identity
    session_id: str | None = None
    step_mode: bool = False
    dataset: str | None = None
    disabled_nodes: list[str] = field(default_factory=list)
    channel: str = "web"
    api_key: str | None = field(default=None, repr=False)  # BYO key: this turn only


@dataclass
class TurnState:
    ctx: TurnContext
    request: TurnRequest
    task: asyncio.Task | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    reply: str = ""
    exit: str = ""


def cap_reply(text: str) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= REPLY_MAX_BYTES:
        return text
    return raw[: REPLY_MAX_BYTES - 16].decode("utf-8", "ignore") + " [truncated]"


class Gateway:
    def __init__(self, harness: Harness) -> None:
        self.h = harness
        self.cfg = harness.settings.get("gateway", {})
        self.turns: dict[str, TurnState] = {}
        self._open_sessions: dict[str, str] = {}  # session_id -> running turn_id
        self._rate: dict[str, list[float]] = {}

    # -- starting a turn ----------------------------------------------------------------------

    def _check_rate(self, key: str, per_hour: int) -> int:
        now = time.time()
        window = [t for t in self._rate.get(key, []) if now - t < 3600]
        if len(window) >= per_hour:
            raise GatewayError(429, "rate_limited", "Too many requests, try again later.")
        window.append(now)
        self._rate[key] = window
        return per_hour - len(window)

    def _validate(self, req: TurnRequest) -> None:
        if not isinstance(req.message, str) or not req.message.strip():
            raise GatewayError(400, "invalid_request", "message must be a non-empty string")
        limit = int(self.cfg.get("max_message_chars", 32000))
        if len(req.message) > limit:
            raise GatewayError(400, "invalid_request", f"message is over {limit} characters")
        nodes = diagram.nodes_by_id()
        for node_id in req.disabled_nodes:
            if node_id not in nodes or not nodes[node_id].get("can_disable"):
                raise GatewayError(422, "invalid_request", f"node {node_id!r} cannot be disabled")
        if req.disabled_nodes and self.h.settings.mode == "public":
            raise GatewayError(
                422, "invalid_request", "disabling nodes is not allowed in public mode"
            )
        pack = self.h.settings.get("dataset", "_starter")
        if req.dataset and req.dataset != pack:
            raise GatewayError(
                422, "invalid_request", f"dataset {req.dataset!r} is not loaded ({pack} is)"
            )

    async def start_turn(self, req: TurnRequest, client_ip: str = "") -> TurnState:
        self._validate(req)
        tenant = req.identity.tenant_id
        remaining = None
        if self.h.settings.mode == "public":
            remaining = self._check_rate(
                f"ip:{client_ip}", int(self.cfg["rate_limit"]["per_ip_per_hour"])
            )
        if self.h.settings.mode != "dev":
            self._check_rate(f"tenant:{tenant}", int(self.cfg["rate_limit"]["per_tenant_per_hour"]))

        session_id = req.session_id or "sess_" + uuid7().replace("-", "")[:20]
        pack = self.h.settings.get("dataset", "_starter")
        try:
            dataset = self.h.session.ensure_session(tenant, session_id, req.dataset or pack)
        except SessionForbidden as exc:
            raise GatewayError(
                404, "session_not_found", f"No session {session_id} for this tenant."
            ) from exc
        if req.dataset and dataset != req.dataset:
            raise GatewayError(422, "invalid_request", "the dataset is fixed after the first turn")
        if session_id in self._open_sessions:
            raise GatewayError(
                409, "turn_in_progress", f"Session {session_id} already has a running turn."
            )

        turn_id = "turn_" + uuid7().replace("-", "")[:24]
        trace_id = new_trace_id()
        capture = bool(self.h.settings.get("trace.capture_content", True))
        emitter = TurnEmitter(
            trace_id=trace_id,
            turn_id=turn_id,
            session_id=session_id,
            tenant_id=tenant,
            bus=self.h.bus,
            validate=bool(self.h.settings.get("trace.validate_events", True)),
            breakpoints=Breakpoints(req.step_mode),
            detail_sink=(
                (lambda event_id, rec: self.h.tracer.put_detail(tenant, trace_id, event_id, rec))
                if capture
                else None
            ),
        )
        ctx = TurnContext(
            tenant_id=tenant,
            session_id=session_id,
            turn_id=turn_id,
            trace_id=trace_id,
            user_id=req.identity.user_id,
            mode=self.h.settings.mode,  # type: ignore[arg-type]
            emit=emitter,
            turn_index=self.h.session.next_turn_index(tenant, session_id),
            identity=req.identity,
            disabled_nodes=frozenset(req.disabled_nodes),
        )
        state = TurnState(ctx=ctx, request=req)
        self.turns[turn_id] = state
        self._open_sessions[session_id] = turn_id
        emitter.turn_start(
            {
                "kind": "chat",
                "channel": req.channel,
                "mode": ctx.mode,
                "step_mode": req.step_mode,
                "message_chars": len(req.message),
                "dataset": dataset,
                "turn_index": ctx.turn_index,
            }
        )
        state.task = asyncio.create_task(self.run_turn(state, remaining))
        return state

    # -- running a turn -----------------------------------------------------------------------

    async def run_turn(self, state: TurnState, rate_remaining: int | None = None) -> None:
        ctx, req, h = state.ctx, state.request, self.h
        emit = ctx.emit
        exit_, steps, reply, user_text = "answered", 0, "", None
        try:
            async with node(ctx, "gateway") as span:
                span.set(
                    phase="receive", channel=req.channel, authenticated=not req.identity.anonymous
                )
                if rate_remaining is not None:
                    span.set(rate_limit_remaining=rate_remaining)

            emit.edge("gateway_to_input_guardrails")
            guard = await h.input_guardrails.run(ctx, req.message)
            if guard.action == "block":
                exit_ = "blocked"
                why = guard.reason or "it was blocked"
                reply = f"I can't take this message: {why}. Please shorten or rephrase it."
            else:
                user_text = guard.text
                emit.edge("input_guardrails_to_context_builder")
                assembled = await h.context_builder.build(ctx, user_text)
                emit.edge("context_builder_to_loop")
                try:
                    result = await h.loop.run(
                        ctx,
                        assembled.messages,
                        h.tools.schemas(ctx.mode),
                        Limits.from_config(h.settings.get("loop", {})),
                        api_key=req.api_key,
                    )
                    answer, steps = result.answer.text, result.steps
                    exit_ = EXIT_FOR_REASON[result.exit_reason]
                except HarnessError:
                    answer, exit_ = h.loop.notices.get("error", "Something went wrong."), "error"
                emit.edge("loop_to_output_guardrails")
                checked = await h.output_guardrails.run(ctx, answer)
                if checked.action == "block":
                    exit_ = "blocked"
                reply = checked.text
                emit.edge("output_guardrails_to_gateway")

            reply = cap_reply(reply)
            async with node(ctx, "gateway") as span:
                span.set(phase="reply", channel=req.channel, reply_chars=len(reply))
                size = int(self.cfg.get("stream_reply_chars", 0))
                if size > 0:
                    for index, start in enumerate(range(0, len(reply), size)):
                        emit.point(
                            "message_delta",
                            "gateway",
                            "active",
                            {"delta": reply[start : start + size], "index": index},
                        )
            h.session.append_turn(ctx, "gateway", user_text, reply, exit_)
            await run_memory_writer(ctx, [])
            receipt = await run_trace_node(ctx, h.tracer)
            emit.turn_end(
                "done", {"exit": exit_, "steps": steps, "reply": reply}, cost=receipt["total"]
            )
        except asyncio.CancelledError:
            exit_ = "cancelled"
            self._close_after_failure(ctx, "cancelled", "The turn was cancelled.")
        except Exception as exc:  # anything unexpected still ends the turn properly
            exit_ = "error"
            self._close_after_failure(ctx, "internal", f"{type(exc).__name__}: {exc}")
        finally:
            state.reply, state.exit = reply, exit_
            self._open_sessions.pop(ctx.session_id, None)
            state.done.set()

    def _close_after_failure(self, ctx: TurnContext, code: str, message: str) -> None:
        emit = ctx.emit
        if emit.ended:
            return
        for span in reversed(list(emit.stack)):
            emit.fail(span, code, message)
        emit.turn_error(code, message)
        receipt = self.h.tracer.receipt(ctx.tenant_id, ctx.turn_id)
        exit_ = "cancelled" if code == "cancelled" else "error"
        with contextlib.suppress(Exception):  # keep turn numbering gapless after a failure
            self.h.session.append_turn(ctx, "gateway", None, "", exit_)
        emit.turn_end("error", {"exit": exit_, "reply": ""}, cost=receipt["total"])

    # -- controlling a turn -------------------------------------------------------------------

    def get(self, tenant_id: str, turn_id: str) -> TurnState | None:
        state = self.turns.get(turn_id)
        return state if state and state.ctx.tenant_id == tenant_id else None

    def resume(self, tenant_id: str, turn_id: str, mode: str) -> str:
        state = self.get(tenant_id, turn_id)
        if state is None:
            raise GatewayError(404, "turn_not_found", f"No turn {turn_id} for this tenant.")
        from harnesswalk.events import NotPaused

        try:
            return state.ctx.emit.breakpoints.resume(mode)
        except NotPaused as exc:
            raise GatewayError(
                409, "turn_not_paused", f"Turn {turn_id} is not at a breakpoint."
            ) from exc

    def cancel(self, tenant_id: str, turn_id: str) -> None:
        state = self.get(tenant_id, turn_id)
        if state is None:
            raise GatewayError(404, "turn_not_found", f"No turn {turn_id} for this tenant.")
        if state.task and not state.task.done():
            state.task.cancel()

    async def wait(self, turn_id: str, timeout: float | None = None) -> TurnState:
        state = self.turns[turn_id]
        await asyncio.wait_for(state.done.wait(), timeout)
        return state

    async def shutdown(self) -> None:
        for state in self.turns.values():
            if state.task and not state.task.done():
                state.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await state.task

    def describe(self) -> dict[str, Any]:
        return {"running": len(self._open_sessions), "turns": len(self.turns)}
