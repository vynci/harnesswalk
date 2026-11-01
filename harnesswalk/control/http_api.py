"""The gateway's HTTP surface (spec/contracts/http_api.md), as a FastAPI app.

Routes are thin: they resolve identity, parse JSON by hand (so every error has the one shape
`{"error": {"code", "message"}}`), and call the gateway, the tracer or the inspectors.

Identity, P1:

- `dev`: tenant `dev`, or the `X-Harnesswalk-Tenant` header.
- `public`: an anonymous tenant per browser, carried in a signed HTTP-only cookie.
- `private`: sign-in is deferred (P6 shipped public mode only), so every request is `401`.

P6 (public mode, control/public.py): the tenant cookie carries its expiry and is signed with
`HARNESSWALK_SECRET` (the app refuses to start in public mode without it); a tampered or
expired cookie mints a fresh tenant, set on whatever response minted it. `GET /v1/capabilities`
gains the `public` object (every mode), `GET /readyz` reports readiness, `GET /v1/ops/budget`
shows today's hosted spend (dev only), SSE streams are bounded (`gateway.max_open_streams`),
and with `logging.format: json` every request is one JSON log line (ops/logs.py).

The session comes from the body's `session_id`, else the `X-Harnesswalk-Session` header, else
the `harnesswalk_session` cookie, else a new one.

P2 adds cancel, turn summaries, the session turn list, cursor replay with `follow`, the code
peek, capabilities and per-turn options, and serves the built web app (`web/dist`) with a
fallback to `index.html`. In dev mode it answers CORS for the Vite dev server. P4 adds the LLM
Ops routes (http_ops.py) and `step_over` on resume.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from harnesswalk import SPEC_VERSION, __version__, diagram
from harnesswalk.control.approval import ApprovalAlreadyDecided, ApprovalNotFound
from harnesswalk.control.gateway import GatewayError, TurnRequest
from harnesswalk.control.http_lessons import add_lesson_routes
from harnesswalk.control.http_memory import add_memory_routes
from harnesswalk.control.http_ops import add_ops_routes, turn_extras
from harnesswalk.control.public import (
    StreamRefused,
    StreamSlots,
    TenantCookies,
    client_ip,
    rate_key,
    request_is_https,
)
from harnesswalk.events import iso, parse_iso
from harnesswalk.inspectors import inspect_node, node_source
from harnesswalk.nodes import generated_diagram
from harnesswalk.ops import logs
from harnesswalk.runtime import Harness
from harnesswalk.turns import MAX_SESSION_TURNS, session_turns, turn_summary
from harnesswalk.types import ConfigError, Identity

TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
CURSOR = re.compile(r"-?\d{1,18}")  # an event seq: ?after=, ?after_seq= and Last-Event-ID
SESSION_COOKIE = "harnesswalk_session"
TENANT_COOKIE = "harnesswalk_tenant"
DEV_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]  # the Vite dev server
CORS_HEADERS = [
    "Content-Type",
    "Last-Event-ID",
    "X-Harnesswalk-Tenant",
    "X-Harnesswalk-Session",
    "X-Harnesswalk-Provider-Key",
    "X-Harnesswalk-Api-Key",
]
# The BYO key header (P3), and the spelling the P6 contract names; the first one present wins.
BYO_KEY_HEADERS = ("x-harnesswalk-provider-key", "x-harnesswalk-api-key")
# Loop limits a turn might try to set. Never per turn; public mode says so with a 400 (P6).
LOOP_LIMIT_KEYS = ("max_steps", "budget_tokens", "budget_usd", "timeout_s", "stuck_repeats")
# Paths the web app's fallback never answers: the API, health and the OpenAPI docs, and the
# built assets (a missing asset is a 404, not index.html served as JavaScript).
EVAL_TENANT_PREFIX = "evalrun-"  # ops/eval/offline.py scratch_tenant()
API_PREFIXES = ("v1/", "healthz/", "readyz/", "docs/", "openapi.json/", "assets/")
API_PATHS = frozenset({"v1", "healthz", "readyz", "docs", "openapi.json"})
ANY_METHOD = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def error(
    status: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body = {"error": {"code": code, "message": message, **(extra or {})}}
    return JSONResponse(body, status, headers=headers)


class Identities:
    """Resolve who is asking. The only place that knows about cookies and headers."""

    def __init__(self, harness: Harness) -> None:
        settings = harness.settings
        self.harness = harness
        self.mode = settings.mode
        self.cfg = settings.get("gateway", {})
        self.trust_proxy = bool(self.cfg.get("trust_proxy", False))
        self.cookie_secure = settings.get("public.cookie_secure", "auto")
        if self.mode == "public" and not settings.secret:
            raise ConfigError(
                "public mode needs HARNESSWALK_SECRET: it signs the anonymous tenant cookie"
            )
        ttl = float(settings.get("public.session_ttl_s", 7200))
        # Outside public mode no cookie is ever minted, so a throwaway secret is enough.
        self.cookies = TenantCookies(settings.secret or os.urandom(32).hex(), ttl)

    def client_ip(self, request: Request) -> str:
        peer = request.client.host if request.client else ""
        return client_ip(request.headers, peer, self.trust_proxy)

    def count_creation(self, request: Request) -> None:
        """Public mode: one new tenant or session for this client's hourly creation bucket
        (`public.creations_per_ip_per_hour`); a 429 with Retry-After when it is full."""
        if self.mode != "public":
            return
        per_hour = int(self.harness.settings.get("public.creations_per_ip_per_hour", 60))
        key = f"create:{rate_key(self.client_ip(request))}"
        self.harness.gateway._check_rate(key, per_hour)

    def secure(self, request: Request) -> bool:
        """Whether cookies get `Secure`: always, never, or when the request came over HTTPS."""
        if self.cookie_secure in ("always", "never"):
            return self.cookie_secure == "always"
        return request_is_https(request.headers, request.url.scheme, self.trust_proxy)

    def resolve(self, request: Request) -> tuple[Identity, str | None]:
        """The identity, and a tenant cookie value to set when a new anonymous tenant is minted.

        Resolved once per request (a second call returns the same tenant), and a minted cookie
        is set on the response by the middleware, whichever route minted it."""
        cached = getattr(request.state, "identity", None)
        if cached is not None:
            return cached
        resolved = self._resolve(request)
        request.state.identity = resolved
        if resolved[1]:
            request.state.tenant_cookie = resolved[1]
        return resolved

    def _resolve(self, request: Request) -> tuple[Identity, str | None]:
        if self.mode == "dev":
            tenant = self.cfg.get("dev_tenant", "dev")
            header = request.headers.get("x-harnesswalk-tenant")
            if header and self.cfg.get("allow_tenant_header", True):
                if not TOKEN.match(header):
                    raise GatewayError(400, "invalid_request", "bad X-Harnesswalk-Tenant")
                if header.startswith(EVAL_TENANT_PREFIX):
                    # An eval run's scratch tenant: a caller could read a run's case turns or
                    # seed memory into a future run's starting state (P4 integration review).
                    raise GatewayError(400, "invalid_request", "that tenant id is reserved")
                tenant = header
            return Identity(tenant, None, ["user"], anonymous=False), None
        if self.mode == "public":
            checked = self.cookies.check(request.cookies.get(TENANT_COOKIE))
            if checked is not None:
                tenant, expires = checked
                return Identity(tenant, None, ["user"], anonymous=True, expires_at=expires), None
            self.count_creation(request)  # P6 review: minting tenants is rate limited per client
            tenant, expires, value = self.cookies.mint()
            return Identity(tenant, None, ["user"], anonymous=True, expires_at=expires), value
        raise GatewayError(
            401, "unauthenticated", "Sign-in is not offered yet; private mode has no identity."
        )


class WebApp(StaticFiles):
    """The built web app, with a fallback to `index.html` so client-side routes reload."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            api = path in API_PATHS or path.startswith(API_PREFIXES)
            if exc.status_code != 404 or api:
                raise
            return await super().get_response("index.html", scope)


