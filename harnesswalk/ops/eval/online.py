"""Online eval: a judge on a sample of live turns, and the user's thumbs.

Contract: spec/contracts/evaluator.md (P4 additions). Node: `online_eval` (expert child of
`eval`).

After a chat turn ends, the gateway hands it here. With probability `evals.online.sample_rate`
(0 in public mode by default, 0.2 in dev and private) and a configured judge model, the turn is
judged in the background: never a case turn of an eval run, never a turn that used a BYO key,
never a turn without a reply. Scores are stored per turn and per node (`relevance` on `loop`,
the answer; `faithfulness` on `output_guardrails`, the check that the answer stays with its
sources), so the trace shows them beside the receipt.

Feedback is a rating (`good` or `bad`) and an optional note per turn. The note passes the input
guardrails' PII redaction before it is stored, and the `feedback_recorded` event carries only
its length.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from typing import TYPE_CHECKING, Any

from harnesswalk.nodes import register
from harnesswalk.ops.eval.judge import Judge, context_of
from harnesswalk.ops.eval.store import EvalStore
from harnesswalk.ops.ops_events import OPS_STREAM

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

register("online_eval", __name__)
log = logging.getLogger("harnesswalk.eval")

SCORE_NODES = {"relevance": "loop", "faithfulness": "output_guardrails"}
MAX_NOTE_CHARS = 2000


class OnlineEval:
    def __init__(self, harness: Harness, store: EvalStore, judge: Judge) -> None:
        self.h = harness
        self.store = store
        self.judge = judge
        default = 0.0 if harness.settings.mode == "public" else 0.2
        self.sample_rate = float(harness.settings.get("evals.online.sample_rate", default))
        self.random = random.Random()
        self.tasks: set[asyncio.Task] = set()

    def after_turn(self, state: Any) -> bool:
        """Called by the gateway when a turn ends. True when the turn was sampled."""
        ctx, req = state.ctx, state.request
        if ctx.detached or req.channel == "eval" or state.byo_key or state.exit != "answered":
            return False
        if self.sample_rate <= 0 or self.random.random() >= self.sample_rate:
            return False
        if not self.judge.available:
            return False
        task = asyncio.get_running_loop().create_task(self._judge(ctx.tenant_id, ctx.turn_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return True

    async def _judge(self, tenant_id: str, turn_id: str) -> None:
        try:
            events = self.h.tracer.get_turn(tenant_id, turn_id)
            if not events:
                return
            reply = ((events[-1].get("summary") or {}).get("reply")) or ""
            texts = self.h.session.user_texts(tenant_id, [turn_id])
            question = texts.get(turn_id) or ""
            verdict = await self.judge.judge(
                question, reply, context_of(self.h, tenant_id, events), None
            )
            if verdict is not None:
                self.store.put_scores(
                    tenant_id, turn_id, verdict.scores, SCORE_NODES, verdict.model
                )
        except Exception as exc:  # online judging never affects a turn
            log.warning("online judge failed for %s: %s: %s", turn_id, type(exc).__name__, exc)

    async def drain(self) -> None:
        """Wait for the background judges (tests, shutdown)."""
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def aclose(self) -> None:
        for task in list(self.tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # -- feedback --------------------------------------------------------------------------------

    def feedback(
        self, tenant_id: str, turn_id: str, rating: str, note: str | None, by: str
    ) -> dict[str, Any]:
        if note:
            detector = self.h.input_guardrails.pii
            note = detector.redact(note, detector.find(note))
        record = self.store.put_feedback(tenant_id, turn_id, rating, note or None, by)
        first = self.h.tracer.get_turn(tenant_id, turn_id)[:1]
        start = first[0] if first else {}
        summary: dict[str, Any] = {"rating": rating}
        if note:
            summary["note_chars"] = len(note)
        self.h.ops.emit(
            tenant_id,
            OPS_STREAM,
            "feedback_recorded",
            "eval",
            "done",
            summary,
            trace_id=start.get("trace_id"),
            turn_id=turn_id,
            session_id=start.get("session_id"),
        )
        return record

    def scores_for(self, tenant_id: str, turn_id: str) -> dict[str, float] | None:
        rows = self.store.scores(tenant_id, turn_id)
        return {r["name"]: r["value"] for r in rows} or None

    def node_scores(self, tenant_id: str, turn_id: str) -> dict[str, dict[str, float]] | None:
        out: dict[str, dict[str, float]] = {}
        for r in self.store.scores(tenant_id, turn_id):
            out.setdefault(r["node_id"], {})[r["name"]] = r["value"]
        return out or None
