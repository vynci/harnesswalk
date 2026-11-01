"""Diagnose: map a failed eval case or a bad turn to the layer at fault, from the trace (P5).

Contract: spec/contracts/evaluator.md (P5 additions). Node: `diagnose` (expert child of `eval`).

Diagnose reads stored events only, so it needs no model and runs on the fake provider. It
collects trace facts (evidence), then names the first layer a rule points at:

    1 guardrail_block   input or output guardrails blocked, or policy denied a tool   guardrail
    2 budget_exit       the loop stopped on a budget, max_steps, stuck or timeout     budget
    3 tool_error        a tool call errored or timed out                               tool
    4 artifact_changed  an artifact differs from the last passing run's served set     by kind
    5 model_error       a model call failed (provider_error, fixture_mismatch, ...)    model
    6 retrieval_empty   the stores the turn queried returned nothing it needed         retrieval
    7 nothing else                                                                     unknown

Rule 4 compares artifact versions: every turn records the hash of the served set it ran on
(`turn_start.summary.release.artifacts_hash`), and the release store keeps what each hash
stands for. A fake-provider `fixture_mismatch` after an edited skill is rule 4, not rule 5: the
scripted model saw a different prompt, and that change is the cause.

A diagnosis is stored (`diagnoses` in `data/evals.db`), shown on the case and the turn, and
announced with a `diagnose_finished` ops event. It proposes; it never changes anything.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from harnesswalk.events import Event, iso
from harnesswalk.nodes import register
from harnesswalk.ops.eval.scorers import actual_of
from harnesswalk.ops.eval.store import EvalStore
from harnesswalk.ops.ops_events import OPS_STREAM

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

register("diagnose", __name__)

LAYERS = ("retrieval", "prompt_or_skill", "tool", "model", "guardrail", "budget", "unknown")
# Which layer a changed artifact points at, and which kind wins when several changed.
KIND_LAYER = {
    "prompt": "prompt_or_skill",
    "skill": "prompt_or_skill",
    "tools": "tool",
    "policy": "guardrail",
    "context": "retrieval",
    "models": "model",
}
KIND_ORDER = ("prompt", "skill", "tools", "policy", "context", "models")
TOOL_NODES = ("tools", "registry", "sandbox")
MODEL_NODES = ("model_layer", "router", "providers")
RETRIEVED = ("semantic", "episodic", "knowledge")
MAX_EVIDENCE = 20


class DiagnoseError(Exception):
    """A request diagnose cannot answer. Maps to an HTTP status and code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# -- reading the trace ------------------------------------------------------------------------


def _exits(events: list[Event], node_id: str) -> list[dict[str, Any]]:
    return [
        e.get("summary") or {}
        for e in events
        if e["type"] == "node_exit" and e.get("node_id") == node_id
    ]


def _errors(events: list[Event], nodes: tuple[str, ...]) -> list[Event]:
    return [e for e in events if e["type"] == "error" and e.get("node_id") in nodes]


def served_hash(events: list[Event]) -> str | None:
    start = events[0] if events and events[0]["type"] == "turn_start" else {}
    release = (start.get("summary") or {}).get("release") or {}
    return release.get("artifacts_hash")


