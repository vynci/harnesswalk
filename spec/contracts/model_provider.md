# Contract: Model provider and model layer

Nodes: `model_layer` (execution plane, kind `model`) and its expert child `providers`. Routing
is in `router.md`.

## Purpose

One call shape in front of every model. The loop asks the model layer for a completion; the
model layer routes, calls a provider adapter, falls back on failure, and returns text and or tool
calls with usage and cost. Swapping local for hosted changes configuration, not code.

## Methods

```
interface ModelLayer {
  complete(ctx: TurnContext, request: ModelRequest) -> async ModelResponse
}

interface ModelProvider {
  name: string                                   // "fake", "ollama", "anthropic", "openai_compatible"
  models() -> [ModelInfo]
  complete(request: ProviderRequest) -> async ProviderResponse
  embed?(texts: [string], model: string) -> async [vector]
}

record ModelRequest {
  messages: [Message]
  tools: [ToolSpec]                              // empty means tools disabled
  step: int
  risk: "low" | "high"
  modality: "text" | "image"
  output_schema?: JSONSchema
  max_output_tokens: int
}

record ProviderRequest  { model: string, messages: [Message], tools: [ToolSchema], output_schema?: JSONSchema, max_output_tokens: int, api_key?: secret }
record ProviderResponse { text: string | null, tool_calls: [ToolCall], finish_reason: "stop" | "tool_calls" | "length", usage: { input_tokens: int, output_tokens: int, cached_tokens?: int } }
record ModelResponse    { text: string | null, tool_calls: [ToolCall], finish_reason: string, provider: string, model: string, fallback: bool, usage: Usage }
record ModelInfo        { name: string, context_tokens: int, price_in_per_mtok: number, price_out_per_mtok: number, modalities: [string], tools: bool }
```

## Inputs and outputs

- In: a `ModelRequest` from the `loop`; a `Route` from the `router`; prices and provider settings from `config/models.yaml`.
- Out: a `ModelResponse` (text and or tool calls, usage, cost) back to the `loop`.

## Invariants

- The model layer MUST ask the router for the model before every call.
- On a provider error, timeout or rate limit, it MUST try the router's next allowed option
  (`fallback: true` in the summary) and MUST NOT try a model the rules do not allow.
- `usage.usd` MUST be computed from `config/models.yaml` prices; unknown price means `usd: 0`
  and `summary.price_known: false`.
- Tool call ids returned by providers that lack them MUST be assigned `call_<n>` in order within
  the turn.
- A BYO key travels in `ProviderRequest.api_key` for one call and MUST NOT be logged or emitted.
- Provider adapters MUST NOT import an agent framework. One file per provider.
- Structured output: when `output_schema` is set, the layer requests native JSON mode where the
  provider has it; validation of the result is the output guardrail's job.

## The `fake` provider (required in every implementation)

The `fake` provider replays scripted responses from a fixture file. It makes conformance runs
and CI deterministic, offline and free.

Fixture format (YAML, files in `spec/conformance/fixtures/`):

```yaml
fixture: tool_call                 # MUST equal the file stem
description: One read-only tool call, then an answer.
model: fake-small                  # model name reported in summaries (default fake-small)
turns:                             # one entry per chat turn, in order, per session
  - responses:                     # one entry per model call in that turn, in order
      - tool_calls:
          - { name: lookup_order, arguments: { order_id: A-1001 } }
        usage: { input_tokens: 905, output_tokens: 24 }
      - text: Order A-1001 shipped on 24 October and is due to arrive on 30 October.
        usage: { input_tokens: 1012, output_tokens: 21 }
        expect: { tools_enabled: true, last_message_role: tool }
```

Semantics:

- The harness is started with one fixture (`models.providers.fake.fixture`). Each session keeps
  its own cursor: turn `n` of a session reads `turns[n]`, and the `k`th model call in that turn
  reads `responses[k]`.
- A response has `text`, `tool_calls`, or both. `finish_reason` is `tool_calls` when
  `tool_calls` is non-empty, else `stop`, unless the response sets `finish_reason`.
- `call_id` is taken from the fixture when given, else assigned `call_<n>` (1-based, per turn).
- `usage` defaults to zero tokens. `usd` is always 0.
- `expect` is optional. Keys: `tools_enabled` (bool), `last_message_role`,
  `last_message_contains` (substring), and since P4 `system_contains` (a substring of the first
  `system` message, the system prompt and skills index the context builder assembled, so a
  fixture can pin which prompt or skill version a turn ran on). A mismatch fails the call with
  `fixture_mismatch`.
