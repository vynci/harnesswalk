"""Text edits of `config/models.yaml` that promote and rollback make (spec/contracts/tuning.md).

The file is an artifact a learner reads and diffs, so it is edited as text, not re-dumped:
an alias is one flow-mapping line under `models:`, the canary one line under `router:`. Every
edit is parsed back and checked before it is used.
"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

ALIAS_LINE = re.compile(r"^  ([A-Za-z0-9][A-Za-z0-9_.-]*):")


def _flow(value: Any) -> str:
    """A one-line YAML flow mapping (JSON is valid YAML)."""
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def _block(lines: list[str], key: str) -> tuple[int, int]:
    """The line range [start, end) of a top-level mapping `key:` (start is the key line)."""
    start = next((i for i, line in enumerate(lines) if line.rstrip() == f"{key}:"), None)
    if start is None:
        raise ValueError(f"models.yaml has no top-level {key}: block")
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][:1] == " "):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return start, end


def with_alias(text: str, alias: str, config: dict[str, Any], note: str) -> str:
    """Add (or replace) `alias` under `models:` as one line, with a comment above it."""
    lines = text.splitlines()
    start, end = _block(lines, "models")
    for i in range(start + 1, end):
        m = ALIAS_LINE.match(lines[i])
        if m and m.group(1) == alias:
            lines[i] = f"  {alias}: {_flow(config)}"
            return _checked("\n".join(lines) + "\n", alias=alias)
    insert = [f"  # {note}", f"  {alias}: {_flow(config)}"]
    lines[end:end] = insert
    return _checked("\n".join(lines) + "\n", alias=alias)


def with_canary(text: str, canary: dict[str, Any] | None) -> str:
    """Set `router.canary` to `canary` (or remove it with None), one line right under
    `router:`."""
    lines = text.splitlines()
    start, end = _block(lines, "router")
    at = next((i for i in range(start + 1, end) if lines[i].startswith("  canary:")), None)
    if canary is None:
        if at is not None:
            del lines[at]
            if at - 1 > start and lines[at - 1].strip().startswith("# canary"):
                del lines[at - 1]
        return _checked("\n".join(lines) + "\n")
    line = f"  canary: {_flow(canary)}"
    if at is not None:
        lines[at] = line
    else:
        lines[start + 1 : start + 1] = [
            "  # canary (0.2.0): set by `harnesswalk tune promote`, see spec/contracts/router.md",
            line,
        ]
    return _checked("\n".join(lines) + "\n")


def _checked(text: str, alias: str | None = None) -> str:
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        raise ValueError("the edited models.yaml does not parse to a models mapping")
    if alias is not None and alias not in data["models"]:
        raise ValueError(f"the edited models.yaml has no alias {alias}")
    return text


def canary_of(text: str) -> dict[str, Any] | None:
    data = yaml.safe_load(text) or {}
    router = data.get("router") or {}
    return router.get("canary")
