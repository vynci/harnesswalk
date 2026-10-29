"""Built-in tools: a safe calculator, and `read_skill`, the on-demand tier of procedural memory.

Both are `read` tools. The calculator parses arithmetic with `ast` and evaluates only numbers
and arithmetic operators; it never calls `eval`. Its function lives in `tool_impls.py` so the
subprocess sandbox can run it; `read_skill` reads harness memory and stays in-process.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from harnesswalk.execution.registry import ToolSpec
from harnesswalk.execution.tool_impls import calculator, evaluate

__all__ = ["CALCULATOR", "calculator", "evaluate", "read_skill_tool"]


CALCULATOR = ToolSpec(
    name="calculator",
    description="Evaluate an arithmetic expression with + - * / // % ** and parentheses.",
    parameters={
        "type": "object",
        "additionalProperties": False,
        "required": ["expression"],
        "properties": {"expression": {"type": "string", "maxLength": 200}},
    },
    action_class="read",
    timeout_ms=1000,
    impl=calculator,
    pure=True,
    entrypoint="harnesswalk.execution.tool_impls:calculator",
)


def read_skill_tool(skill_body: Callable[[str], str | None], names: list[str]) -> ToolSpec:
    """`read_skill` returns a skill body by exact name; the index is already in context."""

    def read_skill(arguments: dict[str, Any]) -> str:
        body = skill_body(arguments["name"])
        if body is None:
            return json.dumps({"error": "unknown_skill", "known": names})
        return body

    return ToolSpec(
        name="read_skill",
        description="Read the full instructions of a skill listed in the skills index, by name.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "required": ["name"],
            "properties": {"name": {"type": "string", "maxLength": 64}},
        },
        action_class="read",
        timeout_ms=1000,
        impl=read_skill,
        pure=True,
        sandbox="inprocess",  # it reads the harness's procedural store, so it stays in-process
    )
