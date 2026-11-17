"""Ollama adapter: POST /api/chat with tools, stream off. The local model, yours to serve.

Base URL from `models.providers.ollama.base_url`, or HARNESSWALK_OLLAMA_URL when set.
"""

from __future__ import annotations

import os
from typing import Any

from harness.core.providers.http import post_json


class OllamaProvider:
    def __init__(self, settings: Any) -> None:
        cfg = settings.get("models.providers.ollama", {})
        self.base_url = (os.environ.get("HARNESSWALK_OLLAMA_URL") or cfg.get("base_url")
                         or "http://127.0.0.1:11434").rstrip("/")  # fmt: skip
        self.timeout = float(cfg.get("timeout_s", 180))

    def complete(self, turn: Any, request: dict[str, Any]) -> dict[str, Any]:
        messages = []
        for m in request["messages"]:
            item: dict[str, Any] = {"role": m["role"], "content": m.get("content") or ""}
            if m.get("tool_calls"):
                item["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["arguments"]}}
                                      for c in m["tool_calls"]]  # fmt: skip
            if m["role"] == "tool":
                item["tool_name"] = m.get("name", "")
            messages.append(item)
        body: dict[str, Any] = {"model": request["model"], "messages": messages, "stream": False,
                                "options": {"num_predict": request["max_output_tokens"],
                                            "temperature": 0}}  # fmt: skip
        if request.get("context_tokens"):
            body["options"]["num_ctx"] = int(request["context_tokens"])
        if request["tools"]:
            body["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""),
                "parameters": t["parameters"]}} for t in request["tools"]]  # fmt: skip
        if request.get("output_schema"):
            body["format"] = request["output_schema"]
        for key, value in request["params"].items():  # for example think: false
            body.setdefault(key, value)
        data = post_json(f"{self.base_url}/api/chat", body, {}, self.timeout)
        msg = data.get("message") or {}
        calls = [{"name": c["function"]["name"], "arguments": c["function"].get("arguments") or {}}
                 for c in msg.get("tool_calls") or []]  # fmt: skip
        return {"text": msg.get("content") or None, "tool_calls": calls,
                "finish_reason": "tool_calls" if calls else
                ("length" if data.get("done_reason") == "length" else "stop"),
                "usage": {"input_tokens": data.get("prompt_eval_count", 0),
                          "output_tokens": data.get("eval_count", 0)}}  # fmt: skip
