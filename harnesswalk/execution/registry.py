"""Tool registry: which tools exist, and how each is described to the model.

Contract: spec/contracts/tool_registry.md. Node: `registry` (expert child of `tools`).

Tools register at startup and the registry is read-only during a turn. Every tool declares an
`action_class` (read, write, destructive, external, memory.write) and a `timeout_ms`;
registration without them fails. Tools come from built-ins in code (`builtin_tools.py`) and
dataset `tools.yaml` files. A `tools.yaml` entry carries either a lookup `table` (a pure
function of the pack) or an `entrypoint` (`module:function`, or `file.py:function` relative to
the yaml file). Optional keys (P3): `sandbox` (`inprocess` or `subprocess`, overriding
`sandbox.level`) and `audit.before` (a read tool whose result is the audit record's `before`).
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from harnesswalk.execution.providers import ToolSchema
from harnesswalk.execution.tool_impls import table_lookup
from harnesswalk.nodes import node, register
from harnesswalk.types import ACTION_CLASSES, ConfigError, TurnContext

register("registry", __name__)

NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ToolImpl = Callable[[dict[str, Any]], Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    action_class: str
    timeout_ms: int
    impl: ToolImpl = field(repr=False)
    idempotent: bool = True
    source: str = "local"  # local | mcp | dataset
    pure: bool = False
    sandbox: str | None = None  # inprocess | subprocess; None means the sandbox.level default
    entrypoint: str | None = None  # how a subprocess imports it: module:function or file.py:fn
    entry_data: dict[str, Any] | None = field(default=None, repr=False)  # passed as `data`
    audit_before: str | None = None  # read tool that snapshots the state before a write

    def schema(self) -> ToolSchema:
        return ToolSchema(self.name, self.description, self.parameters)


TABLE_ENTRYPOINT = "harnesswalk.execution.tool_impls:table_lookup"


def table_data(entry: dict[str, Any]) -> dict[str, Any]:
    """What the table entrypoint needs: the key argument, the table and the miss result."""
    return {
        "key": entry["parameters"]["required"][0],
        "table": entry.get("table", {}),
        "missing": entry.get("missing", {"error": "not_found"}),
    }


def table_tool(entry: dict[str, Any]) -> ToolImpl:
    """A pure lookup: the first required argument is the key into `table`."""
    data = table_data(entry)

    def lookup(arguments: dict[str, Any]) -> str:
        return table_lookup(arguments, data)

    return lookup


def load_entrypoint(entrypoint: str, data: dict[str, Any] | None = None) -> ToolImpl:
    """Import `module:function` or `path/file.py:function` (for the in-process level)."""
    target, _, func = entrypoint.rpartition(":")
    if not target or not func:
        raise ConfigError(f"entrypoint {entrypoint!r} must be module:function or file.py:function")
    if target.endswith(".py"):
        spec = importlib.util.spec_from_file_location(Path(target).stem, target)
        if spec is None or spec.loader is None:
            raise ConfigError(f"entrypoint file {target} cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    fn = getattr(module, func, None)
    if not callable(fn):
        raise ConfigError(f"entrypoint {entrypoint!r} is not a function")
    if data is None:
        return fn
    return lambda arguments: fn(arguments, data)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not NAME.match(spec.name):
            raise ConfigError(f"tool name {spec.name!r} must match {NAME.pattern}")
        if spec.action_class not in ACTION_CLASSES:
            raise ConfigError(f"tool {spec.name} needs an action_class in {ACTION_CLASSES}")
        if not isinstance(spec.timeout_ms, int) or spec.timeout_ms <= 0:
            raise ConfigError(f"tool {spec.name} needs a positive timeout_ms")
        jsonschema.Draft202012Validator.check_schema(spec.parameters)
        if spec.action_class == "memory.write" and (spec.name, spec.source) != (
            "remember",
            "local",
        ):
            # The class skips the tool-call budget and the tool_executed audit (the writer
            # records the write), so only the built-in `remember`, which only proposes, has it.
            raise ConfigError(f"tool {spec.name}: memory.write is reserved for remember")
        if spec.name in self._tools:
            raise ConfigError(f"tool {spec.name} is registered twice")
        self._tools[spec.name] = spec

    def load_yaml(self, path: Path) -> None:
        entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for entry in entries:
            for key in ("name", "description", "parameters", "action_class", "timeout_ms"):
                if key not in entry:
                    raise ConfigError(f"{path}: tool {entry.get('name')} has no {key}")
            audit_before = (entry.get("audit") or {}).get("before")
            if entry.get("sandbox") not in (None, "inprocess", "subprocess"):
                raise ConfigError(f"{path}: tool {entry['name']} has an unknown sandbox level")
            common = {
                "name": entry["name"],
                "description": entry["description"],
                "parameters": entry["parameters"],
                "action_class": entry["action_class"],
                "timeout_ms": entry["timeout_ms"],
                "idempotent": entry.get("idempotent", True),
                "source": "dataset",
                "sandbox": entry.get("sandbox"),
                "audit_before": audit_before,
            }
            if "table" in entry:
                data = table_data(entry)
                spec = ToolSpec(
                    **common,
                    impl=table_tool(entry),
                    pure=entry.get("pure", True),
                    entrypoint=TABLE_ENTRYPOINT,
                    entry_data=data,
                )
            elif "entrypoint" in entry:
                entrypoint = entry["entrypoint"]
                target, _, func = entrypoint.rpartition(":")
                if target.endswith(".py") and not Path(target).is_absolute():
                    entrypoint = f"{(path.parent / target).resolve()}:{func}"
                data = entry.get("data")
                spec = ToolSpec(
                    **common,
                    impl=load_entrypoint(entrypoint, data),
                    pure=entry.get("pure", False),
                    entrypoint=entrypoint,
                    entry_data=data,
                )
            else:
                raise ConfigError(f"{path}: tool {entry['name']} needs a table or an entrypoint")
            self.register(spec)
        for spec in self._tools.values():
            if spec.audit_before and spec.audit_before not in self._tools:
                raise ConfigError(f"tool {spec.name}: audit.before names unknown tool")

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def list(self, mode: str, allow_tools: list[str] | None) -> list[ToolSpec]:
        """Tools this agent may be offered in this mode, in registration order.

        Public mode offers pure read tools only: they run in-process there (sandbox.md).
        """
        out = []
        for spec in self._tools.values():
            if allow_tools is not None and spec.name not in allow_tools:
                continue
            if mode == "public" and not (spec.pure and spec.action_class == "read"):
                continue
            out.append(spec)
        return out


async def run_registry(ctx: TurnContext, registry: ToolRegistry, name: str) -> ToolSpec | None:
    async with node(ctx, "registry") as span:
        spec = registry.get(name)
        span.set(tool=name, found=spec is not None)
        if spec:
            span.set(action_class=spec.action_class, timeout_ms=spec.timeout_ms)
    return spec
