"""The ``harnesswalk`` command (also ``python -m harnesswalk``).

harnesswalk serve                         start the HTTP API (uvicorn)
harnesswalk turn "<prompt>" --provider fake --fixture plain_answer
                                          run one turn in-process and print the walk
harnesswalk turn "<prompt>" --cancel-after loop
                                          cancel the turn as soon as that node is entered
harnesswalk events <turn_id>              dump a stored turn as JSON Lines
harnesswalk turns <session_id>            list a session's turns, newest first
harnesswalk ingest [<pack>] [--force]     index a pack's docs and load its seed facts and events
harnesswalk eval <pack> --suite golden    run the pack's golden set (fake provider) and score it
harnesswalk eval <pack> --suite judge --model hosted-haiku
                                          answer on a model and score with the judge
harnesswalk release list|create|rollback <id>|diff <id> [--pack ID]
                                          the versioned artifacts and their releases
harnesswalk release create --no-gate      skip the eval gate (dev mode only, audited)
harnesswalk serve --takeover              take over a data directory another serve holds
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from typing import Any

from harnesswalk import SPEC_VERSION, __version__

SUMMARY_KEYS = (
    "phase", "action", "tool", "decision", "rule", "used_tokens", "compacted", "exit_reason",
    "steps", "step", "provider", "model", "finish_reason", "tool_calls", "fallback",
    "tools_enabled", "outcome", "executed", "reply_chars", "events", "persisted", "approval_id",
    "record_id", "prompt_version", "skills_indexed", "turns", "hits", "tokens", "input_tokens",
    "output_tokens", "citations", "proposed", "written", "rejected", "pending", "gate",
    "injection_score", "restored", "level", "network", "budget_source", "cascade",
    "prompt_artifact_version", "release_id",
)  # fmt: skip


def _settings(args: argparse.Namespace, **extra: Any):
    """Settings from the common flags; `extra` are dotted overrides the command adds (they go
    through load_settings so a release reload keeps them)."""
    import yaml

    from harnesswalk.config.loader import load_settings

    overrides = dict(extra)
    for item in getattr(args, "set", None) or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"--set needs key=value, got {item!r}")
        overrides[key] = yaml.safe_load(value)
    return load_settings(
        mode=getattr(args, "mode", None),
        provider=getattr(args, "provider", None),
        fixture=getattr(args, "fixture", None),
        profile=getattr(args, "profile", None),
        overrides=overrides,
    )


def _short(summary: dict[str, Any]) -> str:
    parts = []
    for key in SUMMARY_KEYS:
        if key in summary and not isinstance(summary[key], (dict, list)):
            parts.append(f"{key}={summary[key]}")
    return " ".join(parts)


def format_walk(events: list[dict[str, Any]]) -> list[str]:
    """One line per node span, in enter order, indented by nesting, with status and ms."""
    lines: list[tuple[int, int]] = []  # (event index of enter, depth)
    closing: dict[int, dict[str, Any]] = {}
    stack: list[int] = []
    for i, e in enumerate(events):
        if e["type"] == "node_enter":
            lines.append((i, len(stack)))
            stack.append(i)
        elif e["type"] in ("node_exit", "error") and e.get("node_id"):
            closing[stack.pop()] = e
    out = []
    for i, depth in lines:
        enter, close = events[i], closing.get(i, {})
        label = "  " * depth + enter["node_id"]
        ms = close.get("duration_ms")
        timing = f"{ms:9.2f} ms" if ms is not None else " " * 12
        detail = _short({**(enter.get("summary") or {}), **(close.get("summary") or {})})
        if close.get("error"):
            detail = f"error={close['error']['code']} {detail}"
        out.append(f"  {label:<28} {close.get('status', 'open'):<8}{timing}  {detail}".rstrip())
    return out


async def _run_turn(args: argparse.Namespace) -> int:
    from harnesswalk.control.gateway import GatewayError, TurnRequest
    from harnesswalk.runtime import Harness
    from harnesswalk.types import Identity

    harness = Harness(_settings(args))
    loop = asyncio.get_running_loop()
    holder: dict[str, str] = {}

    async def ask(prompt: str) -> str:
        if not sys.stdin.isatty():
            return ""
        return await asyncio.to_thread(input, prompt)

    async def on_breakpoint(node_id: str) -> None:
        answer = await ask(f"  paused at {node_id}: [enter] step, c continue > ")
        harness.gateway.resume(
            args.tenant, holder["turn_id"], "continue" if answer.strip() == "c" else "step"
        )

    async def on_approval(summary: dict[str, Any]) -> None:
        decision = args.approval
        if decision == "ask":
            answer = await ask(
                f"  approve {summary['tool']} {json.dumps(summary.get('args_preview'))}? [y/N] > "
            )
            decision = "approve" if answer.strip().lower() in ("y", "yes") else "deny"
        harness.approvals.decide(
            summary["approval_id"], args.tenant, "user:cli", decision, "from the CLI"
        )

    def cancel_now() -> None:
        with contextlib.suppress(GatewayError):  # the turn may have ended meanwhile
            harness.gateway.cancel(args.tenant, holder["turn_id"])

    def on_event(event: dict[str, Any]) -> None:
        if event["type"] == "node_enter" and event["node_id"] == args.cancel_after:
            loop.call_soon(cancel_now)
        if event["type"] == "node_paused" and event["summary"]["reason"] == "breakpoint":
            loop.create_task(on_breakpoint(event["node_id"]))
        elif event["type"] == "approval_requested":
            loop.create_task(on_approval(event["summary"]))

    harness.bus.subscribe(on_event)
    try:
        state = await harness.gateway.start_turn(
            TurnRequest(
                message=args.prompt,
                identity=Identity(args.tenant, None, ["user"], anonymous=False),
                session_id=args.session,
                step_mode=args.step,
                disabled_nodes=args.disable or [],
                channel="cli",
                model=args.model,
            )
        )
        holder["turn_id"] = state.ctx.turn_id
        await harness.gateway.wait(state.ctx.turn_id)
        events = harness.tracer.get_turn(args.tenant, state.ctx.turn_id)
    finally:
        await harness.aclose()

    if args.json:
        for event in events:
            print(json.dumps(event, separators=(",", ":")))
        return 0
    end = events[-1]
    cost = end.get("cost", {})
    fixture = harness.health().get("fixture")
    print(f"turn {state.ctx.turn_id}  session {state.ctx.session_id}  tenant {args.tenant}")
    print(f"provider {harness.provider_name}" + (f"  fixture {fixture}" if fixture else ""))
    print("\nwalk (node, status, time, summary)")
    print("\n".join(format_walk(events)))
    print("\nreply")
    print("  " + (end.get("summary", {}).get("reply") or "(none)").replace("\n", "\n  "))
    cited = end.get("summary", {}).get("citations") or []
    if cited:  # turn_end.summary.citations: what each [n] in the reply names
        print("\nsources")
        for c in cited:
            print(f"  [{c['n']}] {c['citation']}  ({c['doc']}#{c['chunk']})")
    print(
        f"\nexit {end['summary'].get('exit')}, status {end['status']}, "
        f"{end['summary'].get('steps', 0)} step(s), "
        f"{cost.get('input_tokens', 0)} in / {cost.get('output_tokens', 0)} out tokens, "
        f"${cost.get('usd', 0):.6f}, {end['duration_ms']:.1f} ms"
    )
    print(f"{len(events)} events stored; dump them with: harnesswalk events {state.ctx.turn_id}")
    return 0 if end["status"] == "done" else 1


def _read_stores(args: argparse.Namespace, *names: str) -> list[Any] | None:
    """The trace and session stores, opened read-only, or None when one does not exist yet.

    Read commands never create a store file or change one a running server is using.
    """
    import sqlite3

    from harnesswalk.context.stores.session import SessionStore
    from harnesswalk.ops.trace import Tracer

    settings = _settings(args)
    kinds = {
        "trace": (Tracer, "trace.sqlite_path", "data/trace.db"),
        "session": (SessionStore, "stores.session.sqlite_path", "data/session.db"),
    }
    stores = []
    try:
        for name in names:
            cls, key, default = kinds[name]
            stores.append(cls(str(settings.resolve(settings.get(key, default))), readonly=True))
    except sqlite3.Error:
        for store in stores:
            store.db.close()
        return None
    return stores


def _events(args: argparse.Namespace) -> int:
    stores = _read_stores(args, "trace")
    events = stores[0].get_turn(args.tenant, args.turn_id) if stores else []
    if not events:
        print(f"no stored turn {args.turn_id} for tenant {args.tenant}", file=sys.stderr)
        return 1
    for event in events:
        print(json.dumps(event, separators=(",", ":"), ensure_ascii=False))
    return 0


def _turns(args: argparse.Namespace) -> int:
    from harnesswalk.turns import session_turns

    stores = _read_stores(args, "trace", "session")
    turns = session_turns(*stores, args.tenant, args.session_id, args.limit) if stores else None
    if turns is None:
        print(f"no session {args.session_id} for tenant {args.tenant}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(turns, indent=2, ensure_ascii=False))
        return 0
    for t in turns:
        cost = t["cost"]
        said = (t["message"] or "").replace("\n", " ")[:40]
        reply = (t["reply"] or "").replace("\n", " ")[:60]
        print(
            f"{t['turn_index']:>3}  {t['turn_id']}  {t['status']:<7} {t['exit'] or '-':<9} "
            f"{t['started_at']}  {cost['input_tokens']}/{cost['output_tokens']} tok  "
            f"{said!r} -> {reply!r}"
        )
    return 0


def _ingest(args: argparse.Namespace) -> int:
    """Index `datasets/<pack>/docs/` and load the pack's seeds into the memory stores."""
    from harnesswalk.runtime import Harness

    extra: dict[str, Any] = {"stores.ingest_on_start": False}
    if args.pack:
        extra["dataset"] = args.pack
    settings = _settings(args, **extra)
    pack = settings.get("dataset", "_starter")
    if not (settings.root / "datasets" / pack).is_dir():
        print(f"no dataset pack {pack!r} under {settings.root / 'datasets'}", file=sys.stderr)
        return 1
    harness = Harness(settings)
    try:
        out = harness.ingest(force=args.force)
    finally:
        asyncio.run(harness.aclose())
    seeded = out["seeded"]
    state = "indexed" if out["changed"] else "unchanged"
    print(
        f"pack {pack}: {out['docs']} documents, {out['chunks']} chunks ({state}); "
        f"seeded {seeded['facts']} facts and {seeded['episodes']} events; "
        f"embedder {out['embedder']}, vectors {out['vectors']}"
    )
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from harnesswalk.control.http_api import create_app
    from harnesswalk.ops.lease import Lease, LeaseHeld
    from harnesswalk.runtime import Harness

    settings = _settings(args)
    lease = Lease(
        settings.data_dir, heartbeat_s=float(settings.get("release.lease.heartbeat_s", 5))
    )
    try:
        previous = lease.acquire(takeover=args.takeover)
    except LeaseHeld as exc:
        print(
            f"harnesswalk serve: {exc} ({lease.path}). Stop it, or start this one with"
            " --takeover to replace it.",
            file=sys.stderr,
        )
        return 2
    if previous:
        print(f"took over {lease.path} from pid {previous.get('pid')}")
    try:
        harness = Harness(settings)
        print(
            f"harnesswalk {__version__} (spec {SPEC_VERSION}), mode {harness.settings.mode}, "
            f"provider {harness.provider_name}"
        )
        uvicorn.run(create_app(harness), host=args.host, port=args.port, log_level=args.log_level)
    finally:
        lease.release()
    return 0


