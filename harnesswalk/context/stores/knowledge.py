"""Knowledge memory. Status in P1: **stub**.

Contract: spec/contracts/store.md. Node: `knowledge` (expert child of `memory`).

Documents for retrieval (RAG): hybrid keyword and vector search, then rerank.
P1 registers the node and shows it on every retrieval pass as `skipped` with no hits, so the
diagram and the stream already have its place. The real store arrives in P3.
"""

from __future__ import annotations

from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("knowledge", __name__, stub=True)


async def run_knowledge(ctx: TurnContext) -> list[dict]:
    async with node(ctx, "knowledge") as span:
        span.skip(hits=0, tokens=0, stub=True)
    return []
