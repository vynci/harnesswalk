"""HTTP routes of the context plane: memory proposals under review and knowledge documents.

Part of spec/contracts/http_api.md, served by http_api.py. Every route is tenant-scoped through
the same identity rules as the rest of the API.

    GET  /v1/memory/proposals?status=pending   the caller's proposals (pending review by default)
    POST /v1/memory/proposals/{proposal_id}    {"decision": "accept" | "reject", "reason"?}
    GET  /v1/knowledge/docs                    documents the caller can retrieve from
    GET  /v1/knowledge/docs/{doc_id}           one document, its chunks and their citations
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
    writer = harness.memory_writer

    @app.get("/v1/memory/proposals")
    async def list_proposals(request: Request, status: str = "pending", limit: int = 20):
        identity, _ = identities.resolve(request)
        limit = max(1, min(limit, 100))
        if status == "pending":
            items = writer.pending(identity.tenant_id, limit)
        elif status == "all":
            items = writer.recent(identity.tenant_id, limit)
        else:
            raise GatewayError(400, "invalid_request", "status must be pending or all")
        return JSONResponse({"proposals": items})

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
    async def list_docs(request: Request) -> JSONResponse:
        identity, _ = identities.resolve(request)
        view = SimpleNamespace(tenant_id=identity.tenant_id, user_id=identity.user_id)
        return JSONResponse({"pack": harness.pack, "docs": harness.knowledge.docs(view)})

    @app.get("/v1/knowledge/docs/{doc_id}")
    async def get_doc(request: Request, doc_id: str) -> JSONResponse:
        identity, _ = identities.resolve(request)
        view = SimpleNamespace(tenant_id=identity.tenant_id, user_id=identity.user_id)
        doc = harness.knowledge.doc(view, doc_id)
        if doc is None:
            raise GatewayError(404, "doc_not_found", f"No document {doc_id} for this tenant.")
        return JSONResponse(doc)
