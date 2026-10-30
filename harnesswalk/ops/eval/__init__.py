"""Eval: golden sets offline, a sampled judge and feedback online.

Contract: spec/contracts/evaluator.md. Node: `eval` (LLM Ops). Its expert children are
`offline_eval` (offline.py: golden runs, scorers, the diff against the previous run) and
`online_eval` (online.py: the sampled judge on live turns, thumbs and notes). `diagnose`
arrives later and stays unregistered.

`Evals` is what the harness holds: the store (`data/evals.db`), the judge, and both runners.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harnesswalk.nodes import register
from harnesswalk.ops.eval.judge import Judge
from harnesswalk.ops.eval.offline import EvalRequestError, OfflineRunner
from harnesswalk.ops.eval.online import OnlineEval
from harnesswalk.ops.eval.store import EvalStore

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

register("eval", __name__)

__all__ = ["EvalRequestError", "Evals"]


class Evals:
    def __init__(self, harness: Harness) -> None:
        s = harness.settings
        self.store = EvalStore(str(s.resolve(s.get("evals.sqlite_path", "data/evals.db"))))
        self.judge = Judge(harness)
        self.offline = OfflineRunner(harness, self.store, self.judge)
        self.online = OnlineEval(harness, self.store, self.judge)

    def running(self) -> bool:
        return self.offline.running()

    async def aclose(self) -> None:
        await self.offline.aclose()
        await self.online.aclose()
