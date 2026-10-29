"""Semantic memory. Status in P1: **stub**.

Contract: spec/contracts/store.md. Node: `semantic` (expert child of `memory`).

Durable facts, written only by the memory writer. Entity match first, then similarity.
P1 registers the node and shows it on every retrieval pass as `skipped` with no hits, so the
diagram and the stream already have its place. The real store arrives in P3.
"""

from __future__ import annotations

from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("semantic", __name__, stub=True)


async def run_semantic(ctx: TurnContext) -> list[dict]:
    async with node(ctx, "semantic") as span:
        span.skip(hits=0, tokens=0, stub=True)
    return []
