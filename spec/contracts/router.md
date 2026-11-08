# Contract: Router

Node: `router` (expert child of `model_layer`, kind `config`).

## Purpose

Choose a model for each call by rules: step, risk, cost and modality. A small model for lookups,
a larger one for synthesis, a vision model for images. Rules first; a model may only choose among
options the rules allow.

## Methods

```
interface Router {
  route(ctx: TurnContext, request: ModelRequest) -> Route
  rules() -> [RouteRule]                         // for the inspector
}

record RouteRule {
  id: string
  when: { step?: "first" | "after_tool" | "final" | "summarize" | int, risk?: "low" | "high", modality?: string, tools?: bool, mode?: string }
  use: [string]                                  // model aliases in preference order; the tail is the fallback chain
}

record Route { rule: string, model: string, alternatives: [string] }
```

Step kinds: `first` is the loop's first call, `after_tool` a call that follows tool results,
`final` the tools-off call after `max_steps` or `stuck`, and `summarize` the context builder's
compaction call. When the route lands on the `fake` provider, compaction truncates instead
of calling a model.

## Inputs and outputs

- In: the `ModelRequest` (step, risk, modality, whether tools are enabled), the mode, provider health, the hosted budget state.
- Out: a `Route` (chosen model alias, the rule that chose it, the allowed fallbacks) to the model layer.

## Invariants

- Rules are evaluated in order; the first whose `when` matches wins. A final rule with an empty
  `when` MUST exist (the default).
- `model` is the first alias in `use` whose provider is configured and healthy; `alternatives`
  is the rest of `use`, in order, and is the only set the model layer may fall back to.
- MUST be pure and deterministic for the same request and config.
- `modality: image` MUST route to a model that lists `image` in `modalities`, or fail with
  `provider_error` naming the missing capability.
- In public mode, aliases whose provider is hosted MUST be skipped once the daily hosted budget
  is spent, which leaves local models only.
- A request may carry a model hint (BYO key flow); it is honoured only if it is in the matched
  rule's `use` list.

## Failure behaviour

No rule matches (misconfiguration): fail at load time. No healthy model in `use`: the model
layer fails with `provider_error`.

## Events

Optional `node_enter` / `node_exit` on `router` inside the `model_layer` span, summary `rule`,
`model`, `alternatives`. Edge: `router_to_providers`.

## Configuration keys (`config/models.yaml`)

```yaml
router:
  rules:
    - { id: vision,  when: { modality: image },  use: [hosted-vision] }
    - { id: lookup,  when: { step: first, risk: low }, use: [local-small, local-8b] }
    - { id: default, when: {},                   use: [local-8b, hosted-sonnet, hosted-haiku] }
```

| Key | Meaning |
|---|---|
| `router.rules` | Ordered rules |
| `router.health_check_s` | How often provider health is refreshed, default 30 |
| `router.public_hosted_budget_usd_per_day` | Default 5 |

## P3 additions

- **Step kinds** add `next` (a call that is neither first nor after a tool), `classify` (the
  input guardrail's optional injection classifier) and `judge` (P4 evals).
- **Risk.** The loop sends `risk: high` once any tool call of the turn has a class other than
  `read`, so a rule can send consequential steps to a stronger model.
- **Cost.** `when.cost: low | normal`: a request is `cost: low` once the turn has spent
  `router.cost.tight_fraction` (default 0.75) of its dollar budget. A rule MAY also set
  `max_price_out_per_mtok` to drop aliases priced above it. When the cap drops every
  configured alias of a matching rule, that rule does not apply and the next matching rule
  routes the call (P5 integration); a rule with no configured alias at all is still a
  `provider_error`.
- **Health.** `model` is the first alias whose provider is configured and healthy. Health is a
  probe per provider every `router.health_check_s` (a cheap list-models call; `fake` is always
  healthy) plus a passive signal: a retryable failure marks the provider unhealthy until the
  next probe. A provider never probed counts as healthy. `GET /healthz` reports it per provider.
- **Hints.** A turn MAY carry a model alias hint; it is used only if it is in the matched
  rule's `use` list and healthy.
- **Cascade (documented option, off by default).** With `router.cascade.enabled`, a response
  from a local model whose confidence signal is below `router.cascade.min_confidence` is
  retried on the next hosted alias in `alternatives` (`fallback: true`, `cascade:
  low_confidence` in the `model_layer` summary). Confidence is deterministic: 0 for an empty
  answer, a `length` stop or a call to a tool that was not offered, 0.5 for a hedge ("I am not
  sure", "I don't know"), else 1. Failures already fall back without the cascade.
- Pinned hosted ids (`config/models.yaml`): `claude-haiku-5-5` (alias `hosted-haiku`, router,
  summarize, classify and judge steps) and `claude-sonnet-5-5` (alias `hosted-sonnet`, the
  loop in hosted demos). A model entry MAY carry `params` (provider request fields such as
  `thinking`) passed through by the adapter.

## P6 additions: the public rule and the budget

- **The `public` rule.** A rule whose `use` is the string `public.models` gets
  `config/harness.yaml` `public.models` when the settings load, with `public.default_model`
  first, so the allowlist has one source. `config/models.yaml` puts it first with
  `when: { mode: public }`, so every step of a public turn (summarize, classify and judge too)
  stays on the allowlist. A turn's `model` hint (public mode accepts one, but only from
  `public.models`, http_api.md P6 additions) picks among the usable aliases as any hint does.
- **Alias filter.** A router MAY carry an alias filter on top of provider health. In public
  mode the model layer narrows every call to the aliases of `public.models` that can answer
  now: the provider is built and healthy; a hosted provider has the site's key or the turn
  brings a BYO key; and an alias with a price (`price_in_per_mtok` or `price_out_per_mtok`
  above 0) fits the shared daily budget unless the turn brings a BYO key.
- **Budget fall-through.** The model layer charges the shared ledger after every priced call
  a BYO key did not pay for (every mode) and the filter reads it before every call. When the
  spent budget is what moved a call off a priced alias (the route without the budget part of
  the filter would have chosen it), the call runs on the next alias of the rule
  (`fake-small`), and the `model_layer` exit summary carries `notice: budget_exhausted` and
  `downgraded_from: <alias>`; the turn's notice reaches `turn_end.summary.notices`. A spent
  budget never fails a turn while a free alias is in the rule.

## Model suite: the pinned router

Additive (spec 0.1.0). An eval case turn of the `model` suite, or of a `judge` run that names
a model (`evaluator.md`, "Model suite and the scorecard"), pins every model call to one alias.
Rules are still evaluated in order and matched by `when`, and the first matching rule's id is
what the `router` span reports, so rule checks stay meaningful. A rule's
`max_price_out_per_mtok` does not apply to a pinned alias: the run chose the alias on purpose,
and a cap that pushed a priced alias to the next rule would misreport the step (a hosted
model's after-tool call as `default` instead of `lookup`). The rule's `use` list is not
consulted: the route's `model` is the pinned alias and `alternatives` is empty (no fallback). A pinned alias
whose provider is not configured or not healthy fails the call with `provider_error`. Pins are
set only by the harness for its own eval turns, never from HTTP.

