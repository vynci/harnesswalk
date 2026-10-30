"""HTTP routes of LLM Ops (P4): evals, feedback, artifacts and releases, the trace query.

Part of spec/contracts/http_api.md ("P4 additions"), served by http_api.py with the same
identity rules, error shape and content-type rule as every other route.

    POST /v1/evals/runs                               start a run (dev and private)
    GET  /v1/evals/runs                               list runs
    GET  /v1/evals/runs/{run_id}                      run detail, per-case results, the diff
    GET  /v1/evals/runs/{run_id}/events               the run's progress (SSE)
    GET  /v1/evals/runs/{run_id}/cases/{case_id}/events  one case turn's stream (SSE)
    GET  /v1/evals/golden                             the golden set
    POST /v1/turns/{turn_id}/feedback                 thumbs and a note
    GET  /v1/artifacts, /v1/artifacts/{kind}/{name}   the working set, one artifact
    PUT  /v1/artifacts/{kind}/{name}                  edit (dev and private)
    GET  /v1/releases, /v1/releases/{id}              releases, one with its diff
    POST /v1/releases, /v1/releases/{id}/rollback     create, roll back (dev and private)
    GET  /v1/trace/turns, /v1/trace/turns/{id}/receipt, /v1/trace/ops-events
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from harnesswalk.control.gateway import GatewayError
from harnesswalk.events import iso, parse_iso
from harnesswalk.ops import release as rel
from harnesswalk.ops.eval import EvalRequestError
from harnesswalk.ops.eval.golden import GoldenError, golden_path
from harnesswalk.ops.eval.offline import scratch_tenant, stream_of
from harnesswalk.ops.eval.online import MAX_NOTE_CHARS
from harnesswalk.ops.ops_events import OPS_STREAM
from harnesswalk.ops.trace import full_receipt

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

CURSOR = re.compile(r"-?\d{1,18}")
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def sse(event: dict[str, Any]) -> str:
    data = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
    return f"id: {event['seq']}\nevent: {event['type']}\ndata: {data}\n\n"


def _cursor(request: Request, after: str | None) -> int:
    values = [-1]
    for name, raw in (("after", after), ("Last-Event-ID", request.headers.get("last-event-id"))):
        raw = (raw or "").strip()
        if not raw:
            continue
        if not CURSOR.fullmatch(raw):
            raise GatewayError(400, "invalid_request", f"{name} must be an integer seq")
        values.append(int(raw))
    return max(values)


def _timestamp(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not re.search(r"(Z|[+-]\d{2}:\d{2})$", value.strip()):
        raise GatewayError(400, "invalid_request", f"{name} must be RFC 3339 with an offset")
    try:
        return iso(parse_iso(value.strip()))
    except ValueError as exc:
        raise GatewayError(400, "invalid_request", f"{name} must be RFC 3339") from exc


def turn_extras(h: Harness, tenant_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    """What GET /v1/turns/{id} gains in P4: feedback, online scores, checkpoint, release."""
    turn_id = summary["turn_id"]
    summary["feedback"] = h.evals.store.feedback(tenant_id, turn_id)
    summary["scores"] = h.evals.online.scores_for(tenant_id, turn_id)
    summary["checkpoint"] = h.session.checkpoint_info(tenant_id, turn_id)
    first = h.tracer.get_turn(tenant_id, turn_id, -1)[:1]
    summary["release"] = ((first[0].get("summary") or {}).get("release")) if first else None
    return summary


def add_ops_routes(
    app: FastAPI,
    h: Harness,
    identities: Any,
    read_json: Callable[[Request], Awaitable[dict[str, Any]]],
) -> None:
    def forbid_public(what: str) -> None:
        if h.settings.mode == "public":
            raise GatewayError(403, "forbidden_in_mode", f"{what} is not offered in public mode.")

    def tenant(request: Request) -> str:
        identity, _ = identities.resolve(request)
        return identity.tenant_id

    # -- evals ----------------------------------------------------------------------------------

    @app.post("/v1/evals/runs", status_code=202)
    async def start_run(request: Request) -> JSONResponse:
        tenant_id = tenant(request)
        data = await read_json(request)
        forbid_public("Eval runs")
        pack, suite = data.get("pack"), data.get("suite")
        model, cases = data.get("model"), data.get("cases")
        if not isinstance(pack, str) or not isinstance(suite, str):
            raise GatewayError(400, "invalid_request", "pack and suite are required strings")
        if model is not None and not isinstance(model, str):
            raise GatewayError(400, "invalid_request", "model must be a string")
        if cases is not None and (
            not isinstance(cases, list) or not all(isinstance(c, str) for c in cases)
        ):
            raise GatewayError(400, "invalid_request", "cases must be a list of case ids")
        try:
            run = h.evals.offline.start(
                tenant_id, pack, suite, model=model, case_ids=cases, by=f"user:{tenant_id}"
            )
        except EvalRequestError as exc:
            raise GatewayError(exc.status, exc.code, exc.message) from exc
        run_id = run["run_id"]
        return JSONResponse(
            {"run_id": run_id, "events_url": f"/v1/evals/runs/{run_id}/events"}, 202
        )

    @app.get("/v1/evals/runs")
    async def list_runs(request: Request, pack: str | None = None, limit: int = 20) -> Response:
        tenant_id = tenant(request)
        if not 1 <= limit <= 200:
            raise GatewayError(400, "invalid_request", "limit must be between 1 and 200")
        return JSONResponse({"runs": h.evals.store.runs(tenant_id, pack, limit)})

    def run_or_404(tenant_id: str, run_id: str) -> dict[str, Any]:
        run = h.evals.store.run(tenant_id, run_id)
        if run is None:
            raise GatewayError(404, "run_not_found", f"No eval run {run_id} for this tenant.")
        return run

    @app.get("/v1/evals/runs/{run_id}")
    async def get_run(request: Request, run_id: str) -> JSONResponse:
        run = run_or_404(tenant(request), run_id)
        run["cases"] = h.evals.store.cases(run_id)
        return JSONResponse(run)

    @app.get("/v1/evals/runs/{run_id}/events")
    async def run_events(request: Request, run_id: str, after: str | None = None) -> Response:
        tenant_id = tenant(request)
        run_or_404(tenant_id, run_id)
        start = _cursor(request, after)
        live = run_id in h.evals.offline.tasks and not h.evals.offline.tasks[run_id].done()

        async def stream():
            source = h.ops.follow(
                tenant_id, stream_of(run_id), after=start, until="eval_run_finished", live=live
            )
            async for event in source:
                # The UI reads run_id off each message; the stored event keeps the envelope.
                yield ": keepalive\n\n" if event is None else sse({**event, "run_id": run_id})

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/v1/evals/runs/{run_id}/cases/{case_id}/events")
    async def case_events(request: Request, run_id: str, case_id: str) -> Response:
        tenant_id = tenant(request)
        run_or_404(tenant_id, run_id)
        case = next((c for c in h.evals.store.cases(run_id) if c["case_id"] == case_id), None)
        if case is None or not case.get("turn_id"):
            raise GatewayError(404, "case_not_found", f"No case {case_id} in run {run_id}.")
        events = h.tracer.get_turn(scratch_tenant(run_id), case["turn_id"])

        async def stream():
            for event in events:
                yield sse(event)

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/v1/evals/golden")
    async def get_golden(request: Request, pack: str | None = None) -> JSONResponse:
        tenant(request)
        pack = pack or h.settings.get("dataset", "_starter")
        if pack != h.settings.get("dataset", "_starter"):
            raise GatewayError(422, "invalid_request", f"pack {pack!r} is not loaded")
        try:
            cases = h.evals.offline.golden(pack)
        except GoldenError as exc:
            raise GatewayError(404, "golden_not_found", str(exc)) from exc
        path = golden_path(h.settings, pack)
        shown = path.relative_to(h.settings.root) if path.is_relative_to(h.settings.root) else path
        return JSONResponse(
            {"pack": pack, "path": str(shown), "cases": [c.to_dict() for c in cases]}
        )

    # -- feedback -------------------------------------------------------------------------------

    @app.post("/v1/turns/{turn_id}/feedback")
    async def feedback(request: Request, turn_id: str) -> JSONResponse:
        tenant_id = tenant(request)
        data = await read_json(request)
        rating, note = data.get("rating"), data.get("note")
        if rating not in ("good", "bad"):
            raise GatewayError(400, "invalid_request", "rating must be good or bad")
        if note is not None and (not isinstance(note, str) or len(note) > MAX_NOTE_CHARS):
            raise GatewayError(
                400, "invalid_request", f"note must be a string of at most {MAX_NOTE_CHARS}"
            )
        if not h.tracer.turn_exists(tenant_id, turn_id):
            raise GatewayError(404, "turn_not_found", f"No turn {turn_id} for this tenant.")
        record = h.evals.online.feedback(tenant_id, turn_id, rating, note, f"user:{tenant_id}")
        return JSONResponse(record)

    # -- artifacts and releases -----------------------------------------------------------------

    def check_pack(pack: str | None) -> None:
        if pack is not None and pack != h.release.pack:
            raise GatewayError(422, "invalid_request", f"pack {pack!r} is not loaded")

    @app.get("/v1/artifacts")
    async def list_artifacts(request: Request, pack: str | None = None) -> JSONResponse:
        tenant(request)
        check_pack(pack)
        return JSONResponse(
            {
                "pack": h.release.pack,
                "artifacts": h.release.listing(),
                "release": h.release.status(),
            }
        )

    @app.get("/v1/artifacts/{kind}/{name}")
    async def get_artifact(
        request: Request, kind: str, name: str, version: int | None = None
    ) -> JSONResponse:
        tenant(request)
        try:
            return JSONResponse(h.release.artifact(kind, name, version))
        except rel.ArtifactNotFound as exc:
            raise GatewayError(404, "artifact_not_found", str(exc).strip("'\"")) from exc

    @app.put("/v1/artifacts/{kind}/{name}")
    async def put_artifact(request: Request, kind: str, name: str) -> JSONResponse:
        tenant_id = tenant(request)
        data = await read_json(request)
        forbid_public("Editing artifacts")
        body, note = data.get("body"), data.get("note")
        if not isinstance(body, str):
            raise GatewayError(400, "invalid_request", "body must be a string")
        if note is not None and not isinstance(note, str):
            raise GatewayError(400, "invalid_request", "note must be a string")
        try:
            out = rel.apply_edit(h, tenant_id, kind, name, body, note=note)
        except rel.ArtifactNotFound as exc:
            raise GatewayError(404, "artifact_not_found", str(exc).strip("'\"")) from exc
        except rel.Busy as exc:
            raise GatewayError(409, "turn_running", str(exc)) from exc
        except rel.ArtifactInvalid as exc:
            raise GatewayError(422, "invalid_artifact", str(exc)) from exc
        return JSONResponse(out)

    @app.get("/v1/releases")
    async def list_releases(request: Request, pack: str | None = None) -> JSONResponse:
        tenant(request)
        check_pack(pack)
        return JSONResponse(h.release.releases())

    @app.get("/v1/releases/{release_id}")
    async def get_release(request: Request, release_id: str) -> JSONResponse:
        tenant(request)
        detail = h.release.release_detail(release_id)
        if detail is None:
            raise GatewayError(404, "release_not_found", f"No release {release_id}.")
        return JSONResponse(detail)

    @app.post("/v1/releases", status_code=201)
    async def create_release(request: Request) -> JSONResponse:
        tenant_id = tenant(request)
        data = await read_json(request)
        forbid_public("Releases")
        note = data.get("note")
        if note is not None and not isinstance(note, str):
            raise GatewayError(400, "invalid_request", "note must be a string")
        try:
            return JSONResponse(rel.apply_create(h, tenant_id, note=note), 201)
        except rel.Busy as exc:
            raise GatewayError(409, "turn_running", str(exc)) from exc

    @app.post("/v1/releases/{release_id}/rollback")
    async def rollback(request: Request, release_id: str) -> JSONResponse:
        tenant_id = tenant(request)
        await read_json(request)
        forbid_public("Rollback")
        try:
            return JSONResponse(rel.apply_rollback(h, tenant_id, release_id))
        except KeyError as exc:
            raise GatewayError(404, "release_not_found", f"No release {release_id}.") from exc
        except rel.Busy as exc:
            raise GatewayError(409, "turn_running", str(exc)) from exc

    # -- trace ------------------------------------------------------------------------------------

    @app.get("/v1/trace/turns")
    async def trace_turns(
        request: Request,
        tenant: str | None = None,  # noqa: A002 - the query parameter's name
        session: str | None = None,
        node: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> JSONResponse:
        identity, _ = identities.resolve(request)
        tenant_id = identity.tenant_id
        if tenant is not None and tenant != tenant_id:
            raise GatewayError(404, "tenant_not_found", f"No tenant {tenant} for this caller.")
        if status not in (None, "running", "paused", "done", "error"):
            raise GatewayError(
                400, "invalid_request", "status must be running, paused, done or error"
            )
        if kind not in (None, "chat", "eval"):
            raise GatewayError(400, "invalid_request", "kind must be chat or eval")
        if not 1 <= limit <= 200:
            raise GatewayError(400, "invalid_request", "limit must be between 1 and 200")
        try:
            turns, next_after = h.tracer.query_turns(
                tenant_id,
                session=session,
                node=node,
                status=status,
                kind=kind,
                since=_timestamp(since, "since"),
                until=_timestamp(until, "until"),
                limit=limit,
                after=after,
            )
        except KeyError as exc:
            raise GatewayError(400, "invalid_request", "after names no turn of yours") from exc
        return JSONResponse({"turns": turns, "next_after": next_after})

    @app.get("/v1/trace/turns/{turn_id}/receipt")
    async def trace_receipt(request: Request, turn_id: str) -> JSONResponse:
        tenant_id = tenant(request)
        events = h.tracer.get_turn(tenant_id, turn_id)
        if not events:
            raise GatewayError(404, "turn_not_found", f"No turn {turn_id} for this tenant.")
        out = full_receipt(turn_id, events)
        out["scores"] = h.evals.online.node_scores(tenant_id, turn_id)
        out["feedback"] = h.evals.store.feedback(tenant_id, turn_id)
        return JSONResponse(out)

    @app.get("/v1/trace/ops-events")
    async def ops_events(
        request: Request,
        type: str | None = None,
        after: int = -1,
        limit: int = 200,  # noqa: A002
    ) -> JSONResponse:
        tenant_id = tenant(request)
        if not 1 <= limit <= 1000:
            raise GatewayError(400, "invalid_request", "limit must be between 1 and 1000")
        events = h.ops.list(tenant_id, OPS_STREAM, after=after, limit=limit, type_=type)
        next_after = events[-1]["seq"] if len(events) == limit else None
        return JSONResponse({"events": events, "next_after": next_after})