def _eval(args: argparse.Namespace) -> int:
    """Run a golden set in-process and print one line per case. Exit 1 when a case fails."""
    from harnesswalk.ops.eval import EvalRequestError
    from harnesswalk.runtime import Harness

    if args.suite == "golden" and not args.provider:
        args.provider = "fake"  # deterministic answers and hash embeddings, as in CI
    settings = _settings(args, **{"dataset": args.pack, "sandbox.level": "inprocess"})

    async def run() -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        harness = Harness(settings)
        try:
            start = harness.evals.offline.start(
                args.tenant, args.pack, args.suite, model=args.model, case_ids=args.case,
                by="cli",
            )  # fmt: skip
            await harness.evals.offline.wait(start["run_id"])
            done = harness.evals.store.run(args.tenant, start["run_id"])
            return done, harness.evals.store.cases(start["run_id"])  # type: ignore[return-value]
        except EvalRequestError as exc:
            print(f"harnesswalk eval: {exc.code}: {exc.message}", file=sys.stderr)
            return None
        finally:
            await harness.aclose()

    out = asyncio.run(run())
    if out is None:
        return 2
    summary, cases = out
    if args.json:
        print(json.dumps({**summary, "cases": cases}, indent=2, ensure_ascii=False))
    else:
        for c in cases:
            failing = {k: v["detail"] for k, v in c["scores"].items() if not v["pass"]}
            mark = "  REGRESSION" if c.get("regression") else ""
            line = f"{c['status'].upper():<7} {c['case_id']:<24} {c['title']}"
            print(line + mark)
            for name, detail in failing.items():
                print(f"          {name}: {detail}")
            for err in (c.get("actual") or {}).get("errors", [])[:1]:
                print(f"          error: {err['code']} at {err['node_id']}: {err['message']}")
            if c.get("error"):
                print(f"          error: {c['error']['code']}: {c['error']['message']}")
        t, cost = summary["totals"], summary["cost"]
        print(
            f"\nrun {summary['run_id']} ({summary['suite']}, pack {summary['pack']}): "
            f"{t['passed']} passed, {t['failed']} failed, {t['skipped']} skipped of {t['cases']};"
            f" {summary['regressions']} regression(s) against"
            f" {summary['previous_run_id'] or 'no earlier run'};"
            f" release {summary['release_id']}{' + working set' if summary['working_set'] else ''};"
            f" {cost['input_tokens']} in / {cost['output_tokens']} out tokens, ${cost['usd']:.6f}"
        )
    return 0 if summary["status"] == "done" and summary["totals"]["failed"] == 0 else 1


