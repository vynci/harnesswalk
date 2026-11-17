"""Anthropic adapter: the Messages API over plain HTTP. Key from ANTHROPIC_API_KEY only.

The key is read from the environment for each call and never logged, stored or emitted.
"""

from __future__ import annotations

import json
import os
from typing import Any

from harness.core.events import NodeError
from harness.core.providers.http import post_json

URL = "https://api.anthropic.com/v1/messages"


class AnthropicProvider:
    def __init__(self, settings: Any) -> None:
        cfg = settings.get("models.providers.anthropic", {})
        self.key_env = cfg.get("api_key_env", "ANTHROPIC_API_KEY")
        self.timeout = float(cfg.get("timeout_s", 120))

    def complete(self, turn: Any, request: dict[str, Any]) -> dict[str, Any]:
        key = os.environ.get(self.key_env)
        if not key:
            raise NodeError("provider_error", f"{self.key_env} is not set")
        system, messages = "", []
        for m in request["messages"]:
            if m["role"] == "system":
                system += m["content"]
                continue
            if m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["call_id"], "content": m["content"]}
                role, content = "user", [block]
            elif m.get("tool_calls"):
                role = "assistant"
                content = ([{"type": "text", "text": m["content"]}] if m.get("content") else []) + [
                    {"type": "tool_use", "id": c["call_id"], "name": c["name"], "input": c["arguments"]}
                    for c in m["tool_calls"]]  # fmt: skip
            else:
                role, content = m["role"], [{"type": "text", "text": m.get("content") or "."}]
            if messages and messages[-1]["role"] == role:  # merge same-role neighbours
                messages[-1]["content"] += content
            else:
                messages.append({"role": role, "content": content})
        body: dict[str, Any] = {"model": request["model"], "system": system, "messages": messages,
                                "max_tokens": request["max_output_tokens"], "temperature": 0}  # fmt: skip
        if request["tools"]:
            body["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                              "input_schema": t["parameters"]} for t in request["tools"]]  # fmt: skip
        for key, value in request["params"].items():  # for example thinking: disabled
            body.setdefault(key, value)
        data = post_json(URL, body, {"x-api-key": key, "anthropic-version": "2023-06-01"},
                         self.timeout)  # fmt: skip
        if "content" not in data:
            raise NodeError("provider_error", json.dumps(data.get("error", data))[:300])
        text = "".join(b.get("text", "") for b in data["content"] if b["type"] == "text")
        calls = [{"call_id": b["id"], "name": b["name"], "arguments": b.get("input") or {}}
                 for b in data["content"] if b["type"] == "tool_use"]  # fmt: skip
        usage = data.get("usage") or {}
        return {"text": text or None, "tool_calls": calls,
                "finish_reason": "tool_calls" if calls else
                ("length" if data.get("stop_reason") == "max_tokens" else "stop"),
                "usage": {"input_tokens": usage.get("input_tokens", 0),
                          "output_tokens": usage.get("output_tokens", 0)}}  # fmt: skip
