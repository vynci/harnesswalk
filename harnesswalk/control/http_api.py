"""The gateway's HTTP surface (spec/contracts/http_api.md), as a FastAPI app.

Routes are thin: they resolve identity, parse JSON by hand (so every error has the one shape
`{"error": {"code", "message"}}`), and call the gateway, the tracer or the inspectors.

Identity, P1:

- `dev`: tenant `dev`, or the `X-Harnesswalk-Tenant` header.
- `public`: an anonymous tenant per browser, carried in a signed HTTP-only cookie.
- `private`: sign-in arrives in P6, so every request is `401` for now.

The session comes from the body's `session_id`, else the `X-Harnesswalk-Session` header, else
the `harnesswalk_session` cookie, else a new one.
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
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from harnesswalk import SPEC_VERSION, __version__, diagram
from harnesswalk.control.approval import ApprovalAlreadyDecided, ApprovalNotFound
from harnesswalk.control.gateway import GatewayError, TurnRequest
from harnesswalk.inspectors import inspect_node
from harnesswalk.nodes import generated_diagram
from harnesswalk.runtime import Harness
from harnesswalk.types import Identity

TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SESSION_COOKIE = "harnesswalk_session"
TENANT_COOKIE = "harnesswalk_tenant"


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


def create_app(harness: Harness) -> FastAPI:
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

    async def read_json(request: Request) -> dict[str, Any]:
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
        disabled = options.get("disabled_nodes") or []
        if not isinstance(disabled, list):
            raise GatewayError(400, "invalid_request", "options.disabled_nodes must be a list")
        state = await gateway.start_turn(
            TurnRequest(
                message=data.get("message"),  # type: ignore[arg-type]
                identity=identity,
                session_id=session_id,
                step_mode=bool(options.get("step_mode", False)),
                dataset=options.get("dataset"),
                disabled_nodes=disabled,
                api_key=request.headers.get("x-harnesswalk-provider-key"),
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

    @app.get("/v1/turns/{turn_id}/events")
    async def turn_events(request: Request, turn_id: str, after_seq: int | None = None) -> Response:
        identity, _ = identities.resolve(request)
        tenant = identity.tenant_id
        if gateway.get(tenant, turn_id) is None and not harness.tracer.turn_exists(tenant, turn_id):
            raise GatewayError(404, "turn_not_found", f"No turn {turn_id} for this tenant.")
        last = request.headers.get("last-event-id")
        start = (
            after_seq if after_seq is not None else (int(last) if last and last.isdigit() else -1)
        )

        async def stream():
            async for event in harness.tracer.subscribe(tenant, turn_id, start):
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

    return app
