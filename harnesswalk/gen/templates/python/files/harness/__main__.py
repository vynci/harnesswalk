"""The command line: `serve` the HTTP API, run one `turn` and print the walk, or `eval` the
golden set. `python -m harness --help` lists the flags."""

from __future__ import annotations

import argparse
import os
import sys
import time

from harness.core import config, evals, gateway
from harness.core.runtime import Harness


def build(args: argparse.Namespace) -> Harness:
    settings = config.load({"profile": args.profile, "set": args.set, "mode": args.mode,
                            "provider": args.provider, "fixture": args.fixture, "data": args.data})  # fmt: skip
    return Harness(settings)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness")
    sub = ap.add_subparsers(dest="cmd", required=True)
#% if tune
    for name in ("serve", "turn", "eval", "tune"):
#% else
    for name in ("serve", "turn", "eval"):
#% endif
        p = sub.add_parser(name)
        p.add_argument("--profile")
        p.add_argument("--set", action="append", default=[], metavar="KEY=JSON")
        p.add_argument("--mode")
        p.add_argument("--provider")
        p.add_argument("--fixture")
        p.add_argument("--data")
        if name == "serve":
            p.add_argument("--port", type=int, default=int(os.environ.get("HARNESSWALK_PORT", 8000)))
            p.add_argument("--host", default="127.0.0.1")
        if name == "turn":
            p.add_argument("message")
            p.add_argument("--approval", choices=["approve", "deny"], default="deny")
#% if tune
        if name == "tune":  # spec/contracts/tuning.md, in miniature (harness/core/tune.py)
            p.add_argument("action", choices=["curate", "run"])
            p.add_argument("--name", help="curate: the dataset name")
            p.add_argument("--teacher", default="fake-small", help="curate: the teacher alias")
            p.add_argument("--dataset", help="run: the dataset name")
            p.add_argument("--trainer", choices=["fake", "external"], default="fake")
            p.add_argument("--student", default="local", help="run: the model alias to tune")
#% endif
    args = ap.parse_args(argv)
    try:
        h = build(args)
    except config.ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.cmd == "serve":
        from harness.core.http_api import serve

        gateway.recover(h)
        serve(h, args.port, args.host)
        return 0
    if args.cmd == "turn":
        turn = gateway.start_turn(h, "dev", {"message": args.message}, extra={"approval": args.approval})
        while not turn.done:
            time.sleep(0.05)
        depth = 0
        for e in turn.events:  # the walk: each node with the status its span closed with
            if e["type"] == "node_enter":
                depth += 1
            elif e["type"] in ("node_exit", "error") and e.get("node_id"):
                depth -= 1
                print("  " * depth + f"{e['node_id']} {e['status']}")
        print(turn.events[-1]["summary"].get("reply", ""))
        return 0
#% if tune
    if args.cmd == "tune":
        import json

        from harness.core import tune

        if args.action == "curate":
            out = tune.curate(h, args.name or "dataset", args.teacher)
        else:
            out = tune.run(h, args.dataset or "dataset", args.trainer, args.student)
        print(json.dumps(out, indent=2))
        return 0
#% endif
    run_id = evals.start(h, "dev", h.settings.default_pack)
    while (h.db.one("eval_runs", run_id=run_id) or {}).get("status") == "running":
        time.sleep(0.1)
    run = h.db.one("eval_runs", run_id=run_id)
    for c in run["cases"]:
        failed = [f"{k}: {v['detail']}" for k, v in c["scores"].items() if not v["pass"]]
        print(f"{c['status'].upper():8} {c['case_id']}" + (f"  ({failed[0]})" if failed else ""))
    t = run["totals"]
    print(f"{t['passed']}/{t['cases']} golden cases passed, {t['skipped']} skipped")
    return 0 if t["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
