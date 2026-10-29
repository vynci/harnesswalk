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
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from harnesswalk import diagram
from harnesswalk.context.memory_writer import run_memory_writer
from harnesswalk.context.stores.session import SessionForbidden
from harnesswalk.events import Breakpoints, TurnEmitter, new_trace_id, uuid7
from harnesswalk.execution.loop import Limits
from harnesswalk.execution.providers import PROVIDER_NAMES
from harnesswalk.execution.providers.fake import FIXTURE_NAME
from harnesswalk.nodes import node, register
from harnesswalk.ops.trace import run_trace_node
from harnesswalk.types import ConfigError, Identity, TurnContext, Usage

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
    provider: str | None = None  # dev mode only: run this turn on one provider
    fixture: str | None = None  # dev mode only: the fake provider's fixture for this turn


@dataclass
class TurnState:
    ctx: TurnContext
    request: TurnRequest
    task: asyncio.Task | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    reply: str = ""
    exit: str = ""
    replied: bool = False  # the reply phase closed: a cancel has nothing left to stop
    recorded: bool = False  # the session holds this turn: a later failure must not rewrite it
    finished_at: float | None = None  # monotonic time the turn ended, for eviction


def cap_reply(text: str) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= REPLY_MAX_BYTES:
        return text
    return raw[: REPLY_MAX_BYTES - 16].decode("utf-8", "ignore") + " [truncated]"


