"""Gateway: the front door and the exit. It turns a request into a turn and runs it.

Contract: spec/contracts/gateway.md (HTTP surface in http_api.md, served by http_api.py).
Node: `gateway` (control plane, kind code), entered twice per chat turn: `phase: receive`
first, `phase: reply` last.

`start_turn` resolves the session, allocates `turn_id` and `trace_id`, emits `turn_start`
(seq 0) and schedules `run_turn`, which walks the request through the harness:

    gateway (receive) -> input_guardrails -> context_builder (memory) -> loop
        (model_layer, tools ...) -> output_guardrails -> gateway (reply)
        -> [memory_writer] -> trace -> turn_end

`turn_end.summary.citations` lists the knowledge chunks the reply cites with `[n]`.

When input guardrails block, the walk is gateway, input_guardrails, gateway, trace.

P5: the session's dataset pack picks what the turn runs on. `start_turn` resolves the pack's
view of the harness (`Harness.view`: the harness itself for the configured pack, a
`PackRuntime` for another), keeps it on the turn's state, and every per-pack component the walk
calls (guardrails, context builder, loop, tools, memory writer) comes from it.

P6 (public mode, control/public.py): a turn's `model` must be one of `public.models` (400
otherwise), a priced alias with the shared daily budget spent and no BYO key is a 409
`budget_exhausted`, the anonymous tenant is registered with its expiry for the TTL sweeper, the
rate-limit table is bounded, and the turn's notices (`budget_exhausted` when it fell through to
the fake model) end up on `turn_end.summary.notices`. A public turn may name a `fixture` when it
runs on the scripted model (`fake-small`, by its hint or by the router while the budget is
spent); the name must be one of the turn's pack's fixtures.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import jsonschema

from harnesswalk import diagram
from harnesswalk.context.stores.session import SessionForbidden
from harnesswalk.control.public import (
    BUDGET_EXHAUSTED,
    PROVIDER_UNCONFIGURED,
    RateLimited,
    RateLimiter,
    pack_fixture_names,
    rate_key,
)
from harnesswalk.events import Breakpoints, TurnEmitter, new_trace_id, uuid7
from harnesswalk.execution.loop import Limits
from harnesswalk.execution.providers import PROVIDER_NAMES
from harnesswalk.execution.providers.fake import FIXTURE_NAME
from harnesswalk.nodes import node, register
from harnesswalk.ops import payloads
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
    "budget_tool_calls": "limit",
    "interrupted": "cancelled",
}


class GatewayError(Exception):
    """A request the gateway refuses before any turn exists. Maps to an HTTP status."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}  # more fields of the error object (resets_at, retry_after)
        self.headers = headers or {}


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
    fixture: str | None = None  # the fake provider's fixture: dev, or public on fake-small
    output_schema: dict[str, Any] | None = None  # a structured reply (output guardrails check it)
    model: str | None = None  # a router alias hint: any alias in dev, public.models in public
    # P4: a turn the harness starts for itself (an eval case). It may pick a provider and a
    # fixture in any mode and is not rate-limited. Never set from HTTP.
    internal: bool = False
    eval_run_id: str | None = None
    # The eval `model` suite: pin every model call of the turn to this alias (no fallback).
    # Internal turns only; never set from HTTP.
    pin_model: str | None = None
    # P5: the pack view to run on instead of the session's (the eval gate's candidate). Never
    # set from HTTP.
    runtime: Any = field(default=None, repr=False)


@dataclass
class TurnState:
    ctx: TurnContext
    request: TurnRequest
    task: asyncio.Task | None = None
    # P6 review: the public wall-clock bound (paused time included), cancelled when it ends.
    wall_clock: asyncio.TimerHandle | None = field(default=None, repr=False)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    reply: str = ""
    exit: str = ""
    replied: bool = False  # the reply phase closed: a cancel has nothing left to stop
    cost: dict[str, Any] | None = None  # the receipt total, for the budget ledger
    proposals: list[Any] = field(default_factory=list)  # memory proposals from the loop
    observations: list[str] = field(default_factory=list)  # tool results (output guardrails)
    recorded: bool = False  # the session holds this turn: a later failure must not rewrite it
    finished_at: float | None = None  # monotonic time the turn ended, for eviction
    byo_key: bool = False  # the turn came with a BYO key (kept as a flag only, P4)
    restored: bool = False  # re-attached after a restart (P4 checkpoints)
    byo_key_dropped: bool = False  # restored, and the BYO key it had is gone
    view: Any = None  # P5: the pack view the turn runs on (Harness.view)


