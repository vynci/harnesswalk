"""Memory: the read side of the five stores, behind one retrieval pass.

Contract: spec/contracts/store.md. Node: `memory` (context plane, kind store, a group).

The context builder enters `memory` once per turn. Inside it each store gets its own expert
span: `procedural` and `session` are real in P1; `semantic`, `episodic` and `knowledge` are
stubs that report `skipped` with no hits.
"""

from __future__ import annotations

from dataclasses import dataclass

from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("memory", __name__)


@dataclass
class Retrieval:
    procedural: str
    session: object  # stores.session.SessionView
    semantic: list[dict]
    episodic: list[dict]
    knowledge: list[dict]


class Memory:
    def __init__(self, procedural, session, *, estimator: str = "chars_div_4") -> None:
        self.procedural = procedural
        self.session = session
        self.estimator = estimator

    async def retrieve_all(self, ctx: TurnContext) -> Retrieval:
        from harnesswalk.context.stores.episodic import run_episodic
        from harnesswalk.context.stores.knowledge import run_knowledge
        from harnesswalk.context.stores.procedural import run_procedural
        from harnesswalk.context.stores.semantic import run_semantic
        from harnesswalk.context.stores.session import run_session

        async with node(ctx, "memory") as span:
            procedural = await run_procedural(ctx, self.procedural, self.estimator)
            session = await run_session(ctx, self.session, self.estimator)
            semantic = await run_semantic(ctx)
            episodic = await run_episodic(ctx)
            knowledge = await run_knowledge(ctx)
            span.set(
                stores_queried=["procedural", "session"],
                stores_stubbed=["semantic", "episodic", "knowledge"],
                hits={
                    "procedural": 1 + len(self.procedural.skills),
                    "session": len(session.turns) + (1 if session.summary else 0),
                    "semantic": 0,
                    "episodic": 0,
                    "knowledge": 0,
                },
            )
        return Retrieval(procedural, session, semantic, episodic, knowledge)
