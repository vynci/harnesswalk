"""`harnesswalk tune ...`: the tuning loop from the command line (spec/contracts/tuning.md, CLI).

    tune curate --pack ecommerce --pack logistics --teacher local-27b --out dataset/approvals
    tune run --dataset approvals --trainer homelab-trl --student local-4b [--name approvals_4b]
    tune compare --candidate model/approvals_4b --against local-4b \\
        --packs ecommerce,logistics --canary-packs sports,_starter [--samples 2] [--out PATH]
    tune promote --candidate model/approvals_4b [--fraction 0.1] [--full] [--force]
    tune rollback
    tune status

Each command runs in-process on this data directory, like `eval` and `scorecard`: the provider
is the one the models it names use (`ollama` for local aliases, `fake` for scripted ones), and
retrieval uses hash vectors so every arm and the teacher see the same context.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any


def add_parser(sub: Any, common: Any) -> None:
    tp = sub.add_parser("tune", help="curate, train, compare, promote and roll back a model")
    common(tp)
    tp.add_argument("action", choices=["curate", "run", "compare", "promote", "rollback", "status"])
    tp.add_argument("--pack", action="append", help="curate: a pack to curate from (repeatable)")
    tp.add_argument("--teacher", help="curate: the teacher alias (default tune.teacher)")
    tp.add_argument("--out", help="curate: dataset/<name>; compare: a Markdown file to write")
    tp.add_argument("--variants", type=int, help="curate: teacher rewrites per golden case")
    tp.add_argument("--seed", type=int, help="curate: the rewrite seed")
    tp.add_argument("--no-teacher-prompt-arm", action="store_true",
                    help="curate: the teacher runs without the control arm's addition")  # fmt: skip
    tp.add_argument("--dataset", help="run: dataset/<name>[@v<n>]")
    tp.add_argument("--trainer", default="fake", help="run: a trainer alias (config/trainers.yaml)")
    tp.add_argument("--student", help="run: the student model alias")
    tp.add_argument("--name", help="run: the adapter and model name")
    tp.add_argument("--config", action="append", metavar="KEY=VALUE",
                    help="run: a training value (epochs=3, outcome=forgets)")  # fmt: skip
    tp.add_argument("--candidate", help="compare, promote: model/<name>[@v<n>]")
    tp.add_argument("--against", help="compare: the base alias")
    tp.add_argument("--packs", help="compare: the target packs; promote: the canary's packs")
    tp.add_argument("--canary-packs", default="", help="compare: the forgetting canary packs")
    tp.add_argument("--samples", type=int, help="compare: model-suite runs per arm and pack")
    tp.add_argument("--no-prompt-arm", action="store_true", help="compare: skip the control arm")
    tp.add_argument("--fraction", type=float, help="promote: the canary's share of turns")
    tp.add_argument("--full", action="store_true", help="promote: widen the canary to every turn")
    tp.add_argument("--force", action="store_true", help="promote: skip the gate (dev, audited)")
    tp.add_argument("--json", metavar="PATH", help="write the job record as JSON")
    tp.add_argument("--tenant", default="dev")
    tp.add_argument("--case-timeout", type=float, default=180.0, help="seconds per case turn")


def _provider(base: Any, aliases: list[str | None]) -> str:
    models = base.get("models.models", {})
    kinds = {(models.get(a) or {}).get("provider") for a in aliases if a}
    kinds.discard(None)
    if "ollama" in kinds:
        return "ollama"
    return "fake" if kinds <= {"fake"} else sorted(kinds)[0]


def _config_values(items: list[str] | None) -> dict[str, Any]:
    import yaml

    out: dict[str, Any] = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"--config needs key=value, got {item!r}")
        out[key] = yaml.safe_load(value)
    return out


def run(args: argparse.Namespace, settings_of: Any) -> int:
    import faulthandler
    import signal

    with contextlib.suppress(AttributeError, ValueError):  # `kill -USR1 <pid>` dumps the stacks
        faulthandler.register(signal.SIGUSR1, all_threads=True)
    from harnesswalk.ops.tune import TuneError, render_comparison
    from harnesswalk.runtime import Harness

    base = settings_of(args)
    aliases = {
        "curate": [args.teacher or base.get("tune.teacher", "local-27b")],
        "run": [args.student],
        "compare": [args.against],
    }.get(args.action, [])
    if args.action == "compare" and args.candidate:
        aliases.append(args.against)
    if not args.provider:
        args.provider = _provider(base, aliases)
    if args.provider == "fake" and not args.fixture:
        args.fixture = "plain_answer"
    extra: dict[str, Any] = {
        "sandbox.level": "inprocess",
        "stores.embeddings.provider": "fake",
        "evals.case_timeout_s": args.case_timeout,
    }
    settings = settings_of(args, **extra)

    def say(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    def progress(event: dict[str, Any]) -> None:
        s = event.get("summary") or {}
        kind = event.get("type")
        if kind == "tune_step":
            total = s.get("total_steps") or 0
            if s["step"] in (1, total) or s["step"] % max(1, total // 20) == 0:
                eta = f" eta {s['eta_s']:.0f}s" if s.get("eta_s") else ""
                say(f"step {s['step']}/{total} loss {s.get('loss')}{eta}")
        elif kind == "eval_case_finished" and event.get("node_id") == "curate":
            mark = "kept" if s.get("kept") else s.get("status")
            say(f"[{s.get('index')}/{s.get('total')}] {s.get('case_id')} {mark}")
        elif kind == "eval_run_finished" and event.get("node_id") == "eval":
            t = s.get("totals") or {}
            say(f"run {s.get('run_id')}: {t.get('passed')}/{t.get('cases')} passed")
        elif kind in ("tune_run_started", "tune_run_finished", "release_gated"):
            say(f"{kind}: {json.dumps(s)[:300]}")

    async def go() -> int:
        h = Harness(settings)
        unsubscribe = h.ops.subscribe(progress)
        try:
            t = h.tune
            if args.action == "status":
                print(json.dumps({
                    "artifacts": t.list_artifacts(), "runs": t.store.list("run", None, 5),
                    "canary": t.canary(args.tenant), "verify": t.verify(),
                }, indent=2, default=str))  # fmt: skip
                return 0
            if args.action in ("promote", "rollback"):
                if args.action == "promote":
                    out = await t.promote(args.tenant, {
                        "candidate": args.candidate, "packs": args.packs,
                        "stage": "full" if args.full else "canary", "fraction": args.fraction
                        if args.fraction is not None else t.canary_cfg()["fraction"],
                        "force": args.force,
                    })  # fmt: skip
                else:
                    out = await t.rollback(args.tenant, {})
                print(json.dumps({k: out[k] for k in ("setting", "releases")}, indent=2))
                return 0
            if args.action == "curate":
                body = {
                    "name": (args.out or "").removeprefix("dataset/"),
                    "packs": args.pack or [],
                    "teacher": args.teacher,
                    "teacher_prompt_arm": not args.no_teacher_prompt_arm,
                }
                if args.variants is not None:
                    body["variants"] = args.variants
                if args.seed is not None:
                    body["seed"] = args.seed
                record = t.start_curation(
                    args.tenant, {k: v for k, v in body.items() if v not in (None, "", [])}
                )
            elif args.action == "run":
                record = t.start_run(args.tenant, {
                    "dataset": args.dataset, "trainer": args.trainer, "student": args.student,
                    "name": args.name, "config": _config_values(args.config),
                })  # fmt: skip
            else:
                record = t.start_comparison(args.tenant, {
                    "candidate": args.candidate, "against": args.against, "packs": args.packs,
                    "canary_packs": args.canary_packs, "samples": args.samples,
                    "prompt_arm": not args.no_prompt_arm,
                })  # fmt: skip
            await t.wait()
            record = t.store.get(record["id"]) or record
            if args.json:
                Path(args.json).write_text(json.dumps(record, indent=2, default=str) + "\n")
            if args.action == "compare" and args.out and record.get("verdict"):
                Path(args.out).write_text(render_comparison(record), encoding="utf-8")
            summary = {k: record.get(k) for k in ("id", "status", "error", "dataset", "counts",
                                                   "adapter", "model", "metrics")}  # fmt: skip
            if record.get("verdict"):
                v = record["verdict"]
                summary["verdict"] = {
                    k: v.get(k)
                    for k in ("status", "reason", "gain", "canary_drop", "table", "prompt_arm")
                }
            print(json.dumps({k: v for k, v in summary.items() if v is not None}, indent=2))
            if record["status"] != "done":
                return 1
            return 1 if (record.get("verdict") or {}).get("status") == "failed" else 0
        except TuneError as exc:
            print(f"harnesswalk tune: {exc.code}: {exc.message}", file=sys.stderr)
            return 2
        finally:
            unsubscribe()
            await h.aclose()

    return asyncio.run(go())