- `latency_ms` (optional, default 0) delays the response, for step-mode and timeout tests.
- More calls than scripted fails with `fixture_exhausted`. Fewer calls is not an error, but the
  conformance runner reports unused responses.
- The fake provider MUST NOT read the network, the clock (except for `latency_ms`) or any
  randomness. Same fixture and same inputs give the same outputs.
- `embed` on the fake provider returns a deterministic hash embedding (`fake_embed`, 64 dims,
  derived from sha256 of the text) so knowledge search works offline.

## Other providers

| Provider | Transport | Notes |
|---|---|---|
| `ollama` | Ollama HTTP API, `models.providers.ollama.base_url` | Local default for people; also embeddings |
| `anthropic` | Messages API, key from env `ANTHROPIC_API_KEY` or BYO | Hosted, pay per use |
| `openai_compatible` | Chat Completions shape, `base_url` + key | vLLM on the homelab, other hosts |

## Failure behaviour

| Condition | Result |
|---|---|
| Provider error, fallback succeeds | `node_exit` with `fallback: true` |
| All allowed options fail | `model_layer` closes with `error` `provider_error` or `provider_timeout` |
| Fake fixture exhausted or mismatched | `error` `fixture_exhausted` or `fixture_mismatch` |

## Events

`node_enter` (summary `step`) and `node_exit` with `cost` on `model_layer`; optional expert
children `router` and `providers` inside its span. Edges: `loop_to_model_layer`,
`model_layer_to_loop`, `router_to_providers`.

## Configuration keys (`config/models.yaml`)

```yaml
providers:
  fake:   { fixture: plain_answer }
  ollama: { base_url: http://ollama:11434, timeout_s: 60 }
  anthropic: { api_key_env: ANTHROPIC_API_KEY }
  openai_compatible: { base_url: "", api_key_env: OPENAI_API_KEY }
models:
  fake-small:   { provider: fake,   context_tokens: 32000, price_in_per_mtok: 0, price_out_per_mtok: 0, tools: true }
  local-8b:     { provider: ollama, name: "<8B instruct, Q4>", context_tokens: 32000, price_in_per_mtok: 0, price_out_per_mtok: 0, tools: true }
  local-embed:  { provider: ollama, name: nomic-embed-text, embedding: true }
```

| Key | Meaning |
|---|---|
| `providers.<name>.*` | Adapter settings; keys only by env var name, never by value |
| `models.<alias>` | Alias used by router rules, with provider, real model name, prices, limits |
| `model_layer.timeout_s` | Per call, default 60 |
| `model_layer.max_output_tokens` | Default 1024 |
| `model_layer.cache` | `none` or `prompt_prefix` (use provider prefix caching when available) |

## P6 additions

- **`cycle` fixtures.** A fixture MAY set `cycle: true` (spec/conformance/fixture.schema.json):
  turn `n` reads `turns[n mod len(turns)]`, and a call past the turn's last response answers
  with the last scripted text and no tool calls, so the fixture never fails with
  `fixture_exhausted`. Public mode's `fake-small` runs `config/fixtures/public_demo.yaml`
  (`public.fake_fixture`), which says it is scripted and costs nothing. `config/fixtures` is the
  last fixture directory searched.
- **Labels.** A model entry MAY carry `label`, the name a UI shows (`GET /v1/capabilities`
  `public.models[].label`).
- **Anthropic.** Two user messages in a row (the quoted retrieved data, then the new input,
  context_builder.md P6) are sent as one user message with two text blocks. In public mode a
  provider built without the site's key answers only BYO-key calls and is not probed.

## 0.2.0: fake styles (scripted students)

Additive, for the tuning loop (`tuning.md`). A `fake` alias MAY carry `params.fake_style`; the
fake provider then answers from the fixture as always, but changes a response the style names,
deterministically:

| Style | Changes | Into |
|---|---|---|
| `ask_first` | A response that calls a tool whose action class is `write`, `destructive` or `external` | A text-only reply asking the user to approve first, naming the tool; no tool call. The approval node never decides |
| `over_call` | The first call of a turn (no tool result yet) when it answers without a tool and tools are offered | A call to the first offered tool with empty arguments (a tool when none is needed) |

`ToolSchema` gains `action_class` (default `read`) so the provider can tell; adapters never
send it. Any other style is ignored. The conformance profile and `config/models.yaml` define
`fake-student` (`ask_first`); the fake trainer's `forgets` outcome serves an `over_call` alias.