CITATION_MARK = re.compile(r"\[(\d{1,2})\]")


def cited_sources(reply: str, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The knowledge chunks the reply cites with `[n]`, in first-cited order."""
    by_n = {c["n"]: c for c in citations}
    out, seen = [], set()
    for m in CITATION_MARK.finditer(reply):
        n = int(m.group(1))
        if n in by_n and n not in seen:
            seen.add(n)
            c = by_n[n]
            out.append({"n": n, "doc": c["doc"], "chunk": c["chunk"], "citation": c["citation"]})
    return out


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
        public = harness.settings.get("public", {}) or {}
        self.rate = RateLimiter(int(public.get("rate_limit_max_keys", 10000)))
        self.turn_state_max = max(1, int(self.cfg.get("turn_state_max", 500)))
        self.turn_state_ttl_s = max(0.0, float(self.cfg.get("turn_state_ttl_seconds", 3600)))
        self._finished: OrderedDict[str, float] = OrderedDict()  # turn_id -> finished_at
        self._clock = time.monotonic
        # P6 review: public turns on a priced alias the site pays for, running now: turn_id ->
        # the client's rate key. Bounded globally and per client (`public.max_running_hosted_*`).
        self._hosted_running: dict[str, str] = {}

    # -- starting a turn ----------------------------------------------------------------------

    def _check_rate(self, key: str, per_hour: int) -> int:
        try:
            return self.rate.hit(key, per_hour)
        except RateLimited as exc:
            raise GatewayError(
                429,
                "rate_limited",
                f"Too many requests, try again in {exc.retry_after} seconds.",
                extra={"retry_after": exc.retry_after},
                headers={"Retry-After": str(exc.retry_after)},
            ) from exc

    def _check_hosted_running(self, req: TurnRequest, ip_key: str) -> bool:
        """Public mode: refuse a site-paid turn on a priced alias when too many run already.
        Returns whether the turn counts (so its slot is freed when it ends)."""
        public = self.h.public_models
        if req.api_key:
            return False
        alias = req.model if isinstance(req.model, str) else public.effective_default()
        if alias is None or not public.metered(alias):
            return False
        cfg = self.h.settings.get("public", {}) or {}
        per_ip = int(cfg.get("max_running_hosted_turns_per_ip", 2))
        total = int(cfg.get("max_running_hosted_turns", 10))
        mine = sum(1 for k in self._hosted_running.values() if k == ip_key)
        if mine >= per_ip or len(self._hosted_running) >= total:
            why = "from this address" if mine >= per_ip else "on the site"
            raise GatewayError(
                429,
                "rate_limited",
                f"Too many Claude turns are running {why} right now. Try again in a few seconds,"
                " or use the fake model.",
                extra={"retry_after": 10, "limit": "running_turns"},
                headers={"Retry-After": "10"},
            )
        return True

    def rate_remaining(self, client_ip: str) -> int:
        """Turns this IP may still start this hour (GET /v1/capabilities, public mode)."""
        return self.rate.remaining(
            f"ip:{rate_key(client_ip)}", int(self.cfg["rate_limit"]["per_ip_per_hour"])
        )

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
        if req.dataset is not None and not self.h.pack_known(req.dataset):
            raise GatewayError(422, "invalid_request", f"no dataset pack {req.dataset!r}")

    def _validate_public_model(self, req: TurnRequest) -> None:
        """A public turn's `model` (P6): one of `public.models` that can answer now."""
        public = self.h.public_models
        if not isinstance(req.model, str) or not public.allowed(req.model):
            raise GatewayError(400, "invalid_request", "model not available in public mode")
        reason = public.reason(req.model, byo=bool(req.api_key))
        if reason == BUDGET_EXHAUSTED:
            resets_at = self.h.hosted_budget.public_view()["resets_at"]
            raise GatewayError(
                409,
                "budget_exhausted",
                "Today's shared budget for Claude models is spent. It resets at 00:00 UTC."
                " Bring your own key or use the fake model.",
                extra={"resets_at": resets_at},
            )
        if reason == PROVIDER_UNCONFIGURED:
            raise GatewayError(
                422,
                "provider_not_configured",
                f"No key is configured for {req.model}. Bring your own key.",
            )

    def _validate_provider(self, req: TurnRequest) -> None:
        """Per-turn `provider` and `fixture`: dev mode only; `model`: dev and public (P6)."""
        if req.model is not None and self.h.settings.mode == "public" and not req.internal:
            self._validate_public_model(req)
        elif req.model is not None:
            if self.h.settings.mode != "dev" and not req.internal:
                raise GatewayError(
                    400, "invalid_request", "model can only be chosen in dev and public mode"
                )
            if req.model not in self.h.settings.get("models.models", {}):
                raise GatewayError(400, "invalid_request", f"unknown model alias {req.model!r}")
        if req.output_schema is not None:
            try:
                jsonschema.Draft202012Validator.check_schema(req.output_schema)
            except jsonschema.SchemaError as exc:
                raise GatewayError(400, "invalid_request", "output_schema is not valid") from exc
        if req.provider is None and req.fixture is None:
            return
        if self.h.settings.mode != "dev" and not req.internal and not self._public_fixture(req):
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

    def _public_fixture(self, req: TurnRequest) -> bool:
        """Public mode: a `fixture` (and no `provider`) on a turn that runs on the scripted
        model, named by its hint or picked by the router now (the budget is spent)."""
        if self.h.settings.mode != "public" or req.provider is not None or req.fixture is None:
            return False
        public = self.h.public_models
        alias = req.model if isinstance(req.model, str) else None
        return public.scripted(alias or public.effective_default(byo=bool(req.api_key)))

    def _turn_providers(self, req: TurnRequest, pack: str) -> dict[str, Any] | None:
        if req.provider is None and req.fixture is None:
            return None
        public = self.h.settings.mode == "public" and not req.internal
        if public and req.fixture not in pack_fixture_names(self.h, pack):
            # Checked by name first, so the refusal names no server path.
            raise GatewayError(400, "invalid_request", f"no fixture named {req.fixture!r}")
        try:
            return self.h.turn_providers(req.provider, req.fixture, pack)
        except ConfigError as exc:
            message = f"fixture {req.fixture!r} cannot be loaded" if public else str(exc)
            raise GatewayError(400, "invalid_request", message) from exc

    async def start_turn(self, req: TurnRequest, client_ip: str = "") -> TurnState:
        self._validate(req)
        tenant = req.identity.tenant_id
        remaining = None
        hosted = False
        if self.h.settings.mode == "public" and not req.internal:
            hosted = self._check_hosted_running(req, rate_key(client_ip))
            remaining = self._check_rate(
                f"ip:{rate_key(client_ip)}", int(self.cfg["rate_limit"]["per_ip_per_hour"])
            )
        if self.h.settings.mode != "dev" and not req.internal:
            self._check_rate(f"tenant:{tenant}", int(self.cfg["rate_limit"]["per_tenant_per_hour"]))

        if req.identity.expires_at is not None:  # P6: the sweeper deletes it after this
            self.h.tenants.register(tenant, req.identity.expires_at)
        session_id = req.session_id or "sess_" + uuid7().replace("-", "")[:20]
        owner = self.h.session.owner(session_id)
        if owner is not None and owner != tenant:
            raise GatewayError(
                404, "session_not_found", f"No session {session_id} for this tenant."
            )
        existing = self.h.session.dataset_of(tenant, session_id) if owner else None
        if req.dataset and existing is not None and existing != req.dataset:
            raise GatewayError(422, "invalid_request", "the dataset is fixed after the first turn")
        # The session's pack decides what the turn runs on (P5); the eval gate brings its own.
        view = req.runtime or self._view(existing or req.dataset or self.h.pack)
        turn_providers = self._turn_providers(req, view.pack)  # a fixture of the turn's pack
        try:
            dataset = self.h.session.ensure_session(tenant, session_id, view.pack)
        except SessionForbidden as exc:
            raise GatewayError(
                404, "session_not_found", f"No session {session_id} for this tenant."
            ) from exc
        if req.runtime is None:
            view.refresh_artifacts()  # another process (the CLI) may have changed a release
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
            model_hint=req.model,
            model_pin=req.pin_model if req.internal else None,
            budget=self.h.budgets.for_turn(tenant),
            output_schema=req.output_schema,
            byo_key=bool(req.api_key),
        )
        state = TurnState(ctx=ctx, request=req, byo_key=bool(req.api_key), view=view)
        self.evict_finished()
        self.turns[turn_id] = state
        self._open_sessions[session_id] = turn_id
        if hosted:
            self._hosted_running[turn_id] = rate_key(client_ip)
        if req.api_key:  # the turn's internal calls are paid by the same key (model layer)
            self.h.turn_keys[turn_id] = req.api_key
        start: dict[str, Any] = {
            "kind": "chat",
            "channel": req.channel,
            "mode": ctx.mode,
            "step_mode": req.step_mode,
            "message_chars": len(req.message),
            "dataset": dataset,
            "pack": dataset,
            "turn_index": ctx.turn_index,
        }
        if turn_providers is not None:
            start["provider"] = next(iter(turn_providers))
        if req.fixture is not None:
            start["fixture"] = req.fixture
        if req.disabled_nodes:
            start["disabled"] = list(req.disabled_nodes)
        if req.model is not None:
            start["model_hint"] = req.model
        start["release"] = view.release.stamp()
        if req.eval_run_id is not None:
            start["eval_run_id"] = req.eval_run_id
        if req.output_schema is not None:
            start["output_schema"] = True
        emitter.turn_start(start)
        state.task = asyncio.create_task(self.run_turn(state, remaining))
        wall_s = float((self.h.settings.get("public", {}) or {}).get("max_turn_wall_s", 600) or 0)
        if self.h.settings.mode == "public" and not req.internal and wall_s > 0:
            # loop.timeout_s counts active time only, so a paused step-mode turn would hold its
            # session, task and stream forever: past the bound it is cancelled like a Stop.
            state.wall_clock = asyncio.get_running_loop().call_later(
                wall_s, self._wall_clock_ended, tenant, turn_id
            )
        return state

    def _wall_clock_ended(self, tenant_id: str, turn_id: str) -> None:
        state = self.turns.get(turn_id)
        if state is None or state.done.is_set():
            return
        with contextlib.suppress(GatewayError):
            self.cancel(tenant_id, turn_id)

    # -- running a turn -----------------------------------------------------------------------

    def _view(self, pack: str) -> Any:
        from harnesswalk.runtime import PackUnavailable

        try:
            return self.h.view(pack)
        except PackUnavailable as exc:
            raise GatewayError(422, "invalid_request", str(exc.args[0])) from exc
        except KeyError as exc:
            raise GatewayError(422, "invalid_request", f"no dataset pack {pack!r}") from exc

    async def run_turn(self, state: TurnState, rate_remaining: int | None = None) -> None:
        ctx, req, h = state.ctx, state.request, state.view or self.h
        emit = ctx.emit
        try:
            received = self._received_payload(ctx, h, req) if payloads.capturing(ctx) else None
            async with node(ctx, "gateway", enter_detail=self._at(received, "enter")) as span:
                span.set(
                    phase="receive", channel=req.channel, authenticated=not req.identity.anonymous
                )
                if rate_remaining is not None:
                    span.set(rate_limit_remaining=rate_remaining)
                span.detail = self._at(received, "exit")

            emit.edge("gateway_to_input_guardrails")
            guard = await h.input_guardrails.run(ctx, req.message)
            if guard.action == "block":
                why = guard.reason or "it was blocked"
                hint = (
                    "shorten or rephrase it" if guard.results[-1].check == "size" else "rephrase it"
                )
                reply = f"I can't take this message: {why}. Please {hint}."
                await self._reply_and_close(state, reply, "blocked", 0, None)
                return
            user_text = guard.text
            notice = h.loop.notices.get("error", "Something went wrong.")
            chunks: list[dict[str, Any]] = []
            try:
                emit.edge("input_guardrails_to_context_builder")
                assembled = await h.context_builder.build(ctx, user_text)
                chunks = list(ctx.citations)  # the knowledge chunks numbered for this turn
                emit.edge("context_builder_to_loop")
                result = await h.loop.run(
                    ctx,
                    assembled.messages,
                    h.tools.schemas(ctx.mode),
                    Limits.from_config(h.settings.get("loop", {}), ctx.budget),
                    api_key=req.api_key,
                )
                answer, steps = result.answer.text, result.steps
                state.proposals = list(result.proposals)
                state.observations = list(result.observations)
                exit_ = EXIT_FOR_REASON[result.exit_reason]
            except Exception:
                # A downstream node failed and closed its own span: answer with a notice.
                if emit.stack:
                    raise
                answer, exit_, steps = notice, "error", 0
            await self._check_and_reply(state, answer, exit_, steps, user_text, chunks)
        except asyncio.CancelledError:
            state.exit = "cancelled"
            self._close_after_failure(state, "cancelled", "The turn was cancelled.")
        except Exception as exc:  # anything unexpected still ends the turn properly
            state.exit = "error"
            self._close_after_failure(state, "internal", f"{type(exc).__name__}: {exc}")
        finally:
            self._finish(state)

    async def _check_and_reply(
        self,
        state: TurnState,
        answer: str,
        exit_: str,
        steps: int,
        user_text: str | None,
        chunks: list[dict[str, Any]],
    ) -> None:
        """Output guardrails on the answer, then the reply. The tail every turn shares."""
        ctx, h = state.ctx, state.view or self.h
        notice = h.loop.notices.get("error", "Something went wrong.")
        try:
            ctx.emit.edge("loop_to_output_guardrails")
            checked = await h.output_guardrails.run(
                ctx, answer, state.request.output_schema, chunks, state.observations
            )
            if checked.action == "block":
                exit_ = "blocked"
            reply = checked.text
        except Exception:
            if ctx.emit.stack:
                raise
            reply, exit_ = notice, "error"
        ctx.emit.edge("output_guardrails_to_gateway")
        await self._reply_and_close(state, reply, exit_, steps, user_text)

    async def _reply_and_close(
        self, state: TurnState, reply: str, exit_: str, steps: int, user_text: str | None
    ) -> None:
        """The reply span (with message_delta), the session record, memory writer, trace."""
        ctx, h, emit = state.ctx, state.view or self.h, state.ctx.emit
        req = state.request
        state.exit = exit_
        full_reply, reply = reply, cap_reply(reply)
        about = None
        if payloads.capturing(ctx):
            about = {"payload": payloads.record("gateway", "enter", phase="reply")}
            about["payload"]["reply"] = payloads.text(full_reply)
        async with node(ctx, "gateway", enter_detail=about) as span:
            span.set(phase="reply", channel=req.channel, reply_chars=len(reply))
            if about is not None:
                span.detail = self._reply_payload(ctx, h, req, user_text, full_reply)
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
        state.reply = reply
        # The session keeps what the model saw: placeholders, not the restored values.
        stored = reply
        vault = ctx.pii_vault
        if vault is not None and len(vault):
            stored = self._reredact(reply, vault)
        h.session.append_turn(ctx, "gateway", user_text, stored, exit_)
        state.recorded = True
        # Memory sees the redacted text too: a restored email never becomes a memory.
        proposals = list(state.proposals)
        cause = (emit.first_error or {}).get("code")
        proposals += h.memory_writer.turn_episode(ctx, user_text, stored, exit_, cause)
        await h.memory_writer.run(ctx, proposals, outcome=exit_)
        receipt = await run_trace_node(ctx, h.tracer)
        state.cost = receipt["total"]
        end: dict[str, Any] = {"exit": exit_, "steps": steps, "reply": reply}
        if ctx.notices:  # P6: what the UI shows under the reply (budget_exhausted)
            end["notices"] = list(ctx.notices)
        if state.byo_key_dropped:
            end["byo_key_dropped"] = True
        cited = cited_sources(reply, ctx.citations)
        if cited:
            end["citations"] = cited
        emit.turn_end(
            "done",
            end,
            cost=receipt["total"],
            # A truncated reply points at the full text in the trace (events.md rule 12).
            detail={"reply": full_reply} if full_reply != reply else None,
        )

    # -- payloads (tracer.md "Payloads") ------------------------------------------------------

    @staticmethod
    def _at(rec: dict[str, Any] | None, at: str) -> dict[str, Any] | None:
        return None if rec is None else {"payload": {**rec, "at": at}}

    @staticmethod
    def _original(
        ctx: TurnContext, h: Any, req: TurnRequest, changed: bool
    ) -> tuple[dict[str, Any] | None, bool]:
        allowed = payloads.raw_allowed(ctx.mode, h.input_guardrails.restore_modes)
        return payloads.kept_original(req.message, changed, allowed)

    def _received_payload(self, ctx: TurnContext, h: Any, req: TurnRequest) -> dict[str, Any]:
        """The message as received. Before the guardrails ran, a text they would change
        (personal data, control characters) is kept only where the PII restore rule is on."""
        kept, withheld = self._original(ctx, h, req, h.input_guardrails.would_change(req.message))
        return payloads.record(
            "gateway",
            "exit",
            phase="receive",
            channel=req.channel,
            received=kept,
            withheld=withheld,
            chars=len(req.message),
        )

    def _reply_payload(
        self, ctx: TurnContext, h: Any, req: TurnRequest, user_text: str | None, reply: str
    ) -> dict[str, Any]:
        """The whole hop: the message as received, as the model saw it (after redaction),
        how many values were replaced (never the values), and the reply sent."""
        if user_text is None:  # blocked: the guardrails' text never went on
            changed = h.input_guardrails.would_change(req.message)
        else:
            changed = user_text != req.message
        kept, withheld = self._original(ctx, h, req, changed)
        return {
            "payload": payloads.record(
                "gateway",
                "exit",
                phase="reply",
                channel=req.channel,
                received=kept,
                withheld=withheld,
                redacted=payloads.text(user_text) if user_text is not None else None,
                blocked=user_text is None,
                placeholders=payloads.placeholders(ctx.pii_vault),
                reply=payloads.text(reply),
            )
        }

    @staticmethod
    def _reredact(text: str, vault: Any) -> str:
        for (_, value), token in vault.by_value.items():
            text = text.replace(value, token)
        return text

    def _finish(self, state: TurnState) -> None:
        """Bookkeeping when a turn's task ends, however it ended."""
        ctx, req = state.ctx, state.request
        req.api_key = None  # a BYO key lives for this turn only
        if state.wall_clock is not None:
            state.wall_clock.cancel()
        self.h.turn_keys.pop(ctx.turn_id, None)
        self._hosted_running.pop(ctx.turn_id, None)
        ctx.pii_vault = None  # the placeholders' values live for this turn only
        if not ctx.detached:
            with contextlib.suppress(Exception):
                cost = state.cost or self.h.tracer.receipt(ctx.tenant_id, ctx.turn_id)["total"]
                calls = ctx.budget.tool_calls if ctx.budget is not None else 0
                tokens = cost["input_tokens"] + cost["output_tokens"]
                self.h.budgets.record(ctx.tenant_id, ctx.turn_id, tokens, cost["usd"], calls)
        self.h.sandbox.end_turn(ctx)
        self._open_sessions.pop(ctx.session_id, None)
        state.finished_at = self._clock()
        self._finished[ctx.turn_id] = state.finished_at
        state.done.set()
        self.evict_finished()
        with contextlib.suppress(Exception):  # online judging never affects the turn
            self.h.evals.online.after_turn(state)

    def running_tenants(self) -> set[str]:
        """Tenants with a turn running or paused here: the TTL sweeper leaves them for later."""
        return {s.ctx.tenant_id for s in self.turns.values() if not s.done.is_set()}

    def forget_tenant(self, tenant_id: str) -> None:
        """Drop what this process keeps in memory for a deleted tenant (P6 sweeper)."""
        for turn_id, state in list(self.turns.items()):
            if state.ctx.tenant_id == tenant_id and state.done.is_set():
                self.turns.pop(turn_id, None)
                self._finished.pop(turn_id, None)
        self.rate.forget(f"tenant:{tenant_id}")

    def running_turns(self) -> list[str]:
        """Turn ids this process is running (or holding paused), for the release lock."""
        return [t for t, s in self.turns.items() if not s.done.is_set()]

    # -- recovery after a restart (approval_queue.md, P3 additions) ---------------------------

    def recover(self) -> dict[str, list[str]]:
        """Re-attach turns paused on an approval; close every other unfinished turn.

        Runs once at start-up, on the event loop, before the server takes requests. Returns the
        turn ids it resumed and closed, for the start-up log and the tests.
        """
        out: dict[str, list[str]] = {"resumed": [], "closed": []}
        for tenant_id, turn_id in self.h.tracer.unfinished_turns():
            if turn_id in self.turns:
                continue
            events = self.h.tracer.get_turn(tenant_id, turn_id)
            if not events:
                continue
            try:
                resumed = self._reattach(events)
            except Exception:  # anything odd about the stored turn: close it instead
                resumed = False
            if resumed:
                out["resumed"].append(turn_id)
            else:
                self._close_orphan(events)
                out["closed"].append(turn_id)
        return out

    def _restored_ctx(self, events: list[dict[str, Any]], *, validate: bool = True) -> TurnContext:
        start = events[0]
        summary = start.get("summary") or {}
        tenant, trace_id = start["tenant_id"], start["trace_id"]
        capture = bool(self.h.settings.get("trace.capture_content", True))
        emitter = TurnEmitter.restore(
            events,
            bus=self.h.bus,
            validate=validate and bool(self.h.settings.get("trace.validate_events", True)),
            detail_sink=(
                (lambda event_id, rec: self.h.tracer.put_detail(tenant, trace_id, event_id, rec))
                if capture
                else None
            ),
        )
        mode = self.h.settings.mode
        identity = Identity(tenant, None, ["user"], anonymous=mode == "public")
        ctx = TurnContext(
            tenant_id=tenant,
            session_id=start["session_id"],
            turn_id=start["turn_id"],
            trace_id=trace_id,
            user_id=None,
            mode=mode,  # type: ignore[arg-type]
            emit=emitter,
            turn_index=int(summary.get("turn_index") or 0),
            identity=identity,
            disabled_nodes=frozenset(summary.get("disabled") or []),
            model_hint=summary.get("model_hint"),
        )
        model_exits = [
            e for e in events if e["type"] == "node_exit" and e.get("node_id") == "model_layer"
        ]
        ctx.model_calls = len(model_exits)
        ctx.calls_issued = sum(
            int((e.get("summary") or {}).get("tool_calls", 0)) for e in model_exits
        )
        return ctx

    def _reattach(self, events: list[dict[str, Any]]) -> bool:
        from harnesswalk.turns import summarize

        h = self.h
        view = summarize(events)
        if not view or view["paused_at_node"] != "approval" or not view["pending_approval_id"]:
            return False
        ctx = self._restored_ctx(events)
        if [s.node_id for s in ctx.emit.stack] != ["loop", "tools", "approval"]:
            return False
        record = h.approvals.get(view["pending_approval_id"], ctx.tenant_id)
        checkpoint = h.session.last_checkpoint(ctx.tenant_id, ctx.turn_id)
        if not checkpoint or not checkpoint.get("pending_call"):
            return False
        summary = events[0].get("summary") or {}
        view = h.view(summary.get("pack") or summary.get("dataset") or h.pack)
        provider, fixture = summary.get("provider"), summary.get("fixture")
        if provider is not None or fixture is not None:
            ctx.providers = h.turn_providers(provider, fixture, view.pack)
            fake = ctx.providers.get("fake")
            if fixture is not None and fake is not None:
                fake.resume_turn(  # type: ignore[attr-defined]
                    ctx.tenant_id, ctx.session_id, ctx.turn_id, self._fixture_index(ctx, fixture)
                )
        ctx.budget = h.budgets.for_turn(ctx.tenant_id)
        user_text = next(
            (m["content"] for m in reversed(checkpoint["messages"]) if m["role"] == "user"), None
        )
        kept = checkpoint.get("turn") or {}
        request = TurnRequest(
            message=user_text or "",
            identity=ctx.identity,  # type: ignore[arg-type]
            session_id=ctx.session_id,
            channel=summary.get("channel", "web"),
            provider=provider,
            fixture=fixture,
            output_schema=kept.get("output_schema"),
            eval_run_id=summary.get("eval_run_id"),
        )
        ctx.output_schema = request.output_schema
        ctx.citations = list(kept.get("citations") or [])
        ctx.byo_key = bool(kept.get("byo_key"))
        state = TurnState(ctx=ctx, request=request, restored=True, view=view)
        # The key itself was never stored: the turn goes on with the configured provider.
        state.byo_key_dropped = ctx.byo_key
        h.session.mark_restored(ctx.tenant_id, ctx.turn_id)
        self.turns[ctx.turn_id] = state
        self._open_sessions[ctx.session_id] = ctx.turn_id
        state.task = asyncio.create_task(self._resume(state, record, checkpoint, user_text))
        return True

    def _fixture_index(self, ctx: TurnContext, fixture: str) -> int:
        """Where this turn sits among the session's turns on the same per-turn fixture."""
        index = 0
        for turn_id in self.h.tracer.session_turn_ids(ctx.tenant_id, ctx.session_id, 200):
            first = self.h.tracer.get_turn(ctx.tenant_id, turn_id, -1)[:1]
            summary = (first[0].get("summary") or {}) if first else {}
            if summary.get("fixture") == fixture and int(summary.get("turn_index") or 0) < (
                ctx.turn_index
            ):
                index += 1
        return index

    async def _resume(
        self,
        state: TurnState,
        record: dict[str, Any],
        checkpoint: dict[str, Any],
        user_text: str | None,
    ) -> None:
        """The rest of a turn re-attached after a restart, from the approval decision on."""
        from harnesswalk.nodes import fail_span
        from harnesswalk.types import ToolCall

        ctx, h = state.ctx, state.view or self.h
        notice = h.loop.notices.get("error", "Something went wrong.")
        try:
            loop_span, tools_span, approval_span = ctx.emit.stack
            call = ToolCall(**checkpoint["pending_call"])
            steps = int(checkpoint.get("step", 0))
            try:
                try:
                    decided = await h.approvals.resume_wait(ctx, approval_span, record)
                except BaseException as exc:
                    fail_span(ctx, approval_span, exc)
                    raise
                ctx.emit.exit(approval_span)
                try:
                    observation = await h.tools.finish_after_approval(
                        ctx, tools_span, call, decided
                    )
                except BaseException as exc:
                    fail_span(ctx, tools_span, exc)
                    raise
                ctx.emit.edge("tools_to_loop")
                limits = Limits.from_config(h.settings.get("loop", {}), ctx.budget)
                result = await h.loop.resume(
                    ctx, loop_span, checkpoint, observation, h.tools.schemas(ctx.mode), limits
                )
                answer, steps = result.answer.text, result.steps
                state.proposals = list(result.proposals)
                state.observations = list(result.observations)
                exit_ = EXIT_FOR_REASON[result.exit_reason]
            except Exception:
                if ctx.emit.stack:
                    raise
                answer, exit_ = notice, "error"
            await self._check_and_reply(state, answer, exit_, steps, user_text, list(ctx.citations))
        except asyncio.CancelledError:
            state.exit = "cancelled"
            self._close_after_failure(state, "cancelled", "The turn was cancelled.")
        except Exception as exc:
            state.exit = "error"
            self._close_after_failure(state, "internal", f"{type(exc).__name__}: {exc}")
        finally:
            self._finish(state)

    def _close_orphan(self, events: list[dict[str, Any]]) -> None:
        """A turn the restart cut off: close its spans, end it, record it, expire approvals."""
        ctx = self._restored_ctx(events)
        emit = ctx.emit
        message = "The harness restarted while this turn was running."
        for span in reversed(list(emit.stack)):
            emit.fail(span, "restarted", message)
        emit.turn_error("restarted", message)
        cost = Usage().cost()
        with contextlib.suppress(Exception):
            cost = self.h.tracer.receipt(ctx.tenant_id, ctx.turn_id)["total"]
        if self.h.session.turn_at(ctx.tenant_id, ctx.session_id, ctx.turn_index) is None:
            with contextlib.suppress(Exception):  # keeps turn_index unique after the restart
                self.h.session.append_turn(ctx, "gateway", None, "", "error")
        for record in self.h.approvals.for_turn(ctx.tenant_id, ctx.turn_id):
            if record["status"] == "pending":
                with contextlib.suppress(Exception):
                    self.h.approvals.decide(
                        record["approval_id"], ctx.tenant_id, "system", "expired", "restarted"
                    )
        emit.turn_end("error", {"exit": "error", "reply": ""}, cost=cost)

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
        if emit.ended or ctx.detached:  # a detached turn is left for recovery to finish
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
        """Stop every turn task. A turn waiting on an approval is detached, not cancelled: its
        stream stays as stored and its record stays pending, so the next boot resumes it."""
        waiting = self.h.approvals.waiting_turns()
        for state in list(self.turns.values()):  # a finishing turn may evict others
            if state.task and not state.task.done():
                if state.ctx.turn_id in waiting:
                    state.ctx.detached = True
                    state.ctx.emit.detached = True
                state.task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await state.task

    def describe(self) -> dict[str, Any]:
        return {
            "running": len(self._open_sessions),
            "turns": len(self.turns),
            "finished_kept": len(self._finished),
        }
