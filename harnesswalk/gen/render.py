"""The template preprocessor: line directives and `@@name@@` substitutions, nothing more.

A template is a plain file that stays valid Python (or YAML, Markdown) to read. Lines

    #% if <expr>      #% elif <expr>      #% else      #% endif

(any indentation, nested) keep or drop the lines between them; the directive lines are
removed. `<expr>` is a Python expression over the render context: `has("policy")`,
`"ollama" in providers`, `release == "chain"`, `storage`, `transport`, `profile`. Then each
`@@name@@` is replaced by `str(context[name])`. Option B of 03-AGENT-BUILDER (template-first):
a seam is a branch in a template, never a deletion from the reference.
"""

from __future__ import annotations

import re
from typing import Any

DIRECTIVE = re.compile(r"^\s*#%\s*(if|elif|else|endif)\b\s*(.*)$")
SUBST = re.compile(r"@@([a-z_][a-z0-9_]*)@@")


class TemplateError(ValueError):
    pass


def evaluate(expr: str, context: dict[str, Any]) -> bool:
    return bool(eval(expr, {"__builtins__": {"set": set, "len": len}}, context))  # noqa: S307


def render(text: str, context: dict[str, Any], name: str = "<template>") -> str:
    out: list[str] = []
    stack: list[tuple[bool, bool]] = []  # (this branch is on, some branch was taken)
    for n, line in enumerate(text.splitlines(keepends=True), start=1):
        m = DIRECTIVE.match(line)
        if not m:
            if all(on for on, _ in stack):
                out.append(line)
            continue
        kind, expr = m.group(1), m.group(2).strip()
        try:
            if kind == "if":
                on = evaluate(expr, context)
                stack.append((on, on))
            elif kind == "elif":
                _, taken = stack[-1]
                on = not taken and evaluate(expr, context)
                stack[-1] = (on, taken or on)
            elif kind == "else":
                stack[-1] = (not stack[-1][1], True)
            else:
                stack.pop()
        except (IndexError, SyntaxError, NameError) as exc:
            raise TemplateError(f"{name}:{n}: bad directive {line.strip()!r}: {exc}") from exc
    if stack:
        raise TemplateError(f"{name}: unclosed #% if")

    def sub(m: re.Match[str]) -> str:
        if m.group(1) not in context:
            raise TemplateError(f"{name}: no value for @@{m.group(1)}@@")
        return str(context[m.group(1)])

    return SUBST.sub(sub, "".join(out))
