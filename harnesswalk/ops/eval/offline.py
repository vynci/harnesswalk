"""Offline eval: run a pack's golden set through the harness and score every case.

Contract: spec/contracts/evaluator.md (P4 additions), HTTP in http_api.md. Node:
`offline_eval` (expert child of `eval`).

A run is a sequence of case turns, one at a time, always in the golden file's order (a case with
`after` reads what that earlier case's memory writer wrote in the same case tenant, so the order
is part of the contract). Each case is a real turn through the whole
harness (gateway to trace), in its own session, in a scratch tenant for the run
(`evalrun-<run_id>`), so a run never reads or writes the caller's memory and every run starts
from the same state. On the `golden` suite the case answers from its `fake` fixture; on
`judge` it answers from the given model (or its fixture) and a judge model scores it; on
`model` it answers from the given model alias, pinned for every call of the case turn (a `fake`
alias answers from the case's fixture), and the deterministic scorers judge it under model
grading (ordered tool calls with read calls allowed, normalized answer matching), no LLM judge.
A `judge` run with a model pins it the same way.
The runner decides any approval with the case's `approval` (default deny).

P2 (additive): a multi-turn case (`turns`) runs its turns in order in one session, each a real
turn; a turn with `expect` or `checks` is scored on its own events as `turn<n>.<scorer>`, and
the case's own `expect` and `checks` are scored over the whole session (paths and tool calls
in order across the turns, the last turn's reply). A case's `roles` are the case identity's.

Progress is an ops stream (`eval:<run_id>`): `eval_run_started`, one `eval_case_finished` per
case, `eval_run_finished`. The run is compared with the previous finished run of the same
tenant, pack and suite (and, on `model`, model): a case that passed then and fails now is a
regression.

P5: a run names any pack the harness can load and runs on that pack's view (or on a release
candidate's, for the eval gate: `purpose: release_gate`); regressed cases are diagnosed before
`eval_run_finished`; `evals.scratch_tenant: false` runs case turns in the caller's tenant.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import time
from typing import TYPE_CHECKING, Any

from harnesswalk.events import Event, iso, new_trace_id
from harnesswalk.nodes import register
from harnesswalk.ops.eval.golden import GoldenCase, GoldenError, golden_path, load_golden
from harnesswalk.ops.eval.judge import Judge, context_of
from harnesswalk.ops.eval.scorers import DETERMINISTIC, actual_of, deterministic, session_actual
from harnesswalk.ops.eval.store import EvalStore
from harnesswalk.types import Identity, Usage

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

register("offline_eval", __name__)
log = logging.getLogger("harnesswalk.eval")

SUITES = ("golden", "judge", "model")
JUDGE_DETERMINISTIC = ("schema", "pii_leak")


class EvalRequestError(Exception):
    """A run that cannot start. Maps to an HTTP status and code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def read_tools_of(view: Any) -> frozenset[str]:
    """The tools of action class `read` in a pack view: the extra calls model grading allows."""
    return frozenset(spec.name for spec in view.registry.all() if spec.action_class == "read")


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
        self.scratch = bool(harness.settings.get("evals.scratch_tenant", True))
        self.diagnoser: Any = None  # set by Evals (P5)
        self._views: dict[str, tuple[Any, bool]] = {}  # run_id -> (pack view, is a candidate)

    # -- starting ------------------------------------------------------------------------------

    def running(self, tenant_id: str | None = None) -> bool:
        return any(
            not task.done() and (tenant_id is None or task.get_name() == tenant_id)
            for task in self.tasks.values()
        )

    def golden(self, pack: str) -> list[GoldenCase]:
        settings = self.h.settings
        if pack != self.h.pack and pack in self.h.packs:
            settings = self.h.packs[pack].settings
        return load_golden(golden_path(settings, pack))

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
        if cfg.get("provider") == "fake":
            return  # a fake alias answers from the case fixtures, on any harness
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
        purpose: str | None = None,
        runtime: Any = None,
    ) -> dict[str, Any]:
        """Validate, store the run, emit `eval_run_started` and schedule it. Returns the run.

        `runtime` (P5) is the pack view the case turns run on instead of the pack's own: the
        eval gate's candidate built from the working set."""
        if suite not in SUITES:
            raise EvalRequestError(400, "invalid_request", "suite must be golden, judge or model")
        if suite == "model" and not model:
            raise EvalRequestError(400, "invalid_request", "the model suite needs a model alias")
        if not self.h.pack_known(pack):
            raise EvalRequestError(422, "invalid_request", f"no dataset pack {pack!r}")
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
        previous = self.store.previous_run(
            tenant_id, pack, suite, model=model if suite == "model" else None
        )
        try:
            view = runtime or self.h.view(pack)
        except KeyError as exc:  # PackUnavailable
            raise EvalRequestError(422, "invalid_request", str(exc.args[0])) from exc
        if runtime is None:
            view.refresh_artifacts()  # what another process released is what the run tests
        seq, run_id = self.store.new_run_id()
        stamp = view.release.stamp()
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
            "purpose": purpose,
            "case_tenant": scratch_tenant(run_id) if self.scratch else tenant_id,
        }
        self.store.create_run(seq, run)
        self._views[run_id] = (view, runtime is not None)
        self._emit(run, "eval_run_started", "active", {
            "run_id": run_id, "pack": pack, "suite": suite, "model": model, "total": len(cases),
            "purpose": purpose,
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

    def _diagnose_on(self, run: dict[str, Any]) -> bool:
        """Automatic diagnosis runs unless the pack's profile switches `diagnose` off (0.2.0)."""
        from harnesswalk.config.loader import profile_for

        try:
            profile = profile_for(self.h.settings, run["pack"])
        except Exception:  # an unreadable profile never stops a run
            return True
        return profile.enabled("diagnose", self.h.settings.mode)

    def _finish(
        self, run: dict[str, Any], results: list[dict], previous: dict[str, str], cost: Usage
    ) -> None:
        totals = {"cases": run["totals"]["cases"], "passed": 0, "failed": 0, "skipped": 0}
        for r in results:
            key = {"passed": "passed", "skipped": "skipped"}.get(r["status"], "failed")
            totals[key] += 1
        regressed = [r["case_id"] for r in results if r["regression"]]
        if regressed and self.diagnoser is not None and self._diagnose_on(run):  # P5
            try:
                run["diagnoses"] = self.diagnoser.after_run(run, regressed)
            except Exception:  # a diagnosis never fails the run
                log.exception("diagnosing run %s failed", run["run_id"])
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
        self._views.pop(run["run_id"], None)
        with contextlib.suppress(Exception):
            self._emit(run, "eval_run_finished", "done" if run["status"] == "done" else "error", {
                "run_id": run["run_id"], "status": run["status"], "totals": totals,
                "regressions": len(regressed), "regression_cases": regressed,
                "previous_run_id": run["previous_run_id"],
            })  # fmt: skip

    def _turn_request(self, run: dict[str, Any], case: GoldenCase, n: int = 0) -> Any:
        """The request for turn `n` of the case (a one-prompt case has only turn 0)."""
        from harnesswalk.control.gateway import TurnRequest

        tenant = run.get("case_tenant") or scratch_tenant(run["run_id"])
        view, candidate = self._views.get(run["run_id"], (None, False))
        provider = fixture = model = pin = None
        case_fixture = case.fixture_for(n)
        if run["suite"] == "model":
            pin = run["model"]
            provider = self.h.settings.get(f"models.models.{pin}.provider")
            if provider == "fake":  # a scripted alias: the case's fixture is its answer
                if not case_fixture:
                    return None
                fixture = case_fixture
        elif run["suite"] == "judge" and run["model"]:
            # Pinned as on `model` (a hint alone only picks among the matched rule's aliases,
            # so `--model local-4b` answered on local-8b before).
            model = pin = run["model"]
            provider = self.h.settings.get(f"models.models.{model}.provider")
        elif case_fixture:
            provider, fixture = "fake", case_fixture
        else:
            return None
        return TurnRequest(
            message=case.turns[n].user if case.turns else case.prompt,
            identity=Identity(tenant, None, list(case.roles or ["user"]), anonymous=False),
            session_id=f"sess_{run['run_id']}_{case.id}"[:120],
            channel="eval",
            provider=provider,
            fixture=fixture,
            model=model,
            pin_model=pin,
            output_schema=case.output_schema,
            internal=True,
            eval_run_id=run["run_id"],
            dataset=run["pack"],
            runtime=view if candidate else None,
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
        if case.checks:
            result["expected"]["checks"] = case.checks
        if case.after:
            result["after"] = case.after
        mode = self.h.settings.mode
        if not case.applies_in(mode):
            result["detail"] = f"applies in {', '.join(case.modes or [])} mode only, not {mode}"
            return result, Usage()
        if case.multi_turn:
            return await self._multi_turn_case(run, case, result)
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
            if run["suite"] in ("judge", "model") and run["model"] and actual.get("cost"):
                c = actual["cost"]
                spent.add(Usage(c["input_tokens"], c["output_tokens"], 0, c["usd"]))
            names = JUDGE_DETERMINISTIC if run["suite"] == "judge" else DETERMINISTIC
            view = self._views.get(run["run_id"], (self.h, False))[0]
            tenant = request.identity.tenant_id
            model_grade = run["suite"] == "model"
            scores = deterministic(
                case,
                actual,
                view.input_guardrails.pii,
                names,
                events=events,
                get_detail=lambda ref: self.h.tracer.get_detail(tenant, ref),
                grading="model" if model_grade else "exact",
                read_tools=read_tools_of(view) if model_grade else frozenset(),
            )
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

    async def _multi_turn_case(
        self, run: dict[str, Any], case: GoldenCase, result: dict[str, Any]
    ) -> tuple[dict[str, Any], Usage]:
        """Every turn of the case in one session, in order; each scored where it has `expect`
        or `checks`, then the case's own expectations over the whole session."""
        requests = [self._turn_request(run, case, n) for n in range(len(case.turns))]
        if any(r is None for r in requests):
            result["detail"] = "no fixture and no model to answer with"
            return result, Usage()
        spent = Usage()
        view = self._views.get(run["run_id"], (self.h, False))[0]
        tenant = requests[0].identity.tenant_id

        def get_detail(ref: str) -> Any:
            return self.h.tracer.get_detail(tenant, ref)

        model_grade = run["suite"] == "model"
        grading = {
            "grading": "model" if model_grade else "exact",
            "read_tools": read_tools_of(view) if model_grade else frozenset(),
        }

        names = JUDGE_DETERMINISTIC if run["suite"] == "judge" else DETERMINISTIC
        per_turn = tuple(n for n in names if n != "pii_leak")  # scored once, on the session
        scores: dict[str, dict[str, Any]] = {}
        actuals: list[dict[str, Any]] = []
        session_events: list[Event] = []
        try:
            for n, (turn, request) in enumerate(zip(case.turns, requests, strict=True)):
                events = await self._play(request, case)
                actual = actual_of(events)
                actuals.append(actual)
                session_events += events
                result["turn_id"] = events[0]["turn_id"]
                result.setdefault("turn_ids", []).append(events[0]["turn_id"])
                if run["suite"] in ("judge", "model") and run["model"] and actual.get("cost"):
                    c = actual["cost"]
                    spent.add(Usage(c["input_tokens"], c["output_tokens"], 0, c["usd"]))
                if turn.expect or turn.checks:
                    one = dataclasses.replace(
                        case, prompt=turn.user, expect=turn.expect, checks=turn.checks,
                        output_schema=None, turns=[],
                    )  # fmt: skip
                    got = deterministic(
                        one, actual, view.input_guardrails.pii, per_turn, events=events,
                        get_detail=get_detail, **grading,
                    )  # fmt: skip
                    scores.update({f"turn{n + 1}.{k}": v for k, v in got.items()})
            result["events_url"] = f"/v1/evals/runs/{run['run_id']}/cases/{case.id}/events"
            actual = session_actual(actuals)
            result["actual"] = actual
            whole = dataclasses.replace(case, prompt="\n".join(t.user for t in case.turns))
            scores.update(
                deterministic(
                    whole, actual, view.input_guardrails.pii, names, events=session_events,
                    get_detail=get_detail, **grading,
                )
            )  # fmt: skip
            if run["suite"] == "judge":
                verdict = await self.judge.judge(
                    case.turns[-1].user,
                    actual.get("reply") or "",
                    context_of(self.h, tenant, session_events),
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
