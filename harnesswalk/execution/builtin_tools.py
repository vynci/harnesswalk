"""Built-in tools: a safe calculator, and `read_skill`, the on-demand tier of procedural memory.

Both are `read` tools. The calculator parses arithmetic with `ast` and evaluates only numbers
and arithmetic operators; it never calls `eval`.
"""

from __future__ import annotations

import ast
import json
import operator
from collections.abc import Callable
from typing import Any

from harnesswalk.execution.registry import ToolSpec

_BINARY: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type, Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def evaluate(expression: str) -> float | int:
    def walk(n: ast.AST) -> float | int:
        if isinstance(n, ast.Constant) and type(n.value) in (int, float):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BINARY:
            left, right = walk(n.left), walk(n.right)
            if isinstance(n.op, ast.Pow) and abs(right) > 64:
                raise ValueError("exponent too large")
            return _BINARY[type(n.op)](left, right)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UNARY:
            return _UNARY[type(n.op)](walk(n.operand))
        raise ValueError(f"unsupported expression element: {type(n).__name__}")

    if len(expression) > 200:
        raise ValueError("expression too long")
    return walk(ast.parse(expression, mode="eval").body)


def calculator(arguments: dict[str, Any]) -> str:
    result = evaluate(arguments["expression"])
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return json.dumps({"expression": arguments["expression"], "result": result})


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
    )
