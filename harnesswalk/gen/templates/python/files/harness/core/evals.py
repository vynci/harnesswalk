"""Offline eval: run the pack's golden set through this harness and score every case.

Contract: spec/contracts/evaluator.md (the `golden` suite) and http_api.md (eval runs).
Node: `offline_eval`, never switched off: the golden set is how every rung is measured. Each
case runs as an ordinary turn on the fake provider with its own fixture, in a scratch tenant
per run (`evalrun-<run_id>`), so a run never touches your memory. Deterministic scorers only:
`path` (a subsequence of the walk), `tool_calls` (exact), `answer_match`, `citations`,
`schema`, `pii_leak` and `checks` (router rules, compaction). A case passes when every
scorer that applies passes; `regression` is a pass in the previous run that fails now.

Lesson: the golden set in CI is what lets you delete a node later and know what broke.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from harness.core import gateway
from harness.core.events import SIMPLE, iso
from harness.core.pii import find_pii
from harness.core.tools import check_schema


def load_cases(h: Any, pack: str) -> list[dict[str, Any]]:
    path = h.settings.packs[pack].golden_path
    if not path or not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def actual_of(events: list[dict[str, Any]]) -> dict[str, Any]:
    end = events[-1] if events and events[-1]["type"] == "turn_end" else {}
    return {"reply": (end.get("summary") or {}).get("reply"), "exit": (end.get("summary") or {}).get("exit"),
            "path": [e["node_id"] for e in events if e["type"] == "node_enter" and e["node_id"] in SIMPLE],
            "tool_calls": [e["summary"]["tool"] for e in events
                           if e["type"] == "node_enter" and e.get("node_id") == "tools"],
            "citations": (end.get("summary") or {}).get("citations") or [],
            "rules": [e["summary"].get("rule") for e in events
                      if e["type"] == "node_exit" and e.get("node_id") == "router"],
            "compacted": any((e.get("summary") or {}).get("compacted") for e in events
                             if e["type"] == "node_exit" and e.get("node_id") == "context_builder"),
            "errors": [e["error"] for e in events if e["type"] == "error"]}  # fmt: skip


def result(ok: bool, detail: str) -> dict[str, Any]:
    return {"value": 1.0 if ok else 0.0, "pass": ok, "detail": detail[:300]}


def score(expect: dict[str, Any], checks: dict[str, Any], act: dict[str, Any], users: list[str],
          schema: dict[str, Any] | None) -> dict[str, dict[str, Any]]:  # fmt: skip
    out: dict[str, dict[str, Any]] = {}
    if "path" in expect:
        it = iter(act["path"])
        ok = all(any(n == x for x in it) for n in expect["path"])
        out["path"] = result(ok, f"expected {expect['path']} in {act['path']}")
    if "tool_calls" in expect:
        out["tool_calls"] = result(expect["tool_calls"] == act["tool_calls"],
                                   f"expected {expect['tool_calls']}, called {act['tool_calls']}")  # fmt: skip
    reply = act["reply"] or ""
    if any(k in expect for k in ("answer_contains", "answer_exact", "answer_regex")):
        ok = all(s in reply for s in expect.get("answer_contains") or [])
        ok = ok and ("answer_exact" not in expect or reply.strip() == expect["answer_exact"].strip())
        ok = ok and ("answer_regex" not in expect or re.search(expect["answer_regex"], reply) is not None)
        out["answer_match"] = result(ok, f"reply: {reply[:200]}")
    if "citations" in expect:
        miss = [c for c in expect["citations"] if not any(
            all(x.get(k) == v for k, v in c.items() if k in ("doc", "chunk")) for x in act["citations"])]
        out["citations"] = result(not miss, f"missing {miss}, cited {act['citations']}")
    if schema:
        try:
            out["schema"] = result(not check_schema(schema, json.loads(reply)), "reply against output_schema")
        except ValueError:
            out["schema"] = result(False, "reply is not JSON")
    leaks = [v for _, v in find_pii(reply) if not any(v in u for u in users)]
    out["pii_leak"] = result(not leaks, f"{len(leaks)} value(s) the user did not type")
    if checks.get("router_rules"):
        out["checks"] = result(act["rules"] == checks["router_rules"], f"router rules {act['rules']}")
    if "compacted" in checks:
        out["checks"] = result(act["compacted"] == checks["compacted"], f"compacted {act['compacted']}")
    return out


def run_turn(h: Any, tenant: str, body: dict[str, Any], extra: dict[str, Any]) -> list[dict[str, Any]]:
    turn = gateway.start_turn(h, tenant, body, kind="eval", extra=extra)
    deadline = time.monotonic() + float(h.settings.get("evals.model_timeout_s", 180))
    with turn.cond:
        while not turn.done and time.monotonic() < deadline:
            turn.cond.wait(0.5)
    return list(turn.events)


def run_case(h: Any, run_id: str, pack: str, case: dict[str, Any]) -> dict[str, Any]:
    tenant = f"evalrun-{run_id}"
    turns = case.get("turns") or [{"user": case["prompt"]}]
    fixtures = case.get("fixture")
    session, scores, acts = None, {}, []
    for n, t in enumerate(turns):
        fx = fixtures[n] if isinstance(fixtures, list) else fixtures
        body = {"message": t["user"], "options": {"pack": pack, "output_schema": case.get("output_schema")}}
        if session:
            body = {**body, "session_id": session, "options": {"output_schema": case.get("output_schema")}}
        events = run_turn(h, tenant, body, {"fixture": fx, "roles": case.get("roles"),
                                            "approval": case.get("approval", "deny"), "eval_run_id": run_id})  # fmt: skip
        session = events[0]["session_id"]
        act = actual_of(events)
        acts.append(act)
        if len(turns) > 1:
            for name, s in score(t.get("expect") or {}, t.get("checks") or {}, act, [t["user"]], None).items():
                if name != "pii_leak" or t.get("expect"):
                    scores[f"turn{n + 1}.{name}"] = s
    whole = {**acts[-1], "path": [p for a in acts for p in a["path"]],
             "tool_calls": [c for a in acts for c in a["tool_calls"]],
             "citations": [c for a in acts for c in a["citations"]],
             "rules": [r for a in acts for r in a["rules"]],
             "compacted": any(a["compacted"] for a in acts)}  # fmt: skip
    scores.update(score(case.get("expect") or {}, case.get("checks") or {}, whole,
                        [t["user"] for t in turns], case.get("output_schema")))  # fmt: skip
    status = "passed" if all(s["pass"] for s in scores.values()) else "failed"
    return {"case_id": case["id"], "title": case.get("title"), "status": status, "scores": scores,
            "expected": case.get("expect"), "actual": whole, "regression": False}  # fmt: skip


def start(h: Any, tenant: str, pack: str, only: list[str] | None = None) -> str:
    cases = [c for c in load_cases(h, pack) if not only or c["id"] in only]
    with h.lock:
        run_id = f"run_{len(h.db.rows('eval_runs')) + 1:04d}"
        h.db.insert("eval_runs", {"run_id": run_id, "tenant": tenant, "pack": pack, "suite": "golden",
                                  "status": "running", "started_at": iso(time.time()),
                                  "totals": {"cases": len(cases), "passed": 0, "failed": 0, "skipped": 0}})  # fmt: skip
    threading.Thread(target=finish, args=(h, tenant, pack, run_id, cases), daemon=True).start()
    return run_id


def finish(h: Any, tenant: str, pack: str, run_id: str, cases: list[dict[str, Any]]) -> None:
    results = []
    for case in cases:
        if not case.get("fixture") or "dev" not in (case.get("modes") or ["dev"]):
            results.append({"case_id": case["id"], "status": "skipped", "scores": {}})
            continue
        try:
            results.append(run_case(h, run_id, pack, case))
        except Exception as exc:  # noqa: BLE001 - one broken case does not stop the run
            results.append({"case_id": case["id"], "status": "error", "scores": {},
                            "error": {"code": "internal", "message": str(exc)[:300]}})  # fmt: skip
    prev = [r for r in h.db.rows("eval_runs", tenant=tenant, pack=pack, status="done")]
    before = {c["case_id"]: c["status"] for c in (prev[-1]["cases"] if prev else [])}
    for c in results:
        c["previous_status"] = before.get(c["case_id"])
        c["regression"] = c["status"] in ("failed", "error") and before.get(c["case_id"]) == "passed"
    ids = [c["case_id"] for c in results]
    regressed = [c["case_id"] for c in results if c["regression"]]
    totals = {"cases": len(results), "passed": sum(c["status"] == "passed" for c in results),
              "failed": sum(c["status"] in ("failed", "error") for c in results),
              "skipped": sum(c["status"] == "skipped" for c in results)}  # fmt: skip
    h.db.update("eval_runs", {"run_id": run_id}, {
        "status": "done", "ended_at": iso(time.time()), "cases": results, "totals": totals,
        "regressions": len(regressed), "regression_cases": regressed,
        "previous_run_id": prev[-1]["run_id"] if prev else None,
        "diff": {"fixed": [i for i in ids if before.get(i) in ("failed", "error")
                           and next(c for c in results if c["case_id"] == i)["status"] == "passed"],
                 "regressed": regressed, "new": [i for i in ids if i not in before],
                 "removed": [i for i in before if i not in ids]}})  # fmt: skip


def summary(run: dict[str, Any]) -> dict[str, Any]:
    keep = ("run_id", "pack", "suite", "status", "started_at", "ended_at", "totals", "regressions",
            "previous_run_id")  # fmt: skip
    return {**{k: run.get(k) for k in keep}, "model": None, "judge_model": None,
            "cost": {"input_tokens": 0, "output_tokens": 0, "usd": 0.0}}  # fmt: skip
