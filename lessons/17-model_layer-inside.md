---
node: model_layer
covers: [router, providers]
try:
  pack: _starter
  fixture: cancel_order
  prompt: "Please cancel order A-1002."
---

# Inside the model layer: router and providers

> Rules choose the model, adapters speak each provider's API, and a fixture can stand in for any of them.

## What it does

Chapter 6 treated the model layer as one call. In the Expert view it opens into two parts, and
every model call shows both: a `router` span that picks the model, then one `providers` span per
attempt.

### Router

The router turns a request into a choice of model alias, using rules from `config/models.yaml`
evaluated in order, first match wins. A rule's `when` can name the step (`first`, `after_tool`,
`summarize`, `judge` and so on), the risk (`high` once the turn has called any tool that is not a
read), the modality, the mode and cost pressure (`low` once a turn has spent most of its dollar
budget). Its `use` list names the aliases allowed, in order; the first one whose provider is
configured and healthy is the model, and the rest are the only fallbacks the model layer may
try. A dev-mode model hint can reorder that list, never extend it: a model may only be chosen
from what the rules allow. The list must end with a `default` rule with an empty `when`, and the
config loader refuses to start without one.

### Providers

A provider adapter turns the harness's one request shape into a provider's API and back: Ollama
locally, Anthropic and any OpenAI-compatible endpoint hosted, and `fake` for tests and lessons.
The fake provider replays a fixture: turn `n` of a session reads `turns[n]`, and the `k`th model
call of that turn reads `responses[k]`. A response can carry an `expect` block that checks the
request (the last message's role and text, whether tools were offered), so a fixture fails loudly
with `fixture_mismatch` when the harness sends the model something other than what the fixture
was written for. That is how the golden set notices a skill edit on a model that never reads it.
Health is tracked per provider: a retryable failure marks it unhealthy so the router skips it,
and a probe brings it back.

## What breaks without it

Pack `_starter`, fixture `cancel_order`, prompt `Please cancel order A-1002.` Run it with
`--approval approve` and look at the two router spans:

```text
router   done   rule=default    model=fake-small
router   done   rule=high_risk  model=fake-small
```

The first call is an ordinary step. The second comes after a destructive tool call, so the loop
marks the turn `risk=high` and the router switches to the `high_risk` rule, whose list starts with
`hosted-sonnet`; on a fake-only setup the first configured alias is still `fake-small`, but on a
real deployment this is where a stronger model takes over consequential steps. Without a router
every step would go to one model, chosen once, and nothing would keep a public visitor's turn on a
local model (`--mode public` shows `rule=public`).

The hint rule is easiest to see with a second alias. Add
`fake-big: { provider: fake, context_tokens: 32000, price_in_per_mtok: 1, price_out_per_mtok: 2, tools: true }`
under `models` and append `fake-big` to the end of the `default` rule's `use` list only. Run the
same prompt with `--model fake-big`: step 1 is `rule=default model=fake-big`, step 2 is
`rule=high_risk model=fake-small`. The hint won where the rule allowed it and lost where it did
not. Undo the edit afterwards.

For the providers, run `What are your support hours?` twice in one session (`--session sess_ex`)
with fixture `plain_answer`. The second turn's `providers` span ends in
`error=fixture_exhausted`: "fixture plain_answer has no response 0 for turn 1". The fake provider
refuses to invent an answer it was not given, which is what makes every lesson in this course
reproducible.

## The code walk

[`Router.route`](../harnesswalk/execution/router.py#Router.route) is the whole decision: loop
over rules, skip the ones that do not match, filter the `use` list with `_usable`, move the hint
to the front if it is in the list, and return the model and the alternatives. A rule with no
usable alias is a non-retryable error that names the rule.

[`Router.matches`](../harnesswalk/execution/router.py#Router.matches) compares `when` with the
request. Notice `step` matches either a step number or a step kind. [`Router._usable`](../harnesswalk/execution/router.py#Router._usable)
drops aliases whose provider is not configured or healthy, priced over `max_price_out_per_mtok`,
or without the needed modality. [`Router.restricted`](../harnesswalk/execution/router.py#Router.restricted)
is how a per-turn provider choice narrows the same rules.

[`FakeProvider.complete`](../harnesswalk/execution/providers/fake.py#FakeProvider.complete) picks
the response by session turn and call index and fails with `fixture_exhausted` when there is none;
[`FakeProvider._check_expect`](../harnesswalk/execution/providers/fake.py#FakeProvider._check_expect)
is the `expect` block.

[`OllamaProvider.complete`](../harnesswalk/execution/providers/ollama.py#OllamaProvider.complete)
and [`AnthropicProvider.complete`](../harnesswalk/execution/providers/anthropic.py#AnthropicProvider.complete)
are real adapters: build the provider's JSON, POST it through `_http.py`, which maps timeouts and
HTTP errors to `provider_timeout` and `provider_error`, and turn the reply into text, tool calls and
usage.

[`ProviderHealth`](../harnesswalk/execution/providers/health.py#ProviderHealth) keeps one state
per provider. [`ProviderHealth.mark_failed`](../harnesswalk/execution/providers/health.py#ProviderHealth.mark_failed)
is the passive signal from the model layer, and `probe` the active one.

## Try this

1. **Public mode.** Run `What are your support hours?` with fixture `plain_answer` and
   `--mode public`. Expected: `rule=public model=fake-small`, because the public rule is first and
   keeps public turns on local models.
2. **Break an expect.** Copy `datasets/_starter/fixtures/fake/tool_call.jsonl` to
   `expect_demo.jsonl` in the same folder and change the second response's
   `last_message_contains` to a word the lookup never returns. Expected: step 2 fails with
   `fixture_mismatch` at `providers`, and the reply is the error notice.
3. **Read the adapter.** Open [`to_anthropic`](../harnesswalk/execution/providers/anthropic.py#to_anthropic)
   and follow one tool round trip. Expected: system messages are joined into the request's
   `system` field, a tool call becomes a `tool_use` block on the assistant turn, and the
   harness's `Message("tool", ...)` becomes a `tool_result` block on the next user turn.

## Check yourself

### 1. The dev-mode model hint names an alias the matched rule does not list. What happens?

- [x] The hint is ignored and the rule's first usable alias is used.
- [ ] The hint wins, because dev mode trusts the developer.
- [ ] The turn fails with an error.

Answer: a hint only reorders the rule's own list, so a model may only be chosen from what the
rules allow.

### 2. Why does the second call of the cancel turn match `high_risk`?

- [ ] Because the user asked for a cancellation.
- [ ] Because the router reads the tool name.
- [x] Because the loop sets `risk=high` once a non-read tool has been called in the turn.

Answer: risk is a fact the loop tracks, and the router's `when: { risk: high }` matches on it.

### 3. What does an `expect` block in a fake fixture protect against?

- [ ] A slow provider.
- [x] The harness sending the model a request the fixture was not written for, such as a changed skill.
- [ ] Running out of responses.

Answer: a mismatch fails with `fixture_mismatch`, so a scripted turn cannot pass while the
context it was scripted for has changed.

## Further reading

- [Chen, Zaharia and Zou, FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance (2023)](https://arxiv.org/abs/2305.05176)
- [Anthropic documentation, Models overview](https://docs.anthropic.com/en/docs/about-claude/models/overview)
- [`spec/contracts/router.md`](../spec/contracts/router.md) and [`spec/contracts/model_provider.md`](../spec/contracts/model_provider.md)
