"""The ``harnesswalk`` command (also ``python -m harnesswalk``).

harnesswalk serve                         start the HTTP API (uvicorn)
harnesswalk turn "<prompt>" --provider fake --fixture plain_answer
                                          run one turn in-process and print the walk
harnesswalk turn "<prompt>" --cancel-after loop
                                          cancel the turn as soon as that node is entered
harnesswalk events <turn_id>              dump a stored turn as JSON Lines
harnesswalk turns <session_id>            list a session's turns, newest first
harnesswalk ingest [<pack>] [--force]     index a pack's docs and load its seed facts and events
harnesswalk packs list|validate [<pack>]|ingest [<pack>]
                                          the dataset packs: list them, check them, index them
harnesswalk eval <pack> --suite golden    run the pack's golden set (fake provider) and score it
harnesswalk eval <pack> --suite judge --model hosted-haiku
                                          answer on a model and score with the judge
harnesswalk eval <pack> --suite model --model local-4b
                                          answer on that model, deterministic scorers only
harnesswalk scorecard --models fake-small,local-4b --out docs/scorecard.md
                                          the pack x model pass matrix (model suite runs)
harnesswalk release list|create|rollback <id>|diff <id> [--pack ID]
                                          the versioned artifacts and their releases
harnesswalk release create --no-gate      skip the eval gate (dev mode only, audited)
harnesswalk serve --takeover              take over a data directory another serve holds
harnesswalk sweep [--now <RFC 3339>]      delete every expired public tenant's rows (P6)
harnesswalk tune curate|run|compare|promote|rollback|status
                                          the tuning loop (0.2.0, spec/contracts/tuning.md)
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


def _clip(text: str, limit: int) -> str:
    """One line of at most `limit` characters, with an ellipsis when it was cut."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def first_error(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The turn's first `error` event: the spans that fail after it only repeat it (a
    provider's fixture_exhausted becomes the loop's provider_error), so it is the cause."""
    return next((e for e in events if e["type"] == "error" and e.get("error")), None)


def format_error(events: list[dict[str, Any]]) -> list[str]:
    """The short block under the reply of a failed turn: the first error's code and message."""
    end = events[-1] if events else {}
    failed = end.get("status") == "error" or (end.get("summary") or {}).get("exit") == "error"
    event = first_error(events) if failed else None
    if event is None:
        return []
    where = f" at {event['node_id']}" if event.get("node_id") else ""
    err = event["error"]
    return ["error", f"  {err['code']}{where}: {_clip(err.get('message', ''), 300)}"]


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
            err = close["error"]
            message = _clip(err.get("message", ""), 72)
            said = f" {json.dumps(message, ensure_ascii=False)}" if message else ""
            detail = f"error={err['code']}{said} {detail}"
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
    failure = format_error(events)
    if failure:  # the cause, so nobody needs the events dump to read it
        print("\n" + "\n".join(failure))
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
    from harnesswalk import packs

    if packs.find(settings, pack) is None:  # any of packs.roots, not only datasets/
        print(f"no dataset pack {pack!r} under {settings.get('packs.roots', ['datasets'])}",
              file=sys.stderr)  # fmt: skip
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


