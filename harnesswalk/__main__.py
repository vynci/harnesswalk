"""The ``harnesswalk`` command (also ``python -m harnesswalk``).

harnesswalk serve                         start the HTTP API (uvicorn)
harnesswalk turn "<prompt>" --provider fake --fixture plain_answer
                                          run one turn in-process and print the walk
harnesswalk events <turn_id>              dump a stored turn as JSON Lines
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from harnesswalk import SPEC_VERSION, __version__

SUMMARY_KEYS = (
    "phase", "action", "tool", "decision", "rule", "used_tokens", "compacted", "exit_reason",
    "steps", "step", "provider", "model", "finish_reason", "tool_calls", "fallback",
    "tools_enabled", "outcome", "executed", "reply_chars", "events", "persisted", "approval_id",
    "record_id", "prompt_version", "skills_indexed", "turns", "hits", "tokens", "input_tokens",
    "output_tokens",
)  # fmt: skip


def _settings(args: argparse.Namespace):
    from harnesswalk.config.loader import load_settings

    return load_settings(
        mode=getattr(args, "mode", None),
        provider=getattr(args, "provider", None),
        fixture=getattr(args, "fixture", None),
        profile=getattr(args, "profile", None),
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
    from harnesswalk.control.gateway import TurnRequest
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

    def on_event(event: dict[str, Any]) -> None:
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
    print(
        f"\nexit {end['summary'].get('exit')}, status {end['status']}, "
        f"{end['summary'].get('steps', 0)} step(s), "
        f"{cost.get('input_tokens', 0)} in / {cost.get('output_tokens', 0)} out tokens, "
        f"${cost.get('usd', 0):.6f}, {end['duration_ms']:.1f} ms"
    )
    print(f"{len(events)} events stored; dump them with: harnesswalk events {state.ctx.turn_id}")
    return 0 if end["status"] == "done" else 1


def _events(args: argparse.Namespace) -> int:
    from harnesswalk.ops.trace import Tracer

    settings = _settings(args)
    tracer = Tracer(str(settings.resolve(settings.get("trace.sqlite_path", "data/trace.db"))))
    events = tracer.get_turn(args.tenant, args.turn_id)
    if not events:
        print(f"no stored turn {args.turn_id} for tenant {args.tenant}", file=sys.stderr)
        return 1
    for event in events:
        print(json.dumps(event, separators=(",", ":"), ensure_ascii=False))
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    from harnesswalk.control.http_api import create_app
    from harnesswalk.runtime import Harness

    harness = Harness(_settings(args))
    print(
        f"harnesswalk {__version__} (spec {SPEC_VERSION}), mode {harness.settings.mode}, "
        f"provider {harness.provider_name}"
    )
    uvicorn.run(create_app(harness), host=args.host, port=args.port, log_level="info")
    return 0


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

    serve = sub.add_parser("serve", help="start the HTTP API")
    common(serve)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    turn = sub.add_parser("turn", help="run one turn in-process and print the walk")
    common(turn)
    turn.add_argument("prompt")
    turn.add_argument("--session", help="continue this session id")
    turn.add_argument("--tenant", default="dev")
    turn.add_argument("--step", action="store_true", help="step mode: pause at every node")
    turn.add_argument("--approval", choices=["ask", "approve", "deny"], default="ask")
    turn.add_argument("--disable", action="append", help="disable a node (repeatable)")
    turn.add_argument("--json", action="store_true", help="print the events instead of the walk")

    events = sub.add_parser("events", help="dump a stored turn as JSON Lines")
    events.add_argument("turn_id")
    events.add_argument("--tenant", default="dev")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    if args.command == "turn":
        return asyncio.run(_run_turn(args))
    if args.command == "events":
        return _events(args)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