class EdgeMiddleware:
    """Plain ASGI middleware (P6): sets a freshly minted tenant cookie on whatever response
    minted it, adds `X-Request-Id`, and with JSON logs writes one line per request."""

    def __init__(self, app: ASGIApp, identities: Identities, json_logs: bool) -> None:
        self.app = app
        self.identities = identities
        self.json_logs = json_logs

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        request = Request(scope)
        request_id = logs.request_id(request.headers.get("x-request-id"))
        status = {"code": 500}

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-Id"] = request_id
                state = scope.get("state") or {}
                minted = state.get("tenant_cookie")
                if minted:
                    expires = state["identity"][0].expires_at or time.time()
                    cookie = SimpleCookie()
                    cookie[TENANT_COOKIE] = minted
                    morsel = cookie[TENANT_COOKIE]
                    morsel["max-age"] = str(max(0, int(expires - time.time())))
                    morsel["path"] = "/"
                    morsel["httponly"] = True
                    morsel["samesite"] = "lax"
                    if self.identities.secure(request):
                        morsel["secure"] = True
                    headers.append("set-cookie", cookie.output(header="").strip())
            await send(message)

        try:
            await self.app(scope, receive, wrapped)
        finally:
            if self.json_logs:
                state = scope.get("state") or {}
                identity = state.get("identity")
                route = scope.get("route")
                logs.request_line(
                    request_id=request_id,
                    method=scope.get("method", ""),
                    route=getattr(route, "path", None) or "unmatched",
                    status=status["code"],
                    duration_ms=(time.monotonic() - started) * 1000,
                    tenant=identity[0].tenant_id if identity else None,
                )