def _packs(args: argparse.Namespace) -> int:
    """`packs list`, `packs validate [pack]` (exit 1 on any issue), `packs ingest [pack]`."""
    from harnesswalk import packs

    roots = packs.pack_roots(_settings(args))  # packs.roots, so a profile can add its own
    try:
        everything = list(packs.discover(roots=roots).values())
        chosen = [packs.get_pack(args.pack, roots=roots)] if args.pack else everything
    except packs.PackError as exc:
        print(f"harnesswalk packs: {exc}", file=sys.stderr)
        return 2
    if args.action == "list":
        rows = [p.card() for p in chosen]
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
            return 0
        for r in rows:
            feature = r["feature"] or "-"
            print(
                f"{r['id']:<16} {r['tier']:<8} {r['counts']['docs']:>2} docs  "
                f"{len(r['tools'])} tools  "
                f"{r['size_bytes'] / 1024:7.1f} KiB  {r['title']}: {feature}"
            )
        return 0
    if args.action == "validate":
        failed = 0
        for p in chosen:
            issues = packs.validate(p, everything)
            failed += bool(issues)
            print(f"{'ok  ' if not issues else 'FAIL'} {p.id} ({len(issues)} issue(s))")
            for issue in issues:
                print(
                    f"       {issue.check}: {issue.message}"
                    + (f" [{issue.path}]" if issue.path else "")
                )
        print(f"\n{len(chosen) - failed} of {len(chosen)} pack(s) valid")
        return 1 if failed else 0
    for p in chosen:  # ingest
        sub = argparse.Namespace(**{**vars(args), "pack": p.id})
        code = _ingest(sub)
        if code:
            return code
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from harnesswalk.control.http_api import create_app
    from harnesswalk.ops.lease import Lease, LeaseHeld
    from harnesswalk.runtime import Harness
    from harnesswalk.types import ConfigError

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
        try:
            app = create_app(harness)
        except ConfigError as exc:  # public mode without HARNESSWALK_SECRET, for one
            print(f"harnesswalk serve: {exc}", file=sys.stderr)
            return 2
        print(
            f"Harnesswalk {__version__} (spec {SPEC_VERSION}), mode {harness.settings.mode}, "
            f"provider {harness.provider_name}"
        )
        options: dict[str, Any] = {}
        if harness.settings.get("logging.format", "text") == "json":
            # Keep the JSON handler create_app installed; the request line replaces the access
            # log (ops/logs.py).
            options = {"log_config": None, "access_log": False}
        # Ctrl+C must end the process even while a browser holds an event stream open: uvicorn
        # waits for every connection to close (asyncio's wait_closed on 3.12 ignores its own
        # force-quit flag). The signal first tells every stream to end (Harness.request_stop,
        # control/sse.py); a stream still open after this many seconds is cancelled instead.
        grace = float(harness.settings.get("gateway.shutdown_grace_s", 2))
        config = uvicorn.Config(
            app,
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            timeout_graceful_shutdown=grace,
            **options,
        )

        class StoppingServer(uvicorn.Server):
            """Its shutdown signal also ends the harness's event streams."""

            def handle_exit(self, sig: int, frame: Any) -> None:
                harness.request_stop()
                super().handle_exit(sig, frame)

        server = StoppingServer(config)
        # What uvicorn.run does for one process: the signal is raised again once the server
        # has shut down, as a KeyboardInterrupt.
        with contextlib.suppress(KeyboardInterrupt):
            server.run()
        if not server.started:
            return 3  # uvicorn's STARTUP_FAILURE (the port is taken, for one)
    finally:
        lease.release()
    return 0


def _sweep(args: argparse.Namespace) -> int:
    """One TTL sweep over the data directory (the server also runs it in public mode)."""
    from harnesswalk.events import parse_iso
    from harnesswalk.runtime import Harness

    now = None
    if args.now:
        try:
            now = parse_iso(args.now)
        except ValueError:
            print(f"harnesswalk sweep: --now {args.now!r} is not an RFC 3339 time", file=sys.stderr)
            return 2
    harness = Harness(_settings(args, **{"stores.ingest_on_start": False}))
    try:
        done = harness.sweeper.sweep(now)
    finally:
        asyncio.run(harness.aclose())
    if args.json:
        print(json.dumps(done, indent=2))
        return 0
    print(f"swept {len(done['tenants'])} expired tenant(s)")
    for tenant in done["tenants"]:
        print(f"  {tenant}")
    for key, n in sorted(done["rows"].items()):
        print(f"  {key}: {n}")
    return 0


def _eval(args: argparse.Namespace) -> int:
    """Run a golden set in-process and print one line per case. Exit 1 when a case fails."""
    from harnesswalk.ops.eval import EvalRequestError
    from harnesswalk.runtime import Harness

    if args.suite == "golden" and not args.provider:
        args.provider = "fake"  # deterministic answers and hash embeddings, as in CI
    extra: dict[str, Any] = {"dataset": args.pack, "sandbox.level": "inprocess"}
    if args.suite == "model":
        # As a scorecard cell: the model alias's provider only, and hash-vector retrieval, so
        # every model sees the same context.
        if not args.provider and args.model:
            probe = _settings(args)
            args.provider = probe.get(f"models.models.{args.model}.provider", None)
        extra["stores.embeddings.provider"] = "fake"
    settings = _settings(args, **extra)

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
            if c["status"] == "skipped" and c.get("detail"):
                print(f"          {c['detail']}")
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


LADDER = "fake-small,local-2b,local-4b,local-9b,local-e4b,local-8b"


def _guardrail_before(harness: Any, tenant: str, turn_id: str | None) -> str | None:
    """The answer a case turn's output guardrails received (their exit payload), or None."""
    if not turn_id:
        return None
    for event in reversed(harness.tracer.get_turn(tenant, turn_id) or []):
        if event.get("node_id") == "output_guardrails" and event["type"] == "node_exit":
            detail = harness.tracer.get_detail(tenant, event.get("detail_ref") or "") or {}
            return ((detail.get("payload") or {}).get("before") or {}).get("text")
    return None


