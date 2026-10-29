"""The HTTP adapters shape requests and parse responses correctly, without any network."""

import json

import httpx
import pytest

from harnesswalk.execution.providers import ProviderError, ProviderRequest, ToolSchema
from harnesswalk.execution.providers.anthropic import AnthropicProvider
from harnesswalk.execution.providers.ollama import OllamaProvider
from harnesswalk.execution.providers.openai_compatible import OpenAICompatibleProvider
from harnesswalk.types import Message, ToolCall

TOOL = ToolSchema(
    "lookup_order",
    "Look up an order.",
    {"type": "object", "properties": {"order_id": {"type": "string"}}},
)
HISTORY = [
    Message("system", "You are helpful."),
    Message("user", "Where is A-1001?"),
    Message(
        "assistant", "", tool_calls=[ToolCall("call_1", "lookup_order", {"order_id": "A-1001"})]
    ),
    Message("tool", '{"status":"shipped"}', name="lookup_order", tool_call_id="call_1"),
]


def transport(seen: list, reply: dict, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=reply)

    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_ollama() -> None:
    seen: list[httpx.Request] = []
    reply = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "lookup_order", "arguments": {"order_id": "A-1002"}}}
            ],
        },
        "prompt_eval_count": 50,
        "eval_count": 7,
        "done_reason": "stop",
    }
    p = OllamaProvider("http://ollama:11434/", transport=transport(seen, reply))
    r = await p.complete(ProviderRequest("llama3.1:8b", HISTORY, [TOOL], 256))
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "http://ollama:11434/api/chat"
    assert body["stream"] is False and body["options"]["num_predict"] == 256
    assert body["tools"][0]["function"]["name"] == "lookup_order"
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == {"order_id": "A-1001"}
    assert r.finish_reason == "tool_calls" and r.tool_calls[0].arguments == {"order_id": "A-1002"}
    assert r.tool_calls[0].call_id == "" and (r.usage.input_tokens, r.usage.output_tokens) == (
        50,
        7,
    )


@pytest.mark.anyio
async def test_openai_compatible(monkeypatch) -> None:
    monkeypatch.setenv("TEST_COMPAT_KEY", "sk-test-not-real")
    seen: list[httpx.Request] = []
    reply = {
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": "Shipped."}}
        ],
        "usage": {
            "prompt_tokens": 80,
            "completion_tokens": 3,
            "prompt_tokens_details": {"cached_tokens": 64},
        },
    }
    p = OpenAICompatibleProvider(
        "http://vllm:8000/v1", "TEST_COMPAT_KEY", transport=transport(seen, reply)
    )
    r = await p.complete(ProviderRequest("qwen", HISTORY, [TOOL]))
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "http://vllm:8000/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-test-not-real"
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"order_id": "A-1001"}'
    assert body["messages"][3] == {
        "role": "tool",
        "content": '{"status":"shipped"}',
        "tool_call_id": "call_1",
    }
    assert (r.text, r.finish_reason, r.usage.cached_tokens) == ("Shipped.", "stop", 64)


@pytest.mark.anyio
async def test_anthropic_messages_shape() -> None:
    seen: list[httpx.Request] = []
    reply = {
        "content": [
            {"type": "text", "text": "Checking."},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "lookup_order",
                "input": {"order_id": "A-1001"},
            },
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }
    p = AnthropicProvider("https://api.example.test", transport=transport(seen, reply))
    byo = "byo-key-not-real"
    r = await p.complete(ProviderRequest("claude-haiku-4-5", HISTORY, [TOOL], 512, api_key=byo))
    body = json.loads(seen[0].content)
    assert str(seen[0].url) == "https://api.example.test/v1/messages"
    assert (
        seen[0].headers["x-api-key"] == byo and seen[0].headers["anthropic-version"] == "2023-06-01"
    )
    assert body["system"] == "You are helpful." and body["max_tokens"] == 512
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["messages"][1]["content"][0] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "lookup_order",
        "input": {"order_id": "A-1001"},
    }
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert body["tools"][0]["input_schema"] == TOOL.parameters
    assert (
        r.text == "Checking."
        and r.tool_calls[0].call_id == "toolu_1"
        and r.finish_reason == "tool_calls"
    )


@pytest.mark.anyio
async def test_errors_never_carry_the_key(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError):
        await AnthropicProvider().complete(ProviderRequest("m", HISTORY, []))  # no key at all
    seen: list = []
    p = AnthropicProvider(
        "https://api.example.test", transport=transport(seen, {"error": "bad key secret-xyz"}, 401)
    )
    with pytest.raises(ProviderError) as exc:
        await p.complete(ProviderRequest("m", HISTORY, [], api_key="secret-xyz"))
    assert exc.value.code == "provider_error" and "secret-xyz" not in exc.value.message

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    p = OllamaProvider("http://ollama:11434", transport=httpx.MockTransport(slow))
    with pytest.raises(ProviderError) as exc:
        await p.complete(ProviderRequest("m", HISTORY, []))
    assert exc.value.code == "provider_timeout"


@pytest.mark.anyio
async def test_fallback_is_visible(tmp_path) -> None:
    """A retryable failure on the first model falls back to the next allowed one."""
    from harnesswalk.execution.model_layer import ModelLayer, ModelRequest
    from harnesswalk.execution.providers import ProviderResponse, ProviderUsage
    from harnesswalk.execution.router import Router
    from tests.unit.support import make_ctx

    class Down:
        name = "ollama"

        async def complete(self, request):
            raise ProviderError("provider_error", "connection refused")

    class Up:
        name = "anthropic"

        async def complete(self, request):
            return ProviderResponse("hello", [], "stop", ProviderUsage(1000, 100))

    models = {
        "local": {"provider": "ollama", "price_in_per_mtok": 0, "price_out_per_mtok": 0},
        "hosted": {"provider": "anthropic", "price_in_per_mtok": 1.0, "price_out_per_mtok": 5.0},
    }
    router = Router(
        [{"id": "default", "when": {}, "use": ["local", "hosted"]}], models, lambda p: True
    )
    layer = ModelLayer({"ollama": Down(), "anthropic": Up()}, router, models)
    ctx, got = make_ctx()
    response = await layer.complete(ctx, ModelRequest([Message("user", "hi")], [], step=1))
    assert response.fallback and response.model == "hosted"
    assert response.usage.usd == pytest.approx(0.0015)
    exit_ = [e for e in got.events if e["type"] == "node_exit" and e["node_id"] == "model_layer"][0]
    assert exit_["summary"]["fallback"] is True and exit_["cost"]["usd"] == pytest.approx(0.0015)
    attempts = [
        e
        for e in got.events
        if e.get("node_id") == "providers" and e["type"] in ("node_exit", "error")
    ]
    assert [e["type"] for e in attempts] == ["error", "node_exit"]
