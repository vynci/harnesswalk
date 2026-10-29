"""The gateway's HTTP surface (spec/contracts/http_api.md), as a FastAPI app.

Routes are thin: they resolve identity, parse JSON by hand (so every error has the one shape
`{"error": {"code", "message"}}`), and call the gateway, the tracer or the inspectors.

Identity, P1:

- `dev`: tenant `dev`, or the `X-Harnesswalk-Tenant` header.
- `public`: an anonymous tenant per browser, carried in a signed HTTP-only cookie.
- `private`: sign-in arrives in P6, so every request is `401` for now.

The session comes from the body's `session_id`, else the `X-Harnesswalk-Session` header, else
the `harnesswalk_session` cookie, else a new one.

P2 adds cancel, turn summaries, the session turn list, cursor replay with `follow`, the code
peek, capabilities and per-turn options, and serves the built web app (`web/dist`) with a
fallback to `index.html`. In dev mode it answers CORS for the Vite dev server.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from harnesswalk import SPEC_VERSION, __version__, diagram
from harnesswalk.control.approval import ApprovalAlreadyDecided, ApprovalNotFound
from harnesswalk.control.gateway import GatewayError, TurnRequest
from harnesswalk.control.http_memory import add_memory_routes
from harnesswalk.inspectors import inspect_node, node_source
from harnesswalk.nodes import generated_diagram
from harnesswalk.runtime import Harness
from harnesswalk.turns import MAX_SESSION_TURNS, session_turns, turn_summary
from harnesswalk.types import Identity

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
]
# Paths the web app's fallback never answers: the API, health and the OpenAPI docs, and the
# built assets (a missing asset is a 404, not index.html served as JavaScript).
API_PREFIXES = ("v1/", "healthz/", "docs/", "openapi.json/", "assets/")
API_PATHS = frozenset({"v1", "healthz", "docs", "openapi.json"})
ANY_METHOD = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def error(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status, headers=headers)


class Identities:
    """Resolve who is asking. The only place that knows about cookies and headers."""

    def __init__(self, harness: Harness) -> None:
        self.mode = harness.settings.mode
        self.cfg = harness.settings.get("gateway", {})
        secret = os.environ.get("HARNESSWALK_SECRET") or secrets.token_hex(32)
        self._key = secret.encode()

    def _sign(self, value: str) -> str:
        return hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()[:32]

    def resolve(self, request: Request) -> tuple[Identity, str | None]:
        """The identity, and a tenant cookie value to set when a new anonymous tenant is minted."""
        if self.mode == "dev":
            tenant = self.cfg.get("dev_tenant", "dev")
            header = request.headers.get("x-harnesswalk-tenant")
            if header and self.cfg.get("allow_tenant_header", True):
                if not TOKEN.match(header):
                    raise GatewayError(400, "invalid_request", "bad X-Harnesswalk-Tenant")
                tenant = header
            return Identity(tenant, None, ["user"], anonymous=False), None
        if self.mode == "public":
            raw = request.cookies.get(TENANT_COOKIE, "")
            tenant, _, sig = raw.partition(".")
            if tenant and hmac.compare_digest(sig, self._sign(tenant)):
                return Identity(tenant, None, ["user"], anonymous=True), None
            tenant = "anon-" + secrets.token_hex(8)
            return Identity(
                tenant, None, ["user"], anonymous=True
            ), f"{tenant}.{self._sign(tenant)}"
        raise GatewayError(
            401, "unauthenticated", "Sign-in arrives in P6; private mode has no identity yet."
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
    return {
        "step_mode": bool(step) or bool(legacy_step),
        "provider": _name(pick("provider"), "provider"),
        "fixture": _name(pick("fixture"), "fixture"),
        "disabled_nodes": disabled,
    }


def create_app(harness: Harness, web_dist: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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
            allow_methods=["GET", "POST"],
            allow_headers=CORS_HEADERS,
        )
    identities = Identities(harness)
    gateway = harness.gateway
    max_body = int(harness.settings.get("gateway.max_body_bytes", 65536))

    @app.exception_handler(GatewayError)
    async def _gateway_error(_: Request, exc: GatewayError) -> JSONResponse:
        headers = {"Retry-After": "3600"} if exc.status == 429 else None
        return error(exc.status, exc.code, exc.message, headers)

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
        state = await gateway.start_turn(
            TurnRequest(
                message=data.get("message"),  # type: ignore[arg-type]
                identity=identity,
                session_id=session_id,
                step_mode=chosen["step_mode"],
                dataset=options.get("dataset"),
                disabled_nodes=chosen["disabled_nodes"],
                api_key=request.headers.get("x-harnesswalk-provider-key"),
                provider=chosen["provider"],
                fixture=chosen["fixture"],
            ),
            client_ip=request.client.host if request.client else "",
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
        response.set_cookie(SESSION_COOKIE, ctx.session_id, httponly=True, samesite="lax")
        if tenant_cookie:
            response.set_cookie(TENANT_COOKIE, tenant_cookie, httponly=True, samesite="lax")
        return response

    def not_found(turn_id: str) -> GatewayError:
        return GatewayError(404, "turn_not_found", f"No turn {turn_id} for this tenant.")

    @app.get("/v1/turns/{turn_id}")
    async def get_turn(request: Request, turn_id: str) -> JSONResponse:
        identity, _ = identities.resolve(request)
        summary = turn_summary(harness.tracer, harness.session, identity.tenant_id, turn_id)
        if summary is None:
            raise not_found(turn_id)
        return JSONResponse(summary)

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
        return JSONResponse(turns)

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
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/turns/{turn_id}/resume")
    async def resume(request: Request, turn_id: str) -> JSONResponse:
        identity, _ = identities.resolve(request)
        data = await read_json(request)
        mode = data.get("mode", "step")
        if mode not in ("step", "continue"):
            raise GatewayError(400, "invalid_request", "mode must be step or continue")
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

    @app.get("/v1/diagram")
    async def get_diagram(request: Request, view: str | None = None) -> JSONResponse:
        if view not in (None, "simple", "expert"):
            raise GatewayError(400, "invalid_request", "view must be simple or expert")
        return JSONResponse(generated_diagram(view))

    @app.get("/v1/nodes/{node_id}")
    async def get_node(request: Request, node_id: str, recent: int = 5) -> JSONResponse:
        identity, _ = identities.resolve(request)
        if node_id not in diagram.nodes_by_id():
            raise GatewayError(404, "node_not_found", f"No node {node_id}.")
        return JSONResponse(
            inspect_node(harness, identity.tenant_id, node_id, max(0, min(recent, 50)))
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
    async def capabilities() -> JSONResponse:
        # No identity needed: a signed-out UI reads this to learn the mode and how to sign in.
        return JSONResponse(harness.capabilities())

    add_memory_routes(app, harness, identities, read_json)

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
