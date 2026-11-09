"""HTTP routes of the tuning loop (0.2.0). spec/contracts/http_api.md, "0.2.0 additions: tuning".

    GET  /v1/tune                                   trainers, students, gate numbers, the canary
    GET  /v1/tune/artifacts[?kind=]                 dataset, adapter and model artifacts
    GET  /v1/tune/artifacts/{kind}/{name}[?version] one manifest with its history and diff
    GET  /v1/tune/verify                            the tune artifacts' references
    POST /v1/tune/datasets                          curate (202)
    GET  /v1/tune/curations[/{id}[/events]]         curations, one, its progress (SSE)
    POST /v1/tune/runs                              train (202)
    GET  /v1/tune/runs[/{id}[/events]]              runs, one with its steps, its progress (SSE)
    POST /v1/tune/comparisons                       the model gate: base, candidate, prompt arm
    GET  /v1/tune/comparisons[/{id}]
    POST /v1/tune/promote                           canary or full (router.canary + releases)
    POST /v1/tune/rollback                          canary to 0
    GET  /v1/tune/canary                            the rule, both arms, the canary gate

Every POST is dev and private only (`403 forbidden_in_mode` in public). In public mode the GET
routes answer from the recorded run in `tune.replay_fixture`, marked `replay: true`.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from harnesswalk.control.gateway import GatewayError
from harnesswalk.control.http_ops import SSE_HEADERS, _cursor, sse
from harnesswalk.ops.tune import TuneError

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

KINDS = {"curations": "curation", "runs": "run", "comparisons": "comparison"}
UNTIL = {"curation": "eval_run_finished", "run": "tune_run_finished"}


@cache
def _replay(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def add_tune_routes(
    app: FastAPI,
    h: Harness,
    identities: Any,
    read_json: Callable[[Request], Awaitable[dict[str, Any]]],
) -> None:
    public = h.settings.mode == "public"

    def tenant(request: Request) -> str:
        identity, _ = identities.resolve(request)
        return identity.tenant_id

    def replay() -> dict[str, Any]:
        rel = h.settings.get("tune.replay_fixture", "config/fixtures/tune_replay.json")
        return _replay(str(h.settings.root / rel))

    def forbid_public(what: str) -> None:
        if public:
            raise GatewayError(
                403, "forbidden_in_mode", f"{what} is not offered in public mode: the sandbox"
                " never tunes, it replays a recorded run."
            )  # fmt: skip

    def fail(exc: TuneError) -> GatewayError:
        return GatewayError(exc.status, exc.code, exc.message)

    def refused(exc: TuneError) -> JSONResponse:
        """A refusal with its reasons beside the error, as release's 409 gate_failed does."""
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        body = {"error": {"code": exc.code, "message": exc.message}, **detail}
        return JSONResponse(body, exc.status)

    @app.get("/v1/tune")
    async def overview(request: Request) -> JSONResponse:
        tenant_id = tenant(request)
        if public:
            return JSONResponse({**(replay().get("overview") or {}), "replay": True})
        return JSONResponse(h.tune.overview(tenant_id))

    @app.get("/v1/tune/artifacts")
    async def artifacts(request: Request, kind: str | None = None) -> JSONResponse:
        tenant(request)
        if public:
            items = replay().get("artifacts") or []
            items = [i for i in items if kind is None or i.get("kind") == kind]
            return JSONResponse({"artifacts": items, "replay": True})
        return JSONResponse({"artifacts": h.tune.list_artifacts(kind)})

    @app.get("/v1/tune/artifacts/{kind}/{name}")
    async def artifact(
        request: Request, kind: str, name: str, version: int | None = None
    ) -> JSONResponse:
        tenant(request)
        if public:
            found = (replay().get("artifact_details") or {}).get(f"{kind}/{name}")
            if found is None:
                raise GatewayError(404, "artifact_not_found", f"No {kind}/{name} in the replay.")
            return JSONResponse({**found, "replay": True})
        try:
            return JSONResponse(h.tune.get_artifact(kind, name, version))
        except TuneError as exc:
            raise fail(exc) from exc

    @app.get("/v1/tune/verify")
    async def verify(request: Request) -> JSONResponse:
        tenant(request)
        if public:
            return JSONResponse({**(replay().get("verify") or {"ok": True}), "replay": True})
        return JSONResponse(h.tune.verify())

    async def start(request: Request, what: str, fn: Callable[[str, dict], dict]) -> JSONResponse:
        tenant_id = tenant(request)
        data = await read_json(request)
        forbid_public(what)
        try:
            record = fn(tenant_id, data)
        except TuneError as exc:
            raise fail(exc) from exc
        kind = record["kind"]
        path = {"curation": "curations", "run": "runs", "comparison": "comparisons"}[kind]
        out = {"id": record["id"], "kind": kind, "status": record["status"],
               "url": f"/v1/tune/{path}/{record['id']}"}  # fmt: skip
        if kind in UNTIL:
            out["events_url"] = f"/v1/tune/{path}/{record['id']}/events"
        return JSONResponse(out, 202)

    @app.post("/v1/tune/datasets", status_code=202)
    async def curate(request: Request) -> JSONResponse:
        return await start(request, "Curation", h.tune.start_curation)

    @app.post("/v1/tune/runs", status_code=202)
    async def run(request: Request) -> JSONResponse:
        return await start(request, "Tune runs", h.tune.start_run)

    @app.post("/v1/tune/comparisons", status_code=202)
    async def compare(request: Request) -> JSONResponse:
        return await start(request, "Comparisons", h.tune.start_comparison)

    @app.post("/v1/tune/promote")
    async def promote(request: Request) -> JSONResponse:
        tenant_id = tenant(request)
        data = await read_json(request)
        forbid_public("Promote")
        try:
            return JSONResponse(await h.tune.promote(tenant_id, data))
        except TuneError as exc:
            if exc.detail is not None:
                return refused(exc)
            raise fail(exc) from exc

    @app.post("/v1/tune/rollback")
    async def rollback(request: Request) -> JSONResponse:
        tenant_id = tenant(request)
        data = await read_json(request)
        forbid_public("Rollback")
        try:
            return JSONResponse(await h.tune.rollback(tenant_id, data))
        except TuneError as exc:
            if exc.detail is not None:
                return refused(exc)
            raise fail(exc) from exc

    @app.get("/v1/tune/canary")
    async def canary(request: Request) -> JSONResponse:
        tenant_id = tenant(request)
        if public:
            return JSONResponse({**(replay().get("canary") or {}), "replay": True})
        return JSONResponse(h.tune.canary(tenant_id))

    for path, kind in KINDS.items():
        add_job_routes(app, h, tenant, replay, public, path, kind)


