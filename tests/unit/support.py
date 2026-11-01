"""Helpers for unit tests: settings, harnesses, contexts and fixtures, all offline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from harnesswalk.config.loader import Settings, load_settings
from harnesswalk.control.gateway import TurnRequest
from harnesswalk.events import Breakpoints, EventBus, TurnEmitter, new_trace_id
from harnesswalk.nodes import load_components
from harnesswalk.runtime import Harness
from harnesswalk.types import Identity, TurnContext
from tests.spec.support import ROOT

PROFILE = ROOT / "spec" / "conformance" / "profile.yaml"
TEST_SECRET = "test-secret"

load_components()


def write_fixture(directory: Path, name: str, turns: list[list[dict[str, Any]]]) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    doc = {"fixture": name, "turns": [{"responses": r} for r in turns]}
    (directory / f"{name}.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return name


def settings(
    tmp_path: Path,
    fixture: str = "plain_answer",
    *,
    profile: Path | None = PROFILE,
    overrides: dict[str, Any] | None = None,
    fixtures: Path | None = None,
    mode: str = "dev",
) -> Settings:
    # Unit tests run tools in-process (fast, deterministic); test_sandbox.py and the
    # conformance runner exercise the subprocess level.
    extra = {"sandbox.level": "inprocess", **(overrides or {})}
    if fixtures is not None:
        extra["models.providers.fake.fixture_dir"] = str(fixtures)
    return load_settings(
        ROOT,
        mode=mode,
        provider="fake",
        fixture=fixture,
        profile=profile,
        overrides=extra,
        data_dir=tmp_path / "data",
        env={"HARNESSWALK_SECRET": TEST_SECRET},  # public mode's cookie secret (P6)
    )


def harness(tmp_path: Path, fixture: str = "plain_answer", **kwargs: Any) -> Harness:
    return Harness(settings(tmp_path, fixture, **kwargs))


async def run_turn(
    h: Harness,
    message: str,
    *,
    tenant: str = "t1",
    session_id: str | None = None,
    approval: str | None = None,
    step: bool = False,
    disabled: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Run one turn to the end, deciding approvals and stepping breakpoints automatically."""
    import asyncio

    loop = asyncio.get_running_loop()
    holder: dict[str, str] = {}

    def on_event(event: dict[str, Any]) -> None:
        if event["type"] == "approval_requested" and approval:
            loop.call_soon(
                h.approvals.decide,
                event["summary"]["approval_id"],
                tenant,
                "user:test",
                approval,
                None,
            )
        if event["type"] == "node_paused" and event["summary"]["reason"] == "breakpoint":
            loop.call_soon(lambda: h.gateway.resume(tenant, holder["turn_id"], "step"))

    unsubscribe = h.bus.subscribe(on_event)
    try:
        state = await h.gateway.start_turn(
            TurnRequest(
                message=message,
                identity=Identity(tenant, None, ["user"], anonymous=False),
                session_id=session_id,
                step_mode=step,
                disabled_nodes=disabled or [],
            )
        )
        holder["turn_id"] = state.ctx.turn_id
        await h.gateway.wait(state.ctx.turn_id, timeout=10)
    finally:
        unsubscribe()
    return h.tracer.get_turn(tenant, state.ctx.turn_id)


class Collector:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def make_ctx(
    tenant: str = "t1", *, step_mode: bool = False, bus: EventBus | None = None
) -> tuple[TurnContext, Collector]:
    bus = bus or EventBus()
    collector = Collector()
    bus.subscribe(collector)
    emitter = TurnEmitter(
        trace_id=new_trace_id(),
        turn_id="turn_test",
        session_id="sess_test",
        tenant_id=tenant,
        bus=bus,
        breakpoints=Breakpoints(step_mode),
    )
    ctx = TurnContext(
        tenant_id=tenant,
        session_id="sess_test",
        turn_id="turn_test",
        trace_id=emitter.ids["trace_id"],
        user_id=None,
        mode="dev",
        emit=emitter,
        identity=Identity(tenant, None, ["user"], anonymous=False),
    )
    emitter.turn_start({"kind": "chat"})
    return ctx, collector


def path_of(events: list[dict[str, Any]]) -> list[str]:
    from tests.spec.support import simple_path

    return simple_path(events)


def exits(events: list[dict[str, Any]], node_id: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "node_exit" and e.get("node_id") == node_id]


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)
