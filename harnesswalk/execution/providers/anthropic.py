"""Anthropic adapter: the Messages API over plain httpx, no SDK.

The system prompt travels in `system`, tool calls are `tool_use` blocks on the assistant turn,
and tool results are `tool_result` blocks on the next user turn (consecutive results merge
into one user message, as the API requires). Content is read by block type, so `thinking`
blocks are skipped; the harness does not replay them, so models in config/models.yaml set
`params.thinking` to a mode that needs no replay (`disabled` for Haiku 5.5). The health probe
is `GET /v1/models`.
"""

from __future__ import annotations

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

API_VERSION = "2023-06-01"


def to_anthropic(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            blocks += [
                {"type": "tool_use", "id": c.call_id, "name": c.name, "input": c.arguments}
                for c in m.tool_calls
            ]
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m.role, "content": m.content})
    return system, out


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        base_url: str = "https://api.anthropic.com",
        api_key_env: str = "ANTHROPIC_API_KEY",
        timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout_s = timeout_s
        self.transport = transport

    @classmethod
    def from_settings(cls, settings: Any) -> AnthropicProvider:
        cfg = settings.get("models.providers.anthropic")
        return cls(
            cfg.get("base_url", "https://api.anthropic.com"),
            cfg.get("api_key_env", "ANTHROPIC_API_KEY"),
            float(cfg.get("timeout_s", 60)),
        )

    def site_key_present(self) -> bool:
        """Whether the server has its own key. Without one only BYO-key turns can use it (P6)."""
        return bool(os.environ.get(self.api_key_env))

    async def health(self) -> bool:
        """GET /v1/models with the key: cheap, and proves the key and the network."""
        key = os.environ.get(self.api_key_env)
        if not key:
            return False
        headers = {"x-api-key": key, "anthropic-version": API_VERSION}
        return await get_ok(
            f"{self.base_url}/v1/models?limit=1", headers=headers, transport=self.transport
        )

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        key = request.api_key or os.environ.get(self.api_key_env)
        if not key:
            raise ProviderError("provider_error", f"no key: set {self.api_key_env} or bring one")
        system, messages = to_anthropic(request.messages)
        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "messages": messages,
        }
        if system:
            body["system"] = system
        if request.tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in request.tools
            ]
        for name, value in (request.params or {}).items():  # models.<alias>.params
            body.setdefault(name, value)
        headers = {"x-api-key": key, "anthropic-version": API_VERSION}
        data = await post_json(
            f"{self.base_url}/v1/messages",
            body,
            headers=headers,
            timeout_s=self.timeout_s,
            transport=self.transport,
        )
        texts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
        calls = [
            ToolCall(call_id=b.get("id", ""), name=b["name"], arguments=b.get("input") or {})
            for b in data.get("content", [])
            if b.get("type") == "tool_use"
        ]
        stop = data.get("stop_reason")
        finish = "tool_calls" if calls else ("length" if stop == "max_tokens" else "stop")
        usage = data.get("usage") or {}
        return ProviderResponse(
            text="".join(texts) or None,
            tool_calls=calls,
            finish_reason=finish,
            usage=ProviderUsage(
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                usage.get("cache_read_input_tokens", 0),
            ),
        )
