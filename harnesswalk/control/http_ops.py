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
    GET  /v1/events                                   every ops event of the tenant, live (SSE)

P5 (http_api.md, "P5 additions"):

    GET  /v1/packs                                    the dataset packs this harness can load
    GET  /v1/packs/{id}, /v1/packs/{id}/files/{path}  one pack's card and file tree, one file
    POST /v1/sessions, GET /v1/sessions/{id}          a session on a pack, and its summary
    POST /v1/diagnose                                 a failed case or a bad turn, diagnosed
    POST /v1/releases                                 runs the eval gate first (409 gate_failed)

`?pack=` (or `pack` in a body) picks the pack an artifact, release, golden set or inspector
call is about; the routes call the pack's view of the harness (`Harness.view`).
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
from harnesswalk.ops.eval.diagnose import DiagnoseError
from harnesswalk.ops.eval.golden import GoldenError, golden_path
from harnesswalk.ops.eval.offline import scratch_tenant, stream_of
from harnesswalk.ops.eval.online import MAX_NOTE_CHARS
from harnesswalk.ops.ops_events import OPS_STREAM
from harnesswalk.ops.trace import full_receipt

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

CURSOR = re.compile(r"-?\d{1,18}")
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
PACK_FILE_MAX_BYTES = 512 * 1024
SESSION_COOKIE = "harnesswalk_session"  # http_api.py sets the same cookie on POST /v1/turns
TENANT_COOKIE = "harnesswalk_tenant"


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
    summary["diagnosis"] = h.evals.store.diagnosis_for_turn(tenant_id, turn_id)  # P5
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

    def pack_view(pack: str | None) -> Any:
        """The harness as this pack runs it (P5); 422 for a pack it cannot find."""
        if pack is None:
            return h
        if not h.pack_known(pack):
            raise GatewayError(422, "invalid_request", f"no dataset pack {pack!r}")
        try:
            return h.view(pack)
        except KeyError as exc:  # PackUnavailable: the pack is there but does not load
            raise GatewayError(422, "invalid_request", str(exc.args[0])) from exc

    def release_view(release_id: str) -> Any:
        """The view of the pack a release belongs to (release ids are unique across packs)."""
        found = h.release.store.release(release_id)
        if found is None or not h.pack_known(found.pack):
            raise GatewayError(404, "release_not_found", f"No release {release_id}.")
        return h.view(found.pack)

    # -- packs and sessions (P5) ----------------------------------------------------------------

    @app.get("/v1/packs")
    async def list_packs() -> JSONResponse:
        return JSONResponse(h.pack_list())

    def find_pack(pack_id: str) -> Any:
        from harnesswalk import packs

        info = packs.find(h.settings, pack_id)
        if info is None:
            raise GatewayError(404, "pack_not_found", f"No dataset pack {pack_id}.")
        return info

    @app.get("/v1/packs/{pack_id}")
    async def get_pack(pack_id: str) -> JSONResponse:
        # Tenant-free like GET /v1/packs: pack content is the same for everyone (CC0).
        info = find_pack(pack_id)
        loaded = pack_id == h.pack or pack_id in h.packs
        card = info.card()
        card.pop("path", None)  # no server paths in a response
        return JSONResponse({**card, **info.summary(h.pack, loaded), "files": info.files()})

    @app.get("/v1/packs/{pack_id}/files/{path:path}")
    async def get_pack_file(pack_id: str, path: str) -> JSONResponse:
        info = find_pack(pack_id)
        listed = {f["path"]: f for f in info.files()}
        entry = listed.get(path)
        if entry is None:  # only files the tree lists: no `..`, no hidden files
            raise GatewayError(404, "file_not_found", f"No file {path} in pack {pack_id}.")
        target = info.path / path
        if entry["bytes"] > PACK_FILE_MAX_BYTES:
            raise GatewayError(413, "file_too_large", f"{path} is over the size the UI shows.")
        if target.suffix.lower() == ".pdf":
            from harnesswalk.packs import pdf_text

            text, kind = pdf_text(target), "pdf_text"
        else:
            try:
                text, kind = target.read_text(encoding="utf-8"), "text"
            except UnicodeDecodeError as exc:
                raise GatewayError(415, "not_text", f"{path} is not a text file.") from exc
        return JSONResponse({"pack": pack_id, **entry, "kind": kind, "text": text})

    @app.post("/v1/sessions", status_code=201)
    async def create_session(request: Request) -> JSONResponse:
        identity, tenant_cookie = identities.resolve(request)
        if not tenant_cookie:  # a freshly minted tenant was counted already
            identities.count_creation(request)
        data = await read_json(request)
        pack = data.get("pack", h.pack)
        if not isinstance(pack, str):
            raise GatewayError(400, "invalid_request", "pack must be a string")
        view = pack_view(pack)  # builds and ingests the pack the first time
        from harnesswalk.events import uuid7

        session_id = "sess_" + uuid7().replace("-", "")[:20]
        h.session.ensure_session(identity.tenant_id, session_id, view.pack)
        info = h.session.info(identity.tenant_id, session_id) or {}
        info.pop("turns", None)
        response = JSONResponse(info, 201)
        response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite="lax")
        if tenant_cookie:
            response.set_cookie(TENANT_COOKIE, tenant_cookie, httponly=True, samesite="lax")
        return response

    @app.get("/v1/sessions/{session_id}")
    async def get_session(request: Request, session_id: str) -> JSONResponse:
        info = h.session.info(tenant(request), session_id)
        if info is None:
            raise GatewayError(
                404, "session_not_found", f"No session {session_id} for this tenant."
            )
        return JSONResponse(info)

    # -- diagnose (P5) ----------------------------------------------------------------------------

    @app.post("/v1/diagnose")
    async def diagnose(request: Request) -> JSONResponse:
        tenant_id = tenant(request)
        data = await read_json(request)
        forbid_public("Diagnose")
        try:
            return JSONResponse(h.evals.diagnose.request(tenant_id, data))
        except DiagnoseError as exc:
            raise GatewayError(exc.status, exc.code, exc.message) from exc

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
        token = app.state.acquire_stream(request, tenant_id)  # P6: SSE streams are bounded

        async def stream():
            source = h.ops.follow(
                tenant_id, stream_of(run_id), after=start, until="eval_run_finished", live=live
            )
            async for event in source:
                # The UI reads run_id off each message; the stored event keeps the envelope.
                yield ": keepalive\n\n" if event is None else sse({**event, "run_id": run_id})

        return StreamingResponse(
            app.state.bounded(token, stream()), media_type="text/event-stream", headers=SSE_HEADERS
        )

    @app.get("/v1/evals/runs/{run_id}/cases/{case_id}/events")
    async def case_events(request: Request, run_id: str, case_id: str) -> Response:
        tenant_id = tenant(request)
        run = run_or_404(tenant_id, run_id)
        case = next((c for c in h.evals.store.cases(run_id) if c["case_id"] == case_id), None)
        if case is None or not case.get("turn_id"):
            raise GatewayError(404, "case_not_found", f"No case {case_id} in run {run_id}.")
        events = h.tracer.get_turn(
            run.get("case_tenant") or scratch_tenant(run_id), case["turn_id"]
        )

        async def stream():
            for event in events:
                yield sse(event)

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.get("/v1/evals/golden")
    async def get_golden(request: Request, pack: str | None = None) -> JSONResponse:
        tenant(request)
        view = pack_view(pack)
        pack = view.pack
        try:
            cases = h.evals.offline.golden(pack)
        except GoldenError as exc:
            raise GatewayError(404, "golden_not_found", str(exc)) from exc
        path = golden_path(view.settings, pack)
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

    @app.get("/v1/artifacts")
    async def list_artifacts(request: Request, pack: str | None = None) -> JSONResponse:
        tenant(request)
        view = pack_view(pack)
        return JSONResponse(
            {
                "pack": view.release.pack,
                "artifacts": view.release.listing(),
                "release": view.release.status(),
            }
        )

    @app.get("/v1/artifacts/{kind}/{name}")
    async def get_artifact(
        request: Request,
        kind: str,
        name: str,
        version: int | None = None,
        pack: str | None = None,
    ) -> JSONResponse:
        tenant(request)
        view = pack_view(pack)
        try:
            return JSONResponse(view.release.artifact(kind, name, version))
        except rel.ArtifactNotFound as exc:
            raise GatewayError(404, "artifact_not_found", str(exc).strip("'\"")) from exc

    @app.put("/v1/artifacts/{kind}/{name}")
    async def put_artifact(
        request: Request, kind: str, name: str, pack: str | None = None
    ) -> JSONResponse:
        tenant_id = tenant(request)
        data = await read_json(request)
        forbid_public("Editing artifacts")
        view = pack_view(pack)
        body, note = data.get("body"), data.get("note")
        if not isinstance(body, str):
            raise GatewayError(400, "invalid_request", "body must be a string")
        if note is not None and not isinstance(note, str):
            raise GatewayError(400, "invalid_request", "note must be a string")
        try:
            out = rel.apply_edit(view, tenant_id, kind, name, body, note=note)
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
        return JSONResponse(pack_view(pack).release.releases())

    @app.get("/v1/releases/{release_id}")
    async def get_release(request: Request, release_id: str) -> JSONResponse:
        tenant(request)
        detail = release_view(release_id).release.release_detail(release_id)
        if detail is None:
            raise GatewayError(404, "release_not_found", f"No release {release_id}.")
        return JSONResponse(detail)

    @app.post("/v1/releases", status_code=201)
    async def create_release(request: Request) -> JSONResponse:
        tenant_id = tenant(request)
        data = await read_json(request)
        forbid_public("Releases")
        note, pack, force = data.get("note"), data.get("pack"), data.get("force", False)
        if note is not None and not isinstance(note, str):
            raise GatewayError(400, "invalid_request", "note must be a string")
        if pack is not None and not isinstance(pack, str):
            raise GatewayError(400, "invalid_request", "pack must be a string")
        if not isinstance(force, bool):
            raise GatewayError(400, "invalid_request", "force must be a boolean")
        if force and h.settings.mode != "dev":
            raise GatewayError(
                403, "forbidden_in_mode", "A release can skip the eval gate in dev mode only."
            )
        view = pack_view(pack)
        try:
            out = await rel.apply_create_gated(view, tenant_id, note=note, force=force)
        except rel.Busy as exc:
            raise GatewayError(409, "turn_running", str(exc)) from exc
        except rel.GateFailed as exc:
            body = {"error": {"code": "gate_failed", "message": str(exc)}, **exc.detail}
            return JSONResponse(body, 409)
        return JSONResponse(out, 201)

    @app.post("/v1/releases/{release_id}/rollback")
    async def rollback(request: Request, release_id: str) -> JSONResponse:
        tenant_id = tenant(request)
        await read_json(request)
        forbid_public("Rollback")
        view = release_view(release_id)
        try:
            return JSONResponse(rel.apply_rollback(view, tenant_id, release_id))
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
        # Case turns of eval runs live in one scratch tenant per run: `kind=eval` lists the
        # caller's runs' case turns, each with the events_url that serves its stream.
        tenants: str | list[str] = tenant_id
        if kind == "eval":
            tenants = [scratch_tenant(r) for r in h.evals.store.run_ids(tenant_id)]
            tenants.append(tenant_id)  # case turns of runs with evals.scratch_tenant: false
        try:
            turns, next_after = h.tracer.query_turns(
                tenants,
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
        if kind == "eval":
            for t in turns:
                found = h.evals.store.case_by_turn(tenant_id, t["turn_id"])
                run_id, case_id = found if found else (None, None)
                t["run_id"] = run_id
                t["case_id"] = case_id
                t["events_url"] = (
                    f"/v1/evals/runs/{run_id}/cases/{case_id}/events" if case_id else None
                )
        return JSONResponse({"turns": turns, "next_after": next_after})

    @app.get("/v1/trace/turns/{turn_id}/receipt")
    async def trace_receipt(request: Request, turn_id: str) -> JSONResponse:
        tenant_id = tenant(request)
        events = h.tracer.get_turn(tenant_id, turn_id)
        if not events:  # a case turn of one of the caller's eval runs
            scratch = [scratch_tenant(r) for r in h.evals.store.run_ids(tenant_id)]
            owner = h.tracer.tenant_of(turn_id, scratch)
            events = h.tracer.get_turn(owner, turn_id) if owner else []
            tenant_id = owner or tenant_id
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

    # -- the tenant's ops events, live ----------------------------------------------------------

    @app.get("/v1/events")
    async def tenant_events(
        request: Request,
        tenant: str | None = None,  # noqa: A002 - the query parameter's name
        after: str | None = None,
        follow: bool = True,
    ) -> Response:
        """Every ops event of the caller's tenant, from every ops stream (eval runs, feedback,
        artifact changes, releases), oldest first. `id:` is a cursor over all of them; without
        `after` or `Last-Event-ID` the stream starts at the newest event (live only)."""
        tenant_id = identities.resolve(request)[0].tenant_id
        if tenant is not None and tenant != tenant_id:
            raise GatewayError(404, "tenant_not_found", f"No tenant {tenant} for this caller.")
        given = after is not None or request.headers.get("last-event-id")
        start = _cursor(request, after) if given else None
        # P6: SSE streams are bounded; the ops stream draws on its own smaller pool.
        token = app.state.acquire_stream(request, tenant_id, "ops")

        async def stream():
            yield ": ops events\n\n"  # sent at once, so the browser sees the stream open
            async for item in h.ops.tail(tenant_id, after=start, follow=follow):
                if item is None:
                    yield ": keepalive\n\n"
                    continue
                cursor, name, event = item
                data = json.dumps(
                    {**event, "cursor": cursor, "stream": name},
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                yield f"id: {cursor}\nevent: {event['type']}\ndata: {data}\n\n"

        return StreamingResponse(
            app.state.bounded(token, stream()), media_type="text/event-stream", headers=SSE_HEADERS
        )
