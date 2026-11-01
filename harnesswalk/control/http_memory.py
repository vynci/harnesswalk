"""HTTP routes of the context plane: memory proposals under review and knowledge documents.

Part of spec/contracts/http_api.md, served by http_api.py. Every route is tenant-scoped through
the same identity rules as the rest of the API.

    GET  /v1/memory/proposals?status=pending   the caller's proposals (pending review by default;
                                               `&pack=` one pack's)
    POST /v1/memory/proposals/{proposal_id}    {"decision": "accept" | "reject", "reason"?}
    GET  /v1/knowledge/docs                    documents the caller can retrieve from
    GET  /v1/knowledge/docs/{doc_id}           one document, its chunks and their citations

P5: `?pack=` on the knowledge routes picks the pack (default the configured one), and a
proposal is decided by the memory writer of the pack its session runs on, so an accepted fact
lands in that pack's memory.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from harnesswalk.control.gateway import GatewayError

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness


def add_memory_routes(
    app: FastAPI,
    harness: Harness,
    identities: Any,
    read_json: Callable[[Request], Awaitable[dict[str, Any]]],
) -> None:
    def pack_view(pack: str | None) -> Any:
        if pack is None:
            return harness
        if not harness.pack_known(pack):
            raise GatewayError(422, "invalid_request", f"no dataset pack {pack!r}")
        try:
            return harness.view(pack)
        except KeyError as exc:  # PackUnavailable
            raise GatewayError(422, "invalid_request", str(exc.args[0])) from exc

    @app.get("/v1/memory/proposals")
    async def list_proposals(
        request: Request, status: str = "pending", limit: int = 20, pack: str | None = None
    ):
        identity, _ = identities.resolve(request)
        limit = max(1, min(limit, 100))
        if pack is not None and not harness.pack_known(pack):
            raise GatewayError(422, "invalid_request", f"no dataset pack {pack!r}")
        tenant_id = identity.tenant_id

        def pack_of(item: dict[str, Any]) -> str | None:  # a row from before rows had a pack
            return harness.session.dataset_of(tenant_id, item["session_id"]) or harness.pack

        writer = harness.memory_writer  # one table for every pack; `pack` filters it
        if status == "pending":
            items = writer.pending(tenant_id, limit, pack, pack_of)
        elif status == "all":
            items = writer.recent(tenant_id, limit, pack, pack_of)
        else:
            raise GatewayError(400, "invalid_request", "status must be pending or all")
        return JSONResponse({"proposals": items, **({"pack": pack} if pack else {})})

    @app.post("/v1/memory/proposals/{proposal_id}")
    async def decide_proposal(request: Request, proposal_id: str) -> JSONResponse:
        identity, _ = identities.resolve(request)
        data = await read_json(request)
        decision = data.get("decision")
        if decision not in ("accept", "reject"):
            raise GatewayError(400, "invalid_request", "decision must be accept or reject")
        reason = data.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise GatewayError(400, "invalid_request", "reason must be a string")
        if harness.settings.mode == "public":
            raise GatewayError(
                403, "forbidden", "Public mode never writes durable memory, so nothing to decide."
            )
        decider = f"user:{identity.user_id or identity.tenant_id}"
        found = harness.memory_writer.get(identity.tenant_id, proposal_id)
        session_pack = (
            harness.session.dataset_of(identity.tenant_id, found["session_id"]) if found else None
        )
        writer = (
            harness.view(session_pack).memory_writer
            if session_pack and harness.pack_known(session_pack)
            else harness.memory_writer
        )
        try:
            record = writer.decide(
                identity.tenant_id, proposal_id, decider, decision, harness.settings.mode, reason
            )
        except KeyError as exc:
            raise GatewayError(
                404, "proposal_not_found", f"No proposal {proposal_id} for this tenant."
            ) from exc
        except ValueError as exc:
            raise GatewayError(409, "proposal_not_pending", str(exc)) from exc
        return JSONResponse(record)

    @app.get("/v1/knowledge/docs")
    async def list_docs(request: Request, pack: str | None = None) -> JSONResponse:
        identity, _ = identities.resolve(request)
        h = pack_view(pack)
        view = SimpleNamespace(tenant_id=identity.tenant_id, user_id=identity.user_id)
        return JSONResponse({"pack": h.pack, "docs": h.knowledge.docs(view)})

    @app.get("/v1/knowledge/docs/{doc_id}")
    async def get_doc(request: Request, doc_id: str, pack: str | None = None) -> JSONResponse:
        identity, _ = identities.resolve(request)
        h = pack_view(pack)
        view = SimpleNamespace(tenant_id=identity.tenant_id, user_id=identity.user_id)
        doc = h.knowledge.doc(view, doc_id)
        if doc is None:
            raise GatewayError(404, "doc_not_found", f"No document {doc_id} for this tenant.")
        return JSONResponse(doc)
