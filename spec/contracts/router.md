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
  when: { step?: "first" | "after_tool" | "final" | int, risk?: "low" | "high", modality?: string, tools?: bool, mode?: string }
  use: [string]                                  // model aliases in preference order; the tail is the fallback chain
}

record Route { rule: string, model: string, alternatives: [string] }
```

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
    - { id: default, when: {},                   use: [local-8b, hosted-small] }
```

| Key | Meaning |
|---|---|
| `router.rules` | Ordered rules |
| `router.health_check_s` | How often provider health is refreshed, default 30 |
| `router.public_hosted_budget_usd_per_day` | Default 5 |
