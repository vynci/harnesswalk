"""OpenAI-compatible adapter: POST {base_url}/chat/completions.

Covers vLLM on the homelab, llama.cpp server, LM Studio, and the many hosts that speak this
shape. `base_url` includes the version prefix, for example http://gpu-box:8000/v1.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from harnesswalk.execution.providers import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)
from harnesswalk.execution.providers._http import get_ok, post_json
from harnesswalk.types import Message, ToolCall


def to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        item: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            item["tool_calls"] = [
                {
                    "id": c.call_id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in m.tool_calls
            ]
        if m.role == "tool":
            item["tool_call_id"] = m.tool_call_id
        out.append(item)
    return out


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": parsed}


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_s = timeout_s
        self.transport = transport

    @classmethod
    def from_settings(cls, settings: Any) -> OpenAICompatibleProvider:
        cfg = settings.get("models.providers.openai_compatible")
        base = cfg.get("base_url") or os.environ.get(cfg.get("base_url_env", ""), "")
        return cls(base, cfg.get("api_key_env", "OPENAI_API_KEY"), float(cfg.get("timeout_s", 60)))

    async def health(self) -> bool:
        key = os.environ.get(self.api_key_env)
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return await get_ok(f"{self.base_url}/models", headers=headers, transport=self.transport)

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        if not self.base_url:
            raise ProviderError("provider_error", "openai_compatible has no base_url")
        body: dict[str, Any] = {
            "model": request.model,
            "messages": to_openai_messages(request.messages),
            "max_tokens": request.max_output_tokens,
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
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": request.output_schema},
            }
        headers = {}
        key = request.api_key or os.environ.get(self.api_key_env)
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data = await post_json(
            f"{self.base_url}/chat/completions",
            body,
            headers=headers,
            timeout_s=self.timeout_s,
            transport=self.transport,
        )
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})
        calls = [
            ToolCall(
                call_id=c.get("id", ""),
                name=c["function"]["name"],
                arguments=_arguments(c["function"].get("arguments")),
            )
            for c in message.get("tool_calls") or []
        ]
        reason = choice.get("finish_reason")
        finish = "tool_calls" if calls else ("length" if reason == "length" else "stop")
        usage = data.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        return ProviderResponse(
            text=message.get("content") or None,
            tool_calls=calls,
            finish_reason=finish,
            usage=ProviderUsage(
                usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), cached or 0
            ),
        )
