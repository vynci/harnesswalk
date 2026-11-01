---
node: model_layer
try:
  pack: _starter
  fixture: tool_call
  prompt: "Where is order A-1001?"
---

# Model layer

> One interface for every model: routing by rules, providers, fallback, cost.

## What it does

The loop never talks to a model provider directly. It hands the model layer a request (the
messages, the tools on offer, which step this is, how risky the turn has become) and gets back
a response with text or tool calls, a finish reason and token usage. Behind that one call the
model layer does four jobs. It asks the router which model alias fits this step. It calls the
provider behind that alias through an adapter, so a local Ollama model, a hosted API and the
scripted fake provider all look the same. If the call fails in a way that is worth retrying,
it falls back to the next alias the router allowed. And it prices the usage from the per-token
prices in `config/models.yaml`, so every call carries a cost.

This is the seam that makes a harness portable. Change `HARNESSWALK_PROVIDER` from `fake` to
`ollama` and nothing else in the harness changes: the same guardrails, context, loop, tools and
trace run against a different model. It is also where model failures become ordinary, typed
errors (`provider_timeout`, `provider_error`) instead of exceptions from an HTTP library,
which is what lets the rest of the turn end cleanly.

## What breaks without it

The node cannot be switched off, but you can take its model away and watch how it fails.

Pack `_starter`, fixture `tool_call`, prompt `Where is order A-1001?`. On the fake provider the
model layer reports `provider=fake model=fake-small finish_reason=tool_calls fallback=False`
for step 1, then `finish_reason=stop` for step 2. Now run the same prompt with no provider
flag, which selects Ollama, and point Ollama at a port where nothing listens:

```sh
HARNESSWALK_OLLAMA_URL=http://127.0.0.1:9 \
  uv run harnesswalk turn "Where is order A-1001?"
```

The router picks `rule=default model=local-8b`, the providers span ends in `error`, the model
layer and the loop close with `error=provider_error`, and there is no fallback because no other
provider is configured. What the learner sees is still a finished turn: the output guardrails
run, the gateway replies "Something went wrong while answering. The trace has the details.",
the trace is persisted and the exit is `error`. In the web app the Providers node turns red
and its Events tab holds the error code.

Without the layer, that connection error would surface wherever the loop happened to be, with
an HTTP library's exception and no record of which model was tried. With it, a second
configured provider would have answered and the summary would say `fallback=True`.

## The code walk

[`ModelLayer.complete`](../harnesswalk/execution/model_layer.py#ModelLayer.complete) is the
call the loop makes. It opens the node's span, runs the router as a child span with a
`RouteRequest` (step, step kind, risk, modality, mode, cost pressure and the dev-mode model
hint), then calls `_call_with_fallback`. The exit summary names the provider and model that
answered, whether it was a fallback, and `price_known`, which is false when an alias has no
prices in config. With `trace.capture_content` on, the full request and response go to the
trace as a detail record.

[`ModelLayer._call_with_fallback`](../harnesswalk/execution/model_layer.py#ModelLayer._call_with_fallback)
walks the routed model and then its alternatives. Each attempt is its own `providers` span with
an `attempt` number, so a fallback is visible as an error span followed by a done one. Read the
`except ProviderError` branch closely: a non-retryable error stops at once, and a failure on a
caller's own key (bring your own key) or a refused request does not mark the provider unhealthy,
so one tenant's bad key cannot take the provider down for everyone. Tool calls without an id get
`call_<n>`, numbered across the whole turn.

[`price`](../harnesswalk/execution/model_layer.py#price) turns tokens into dollars from
`price_in_per_mtok` and `price_out_per_mtok`. The fake and local models cost zero, which is why
receipts read `$0.000000` in the demos.

[`ModelProvider`](../harnesswalk/execution/providers/__init__.py#ModelProvider) is the whole
adapter contract: a `name` and an async `complete`. [`ProviderError`](../harnesswalk/execution/providers/__init__.py#ProviderError)
carries `retryable` and `client_error`, the two facts the fallback logic needs.
[`build_providers`](../harnesswalk/execution/providers/__init__.py#build_providers) builds only
the configured providers; hosted ones need `enabled: true` as well as a key, so a key that
happens to be in your shell never turns into a paid fallback by accident.

## Try this

1. **Put a price on it.** Run the `tool_call` prompt with
   `--set models.models.fake-small.price_in_per_mtok=3 --set models.models.fake-small.price_out_per_mtok=15`.
   Expected: the same walk, and the last line reads `520 in / 45 out tokens, $0.002235`.
2. **Read a fallback.** Run `uv run pytest tests/unit/test_providers_http.py -k fallback -v`
   and read `test_fallback_is_visible`. Expected: it passes, and it shows the event shape of a
   fallback: a `providers` error, then a `providers` exit, and `fallback: true` on the model
   layer's summary with the hosted model's cost.
3. **Add an alias.** Add `fake-big: { provider: fake, context_tokens: 32000, price_in_per_mtok: 1,
   price_out_per_mtok: 2, tools: true }` under `models` in `config/models.yaml`, put it first in
   the `default` rule's `use` list, and run the `tool_call` prompt with `--provider fake`.
   Expected: `model=fake-big` on both model layer spans and a nonzero cost. Undo the edit after.

## Check yourself

### 1. What lets the harness switch from the fake provider to Ollama without changing the loop?

- [ ] The router rewrites the loop's messages.
- [x] Every provider sits behind the same `complete` call shape in the model layer.
- [ ] The fake provider imitates Ollama's HTTP API.

Answer: the loop only knows `ModelLayer.complete`; the adapters translate that one shape into
each provider's API.

### 2. Why does a failure on a caller's own API key not mark the provider unhealthy?

- [x] Because one tenant's bad key would otherwise take the provider away from every tenant.
- [ ] Because a bad key is never retryable.
- [ ] Because health is checked only at start-up.

Answer: a refused request says something about the request, not about the provider, so it must
not feed the shared health signal the router reads.

### 3. With Ollama down and no other provider configured, how does the turn end?

- [ ] The request hangs until the HTTP timeout of the browser.
- [ ] The loop retries forever.
- [x] The model layer raises a typed error, and the gateway still replies with a notice and closes the trace.

Answer: the providers span records `provider_error`, the loop closes in error, and the gateway
answers with the error notice, exit `error`.

## Further reading

- [Anthropic documentation, Messages API](https://docs.anthropic.com/en/api/messages)
- [Ollama documentation, API](https://github.com/ollama/ollama/blob/main/docs/api.md)
- [`spec/contracts/model_provider.md`](../spec/contracts/model_provider.md) and [`spec/contracts/router.md`](../spec/contracts/router.md)