class Gateway:
    """Starts, runs and controls turns.

    `turns` holds the in-process state of running turns and of recently finished ones. A
    finished turn is evicted after `gateway.turn_state_ttl_seconds`, or sooner once more than
    `gateway.turn_state_max` finished turns are kept (oldest finish first). Running and paused
    turns are never evicted. Nothing is lost: summaries, history and replay read the trace store
    (`turns.py`), and a control call on an evicted turn answers as for any finished turn.
    """

    def __init__(self, harness: Harness) -> None:
        self.h = harness
        self.cfg = harness.settings.get("gateway", {})
        self.turns: dict[str, TurnState] = {}
        self._open_sessions: dict[str, str] = {}  # session_id -> running turn_id
        self._rate: dict[str, list[float]] = {}
        self.turn_state_max = max(1, int(self.cfg.get("turn_state_max", 500)))
        self.turn_state_ttl_s = max(0.0, float(self.cfg.get("turn_state_ttl_seconds", 3600)))
        self._finished: OrderedDict[str, float] = OrderedDict()  # turn_id -> finished_at
        self._clock = time.monotonic

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
        self._validate_provider(req)
        pack = self.h.settings.get("dataset", "_starter")
        if req.dataset and req.dataset != pack:
            raise GatewayError(
                422, "invalid_request", f"dataset {req.dataset!r} is not loaded ({pack} is)"
            )

    def _validate_provider(self, req: TurnRequest) -> None:
        """Per-turn `provider` and `fixture`: dev mode only (http_api.md, POST /v1/turns)."""
        if req.provider is None and req.fixture is None:
            return
        if self.h.settings.mode != "dev":
            raise GatewayError(
                400, "invalid_request", "provider and fixture can only be chosen in dev mode"
            )
        if req.provider is not None and req.provider not in PROVIDER_NAMES:
            raise GatewayError(400, "invalid_request", f"unknown provider {req.provider!r}")
        if req.fixture is not None:
            if not isinstance(req.fixture, str) or not FIXTURE_NAME.fullmatch(req.fixture):
                raise GatewayError(400, "invalid_request", "fixture is not a valid name")
            if req.provider not in (None, "fake"):
                raise GatewayError(400, "invalid_request", "a fixture needs the fake provider")
        elif not self.h.provider_available(req.provider):  # type: ignore[arg-type]
            raise GatewayError(
                422, "provider_not_configured", f"provider {req.provider!r} is not configured"
            )

    def _turn_providers(self, req: TurnRequest) -> dict[str, Any] | None:
        if req.provider is None and req.fixture is None:
            return None
        try:
            return self.h.turn_providers(req.provider, req.fixture)
        except ConfigError as exc:
            raise GatewayError(400, "invalid_request", str(exc)) from exc

    async def start_turn(self, req: TurnRequest, client_ip: str = "") -> TurnState:
        self._validate(req)
        turn_providers = self._turn_providers(req)
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
            providers=turn_providers,
        )
        state = TurnState(ctx=ctx, request=req)
        self.evict_finished()
        self.turns[turn_id] = state
        self._open_sessions[session_id] = turn_id
        start: dict[str, Any] = {
            "kind": "chat",
            "channel": req.channel,
            "mode": ctx.mode,
            "step_mode": req.step_mode,
            "message_chars": len(req.message),
            "dataset": dataset,
            "turn_index": ctx.turn_index,
        }
        if turn_providers is not None:
            start["provider"] = next(iter(turn_providers))
        if req.fixture is not None:
            start["fixture"] = req.fixture
        emitter.turn_start(start)
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
                notice = h.loop.notices.get("error", "Something went wrong.")
                try:
                    emit.edge("input_guardrails_to_context_builder")
                    assembled = await h.context_builder.build(ctx, user_text)
                    emit.edge("context_builder_to_loop")
                    result = await h.loop.run(
                        ctx,
                        assembled.messages,
                        h.tools.schemas(ctx.mode),
                        Limits.from_config(h.settings.get("loop", {})),
                        api_key=req.api_key,
                    )
                    answer, steps = result.answer.text, result.steps
                    exit_ = EXIT_FOR_REASON[result.exit_reason]
                except Exception:
                    # A downstream node failed and closed its own span: answer with a notice.
                    if emit.stack:
                        raise
                    answer, exit_ = notice, "error"
                try:
                    emit.edge("loop_to_output_guardrails")
                    checked = await h.output_guardrails.run(ctx, answer)
                    if checked.action == "block":
                        exit_ = "blocked"
                    reply = checked.text
                except Exception:
                    if emit.stack:
                        raise
                    reply, exit_ = notice, "error"
                emit.edge("output_guardrails_to_gateway")

            full_reply, reply = reply, cap_reply(reply)
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
            state.replied = True
            h.session.append_turn(ctx, "gateway", user_text, reply, exit_)
            state.recorded = True
            await run_memory_writer(ctx, [])
            receipt = await run_trace_node(ctx, h.tracer)
            emit.turn_end(
                "done",
                {"exit": exit_, "steps": steps, "reply": reply},
                cost=receipt["total"],
                # A truncated reply points at the full text in the trace (events.md rule 12).
                detail={"reply": full_reply} if full_reply != reply else None,
            )
        except asyncio.CancelledError:
            exit_ = "cancelled"
            self._close_after_failure(state, "cancelled", "The turn was cancelled.")
        except Exception as exc:  # anything unexpected still ends the turn properly
            exit_ = "error"
            self._close_after_failure(state, "internal", f"{type(exc).__name__}: {exc}")
        finally:
            state.reply, state.exit = reply, exit_
            req.api_key = None  # a BYO key lives for this turn only
            self._open_sessions.pop(ctx.session_id, None)
            state.finished_at = self._clock()
            self._finished[ctx.turn_id] = state.finished_at
            state.done.set()
            self.evict_finished()

    def evict_finished(self) -> int:
        """Drop the state of finished turns past the TTL or over the cap. Returns how many."""
        now, evicted = self._clock(), 0
        while self._finished:
            turn_id, finished_at = next(iter(self._finished.items()))
            if now - finished_at < self.turn_state_ttl_s and len(self._finished) <= (
                self.turn_state_max
            ):
                break
            del self._finished[turn_id]
            self.turns.pop(turn_id, None)
            evicted += 1
        return evicted

    def _close_after_failure(self, state: TurnState, code: str, message: str) -> None:
        ctx = state.ctx
        emit = ctx.emit
        if emit.ended:
            return
        for span in reversed(list(emit.stack)):
            emit.fail(span, code, message)
        emit.turn_error(code, message)
        try:
            cost = self.h.tracer.receipt(ctx.tenant_id, ctx.turn_id)["total"]
        except Exception:  # the trace store is down: still end the turn, so streams close
            cost = Usage().cost()
        exit_ = "cancelled" if code == "cancelled" else "error"
        if not state.recorded:  # a turn already in the session keeps its stored answer
            with contextlib.suppress(Exception):  # keep turn numbering gapless after a failure
                self.h.session.append_turn(ctx, "gateway", None, "", exit_)
        emit.turn_end("error", {"exit": exit_, "reply": ""}, cost=cost)

    # -- controlling a turn -------------------------------------------------------------------

    def get(self, tenant_id: str, turn_id: str) -> TurnState | None:
        state = self.turns.get(turn_id)
        return state if state and state.ctx.tenant_id == tenant_id else None

    def _missing(self, tenant_id: str, turn_id: str, code: str, message: str) -> GatewayError:
        """The error for a turn this process holds no state for: evicted, or never there."""
        if self.h.tracer.turn_exists(tenant_id, turn_id):
            return GatewayError(409, code, message)
        return GatewayError(404, "turn_not_found", f"No turn {turn_id} for this tenant.")

    def resume(self, tenant_id: str, turn_id: str, mode: str) -> str:
        state = self.get(tenant_id, turn_id)
        if state is None:
            raise self._missing(
                tenant_id, turn_id, "turn_not_paused", f"Turn {turn_id} is not at a breakpoint."
            )
        from harnesswalk.events import NotPaused

        try:
            return state.ctx.emit.breakpoints.resume(mode)
        except NotPaused as exc:
            raise GatewayError(
                409, "turn_not_paused", f"Turn {turn_id} is not at a breakpoint."
            ) from exc

    def cancel(self, tenant_id: str, turn_id: str) -> TurnState:
        """Ask a turn to stop (http_api.md, cancel). Idempotent until the turn ends.

        Running: the flag stops the loop at its next step boundary (exit `interrupted`, a
        notice reply). Paused, or at the next breakpoint or approval: TurnCancelled closes
        every open span with `error` `cancelled`. Either way the turn ends on its own task.
        After the reply phase (memory writer, trace) there is nothing left to stop: the
        cancel releases any breakpoint and the turn finishes as it would have.
        """
        state = self.get(tenant_id, turn_id)
        if state is None:
            raise self._missing(
                tenant_id, turn_id, "turn_finished", f"Turn {turn_id} has already ended."
            )
        if state.done.is_set():
            raise GatewayError(409, "turn_finished", f"Turn {turn_id} has already ended.")
        if state.replied:
            state.ctx.emit.breakpoints.finish()
            return state
        state.ctx.interrupted = True
        state.ctx.emit.breakpoints.cancel()
        self.h.approvals.cancel_turn(turn_id)
        return state

    async def wait(self, turn_id: str, timeout: float | None = None) -> TurnState:
        """Wait for a turn this process holds. KeyError once a finished turn was evicted."""
        state = self.turns[turn_id]
        await asyncio.wait_for(state.done.wait(), timeout)
        return state

    async def shutdown(self) -> None:
        for state in list(self.turns.values()):  # a finishing turn may evict others
            if state.task and not state.task.done():
                state.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await state.task

    def describe(self) -> dict[str, Any]:
        return {
            "running": len(self._open_sessions),
            "turns": len(self.turns),
            "finished_kept": len(self._finished),
        }
