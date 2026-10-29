"""Eval: golden sets offline, judges and feedback online, diagnosis.

Contract: spec/contracts/evaluator.md. Node: `eval` (LLM Ops). Status in P1: **stub**. An eval
turn opens the `eval` span and exits `skipped`. The golden-set runner arrives in P4; the
conformance suite (tests/conformance/) is its deterministic ancestor.
"""

from __future__ import annotations

from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("eval", __name__, stub=True)


async def run_eval_node(ctx: TurnContext, suite: str) -> None:
    async with node(ctx, "eval") as span:
        span.skip(suite=suite, cases=0, passed=0, failed=0, judged=0, stub=True)
