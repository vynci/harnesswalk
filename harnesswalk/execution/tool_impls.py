"""Tool functions that can run in a sandbox subprocess. Standard library only.

The subprocess sandbox imports a tool by entrypoint (`module:function`), so the functions it
runs must not import the harness. Each takes the call's arguments, plus `data` when the tool's
spec carries some (a lookup table), and returns the observation text.
"""

from __future__ import annotations

import ast
import json
import operator
from collections.abc import Callable
from typing import Any

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
    """Arithmetic only: numbers and the operators above, parsed with `ast`, never `eval`."""

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


def table_lookup(arguments: dict[str, Any], data: dict[str, Any]) -> str:
    """A pure lookup: `data.key` names the argument that indexes `data.table`."""
    row = data["table"].get(arguments[data["key"]], data.get("missing", {"error": "not_found"}))
    return json.dumps(row, sort_keys=True, separators=(",", ":"))