def _scorecard(args: argparse.Namespace) -> int:
    """`harnesswalk scorecard`: one `model`-suite eval run per (pack, model), sequentially,
    rendered as docs/scorecard.md (and JSON). Exit 2 on an unknown pack or model alias."""
    from pathlib import Path

    from harnesswalk import packs as packs_mod
    from harnesswalk.ops.eval import scorecard as sc
    from harnesswalk.ops.eval.offline import read_tools_of
    from harnesswalk.runtime import Harness

    base = _settings(args)
    roots = packs_mod.pack_roots(base)
    found = sorted(packs_mod.discover(roots=roots), key=lambda p: (p != "_starter", p))
    wanted = found if args.packs == "all" else [p.strip() for p in args.packs.split(",")]
    unknown = [p for p in wanted if p not in found]
    if unknown or not wanted:
        print(f"harnesswalk scorecard: no dataset pack {unknown or args.packs}", file=sys.stderr)
        return 2
    aliases = [a.strip() for a in args.models.split(",") if a.strip()]
    configured = base.get("models.models", {})
    bad = [a for a in aliases if a not in configured or configured[a].get("embedding")]
    if bad or not aliases:
        print(f"harnesswalk scorecard: unknown model alias {bad or args.models}", file=sys.stderr)
        return 2
    ollama_url = (base.get("models.providers.ollama.base_url", "") or "").rstrip("/")
    ollama_names = [
        configured[a].get("name", a) for a in aliases if configured[a].get("provider") == "ollama"
    ]
    overrides = {
        "sandbox.level": "inprocess",
        "stores.embeddings.provider": "fake",  # the same retrieval for every model: hash vectors
        "evals.case_timeout_s": args.case_timeout,
    }
    spent = {"usd": 0.0}
    samples = max(1, int(args.samples))
    carry_models = [a.strip() for a in (args.carry_models or "").split(",") if a.strip()]
    if carry_models and not args.carry:
        print("harnesswalk scorecard: --carry-models needs --carry", file=sys.stderr)
        return 2

    def say(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    def temperature(alias: str) -> tuple[dict[str, Any], str]:
        """Temperature 0 where the provider takes it: the overrides and what the page says."""
        cfg = configured[alias]
        provider = cfg.get("provider")
        if provider == "ollama":
            return {f"models.models.{alias}.params.options.temperature": 0}, "0"
        if provider == "anthropic":
            thinking = ((cfg.get("params") or {}).get("thinking") or {}).get("type", "disabled")
            if thinking == "disabled":
                return {f"models.models.{alias}.params.temperature": 0}, "0"
            return {}, "provider default (thinking on)"
        if provider == "fake":
            return {}, "n/a (scripted)"
        return {}, "provider default"

    async def run() -> dict[str, Any]:
        ollama = await sc.probe_ollama(ollama_url, ollama_names) if ollama_names else None
        reasons = {a: sc.availability(a, configured[a], base, ollama) for a in aliases}

        async def prepare(alias: str) -> str | None:
            cfg = configured[alias]
            if cfg.get("provider") != "ollama":
                return None
            say(f"{alias:<14} loading {cfg.get('name', alias)} on {ollama_url}")
            return await sc.preflight_ollama(
                ollama_url,
                cfg.get("name", alias),
                cfg.get("params"),
                context_tokens=cfg.get("context_tokens"),
            )

        async def finish(alias: str) -> None:
            cfg = configured[alias]
            if cfg.get("provider") == "ollama" and await sc.unload_ollama(
                ollama_url, cfg.get("name", alias)
            ):
                say(f"{alias:<14} unloaded {cfg.get('name', alias)}")

        async def run_cell(pack: str, alias: str) -> dict[str, Any]:
            cfg = configured[alias]
            priced = float(cfg.get("price_out_per_mtok") or 0) > 0
            if priced and spent["usd"] >= args.max_usd:
                return sc.not_run(pack, alias, f"spend cap ${args.max_usd:.2f} reached")
            cell_args = argparse.Namespace(**{**vars(args), "provider": cfg["provider"]})
            extra, _ = temperature(alias)
            settings = _settings(cell_args, **{"dataset": pack, **overrides, **extra})
            harness = Harness(settings)
            runs = []
            try:
                read_tools = read_tools_of(harness.view(pack))
                for _ in range(samples):
                    start = harness.evals.offline.start(
                        args.tenant, pack, "model", model=alias, by="cli:scorecard"
                    )
                    await harness.evals.offline.wait(start["run_id"])
                    summary = harness.evals.store.run(args.tenant, start["run_id"]) or {}
                    cases = harness.evals.store.cases(start["run_id"])
                    runs.append(sc.sample_of(summary, cases, read_tools))
                    spent["usd"] += float((summary.get("cost") or {}).get("usd", 0.0))
            finally:
                await harness.aclose()
            return sc.cell_of(pack, alias, samples=runs)

        async def carried() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict]:
            """The --carry-models cells of an earlier scorecard JSON, re-graded under model
            grade from the stored case results (this data directory's eval store and trace)."""
            old = json.loads(Path(args.carry).read_text(encoding="utf-8"))
            old_models = {m["alias"]: m for m in old.get("models") or []}
            harness = Harness(
                _settings(
                    args,
                    **{"stores.ingest_on_start": False, "stores.embeddings.provider": "fake"},
                )
            )
            cells, regraded_all = [], True
            try:
                for alias in carry_models:
                    for cell in [c for c in old["cells"] if c["model"] == alias]:
                        if cell["pack"] not in wanted:
                            continue
                        if cell["status"] != "ok":
                            cells.append({**cell, "carried": True})
                            continue
                        run_id = cell.get("run_id") or sc.samples_of(cell)[0].get("run_id")
                        stored = harness.evals.store.run_of(run_id) if run_id else None
                        if stored is None:
                            regraded_all = False
                            cells.append({**cell, "carried": True})
                            continue
                        read_tools = read_tools_of(harness.view(cell["pack"]))
                        tenant = stored.get("case_tenant") or f"evalrun-{run_id}"
                        cases = [
                            sc.regrade_case(c, read_tools, before_text=_guardrail_before(
                                harness, tenant, c.get("turn_id")
                            ))
                            for c in harness.evals.store.cases(run_id)
                        ]  # fmt: skip
                        totals = sc.regraded_totals(cases, stored.get("totals") or {})
                        summary = {**stored, "totals": totals}
                        cells.append(sc.cell_of(
                            cell["pack"], alias,
                            samples=[sc.sample_of(summary, cases, read_tools)], carried=True,
                        ))  # fmt: skip
            finally:
                await harness.aclose()
            models = [old_models[a] for a in carry_models if a in old_models]
            note = {"from": old.get("generated_at", "?")[:10], "models": carry_models,
                    "regraded": regraded_all, "path": args.carry,
                    "profiles": old.get("pack_profiles")}  # fmt: skip
            return cells, models, note

        cells = await sc.run_matrix(
            wanted, aliases, run_cell, reasons, prepare=prepare, finish=finish, log=say
        )
        carry_note = None
        carry_meta: list[dict[str, Any]] = []
        if carry_models:
            extra_cells, carry_meta, carry_note = await carried()
            cells += extra_cells
        models = []
        for a in aliases:
            cfg = configured[a]
            info = ((ollama or {}).get("models") or {}).get(cfg.get("name", a)) or {}
            models.append({
                "alias": a, "provider": cfg.get("provider"), "name": cfg.get("name", a),
                "label": cfg.get("label"), "details": info.get("details") or {},
                "digest": info.get("digest"), "params": cfg.get("params"),
                "price_in_per_mtok": cfg.get("price_in_per_mtok", 0),
                "price_out_per_mtok": cfg.get("price_out_per_mtok", 0),
                "temperature": temperature(a)[1],
            })  # fmt: skip
        models += carry_meta
        temps = sorted({m["temperature"] for m in models if m.get("temperature")})
        command = " ".join([
            *(["HARNESSWALK_OLLAMA_URL=" + ollama_url] if ollama_names else []),
            *(["ANTHROPIC_API_KEY=<your key>"]
              if any(configured[a].get("provider") == "anthropic" for a in aliases) else []),
            "uv run harnesswalk scorecard", f"--models {','.join(aliases)}",
            f"--packs {args.packs}", f"--out {args.out}",
            *([f"--json {args.json}"] if args.json else []),
            *([f"--samples {samples}"] if samples > 1 else []),
            *([f"--carry {args.carry} --carry-models {','.join(carry_models)}"]
              if carry_models else []),
        ])  # fmt: skip
        return sc.scorecard_data(
            cells=cells, models=models, packs=wanted, tenant=args.tenant,
            harness_version=__version__, spec_version=SPEC_VERSION, ollama=ollama,
            embeddings="fake (hash vectors, the same for every model)",
            case_timeout_s=args.case_timeout, command=command, json_path=args.json,
            grading="model", samples=samples, carried=carry_note,
            pack_profiles={p: packs_mod.get_pack(p, roots=roots).profile for p in wanted},
            temperature="; ".join(temps) if len(temps) > 1 else (temps[0] if temps else "n/a"),
        )  # fmt: skip

    data = asyncio.run(run())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sc.render_markdown(data), encoding="utf-8")
    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stats = sc.summarize(data)
    for a in [m["alias"] for m in data["models"]]:
        s = stats[a]
        state = f"{s['passed']:g}/{s['scored']}" if s["ran"] else "n/a"
        print(f"{a:<14} {state:<9} ${s['usd']:.4f}  {s['seconds']:.0f}s")
    print(f"wrote {out}" + (f" and {args.json}" if args.json else ""))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="harnesswalk", description="Walk through an AI harness, one node at a time."
    )
    parser.add_argument(
        "--version", action="version", version=f"Harnesswalk {__version__} (spec {SPEC_VERSION})"
    )
    sub = parser.add_subparsers(dest="command")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--provider", choices=["fake", "ollama", "anthropic", "openai_compatible"])
        p.add_argument("--fixture", help="fake provider fixture name")
        p.add_argument(
            "--profile",
            help="a harness profile by name (minimal ... operated) or path, or a config overlay"
            " such as spec/conformance/profile.yaml",
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
    ev.add_argument("--suite", choices=["golden", "judge", "model"], default="golden")
    ev.add_argument(
        "--model", help="judge and model suites: the model alias that answers the cases"
    )
    ev.add_argument("--case", action="append", help="run only this case id (repeatable)")
    ev.add_argument("--tenant", default="dev")
    ev.add_argument("--json", action="store_true", help="print the run detail as JSON")

    sc = sub.add_parser("scorecard", help="the pack x model pass matrix (model suite runs)")
    common(sc)
    sc.add_argument("--packs", default="all", help="all (default) or a comma-separated list")
    sc.add_argument("--models", default=LADDER, help=f"comma-separated aliases ({LADDER})")
    sc.add_argument("--out", default="docs/scorecard.md", help="the Markdown to write")
    sc.add_argument("--json", metavar="PATH", help="also write the scorecard data as JSON")
    sc.add_argument("--tenant", default="scorecard", help="the tenant that owns the runs")
    sc.add_argument("--case-timeout", type=float, default=120.0, help="seconds per case turn")
    sc.add_argument("--samples", type=int, default=1, help="eval runs per cell (mean, min, max)")
    sc.add_argument(
        "--carry", metavar="JSON", help="an earlier scorecard JSON to carry columns from"
    )
    sc.add_argument(
        "--carry-models",
        help="the aliases to carry from --carry, re-graded from the stored case results",
    )
    sc.add_argument(
        "--max-usd", type=float, default=5.0, help="stop priced cells once the runs spent this"
    )

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

    sw = sub.add_parser("sweep", help="delete every expired public tenant's rows")
    common(sw)
    sw.add_argument("--now", help="sweep as if it were this RFC 3339 time (tests, drills)")
    sw.add_argument("--json", action="store_true", help="print the result as JSON")

    events = sub.add_parser("events", help="dump a stored turn as JSON Lines")
    events.add_argument("turn_id")
    events.add_argument("--tenant", default="dev")

    ingest = sub.add_parser("ingest", help="index a pack's docs and load its seeds")
    common(ingest)
    ingest.add_argument("pack", nargs="?", help="dataset pack (default: the configured one)")
    ingest.add_argument("--force", action="store_true", help="index again even if unchanged")

    pk = sub.add_parser("packs", help="list, validate or ingest the dataset packs")
    common(pk)
    pk.add_argument("action", choices=["list", "validate", "ingest"])
    pk.add_argument("pack", nargs="?", help="one pack (default: every pack under datasets/)")
    pk.add_argument("--force", action="store_true", help="ingest: index again even if unchanged")
    pk.add_argument("--json", action="store_true", help="list: print the packs as JSON")

    from harnesswalk.ops.tune.cli import add_parser as add_tune_parser

    add_tune_parser(sub, common)  # 0.2.0: curate, run, compare, promote, rollback, status

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
    if args.command == "scorecard":
        return _scorecard(args)
    if args.command == "packs":
        return _packs(args)
    if args.command == "sweep":
        return _sweep(args)
    if args.command == "tune":
        from harnesswalk.ops.tune.cli import run as run_tune

        return run_tune(args, _settings)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