def _release(args: argparse.Namespace) -> int:
    from harnesswalk.ops import release as rel
    from harnesswalk.runtime import Harness

    if not args.provider:
        args.provider = "fake"  # releases never call a model: no provider or embedder probe
    # The eval gate (P5) runs the golden set, which may cite the pack's documents: ingest then.
    gating = args.action == "create" and not args.no_gate
    settings = _settings(args, **{"stores.ingest_on_start": gating, "sandbox.level": "inprocess"})

    async def run() -> int:
        h = Harness(settings)
        try:
            if args.pack is not None and not h.pack_known(args.pack):
                print(f"harnesswalk release: no dataset pack {args.pack!r}", file=sys.stderr)
                return 2
            view = h.view(args.pack)
            if args.action != "create" and args.release_id:
                found = h.release.store.release(args.release_id)
                if found is not None and h.pack_known(found.pack):
                    view = h.view(found.pack)  # release ids are unique across packs
            if args.action == "list":
                data = view.release.releases()
                if args.json:
                    print(json.dumps(data, indent=2))
                    return 0
                for r in data["releases"]:
                    star = "*" if r["active"] else " "
                    print(f"{star} {r['release_id']}  {r['created_at']}  {r['created_by']:<12} "
                          f"{len(r['artifacts'])} artifacts  {r['note'] or ''}")  # fmt: skip
                state = "dirty (unreleased changes)" if data["working_set_dirty"] else "clean"
                print(f"working set {state}; chain {'ok' if data['verify']['ok'] else 'BROKEN'}")
                return 0
            if args.action == "create":
                if args.no_gate and h.settings.mode != "dev":
                    print("harnesswalk release: --no-gate is for dev mode only", file=sys.stderr)
                    return 2
                try:
                    out = await rel.apply_create_gated(
                        view, args.tenant, note=args.note, force=args.no_gate
                    )
                except rel.GateFailed as exc:
                    print(f"gate failed: {exc}")
                    for c in exc.detail["cases"]:
                        why = c["diagnosis"]["layer"] if c.get("diagnosis") else c["status"]
                        print(f"  {c['case_id']}: {why}")
                    return 1
                gate = out.get("gate") or {}
                print(f"created {out['release_id']} with {len(out['artifacts'])} artifacts")
                runs = ", ".join(gate.get("run_ids") or []) or "no run"
                print(
                    f"  gate {gate.get('status')} ({gate.get('reason') or 'ok'}; {runs};"
                    f" pass rate {gate.get('pass_rate')})"
                )
                for d in out.get("diff_from_previous", []):
                    print(f"  {d['kind']}/{d['name']} {d['from_version']} -> {d['to_version']}")
                return 0
            if not args.release_id:
                print(f"harnesswalk release {args.action} needs a release id", file=sys.stderr)
                return 2
            if args.action == "rollback":
                try:
                    out = rel.apply_rollback(view, args.tenant, args.release_id)
                except KeyError:
                    print(f"no release {args.release_id}", file=sys.stderr)
                    return 1
                print(f"rolled back to {args.release_id} (was {out['rollback_of']})")
                for r in out["reset"]:
                    print(f"  {r['kind']}/{r['name']} {r['from_version']} -> {r['to_version']}")
                return 0
            detail = view.release.release_detail(args.release_id)
            if detail is None:
                print(f"no release {args.release_id}", file=sys.stderr)
                return 1
            if args.json:
                print(json.dumps(detail, indent=2))
            else:
                prev = detail["previous_release_id"] or "nothing"
                print(f"{detail['release_id']} against {prev}:")
                for d in detail["diff_from_previous"]:
                    print(d["diff"] or f"  {d['kind']}/{d['name']} (no text change)")
            return 0
        finally:
            await h.aclose()

    return asyncio.run(run())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harnesswalk", description="Walk through an AI harness, one node at a time."
    )
    parser.add_argument(
        "--version", action="version", version=f"harnesswalk {__version__} (spec {SPEC_VERSION})"
    )
    sub = parser.add_subparsers(dest="command")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--provider", choices=["fake", "ollama", "anthropic", "openai_compatible"])
        p.add_argument("--fixture", help="fake provider fixture name")
        p.add_argument(
            "--profile", help="a profile file, for example spec/conformance/profile.yaml"
        )
        p.add_argument("--mode", choices=["dev", "public", "private"])
        p.add_argument(
            "--set", action="append", metavar="KEY=VALUE", help="a dotted config override"
        )

    serve = sub.add_parser("serve", help="start the HTTP API")
    common(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--log-level", default="info")
    serve.add_argument(
        "--takeover", action="store_true", help="take over a data directory another serve holds"
    )

    ev = sub.add_parser("eval", help="run a pack's golden set and score every case")
    common(ev)
    ev.add_argument("pack", help="dataset pack, for example _starter")
    ev.add_argument("--suite", choices=["golden", "judge"], default="golden")
    ev.add_argument("--model", help="judge suite: the model alias that answers the cases")
    ev.add_argument("--case", action="append", help="run only this case id (repeatable)")
    ev.add_argument("--tenant", default="dev")
    ev.add_argument("--json", action="store_true", help="print the run detail as JSON")

    rl = sub.add_parser("release", help="list, create, roll back or diff releases")
    common(rl)
    rl.add_argument("action", choices=["list", "create", "rollback", "diff"])
    rl.add_argument("release_id", nargs="?")
    rl.add_argument("--note", help="create: a note for the release")
    rl.add_argument("--pack", help="the dataset pack (default: the configured one)")
    rl.add_argument(
        "--no-gate", action="store_true", help="create: skip the eval gate (dev only, audited)"
    )
    rl.add_argument("--tenant", default="dev")
    rl.add_argument("--json", action="store_true")

    turn = sub.add_parser("turn", help="run one turn in-process and print the walk")
    common(turn)
    turn.add_argument("prompt")
    turn.add_argument("--session", help="continue this session id")
    turn.add_argument("--tenant", default="dev")
    turn.add_argument("--step", action="store_true", help="step mode: pause at every node")
    turn.add_argument("--approval", choices=["ask", "approve", "deny"], default="ask")
    turn.add_argument("--disable", action="append", help="disable a node (repeatable)")
    turn.add_argument("--json", action="store_true", help="print the events instead of the walk")
    turn.add_argument("--cancel-after", metavar="NODE_ID", help="cancel when this node is entered")
    turn.add_argument("--model", help="a model alias hint for the router (dev mode)")

    events = sub.add_parser("events", help="dump a stored turn as JSON Lines")
    events.add_argument("turn_id")
    events.add_argument("--tenant", default="dev")

    ingest = sub.add_parser("ingest", help="index a pack's docs and load its seeds")
    common(ingest)
    ingest.add_argument("pack", nargs="?", help="dataset pack (default: the configured one)")
    ingest.add_argument("--force", action="store_true", help="index again even if unchanged")

    turns = sub.add_parser("turns", help="list a session's turns, newest first")
    turns.add_argument("session_id")
    turns.add_argument("--tenant", default="dev")
    turns.add_argument("--limit", type=int, default=50)
    turns.add_argument("--json", action="store_true", help="print the turn summaries as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    if args.command == "turn":
        return asyncio.run(_run_turn(args))
    if args.command == "events":
        return _events(args)
    if args.command == "turns":
        return _turns(args)
    if args.command == "ingest":
        return _ingest(args)
    if args.command == "eval":
        return _eval(args)
    if args.command == "release":
        return _release(args)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
