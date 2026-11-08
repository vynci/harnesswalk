"""Memory: the read side of the five stores, behind one retrieval pass.

Contract: spec/contracts/store.md. Node: `memory` (context plane, kind store, a group).

The context builder enters `memory` once per turn. Inside it each store gets its own expert
span, in a fixed order: `procedural` and `session` always, then `semantic`, `episodic` and
`knowledge` when they are listed in `context.stores` (a store left out reports `skipped`
with `enabled: false`). Each retrieving store gets the query, its `top_k` and its slice
budget, and trims its own hits. A store that fails is recorded as degraded and skipped; the
turn goes on without it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from harnesswalk.context.records import Hit, Query
from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("memory", __name__)

RETRIEVING = ("semantic", "episodic", "knowledge")


@dataclass
class Retrieval:
    procedural: str
    session: object  # stores.session.SessionView
    semantic: list[Hit] = field(default_factory=list)
    episodic: list[Hit] = field(default_factory=list)
    knowledge: list[Hit] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)


class Memory:
    def __init__(
        self,
        procedural,
        session,
        *,
        estimator: str = "chars_div_4",
        semantic: Any = None,
        episodic: Any = None,
        knowledge: Any = None,
        top_k: dict[str, int] | None = None,
    ) -> None:
        self.procedural = procedural
        self.session = session
        self.estimator = estimator
        self.semantic = semantic
        self.episodic = episodic
        self.knowledge = knowledge
        self.top_k = {"semantic": 5, "episodic": 5, "knowledge": 6, **(top_k or {})}

    def enabled(self) -> list[str]:
        return [k for k in RETRIEVING if getattr(self, k) is not None]

    async def retrieve_all(
        self, ctx: TurnContext, text: str = "", budgets: dict[str, int] | None = None
    ) -> Retrieval:
        from harnesswalk.context.stores.episodic import run_episodic
        from harnesswalk.context.stores.knowledge import run_knowledge
        from harnesswalk.context.stores.procedural import run_procedural
        from harnesswalk.context.stores.semantic import run_semantic
        from harnesswalk.context.stores.session import run_session

        budgets = budgets or {}
        runners = {"semantic": run_semantic, "episodic": run_episodic, "knowledge": run_knowledge}
        now = time.time()
        async with node(ctx, "memory") as span:
            procedural = await run_procedural(ctx, self.procedural, self.estimator)
            session = await run_session(ctx, self.session, self.estimator)
            out = Retrieval(procedural, session)
            for kind in RETRIEVING:
                # Off for this turn (a profile or the request): the span still opens and
                # exits skipped with `disabled: true`, and nothing is retrieved.
                store = None if ctx.is_disabled(kind) else getattr(self, kind)
                query = None
                if store is not None and text:
                    query = Query(
                        text, now, int(self.top_k.get(kind, 5)), int(budgets.get(kind, 800))
                    )
                try:
                    hits = await runners[kind](ctx, store, query)
                except Exception:  # store.md: record the failure, skip that kind, go on
                    out.degraded.append(kind)
                    hits = []
                setattr(out, kind, hits)
            span.set(
                stores_queried=[
                    "procedural",
                    "session",
                    *(k for k in self.enabled() if not ctx.is_disabled(k)),
                ],
                hits={
                    "procedural": 1 + len(self.procedural.skills),
                    "session": len(session.turns) + (1 if session.summary else 0),
                    "semantic": len(out.semantic),
                    "episodic": len(out.episodic),
                    "knowledge": len(out.knowledge),
                },
            )
            if out.degraded:
                span.set(degraded=out.degraded)
        return out
