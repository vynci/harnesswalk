"""Offline eval: run a pack's golden set through the harness and score every case.

Contract: spec/contracts/evaluator.md (P4 additions), HTTP in http_api.md. Node:
`offline_eval` (expert child of `eval`).

A run is a sequence of case turns, one at a time. Each case is a real turn through the whole
harness (gateway to trace), in its own session, in a scratch tenant for the run
(`evalrun-<run_id>`), so a run never reads or writes the caller's memory and every run starts
from the same state. On the `golden` suite the case answers from its `fake` fixture; on
`judge` it answers from the given model (or its fixture) and a judge model scores it. The
runner decides any approval with the case's `approval` (default deny).

Progress is an ops stream (`eval:<run_id>`): `eval_run_started`, one `eval_case_finished` per
case, `eval_run_finished`. The run is compared with the previous finished run of the same
tenant, pack and suite: a case that passed then and fails now is a regression.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from harnesswalk.events import Event, iso, new_trace_id
from harnesswalk.nodes import register
from harnesswalk.ops.eval.golden import GoldenCase, GoldenError, golden_path, load_golden
from harnesswalk.ops.eval.judge import Judge, context_of
from harnesswalk.ops.eval.scorers import DETERMINISTIC, actual_of, deterministic
from harnesswalk.ops.eval.store import EvalStore
from harnesswalk.types import Identity, Usage

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

register("offline_eval", __name__)
log = logging.getLogger("harnesswalk.eval")

SUITES = ("golden", "judge")
JUDGE_DETERMINISTIC = ("schema", "pii_leak")


class EvalRequestError(Exception):
    """A run that cannot start. Maps to an HTTP status and code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def scratch_tenant(run_id: str) -> str:
    return f"evalrun-{run_id}"


def stream_of(run_id: str) -> str:
    return f"eval:{run_id}"