def trace_facts(events: list[Event], expected: dict[str, Any] | None = None) -> list[dict]:
    """Every rule's evidence except artifact changes, in rule order."""
    out: list[dict[str, Any]] = []
    for node_id in ("input_guardrails", "output_guardrails"):
        for s in _exits(events, node_id):
            if s.get("action") == "block":
                why = s.get("reason") or ", ".join(s.get("reasons") or []) or "no reason given"
                out.append({
                    "kind": "guardrail_block", "node_id": node_id, "reason": why,
                    "fact": f"{node_id} blocked the turn: {why}",
                })  # fmt: skip
    for s in _exits(events, "policy"):
        if s.get("decision") == "deny":
            out.append({
                "kind": "guardrail_block", "node_id": "policy", "tool": s.get("tool"),
                "rule": s.get("rule"),
                "fact": f"policy denied {s.get('tool')} by rule {s.get('rule')}",
            })  # fmt: skip
    for s in _exits(events, "loop"):
        reason = s.get("exit_reason")
        if reason and reason != "answer":
            out.append({
                "kind": "budget_exit", "node_id": "loop", "exit_reason": reason,
                "steps": s.get("steps"),
                "fact": f"the loop stopped with exit_reason {reason} after {s.get('steps')} steps",
            })  # fmt: skip
    for s in _exits(events, "tools"):
        if s.get("outcome") in ("error", "timeout"):
            out.append({
                "kind": "tool_error", "node_id": "tools", "tool": s.get("tool"),
                "outcome": s["outcome"], "fact": f"tool {s.get('tool')} ended {s['outcome']}",
            })  # fmt: skip
    for e in _errors(events, TOOL_NODES):
        out.append({
            "kind": "tool_error", "node_id": e["node_id"], "code": e["error"]["code"],
            "fact": f"{e['node_id']} failed: {e['error']['code']}: {e['error']['message'][:160]}",
        })  # fmt: skip
    for e in _errors(events, MODEL_NODES):
        out.append({
            "kind": "model_error", "node_id": e["node_id"], "code": e["error"]["code"],
            "fact": f"{e['node_id']} failed: {e['error']['code']}: {e['error']['message'][:160]}",
        })  # fmt: skip
    out += _retrieval_facts(events, expected or {})
    return out


