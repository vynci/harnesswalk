"""The node registry, and `node()`, the span every component runs inside.

Every component module calls `register(node_id, __name__)` at import time. An id that is not
in spec/diagram/diagram.json fails right there, at import, so the code cannot drift from the
diagram (spec/harness.md section 6). `GET /v1/diagram` is generated from this registry merged
with the diagram file.

Usage inside a component:

    async with node(ctx, "policy") as span:
        decision = ...
        span.set(decision=decision.decision, rule=decision.rule)

On a normal exit the span closes with `node_exit` (status `done`, or `skipped` after
`span.skip()`), its duration and the summary the component supplied. An exception closes it
with an `error` event and propagates.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from harnesswalk import diagram
from harnesswalk.events import Span, StreamError
from harnesswalk.types import HarnessError, TurnContext


class UnknownNodeError(Exception):
    """A component tried to register under an id the diagram does not have."""


@dataclass(frozen=True)
class Registration:
    node_id: str
    module: str
    stub: bool  # registered so the path is honest, but the real component arrives later


_REGISTRY: dict[str, Registration] = {}

# Every module that registers a node. Importing them all completes the registry.
COMPONENT_MODULES = (
    "harnesswalk.control.gateway",
    "harnesswalk.control.input_guardrails",
    "harnesswalk.control.policy",
    "harnesswalk.control.approval",
    "harnesswalk.control.audit",
    "harnesswalk.context.builder",
    "harnesswalk.context.stores",
    "harnesswalk.context.stores.procedural",
    "harnesswalk.context.stores.session",
    "harnesswalk.context.stores.semantic",
    "harnesswalk.context.stores.episodic",
    "harnesswalk.context.stores.knowledge",
    "harnesswalk.context.memory_writer",
    "harnesswalk.execution.loop",
    "harnesswalk.execution.model_layer",
    "harnesswalk.execution.router",
    "harnesswalk.execution.providers",
    "harnesswalk.execution.tools",
    "harnesswalk.execution.registry",
    "harnesswalk.execution.sandbox",
    "harnesswalk.execution.output_guardrails",
    "harnesswalk.ops.trace",
    "harnesswalk.ops.cost",
    "harnesswalk.ops.eval",
    "harnesswalk.ops.release",
)


def register(node_id: str, module: str, *, stub: bool = False) -> None:
    if node_id not in diagram.nodes_by_id():
        raise UnknownNodeError(
            f"{module} registers node {node_id!r}, which is not in spec/diagram/diagram.json"
        )
    existing = _REGISTRY.get(node_id)
    if existing and existing.module != module:
        raise UnknownNodeError(f"node {node_id!r} is registered twice ({existing.module})")
    _REGISTRY[node_id] = Registration(node_id, module, stub)


def load_components() -> dict[str, Registration]:
    for name in COMPONENT_MODULES:
        importlib.import_module(name)
    return dict(_REGISTRY)


def registered() -> dict[str, Registration]:
    return dict(_REGISTRY)


def implemented(node_id: str) -> bool:
    reg = _REGISTRY.get(node_id)
    return bool(reg and not reg.stub)


@asynccontextmanager
async def node(ctx: TurnContext, node_id: str, **enter_summary: Any) -> AsyncIterator[Span]:
    if node_id not in _REGISTRY:
        raise StreamError(f"node {node_id!r} is not registered by any component")
    span = await ctx.emit.enter(node_id, enter_summary or None)
    try:
        yield span
    except HarnessError as exc:
        if not span.closed:
            ctx.emit.fail(span, exc.code, exc.message)
        raise
    except asyncio.CancelledError:
        if not span.closed:
            ctx.emit.fail(span, "cancelled", "The turn was cancelled.")
        raise
    except Exception as exc:
        if not span.closed:
            ctx.emit.fail(span, "internal", f"{type(exc).__name__}: {exc}")
        raise
    else:
        if not span.closed:
            ctx.emit.exit(span)


def fail_span(ctx: TurnContext, span: Span, exc: BaseException) -> None:
    """Close a span opened outside `node()` (a span restored after a restart) on an exception,
    with the same codes `node()` uses."""
    if span.closed:
        return
    if isinstance(exc, HarnessError):
        ctx.emit.fail(span, exc.code, exc.message)
    elif isinstance(exc, asyncio.CancelledError):
        ctx.emit.fail(span, "cancelled", "The turn was cancelled.")
    else:
        ctx.emit.fail(span, "internal", f"{type(exc).__name__}: {exc}")


def generated_diagram(
    view: str | None = None, disabled: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """diagram.json plus `implemented`, `stub` and `disabled` per node (http_api.md)."""
    doc = copy.deepcopy(diagram.diagram())
    keep_views = {"simple"} if view == "simple" else None
    for n in doc["nodes"]:
        reg = _REGISTRY.get(n["id"])
        n["implemented"] = bool(reg and not reg.stub)
        n["stub"] = bool(reg and reg.stub)
        n["disabled"] = n["id"] in disabled
    if keep_views:
        doc["nodes"] = [n for n in doc["nodes"] if n["view"] in keep_views]
        doc["edges"] = [e for e in doc["edges"] if e["view"] in keep_views]
    return doc
