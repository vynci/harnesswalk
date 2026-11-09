"""The fake provider: replays scripted fixtures, offline and deterministic.

Contract: spec/contracts/model_provider.md, "The `fake` provider". Required in every
implementation. Turn `n` of a session reads `turns[n]` (a per-turn fixture counts only the
session's turns on that fixture), the `k`th call of a turn reads `responses[k]`, and a
response's `expect` checks what the harness actually sent the model.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import yaml

from harness.core.events import NodeError


class FakeProvider:
    def __init__(self, settings: Any) -> None:
        self.dirs: list[Path] = [d for p in settings.packs.values() for d in p.fixture_dirs]

    def find(self, name: str) -> Path | None:
        for d in self.dirs:
            for ext in (".yaml", ".jsonl"):
                if (d / f"{name}{ext}").exists():
                    return d / f"{name}{ext}"
        return None

    def names(self) -> list[str]:
        return sorted({p.stem for d in self.dirs if d.is_dir() for p in d.iterdir()
                       if p.suffix in (".yaml", ".jsonl")})  # fmt: skip

    def load(self, name: str) -> list[dict[str, Any]]:
        path = self.find(name)
        if path is None:
            raise NodeError("fixture_exhausted", f"no fixture named {name}")
        if path.suffix == ".jsonl":  # a pack fixture: one line per turn
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return yaml.safe_load(path.read_text())["turns"]

    def complete(self, turn: Any, request: dict[str, Any]) -> dict[str, Any]:
        turns = self.load(turn.data["fixture"])
        n, k = turn.data["fixture_turn"], turn.data.get("calls", 0)
        if n >= len(turns) or k >= len(turns[n]["responses"]):
            raise NodeError("fixture_exhausted", f"fixture {turn.data['fixture']} has no"
                            f" response {k} in turn {n}")  # fmt: skip
        turn.data["calls"] = k + 1
        response = turns[n]["responses"][k]
        self.check(response.get("expect") or {}, request)
        time.sleep((response.get("latency_ms") or 0) / 1000)
        return {"text": response.get("text"), "usage": response.get("usage") or {},
                "tool_calls": [dict(c) for c in response.get("tool_calls") or []],
                "finish_reason": response.get("finish_reason")}  # fmt: skip

    @staticmethod
    def check(expect: dict[str, Any], request: dict[str, Any]) -> None:
        messages = request["messages"]
        last = messages[-1] if messages else {}
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        problems = []
        if "tools_enabled" in expect and expect["tools_enabled"] != bool(request["tools"]):
            problems.append(f"tools_enabled is {bool(request['tools'])}")
        if "last_message_role" in expect and last.get("role") != expect["last_message_role"]:
            problems.append(f"last message role is {last.get('role')}")
        if expect.get("last_message_contains", "") not in (last.get("content") or ""):
            problems.append(f"last message lacks {expect['last_message_contains']!r}")
        if expect.get("system_contains", "") not in system:
            problems.append(f"system prompt lacks {expect['system_contains']!r}")
        if problems:
            raise NodeError("fixture_mismatch", "; ".join(problems))
