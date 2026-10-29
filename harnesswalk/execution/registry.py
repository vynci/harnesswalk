"""Tool registry: which tools exist, and how each is described to the model.

Contract: spec/contracts/tool_registry.md. Node: `registry` (expert child of `tools`).

Tools register at startup and the registry is read-only during a turn. Every tool declares an
`action_class` (read, write, destructive, external) and a `timeout_ms`; registration without
them fails. Tools come from two places in P1: built-ins in code (`builtin_tools.py`) and
dataset `tools.yaml` files whose entries carry a lookup `table` (pure functions of the pack).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from harnesswalk.execution.providers import ToolSchema
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

    def schema(self) -> ToolSchema:
        return ToolSchema(self.name, self.description, self.parameters)


def table_tool(entry: dict[str, Any]) -> ToolImpl:
    """A pure lookup: the first required argument is the key into `table`."""
    key_name = entry["parameters"]["required"][0]
    table = entry.get("table", {})
    missing = entry.get("missing", {"error": "not_found"})

    def lookup(arguments: dict[str, Any]) -> str:
        result = table.get(arguments[key_name], missing)
        return json.dumps(result, sort_keys=True, separators=(",", ":"))

    return lookup


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
        if spec.name in self._tools:
            raise ConfigError(f"tool {spec.name} is registered twice")
        self._tools[spec.name] = spec

    def load_yaml(self, path: Path) -> None:
        entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for entry in entries:
            for key in ("name", "description", "parameters", "action_class", "timeout_ms"):
                if key not in entry:
                    raise ConfigError(f"{path}: tool {entry.get('name')} has no {key}")
            if "table" not in entry:
                raise ConfigError(f"{path}: tool {entry['name']} needs a lookup table in P1")
            self.register(
                ToolSpec(
                    name=entry["name"],
                    description=entry["description"],
                    parameters=entry["parameters"],
                    action_class=entry["action_class"],
                    timeout_ms=entry["timeout_ms"],
                    impl=table_tool(entry),
                    idempotent=entry.get("idempotent", True),
                    source="dataset",
                    pure=entry.get("pure", True),
                )
            )

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def list(self, mode: str, allow_tools: list[str] | None) -> list[ToolSpec]:
        """Tools this agent may be offered in this mode, in registration order."""
        out = []
        for spec in self._tools.values():
            if allow_tools is not None and spec.name not in allow_tools:
                continue
            if mode == "public" and not (
                (spec.pure or spec.source == "dataset") and spec.action_class == "read"
            ):
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
