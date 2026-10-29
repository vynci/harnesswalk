"""Memory writer: the single gated write path into semantic and episodic memory.

Contract: spec/contracts/memory_writer.md. Node: `memory_writer`. Status in P1: **stub**.

In a chat turn the writer runs only when the loop produced proposals, after the gateway reply
and before `trace`. The P1 loop produces none, so chat turns never visit this node. When it is
called with proposals it reports them as `pending` and writes nothing; gates and versioned
writes arrive in P3.
"""

from __future__ import annotations

from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("memory_writer", __name__, stub=True)


async def run_memory_writer(ctx: TurnContext, proposals: list[dict]) -> None:
    if not proposals:
        return
    async with node(ctx, "memory_writer") as span:
        span.skip(proposed=len(proposals), written=0, rejected=0, pending=len(proposals), stub=True)
