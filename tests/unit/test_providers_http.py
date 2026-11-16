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
    assert (r.usage.gen_ms, r.usage.prompt_ms) == (0.0, 0.0)  # no durations in this reply


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
async def test_an_error_status_carries_the_apis_own_message(monkeypatch) -> None:
    """A bare "returned HTTP 400" hid why hosted-haiku failed every call (2026-11-09)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-site-key-123456")
    reply = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "temperature is deprecated for this model",
        },
    }
    p = AnthropicProvider("https://api.example.test", transport=transport([], reply, 400))
    with pytest.raises(ProviderError) as exc:
        await p.complete(ProviderRequest("claude-haiku-5-5", HISTORY, []))
    assert exc.value.message == (
        "https://api.example.test/v1/messages returned HTTP 400:"
        " temperature is deprecated for this model"
    )
    assert exc.value.client_error is True

    # The key never comes back, even when the body echoes it; long reasons are capped.
    echo = {"error": {"message": "bad key sk-ant-site-key-123456 " + "x" * 500}}
    p = AnthropicProvider("https://api.example.test", transport=transport([], echo, 401))
    with pytest.raises(ProviderError) as exc:
        await p.complete(ProviderRequest("m", HISTORY, []))
    assert "site-key" not in exc.value.message and "[redacted]" in exc.value.message
    assert exc.value.message.endswith("...") and len(exc.value.message) < 300

    # Ollama's shape is a plain string; OpenAI-compatible servers use error.message.
    p = OllamaProvider("http://ollama:11434", transport=transport([], {"error": "no model"}, 404))
    with pytest.raises(ProviderError) as exc:
        await p.complete(ProviderRequest("m", HISTORY, []))
    assert exc.value.message.endswith("returned HTTP 404: no model")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-abcdef")
    p = OpenAICompatibleProvider(
        "http://compat/v1",
        transport=transport([], {"error": {"message": "overloaded, key sk-openai-abcdef"}}, 503),
    )
    with pytest.raises(ProviderError) as exc:
        await p.complete(ProviderRequest("m", HISTORY, []))
    assert exc.value.message.endswith("HTTP 503: overloaded, key [redacted]")
    assert exc.value.retryable and not exc.value.client_error
    p = OllamaProvider(
        "http://ollama:11434",
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="<html>oops</html>")),
    )
    with pytest.raises(ProviderError) as exc:
        await p.complete(ProviderRequest("m", HISTORY, []))
    assert exc.value.message.endswith("returned HTTP 500")  # no JSON reason: the status only


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


@pytest.mark.anyio
async def test_anthropic_prompt_cache_is_opt_in_and_priced() -> None:
    """P6: `params.prompt_cache` marks the tools and the system prompt for caching, never goes
    to the API as a field, and the usage counts cached and written tokens inside input_tokens,
    which the model layer prices with the alias's multipliers."""
    from harnesswalk.execution.model_layer import price

    seen: list[httpx.Request] = []
    reply = {
        "content": [{"type": "text", "text": "Done."}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 3000,
                  "cache_creation_input_tokens": 500},
    }  # fmt: skip
    p = AnthropicProvider("https://api.example.test", transport=transport(seen, reply))
    params = {"prompt_cache": True, "thinking": {"type": "disabled"}}
    r = await p.complete(ProviderRequest("m", HISTORY, [TOOL], 64, params=params, api_key="k"))
    body = json.loads(seen[0].content)
    assert "prompt_cache" not in body and body["thinking"] == {"type": "disabled"}
    assert body["system"] == [
        {"type": "text", "text": "You are helpful.", "cache_control": {"type": "ephemeral"}}
    ]
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert (r.usage.input_tokens, r.usage.cached_tokens, r.usage.cache_write_tokens) == (
        3600, 3000, 500)  # fmt: skip
    haiku = {"price_in_per_mtok": 0.10, "price_out_per_mtok": 0.50, "cache_read_multiplier": 0.1}
    usd, _ = price(haiku, 3600, 10, 3000, 500)
    assert usd == pytest.approx((100 + 300 + 625) * 0.10 / 1e6 + 10 * 0.50 / 1e6)
    # without a configured read discount a cached token costs what any input token costs
    assert price({"price_in_per_mtok": 1, "price_out_per_mtok": 1}, 1000, 0, 1000)[0] == 0.001
    assert price(haiku, 1000, 0)[0] == pytest.approx(0.0001)  # no cache: unchanged
    seen.clear()
    await p.complete(ProviderRequest("m", HISTORY, [TOOL], 64, api_key="k"))
    body = json.loads(seen[0].content)
    assert body["system"] == "You are helpful." and "cache_control" not in body["tools"][-1]
