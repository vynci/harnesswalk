"""Ollama adapter: POST /api/chat with tools, stream off. The local default for people."""

from __future__ import annotations

from typing import Any

import httpx

from harnesswalk.execution.providers import (
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)
from harnesswalk.execution.providers._http import get_ok, post_json
from harnesswalk.types import Message, ToolCall


def to_ollama_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        item: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            item["tool_calls"] = [
                {"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls
            ]
        if m.role == "tool" and m.name:
            item["tool_name"] = m.name
        out.append(item)
    return out


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.transport = transport

    @classmethod
    def from_settings(cls, settings: Any) -> OllamaProvider:
        # `base_url` already holds `base_url_env`'s value when that is set (config/loader.py).
        cfg = settings.get("models.providers.ollama")
        return cls(cfg["base_url"], float(cfg.get("timeout_s", 120)))

    async def health(self) -> bool:
        return await get_ok(f"{self.base_url}/api/tags", transport=self.transport)

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": to_ollama_messages(request.messages),
            "stream": False,
            "options": {"num_predict": request.max_output_tokens},
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
        if request.output_schema:
            body["format"] = request.output_schema
        for name, value in (request.params or {}).items():  # models.<alias>.params
            if name == "options" and isinstance(value, dict):
                body["options"] = {**value, **body["options"]}
            else:
                body.setdefault(name, value)  # for example `think: false`
        data = await post_json(
            f"{self.base_url}/api/chat",
            body,
            timeout_s=self.timeout_s,
            transport=self.transport,
        )
        message = data.get("message", {})
        calls = [
            ToolCall(
                call_id=c.get("id", ""),
                name=c["function"]["name"],
                arguments=c["function"].get("arguments") or {},
            )
            for c in message.get("tool_calls") or []
        ]
        finish = (
            "tool_calls" if calls else ("length" if data.get("done_reason") == "length" else "stop")
        )
        return ProviderResponse(
            text=message.get("content") or None,
            tool_calls=calls,
            finish_reason=finish,
            usage=ProviderUsage(data.get("prompt_eval_count", 0), data.get("eval_count", 0)),
        )
