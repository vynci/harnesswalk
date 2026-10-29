"""Cost: the per-turn receipt, shown as the `cost` child of `trace`.

The receipt is summed from `cost` on node exits (spec/events.md rule 13). This span carries the
numbers in its summary only, never as its own `cost`, so nobody counts them twice.
"""

from __future__ import annotations

from typing import Any

from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("cost", __name__)


async def run_cost_node(ctx: TurnContext, receipt: dict[str, Any]) -> None:
    async with node(ctx, "cost") as span:
        total = receipt["total"]
        span.set(
            input_tokens=total["input_tokens"],
            output_tokens=total["output_tokens"],
            usd=total["usd"],
            by_node={k: v["usd"] for k, v in receipt["by_node"].items()},
        )