def _retrieval_facts(events: list[Event], expected: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    wanted = [c.get("doc") for c in expected.get("citations") or [] if isinstance(c, dict)]
    knowledge = [s for s in _exits(events, "knowledge") if s.get("enabled", True)]
    if expected.get("citations") is not None:
        got_docs = sorted({d for s in knowledge for d in s.get("docs") or []})
        hits = sum(int(s.get("hits") or 0) for s in knowledge)
        if not knowledge or hits == 0:
            out.append({
                "kind": "retrieval_empty", "store": "knowledge",
                "fact": "the case expects citations and knowledge retrieved nothing",
            })  # fmt: skip
        else:
            missing = [d for d in wanted if d and d not in got_docs]
            if missing:
                out.append({
                    "kind": "citation_missing", "store": "knowledge", "expected_docs": missing,
                    "retrieved_docs": got_docs,
                    "fact": f"knowledge retrieved {got_docs or 'no document'}, not {missing}",
                })  # fmt: skip
    queried = {}
    for node_id in RETRIEVED:
        for s in _exits(events, node_id):
            if s.get("enabled", True):
                queried[node_id] = queried.get(node_id, 0) + int(s.get("hits") or 0)
    if queried and not any(queried.values()) and not out:
        stores = ", ".join(sorted(queried))
        out.append({
            "kind": "retrieval_empty", "store": stores,
            "fact": f"every memory store the turn queried ({stores}) returned no hit",
        })  # fmt: skip
    return out


def artifact_changes(
    now: list[dict[str, Any]] | None, then: list[dict[str, Any]] | None, baseline_run: str
) -> list[dict[str, Any]]:
    """Artifacts whose hash differs between two served sets, in KIND_ORDER."""
    if now is None or then is None:
        return []
    a = {(r["kind"], r["name"]): r for r in then}
    b = {(r["kind"], r["name"]): r for r in now}
    out = []
    for key in sorted(set(a) | set(b), key=lambda k: (KIND_ORDER.index(k[0]), k[1])):
        old, new = a.get(key), b.get(key)
        if old and new and old["hash"] == new["hash"]:
            continue
        name = f"{key[0]}/{key[1]}"
        before = f"v{old['version']}" if old else "absent"
        after = f"v{new['version']}" if new else "absent"
        out.append({
            "kind": "artifact_changed", "artifact": name, "artifact_kind": key[0],
            "from_version": old["version"] if old else None,
            "to_version": new["version"] if new else None, "baseline_run_id": baseline_run,
            "fact": f"{name} is {after} here; {baseline_run}, the last pass, ran {before}",
        })  # fmt: skip
    return out


def decide(evidence: list[dict[str, Any]]) -> str:
    kinds = [e["kind"] for e in evidence]
    for kind, layer in (
        ("guardrail_block", "guardrail"),
        ("budget_exit", "budget"),
        ("tool_error", "tool"),
    ):
        if kind in kinds:
            return layer
    changed = [e for e in evidence if e["kind"] == "artifact_changed"]
    if changed:
        return KIND_LAYER[changed[0]["artifact_kind"]]
    if "model_error" in kinds:
        return "model"
    if "retrieval_empty" in kinds or "citation_missing" in kinds:
        return "retrieval"
    return "unknown"


def suggest(layer: str, evidence: list[dict[str, Any]], baseline: dict | None) -> str:
    first = next((e for e in evidence if KIND_FOR_LAYER.get(e["kind"]) == layer), None)
    if layer in ("prompt_or_skill", "tool", "guardrail", "retrieval", "model") and (
        first is None or first["kind"] == "artifact_changed"
    ):
        changed = next((e for e in evidence if e["kind"] == "artifact_changed"), None)
        if changed is not None:
            release = (baseline or {}).get("release_id")
            back = f" or roll back to {release}" if release else ""
            return (
                f"{changed['artifact']} changed since {changed['baseline_run_id']}, where this"
                f" last passed: diff v{changed['from_version']} against v{changed['to_version']}"
                f"{back}, then run the golden set again."
            )
    if first is None:
        return (
            "No layer stands out in the trace: read the reply against the case, or run the judge"
            " suite to score faithfulness and relevance."
        )
    if layer == "guardrail":
        return f"{first['fact']}. If this input should pass, adjust that check or rule."
    if layer == "budget":
        return f"{first['fact']}. Raise the loop limits or narrow the task."
    if layer == "tool":
        return f"{first['fact']}. Open the tools span in the trace for the call and its result."
    if layer == "model":
        return f"{first['fact']}. Check the provider (or the fixture) and the router's choice."
    return f"{first['fact']}. Check the pack's documents and facts, or the retrieval settings."


KIND_FOR_LAYER = {
    "guardrail_block": "guardrail",
    "budget_exit": "budget",
    "tool_error": "tool",
    "model_error": "model",
    "retrieval_empty": "retrieval",
    "citation_missing": "retrieval",
}


# -- the node ---------------------------------------------------------------------------------


class Diagnoser:
    def __init__(self, harness: Harness, store: EvalStore) -> None:
        self.h = harness
        self.store = store
        self.auto = bool(harness.settings.get("evals.diagnose.auto", True))

    def _refs(self, digest: str | None) -> list[dict[str, Any]] | None:
        return self.h.release.store.served_refs(digest)

    # -- targets ---------------------------------------------------------------------------------

    def case(
        self, tenant_id: str, run_id: str, case_id: str, *, automatic: bool = False
    ) -> dict[str, Any]:
        run = self.store.run(tenant_id, run_id)
        if run is None:
            raise DiagnoseError(404, "run_not_found", f"No eval run {run_id} for this tenant.")
        case = self.store.case(run_id, case_id)
        if case is None:
            raise DiagnoseError(404, "case_not_found", f"No case {case_id} in run {run_id}.")
        if case["status"] not in ("failed", "error"):
            raise DiagnoseError(
                422, "nothing_to_diagnose", f"Case {case_id} {case['status']} in run {run_id}."
            )
        case_tenant = run.get("case_tenant") or f"evalrun-{run_id}"
        events = self.h.tracer.get_turn(case_tenant, case["turn_id"]) if case["turn_id"] else []
        evidence = trace_facts(events, case.get("expected"))
        baseline = None
        for earlier in self.store.runs_before(tenant_id, run["pack"], run["suite"], run_id):
            if self.store.case_statuses(earlier["run_id"]).get(case_id) == "passed":
                baseline = self._baseline(earlier, case_id)
                break
        if baseline is not None:
            evidence += artifact_changes(
                self._refs(served_hash(events) or run.get("artifacts_hash")),
                self._refs(baseline["artifacts_hash"]),
                baseline["run_id"],
            )
        for name, score in (case.get("scores") or {}).items():
            if not score.get("pass"):
                evidence.append({
                    "kind": "scorer_failed", "scorer": name, "detail": score.get("detail"),
                    "fact": f"scorer {name} failed: {score.get('detail')}",
                })  # fmt: skip
        if case.get("error"):
            err = case["error"]
            evidence.append({
                "kind": "case_error", "code": err.get("code"),
                "fact": f"the case errored: {err.get('code')}: {str(err.get('message'))[:160]}",
            })  # fmt: skip
        return self._finish(
            tenant_id,
            {
                "target": "case",
                "run_id": run_id,
                "case_id": case_id,
                "turn_id": case.get("turn_id"),
                "pack": run["pack"],
            },
            evidence,
            baseline,
            automatic,
        )

    def _baseline(self, run: dict[str, Any], case_id: str | None) -> dict[str, Any]:
        digest = run.get("artifacts_hash")
        if case_id is not None:  # the case turn's own stamp, when it is still stored
            case = self.store.case(run["run_id"], case_id) or {}
            tenant = run.get("case_tenant") or f"evalrun-{run['run_id']}"
            if case.get("turn_id"):
                digest = served_hash(self.h.tracer.get_turn(tenant, case["turn_id"], -1)[:1]) or (
                    digest
                )
        return {
            "run_id": run["run_id"],
            "release_id": run.get("release_id"),
            "artifacts_hash": digest,
        }

    def turn(self, tenant_id: str, turn_id: str) -> dict[str, Any]:
        events = self.h.tracer.get_turn(tenant_id, turn_id)
        if not events:
            found = self._case_of_turn(tenant_id, turn_id)
            if found is None:
                raise DiagnoseError(404, "turn_not_found", f"No turn {turn_id} for this tenant.")
            return self.case(tenant_id, *found)
        start = events[0].get("summary") or {}
        if start.get("eval_run_id"):  # a case turn in the caller's own tenant
            case_id = self.store.case_of_turn(start["eval_run_id"], turn_id)
            if case_id is not None:
                return self.case(tenant_id, start["eval_run_id"], case_id)
        feedback = self.store.feedback(tenant_id, turn_id)
        actual = actual_of(events)
        bad = feedback is not None and feedback["rating"] == "bad"
        if not bad and actual.get("exit") in ("answered", None):
            raise DiagnoseError(
                422,
                "nothing_to_diagnose",
                f"Turn {turn_id} answered and has no bad feedback.",
            )
        pack = start.get("pack") or start.get("dataset") or self.h.pack
        evidence = trace_facts(events)
        baseline = None
        for run in self.store.runs(tenant_id, pack, 50):
            totals = run.get("totals") or {}
            if (
                run["suite"] == "golden"
                and run["status"] == "done"
                and totals.get("failed") == 0
                and totals.get("passed", 0) > 0
            ):
                baseline = self._baseline(run, None)
                break
        if baseline is not None:
            evidence += artifact_changes(
                self._refs(served_hash(events)),
                self._refs(baseline["artifacts_hash"]),
                baseline["run_id"],
            )
        if actual.get("exit") not in ("answered", None):
            evidence.append({
                "kind": "turn_exit", "exit": actual["exit"],
                "fact": f"the turn ended with exit {actual['exit']}",
            })  # fmt: skip
        if feedback is not None:
            evidence.append({
                "kind": "feedback", "rating": feedback["rating"],
                "note_chars": len(feedback.get("note") or ""),
                "fact": f"the user rated the turn {feedback['rating']}",
            })  # fmt: skip
        target = {"target": "turn", "turn_id": turn_id, "pack": pack}
        return self._finish(
            tenant_id, target, evidence, baseline, False, session_id=events[0]["session_id"]
        )

    def _case_of_turn(self, tenant_id: str, turn_id: str) -> tuple[str, str] | None:
        """(run_id, case_id) when the turn is a case turn of one of the caller's runs."""
        run_ids = self.store.run_ids(tenant_id)
        owner = self.h.tracer.tenant_of(turn_id, [f"evalrun-{r}" for r in run_ids])
        if owner is None:
            return None
        run_id = owner.removeprefix("evalrun-")
        case_id = self.store.case_of_turn(run_id, turn_id)
        return (run_id, case_id) if case_id else None

    def request(self, tenant_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """POST /v1/diagnose: `{run_id?, case_id}` or `{turn_id}`."""
        turn_id, case_id, run_id = data.get("turn_id"), data.get("case_id"), data.get("run_id")
        for name, value in (("turn_id", turn_id), ("case_id", case_id), ("run_id", run_id)):
            if value is not None and not isinstance(value, str):
                raise DiagnoseError(400, "invalid_request", f"{name} must be a string")
        if (turn_id is None) == (case_id is None) or (turn_id is not None and run_id):
            raise DiagnoseError(
                400, "invalid_request", "give either {run_id?, case_id} or {turn_id}"
            )
        if turn_id is not None:
            return self.turn(tenant_id, turn_id)
        assert case_id is not None
        if run_id is None:
            run_id = self.store.latest_failed_case(tenant_id, case_id)
            if run_id is None:
                raise DiagnoseError(
                    404, "case_not_found", f"No run of yours where case {case_id} failed."
                )
        return self.case(tenant_id, run_id, case_id)

    def after_run(self, run: dict[str, Any], regressed: list[str]) -> dict[str, str]:
        """Automatic on regression: diagnose each regressed case. Returns {case_id: layer}."""
        out: dict[str, str] = {}
        if not self.auto:
            return out
        for case_id in regressed:
            try:
                d = self.case(run["tenant_id"], run["run_id"], case_id, automatic=True)
            except DiagnoseError:
                continue
            out[case_id] = d["layer"]
        return out

    # -- recording -------------------------------------------------------------------------------

    def _finish(
        self,
        tenant_id: str,
        target: dict[str, Any],
        evidence: list[dict[str, Any]],
        baseline: dict[str, Any] | None,
        automatic: bool,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        evidence = evidence[:MAX_EVIDENCE]
        layer = decide(evidence)
        record: dict[str, Any] = {
            "diagnosis_id": None,
            "tenant_id": tenant_id,
            **target,
            "layer": layer,
            "evidence": evidence,
            "suggestion": suggest(layer, evidence, baseline),
            "baseline": baseline,
            "automatic": automatic,
            "created_at": iso(time.time()),
        }
        self.store.put_diagnosis(record)
        summary = {
            "layer": layer,
            "diagnosis_id": record["diagnosis_id"],
            "target": record["target"],
            "case_id": record.get("case_id"),
            "run_id": record.get("run_id"),
            "evidence_count": len(evidence),
            "suggestion": record["suggestion"][:200],
            "automatic": automatic,
        }
        own_turn = record["target"] == "turn"
        self.h.ops.emit(
            tenant_id,
            OPS_STREAM,
            "diagnose_finished",
            "diagnose",
            "done",
            {k: v for k, v in summary.items() if v is not None},
            turn_id=record["turn_id"] if own_turn else None,
            session_id=session_id if own_turn else None,
        )
        return record

    def recent(self, tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
        return self.store.diagnoses(tenant_id, limit)