class OfflineRunner:
    def __init__(self, harness: Harness, store: EvalStore, judge: Judge) -> None:
        self.h = harness
        self.store = store
        self.judge = judge
        self.tasks: dict[str, asyncio.Task] = {}
        self.case_timeout_s = float(harness.settings.get("evals.case_timeout_s", 60))

    # -- starting ------------------------------------------------------------------------------

    def running(self, tenant_id: str | None = None) -> bool:
        return any(
            not task.done() and (tenant_id is None or task.get_name() == tenant_id)
            for task in self.tasks.values()
        )

    def golden(self, pack: str) -> list[GoldenCase]:
        return load_golden(golden_path(self.h.settings, pack))

    def _check_model(self, suite: str, model: str | None) -> None:
        if model is None:
            return
        models = self.h.settings.get("models.models", {})
        cfg = models.get(model)
        if cfg is None or cfg.get("embedding"):
            raise EvalRequestError(400, "invalid_request", f"unknown model alias {model!r}")
        if suite == "golden" and cfg.get("provider") != "fake":
            raise EvalRequestError(
                400, "invalid_request", "the golden suite answers from fixtures (fake) only"
            )
        if not self.judge.usable(cfg["provider"]):
            raise EvalRequestError(
                422, "provider_not_configured", f"provider {cfg['provider']!r} is not configured"
            )

    def start(
        self,
        tenant_id: str,
        pack: str,
        suite: str,
        *,
        model: str | None = None,
        case_ids: list[str] | None = None,
        by: str = "user",
    ) -> dict[str, Any]:
        """Validate, store the run, emit `eval_run_started` and schedule it. Returns the run."""
        loaded = self.h.settings.get("dataset", "_starter")
        if suite not in SUITES:
            raise EvalRequestError(400, "invalid_request", "suite must be golden or judge")
        if pack != loaded:
            raise EvalRequestError(
                422, "invalid_request", f"pack {pack!r} is not loaded ({loaded} is)"
            )
        try:
            cases = self.golden(pack)
        except GoldenError as exc:
            raise EvalRequestError(422, "golden_unavailable", str(exc)) from exc
        if case_ids:
            known = {c.id for c in cases}
            unknown = [c for c in case_ids if c not in known]
            if unknown:
                raise EvalRequestError(400, "invalid_request", f"unknown case ids {unknown}")
            cases = [c for c in cases if c.id in set(case_ids)]
        self._check_model(suite, model)
        judge_model = None
        if suite == "judge":
            judge_model = self.judge.resolve()
            if judge_model is None:
                raise EvalRequestError(
                    422, "judge_unavailable", "no judge model is configured (evals.judge.model)"
                )
        if self.running(tenant_id):
            raise EvalRequestError(409, "eval_running", "an eval run is already running")
        previous = self.store.previous_run(tenant_id, pack, suite)
        seq, run_id = self.store.new_run_id()
        stamp = self.h.release.stamp()
        run: dict[str, Any] = {
            "run_id": run_id,
            "tenant_id": tenant_id,
            "pack": pack,
            "suite": suite,
            "model": model,
            "judge_model": judge_model,
            "started_at": iso(time.time()),
            "ended_at": None,
            "status": "running",
            "totals": {"cases": len(cases), "passed": 0, "failed": 0, "skipped": 0},
            "regressions": 0,
            "regression_cases": [],
            "previous_run_id": previous["run_id"] if previous else None,
            "release_id": stamp["release_id"],
            "working_set": stamp["working_set"],
            "artifacts_hash": stamp["artifacts_hash"],
            "cost": Usage().cost(),
            "cases_requested": list(case_ids) if case_ids else None,
            "trace_id": new_trace_id(),
            "by": by,
        }
        self.store.create_run(seq, run)
        self._emit(run, "eval_run_started", "active", {
            "run_id": run_id, "pack": pack, "suite": suite, "model": model, "total": len(cases),
        })  # fmt: skip
        task = asyncio.get_running_loop().create_task(self._run(run, cases), name=tenant_id)
        self.tasks[run_id] = task
        return run

    def _emit(self, run: dict[str, Any], type_: str, status: str, summary: dict) -> Event:
        return self.h.ops.emit(
            run["tenant_id"],
            stream_of(run["run_id"]),
            type_,
            "eval",
            status,
            {k: v for k, v in summary.items() if v is not None},
            trace_id=run["trace_id"],
        )

    # -- running -------------------------------------------------------------------------------

    async def _run(self, run: dict[str, Any], cases: list[GoldenCase]) -> None:
        previous = (
            self.store.case_statuses(run["previous_run_id"]) if run["previous_run_id"] else {}
        )
        cost = Usage()
        results: list[dict[str, Any]] = []
        try:
            for position, case in enumerate(cases):
                result, spent = await self._case(run, case, position)
                cost.add(spent)
                result["previous_status"] = previous.get(case.id)
                result["regression"] = (
                    result["status"] in ("failed", "error")
                    and result["previous_status"] == "passed"
                )
                self.store.put_case(run["run_id"], position, result)
                results.append(result)
                self._emit(run, "eval_case_finished", "done", {
                    "run_id": run["run_id"], "case_id": case.id, "status": result["status"],
                    "index": position, "total": len(cases), "regression": result["regression"],
                    "turn_id": result.get("turn_id"),
                    "scores": {k: v["pass"] for k, v in result["scores"].items()},
                })  # fmt: skip
            run["status"] = "done"
        except asyncio.CancelledError:
            run["status"], run["error"] = "error", {"code": "cancelled", "message": "stopped"}
        except Exception as exc:  # a run that cannot finish is an error, cases so far are kept
            log.exception("eval run %s failed", run["run_id"])
            run["status"] = "error"
            run["error"] = {"code": "internal", "message": f"{type(exc).__name__}: {exc}"[:500]}
        finally:
            self._finish(run, results, previous, cost)

    def _finish(
        self, run: dict[str, Any], results: list[dict], previous: dict[str, str], cost: Usage
    ) -> None:
        totals = {"cases": run["totals"]["cases"], "passed": 0, "failed": 0, "skipped": 0}
        for r in results:
            key = {"passed": "passed", "skipped": "skipped"}.get(r["status"], "failed")
            totals[key] += 1
        regressed = [r["case_id"] for r in results if r["regression"]]
        current = {r["case_id"]: r["status"] for r in results}
        run.update(
            ended_at=iso(time.time()),
            totals=totals,
            regressions=len(regressed),
            regression_cases=regressed,
            cost=cost.cost(),
            diff={
                "fixed": [
                    c for c, s in current.items() if s == "passed" and previous.get(c) in
                    ("failed", "error")
                ],
                "regressed": regressed,
                "new": [c for c in current if previous and c not in previous],
                "removed": (
                    [c for c in previous if c not in current]
                    if not run.get("cases_requested") else []
                ),
            },
        )  # fmt: skip
        self.store.update_run(run)
        with contextlib.suppress(Exception):
            self._emit(run, "eval_run_finished", "done" if run["status"] == "done" else "error", {
                "run_id": run["run_id"], "status": run["status"], "totals": totals,
                "regressions": len(regressed), "regression_cases": regressed,
                "previous_run_id": run["previous_run_id"],
            })  # fmt: skip

    def _turn_request(self, run: dict[str, Any], case: GoldenCase) -> Any:
        from harnesswalk.control.gateway import TurnRequest

        tenant = scratch_tenant(run["run_id"])
        provider = fixture = model = None
        if run["suite"] == "judge" and run["model"]:
            model = run["model"]
            provider = self.h.settings.get(f"models.models.{model}.provider")
        elif case.fixture:
            provider, fixture = "fake", case.fixture
        else:
            return None
        return TurnRequest(
            message=case.prompt,
            identity=Identity(tenant, None, ["user"], anonymous=False),
            session_id=f"sess_{run['run_id']}_{case.id}"[:120],
            channel="eval",
            provider=provider,
            fixture=fixture,
            model=model,
            output_schema=case.output_schema,
            internal=True,
            eval_run_id=run["run_id"],
        )

    async def _case(
        self, run: dict[str, Any], case: GoldenCase, position: int
    ) -> tuple[dict[str, Any], Usage]:
        result: dict[str, Any] = {
            "case_id": case.id,
            "title": case.title,
            "status": "skipped",
            "scores": {},
            "expected": {**case.expect, **({"rubric": case.rubric} if case.rubric else {})},
            "actual": None,
            "turn_id": None,
            "events_url": None,
        }
        if case.output_schema is not None:
            result["expected"]["output_schema"] = case.output_schema
        request = self._turn_request(run, case)
        if request is None:
            result["detail"] = "no fixture and no model to answer with"
            return result, Usage()
        spent = Usage()
        try:
            events = await self._play(request, case)
            turn_id = events[0]["turn_id"]
            result["turn_id"] = turn_id
            result["events_url"] = f"/v1/evals/runs/{run['run_id']}/cases/{case.id}/events"
            actual = actual_of(events)
            result["actual"] = actual
            if run["suite"] == "judge" and run["model"] and actual.get("cost"):
                c = actual["cost"]
                spent.add(Usage(c["input_tokens"], c["output_tokens"], 0, c["usd"]))
            names = DETERMINISTIC if run["suite"] == "golden" else JUDGE_DETERMINISTIC
            scores = deterministic(case, actual, self.h.input_guardrails.pii, names)
            if run["suite"] == "judge":
                verdict = await self.judge.judge(
                    case.prompt,
                    actual.get("reply") or "",
                    context_of(self.h, request.identity.tenant_id, events),
                    case.rubric,
                )
                if verdict is not None:
                    scores.update(verdict.scores)
                    spent.add(verdict.usage)
            result["scores"] = scores
            result["status"] = "passed" if all(s["pass"] for s in scores.values()) else "failed"
        except Exception as exc:
            result["status"] = "error"
            result["error"] = {"code": getattr(exc, "code", "internal"), "message": str(exc)[:500]}
        return result, spent

    async def _play(self, request: Any, case: GoldenCase) -> list[Event]:
        """Run one case turn to its end, deciding approvals as the case says."""
        h = self.h
        tenant = request.identity.tenant_id
        loop = asyncio.get_running_loop()
        holder: dict[str, str] = {}

        def on_event(event: Event) -> None:
            if event.get("tenant_id") != tenant or event.get("turn_id") != holder.get("turn_id"):
                return
            if event["type"] == "approval_requested":
                loop.call_soon(self._decide, event["summary"]["approval_id"], tenant, case.approval)

        unsubscribe = h.bus.subscribe(on_event)
        try:
            state = await h.gateway.start_turn(request)
            holder["turn_id"] = state.ctx.turn_id
            # An approval requested before the id was known (it cannot be: the turn is a task
            # that has not run yet), so on_event sees every event of the turn.
            try:
                await h.gateway.wait(state.ctx.turn_id, timeout=self.case_timeout_s)
            except TimeoutError:
                with contextlib.suppress(Exception):
                    h.gateway.cancel(tenant, state.ctx.turn_id)
                await h.gateway.wait(state.ctx.turn_id, timeout=10)
        finally:
            unsubscribe()
        return h.tracer.get_turn(tenant, state.ctx.turn_id)

    def _decide(self, approval_id: str, tenant: str, decision: str) -> None:
        with contextlib.suppress(Exception):
            self.h.approvals.decide(approval_id, tenant, "eval:runner", decision, "golden case")

    async def wait(self, run_id: str) -> None:
        task = self.tasks.get(run_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def aclose(self) -> None:
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    def recover(self) -> int:
        """Runs left `running` by a process that stopped: mark them `error`."""
        n = 0
        for run in self.store.unfinished_runs():
            if run["run_id"] in self.tasks:
                continue
            run["status"] = "error"
            run["ended_at"] = iso(time.time())
            run["error"] = {"code": "restarted", "message": "the harness stopped during the run"}
            self.store.update_run(run)
            n += 1
        return n
