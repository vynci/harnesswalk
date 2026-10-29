"""Providers: one adapter per model API, all behind the same small interface.

Contract: spec/contracts/model_provider.md. Node: `providers` (expert child of `model_layer`).

An adapter turns a `ProviderRequest` into one HTTP call (or, for `fake`, a fixture lookup) and
returns a `ProviderResponse` with text and or tool calls, a finish reason and token usage.
Adapters never compute dollars (the model layer does, from config/models.yaml), never log keys,
and never import an agent framework. One file per provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from harnesswalk.nodes import register
from harnesswalk.types import HarnessError, Message, ToolCall

register("providers", __name__)


@dataclass
class ToolSchema:
    """What the model sees of a tool."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class TurnRef:
    """Which turn a call belongs to. Only the fake provider reads it (per-session cursors)."""

    tenant_id: str
    session_id: str
    turn_id: str
    turn_index: int


@dataclass
class ProviderRequest:
    model: str  # the provider's own model name
    messages: list[Message]
    tools: list[ToolSchema]
    max_output_tokens: int = 1024
    output_schema: dict[str, Any] | None = None
    api_key: str | None = field(default=None, repr=False)  # BYO key: one call, never logged
    turn: TurnRef | None = None


@dataclass
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class ProviderResponse:
    text: str | None
    tool_calls: list[ToolCall]
    finish_reason: str  # stop | tool_calls | length
    usage: ProviderUsage


class ProviderError(HarnessError):
    """A provider call failed. `retryable` errors let the model layer try a fallback."""

    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(code, message)
        self.retryable = retryable


class ModelProvider(Protocol):
    name: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...


def build_providers(settings: Any) -> dict[str, ModelProvider]:
    """Instantiate every configured provider. Unconfigured ones are simply absent."""
    from harnesswalk.execution.providers.anthropic import AnthropicProvider
    from harnesswalk.execution.providers.fake import FakeProvider
    from harnesswalk.execution.providers.ollama import OllamaProvider
    from harnesswalk.execution.providers.openai_compatible import OpenAICompatibleProvider

    factories = {
        "fake": FakeProvider.from_settings,
        "ollama": OllamaProvider.from_settings,
        "anthropic": AnthropicProvider.from_settings,
        "openai_compatible": OpenAICompatibleProvider.from_settings,
    }
    return {
        name: factory(settings)
        for name, factory in factories.items()
        if settings.provider_configured(name)
    }