def web_dist_dir(harness: Harness) -> Path | None:
    """`HARNESSWALK_WEB_DIST` (default `web/dist`, relative to the repo root) when it is built."""
    raw = os.environ.get("HARNESSWALK_WEB_DIST") or "web/dist"
    path = Path(raw) if Path(raw).is_absolute() else harness.settings.root / raw
    return path if (path / "index.html").is_file() else None


def _flag(value: Any, name: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise GatewayError(400, "invalid_request", f"{name} must be a boolean")


def _name(value: Any, name: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise GatewayError(400, "invalid_request", f"{name} must be a string")


def _names(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise GatewayError(400, "invalid_request", f"{name} must be a list of node ids")
    return value


def turn_options(data: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """Per-turn options, top level first, then `options`, then the P1 spellings."""

    def pick(key: str) -> Any:
        return data[key] if key in data else options.get(key)

    step = _flag(pick("step"), "step")
    legacy_step = _flag(options.get("step_mode"), "options.step_mode")
    disabled: list[str] = []
    for node_id in _names(pick("disable"), "disable") + _names(
        options.get("disabled_nodes"), "options.disabled_nodes"
    ):
        if node_id not in disabled:
            disabled.append(node_id)
    schema = pick("output_schema")
    if schema is not None and not isinstance(schema, dict):
        raise GatewayError(400, "invalid_request", "output_schema must be a JSON Schema object")
    return {
        "step_mode": bool(step) or bool(legacy_step),
        "provider": _name(pick("provider"), "provider"),
        "fixture": _name(pick("fixture"), "fixture"),
        "model": _name(pick("model"), "model"),
        "output_schema": schema,
        "disabled_nodes": disabled,
    }


def create_app(harness: Harness, web_dist: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Recovery first (approval_queue.md, P3): paused turns re-attach, cut-off turns close.
        recovered = await harness.start()
        if recovered["resumed"] or recovered["closed"]:
            logging.getLogger("harnesswalk").warning(
                "recovered after a restart: %d turn(s) waiting on an approval re-attached,"
                " %d cut-off turn(s) closed",
                len(recovered["resumed"]),
                len(recovered["closed"]),
            )
        yield
        await harness.aclose()

    app = FastAPI(
        title="harnesswalk",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.harness = harness
    if harness.settings.mode == "dev":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=DEV_ORIGINS,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT"],
            allow_headers=CORS_HEADERS,
        )
    identities = Identities(harness)
    app.state.identities = identities
    gateway = harness.gateway
    max_body = int(harness.settings.get("gateway.max_body_bytes", 65536))
    public_mode = harness.settings.mode == "public"
    pub = harness.settings.get("public", {}) or {}
    streams = StreamSlots(
        int(harness.settings.get("gateway.max_open_streams", 50)),
        # P6 review: in public mode one client, one tenant or the ops streams cannot take them all.
        per_ip=int(pub.get("max_streams_per_ip", 4)) if public_mode else 0,
        per_tenant=int(pub.get("max_streams_per_tenant", 4)) if public_mode else 0,
        ops_cap=int(pub.get("max_ops_streams", 20)) if public_mode else 0,
    )
    # A stream older than this closes (the browser's EventSource reconnects from its cursor).
    max_stream_s = float(pub.get("max_stream_s", 600) or 0) if public_mode else 0.0
    app.state.streams = streams
    json_logs = harness.settings.get("logging.format", "text") == "json"
    if json_logs:
        logs.configure_json()

    @app.exception_handler(GatewayError)
    async def _gateway_error(_: Request, exc: GatewayError) -> JSONResponse:
        headers = dict(exc.headers)
        if exc.status == 429 and "Retry-After" not in headers:
            headers["Retry-After"] = str(exc.extra.get("retry_after", 3600))
        return error(exc.status, exc.code, exc.message, headers or None, exc.extra)

    app.add_middleware(EdgeMiddleware, identities=identities, json_logs=json_logs)

    def acquire_stream(request: Request, tenant: str = "", pool: str = "turn") -> int:
        """A stream slot for this client and tenant, or 429 naming the cap that was full."""
        try:
            return streams.acquire(rate_key(identities.client_ip(request)), tenant, pool)
        except StreamRefused as exc:
            who = {
                "client": "from this address",
                "tenant": "for this session",
                "ops": "for live updates",
            }.get(exc.scope, "on the server")
            raise GatewayError(
                429,
                "rate_limited",
                f"Too many event streams are open {who}. Close one and try again.",
                extra={"retry_after": 5, "limit": "streams"},
                headers={"Retry-After": "5"},
            ) from exc

    def bounded(token: int, source: AsyncIterator[str]) -> AsyncIterator[str]:
        """An SSE body that gives its stream slot back however it ends, and in public mode ends
        after `public.max_stream_s` (checked on every event and keepalive)."""

        async def body() -> AsyncIterator[str]:
            streams.started(token)
            deadline = time.monotonic() + max_stream_s if max_stream_s > 0 else None
            try:
                async for chunk in source:
                    yield chunk
                    if deadline is not None and time.monotonic() >= deadline:
                        break
            finally:
                streams.release(token)
                if hasattr(source, "aclose"):
                    with contextlib.suppress(Exception):
                        await source.aclose()  # type: ignore[attr-defined]

        return body()

    app.state.acquire_stream = acquire_stream
    app.state.bounded = bounded

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "invalid_request")
        return error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        where = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
        return error(400, "invalid_request", f"invalid parameters: {where}")

    async def read_json(request: Request) -> dict[str, Any]:
        # Only `application/json`: a `text/plain` or form body is a "simple" cross-site request
        # that a browser sends without a CORS preflight, so it could start turns from any page.
        media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type != "application/json":
            raise GatewayError(
                415, "unsupported_media_type", "the request body must be application/json"
            )
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_body:
            raise GatewayError(400, "invalid_request", f"request body is over {max_body} bytes")
        body = await request.body()
        if len(body) > max_body:
            raise GatewayError(400, "invalid_request", f"request body is over {max_body} bytes")
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError as exc:
            raise GatewayError(400, "invalid_request", "request body is not valid JSON") from exc
        if not isinstance(data, dict):
            raise GatewayError(400, "invalid_request", "request body must be a JSON object")
        return data

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        health = harness.health()
        body = {"status": health["status"], "version": __version__, "spec_version": SPEC_VERSION}
        body.update({k: v for k, v in health.items() if k != "status"})
        return JSONResponse(body, 200 if health["status"] == "ok" else 503)

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        # P6: ready to take turns: every store opens, the default model's provider is healthy,
        # and in public mode the cookie secret is there. Unauthenticated and cheap.
        ready = harness.readiness()
        return JSONResponse(ready, 200 if ready["status"] == "ready" else 503)

    @app.post("/v1/turns", status_code=202)
    async def post_turn(request: Request) -> JSONResponse:
        identity, tenant_cookie = identities.resolve(request)
        data = await read_json(request)
        options = data.get("options") or {}
        if not isinstance(options, dict):
            raise GatewayError(400, "invalid_request", "options must be an object")
        session_id = (
            data.get("session_id")
            or request.headers.get("x-harnesswalk-session")
            or request.cookies.get(SESSION_COOKIE)
        )
        if session_id is not None and (
            not isinstance(session_id, str) or not TOKEN.match(session_id)
        ):
            raise GatewayError(400, "invalid_request", "session_id is not a valid id")
        chosen = turn_options(data, options)
        if harness.settings.mode == "public":
            fixed = [k for k in LOOP_LIMIT_KEYS if k in data or k in options]
            if fixed:
                raise GatewayError(
                    400, "invalid_request", f"{fixed[0]} cannot be changed in public mode"
                )
            if tenant_cookie and "session_id" not in data:
                # A fresh tenant (the old one expired): its old session cookie is not its own.
                session_id = request.headers.get("x-harnesswalk-session")
        state = await gateway.start_turn(
            TurnRequest(
                message=data.get("message"),  # type: ignore[arg-type]
                identity=identity,
                session_id=session_id,
                step_mode=chosen["step_mode"],
                dataset=options.get("pack", options.get("dataset")),  # P5 spelling first
                disabled_nodes=chosen["disabled_nodes"],
                api_key=next(
                    (request.headers[h] for h in BYO_KEY_HEADERS if request.headers.get(h)), None
                ),
                provider=chosen["provider"],
                fixture=chosen["fixture"],
                model=chosen["model"],
                output_schema=chosen["output_schema"],
            ),
            client_ip=identities.client_ip(request),
        )
        ctx = state.ctx
        response = JSONResponse(
            {
                "turn_id": ctx.turn_id,
                "trace_id": ctx.trace_id,
                "session_id": ctx.session_id,
                "events_url": f"/v1/turns/{ctx.turn_id}/events",
            },
            202,
        )
        response.set_cookie(
            SESSION_COOKIE,
            ctx.session_id,
            httponly=True,
            samesite="lax",
            secure=identities.secure(request),
        )
        return response  # a minted tenant cookie is set by the middleware

    def not_found(turn_id: str) -> GatewayError:
        return GatewayError(404, "turn_not_found", f"No turn {turn_id} for this tenant.")

    @app.get("/v1/turns/{turn_id}")
    async def get_turn(request: Request, turn_id: str) -> JSONResponse:
        identity, _ = identities.resolve(request)
        summary = turn_summary(harness.tracer, harness.session, identity.tenant_id, turn_id)
        if summary is None:
            raise not_found(turn_id)
        return JSONResponse(turn_extras(harness, identity.tenant_id, summary))

    @app.post("/v1/turns/{turn_id}/cancel", status_code=202)
    async def cancel(request: Request, turn_id: str) -> JSONResponse:
        identity, _ = identities.resolve(request)
        # Like every POST: a page on another origin cannot send this without a preflight, so it
        # cannot stop a learner's turn. The body may be empty or `{}`.
        await read_json(request)
        tenant = identity.tenant_id
        running = gateway.get(tenant, turn_id)
        if running is None or running.done.is_set():
            summary = turn_summary(harness.tracer, harness.session, tenant, turn_id)
            if summary is None:
                raise not_found(turn_id)
            code, message = (
                ("turn_finished", f"Turn {turn_id} has already ended.")
                if summary["status"] == "done"
                else ("turn_not_running", f"Turn {turn_id} is not running in this process.")
            )
            return JSONResponse({"error": {"code": code, "message": message}, "turn": summary}, 409)
        gateway.cancel(tenant, turn_id)
        return JSONResponse({"turn_id": turn_id, "cancel_requested": True}, 202)

    @app.get("/v1/sessions/{session_id}/turns")
    async def list_session_turns(request: Request, session_id: str, limit: int = 50) -> Response:
        identity, _ = identities.resolve(request)
        if not 1 <= limit <= MAX_SESSION_TURNS:
            raise GatewayError(
                400, "invalid_request", f"limit must be between 1 and {MAX_SESSION_TURNS}"
            )
        turns = session_turns(
            harness.tracer, harness.session, identity.tenant_id, session_id, limit
        )
        if turns is None:
            raise GatewayError(
                404, "session_not_found", f"No session {session_id} for this tenant."
            )
        return JSONResponse([turn_extras(harness, identity.tenant_id, t) for t in turns])

    @app.get("/v1/turns/{turn_id}/events")
    async def turn_events(
        request: Request,
        turn_id: str,
        after: str | None = None,
        after_seq: str | None = None,
        follow: bool = True,
    ) -> Response:
        identity, _ = identities.resolve(request)
        tenant = identity.tenant_id
        # Every cursor follows one rule, at most 18 digits, so it always fits SQLite's integer.
        cursors: list[int] = []
        for name, raw in (
            ("after", after),
            ("after_seq", after_seq),
            ("Last-Event-ID", request.headers.get("last-event-id")),
        ):
            raw = (raw or "").strip()
            if not raw:
                continue
            if not CURSOR.fullmatch(raw):
                raise GatewayError(400, "invalid_request", f"{name} must be an integer seq")
            cursors.append(int(raw))
        running = gateway.get(tenant, turn_id)
        if running is None and not harness.tracer.turn_exists(tenant, turn_id):
            raise not_found(turn_id)
        # The largest cursor wins: a browser reconnecting to the same `?after=` URL sends
        # Last-Event-ID too, and must not get the events between the two again.
        start = max([-1, *cursors])
        token = acquire_stream(request, tenant)

        async def stored():
            # No live task (a finished turn, one cut off by a restart, or follow=false).
            for event in harness.tracer.get_turn(tenant, turn_id, start):
                yield event

        async def stream():
            live = follow and running is not None and not running.done.is_set()
            source = harness.tracer.subscribe(tenant, turn_id, start) if live else stored()
            async for event in source:
                if event is None:
                    yield ": keepalive\n\n"
                    continue
                data = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
                yield f"id: {event['seq']}\nevent: {event['type']}\ndata: {data}\n\n"

        return StreamingResponse(
            bounded(token, stream()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/turns/{turn_id}/resume")
    async def resume(request: Request, turn_id: str) -> JSONResponse:
        identity, _ = identities.resolve(request)
        data = await read_json(request)
        mode = data.get("mode", "step")
        if mode not in ("step", "continue", "step_over"):
            raise GatewayError(400, "invalid_request", "mode must be step, continue or step_over")
        node_id = gateway.resume(identity.tenant_id, turn_id, mode)
        return JSONResponse({"turn_id": turn_id, "resumed_node_id": node_id, "mode": mode})

    @app.post("/v1/approvals/{approval_id}")
    async def decide(request: Request, approval_id: str) -> JSONResponse:
        identity, _ = identities.resolve(request)
        data = await read_json(request)
        decision = data.get("decision")
        if decision not in ("approve", "deny"):
            raise GatewayError(400, "invalid_request", "decision must be approve or deny")
        reason = data.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise GatewayError(400, "invalid_request", "reason must be a string")
        decider = f"user:{identity.user_id or identity.tenant_id}"
        try:
            record = harness.approvals.decide(
                approval_id, identity.tenant_id, decider, decision, reason
            )
        except ApprovalNotFound as exc:
            raise GatewayError(
                404, "approval_not_found", f"No approval {approval_id} for this tenant."
            ) from exc
        except ApprovalAlreadyDecided as exc:
            raise GatewayError(
                409, "approval_already_decided", f"Approval {approval_id} is decided."
            ) from exc
        return JSONResponse(record)

    @app.get("/v1/audit")
    async def audit_export(
        request: Request, since: str | None = None, limit: int = 500, after: str | None = None
    ) -> JSONResponse:
        identity, _ = identities.resolve(request)
        if harness.settings.mode == "public":
            raise GatewayError(403, "forbidden_in_mode", "The audit export is not offered here.")
        ceiling = int(harness.settings.get("audit.export_max", 5000))
        if not 1 <= limit <= ceiling:
            raise GatewayError(400, "invalid_request", f"limit must be between 1 and {ceiling}")
        if since is not None:
            # An offset is required: a bare local time would be read in the server's zone.
            if not re.search(r"(Z|[+-]\d{2}:\d{2})$", since.strip()):
                raise GatewayError(
                    400, "invalid_request", "since must be an RFC 3339 timestamp with an offset"
                )
            try:
                parse_iso(since)
            except ValueError as exc:
                raise GatewayError(
                    400, "invalid_request", "since must be an RFC 3339 timestamp"
                ) from exc
            since = iso(parse_iso(since))  # the stored form, so the comparison is exact
        tenant = identity.tenant_id
        try:
            records = harness.audit.export(tenant, since, limit, after)
        except KeyError as exc:
            raise GatewayError(400, "invalid_request", "after names no record of yours") from exc
        full = len(records) == limit
        return JSONResponse(
            {
                "tenant_id": tenant,
                "verify": harness.audit.verify(tenant),
                "records": [r.__dict__ for r in records],
                # Follow `next_after` to page: timestamps can repeat at millisecond precision.
                "next_after": records[-1].record_id if full else None,
                "next_since": records[-1].ts if full else None,
            }
        )

    @app.get("/v1/diagram")
    async def get_diagram(request: Request, view: str | None = None) -> JSONResponse:
        if view not in (None, "simple", "expert"):
            raise GatewayError(400, "invalid_request", "view must be simple or expert")
        return JSONResponse(generated_diagram(view))

    @app.get("/v1/nodes/{node_id}")
    async def get_node(
        request: Request, node_id: str, recent: int = 5, pack: str | None = None
    ) -> JSONResponse:
        identity, _ = identities.resolve(request)
        if node_id not in diagram.nodes_by_id():
            raise GatewayError(404, "node_not_found", f"No node {node_id}.")
        if pack is not None and not harness.pack_known(pack):
            raise GatewayError(422, "invalid_request", f"no dataset pack {pack!r}")
        try:
            view = harness.view(pack)  # P5: the inspectors of the pack the UI has picked
        except KeyError as exc:  # PackUnavailable: the pack is there but does not load
            raise GatewayError(422, "invalid_request", str(exc.args[0])) from exc
        return JSONResponse(
            inspect_node(view, identity.tenant_id, node_id, max(0, min(recent, 50)))
        )

    @app.get("/v1/nodes/{node_id}/source")
    async def get_node_source(node_id: str) -> JSONResponse:
        # No identity needed: the code is open source and the same for every tenant.
        if node_id not in diagram.nodes_by_id():
            raise GatewayError(404, "node_not_found", f"No node {node_id}.")
        source = node_source(harness, node_id)
        if source is None:
            raise GatewayError(404, "source_not_found", f"Node {node_id} has no source file yet.")
        return JSONResponse(source)

    @app.get("/v1/capabilities")
    async def capabilities(request: Request) -> JSONResponse:
        # No sign-in needed: a signed-out UI reads this to learn the mode. In public mode it
        # resolves (or mints) the anonymous tenant, so the session clock starts here (P6).
        identity = None
        if harness.settings.mode in ("dev", "public"):
            identity = identities.resolve(request)[0]
        return JSONResponse(harness.capabilities(identity, identities.client_ip(request)))

    @app.get("/v1/ops/budget")
    async def ops_budget() -> JSONResponse:
        # P6: today's row of the shared hosted budget, dollars included: dev mode only.
        if harness.settings.mode != "dev":
            raise GatewayError(403, "forbidden_in_mode", "The budget is shown in dev mode only.")
        return JSONResponse(harness.hosted_budget.today())

    add_memory_routes(app, harness, identities, read_json)
    add_ops_routes(app, harness, identities, read_json)
    add_lesson_routes(app, harness)

    async def unknown_api_path(request: Request) -> JSONResponse:
        # Any method on an unknown /v1 path is a JSON 404, never the web app's 405 or HTML. A
        # known path with another method stays a 405.
        for route in app.router.routes:
            if route.matches(request.scope)[0] == Match.PARTIAL:
                return error(405, "method_not_allowed", "Method Not Allowed")
        return error(404, "not_found", "Not Found")

    for path in ("/v1", "/v1/{rest:path}"):
        app.add_api_route(path, unknown_api_path, methods=ANY_METHOD, include_in_schema=False)

    # Last, so every API route above wins over the web app's fallback.
    dist = web_dist if web_dist is not None else web_dist_dir(harness)
    if dist is not None:
        app.mount("/", WebApp(directory=dist, html=True), name="web")

    return app