def add_job_routes(
    app: FastAPI,
    h: Harness,
    tenant: Callable[[Request], str],
    replay: Callable[[], dict[str, Any]],
    public: bool,
    path: str,
    kind: str,
) -> None:
    def recorded(job_id: str | None = None) -> Any:
        items = replay().get(path) or []
        if job_id is None:
            return items
        found = next((i for i in items if i.get("id") == job_id), None)
        if found is None:
            raise GatewayError(404, "not_found", f"No {kind} {job_id} in the replay.")
        return found

    async def list_jobs(request: Request, limit: int = 20) -> JSONResponse:
        tenant_id = tenant(request)
        if not 1 <= limit <= 200:
            raise GatewayError(400, "invalid_request", "limit must be between 1 and 200")
        if public:
            return JSONResponse({path: recorded()[:limit], "replay": True})
        return JSONResponse({path: h.tune.store.list(kind, tenant_id, limit)})

    async def get_job(request: Request, job_id: str) -> JSONResponse:
        tenant_id = tenant(request)
        if public:
            return JSONResponse({**recorded(job_id), "replay": True})
        try:
            return JSONResponse(h.tune.job(kind, tenant_id, job_id))
        except TuneError as exc:
            raise GatewayError(exc.status, exc.code, exc.message) from exc

    app.add_api_route(f"/v1/tune/{path}", list_jobs, methods=["GET"])
    app.add_api_route(f"/v1/tune/{path}/{{job_id}}", get_job, methods=["GET"])
    if kind not in UNTIL:
        return

    async def job_events(request: Request, job_id: str, after: str | None = None) -> Response:
        tenant_id = tenant(request)
        start = _cursor(request, after)
        if public:
            events = [e for e in recorded(job_id).get("events") or [] if e["seq"] > start]

            async def replayed():
                for event in events:
                    yield sse(event)

            return StreamingResponse(
                replayed(), media_type="text/event-stream", headers=SSE_HEADERS
            )
        try:
            record = h.tune.job(kind, tenant_id, job_id)
        except TuneError as exc:
            raise GatewayError(exc.status, exc.code, exc.message) from exc
        live = record["status"] == "running"
        token = app.state.acquire_stream(request, tenant_id)

        async def stream():
            source = h.ops.follow(
                tenant_id, record["stream"], after=start, until=UNTIL[kind], live=live
            )
            async for event in source:
                yield ": keepalive\n\n" if event is None else sse(event)

        return StreamingResponse(
            app.state.bounded(token, stream()), media_type="text/event-stream", headers=SSE_HEADERS
        )

    app.add_api_route(f"/v1/tune/{path}/{{job_id}}/events", job_events, methods=["GET"])
