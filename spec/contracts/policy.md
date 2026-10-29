# Contract: Policy

Node: `policy` (control plane, kind `config`, can be disabled in dev and private modes).

## Purpose

Decide whether a tool call may run. Policy is evaluated on every call, from rules in a file, with
the effective permission being the intersection of what the user may do and what the agent may
do. A rule in the system prompt is advice; this is the control.

## Methods

```
interface Policy {
  check(ctx: TurnContext, identity: Identity, call: ToolCall, tool: ToolSpec) -> Decision
  rules() -> [Rule]                               // for the inspector
}

record Decision {
  decision: "allow" | "deny" | "needs_approval"
  rule: string                                    // id of the matching rule, or "default.deny"
  action_class: ActionClass
  reason: string                                  // short, safe to show
  audit: bool                                     // true when this decision must be recorded
}

record Rule {
  id: string                                      // "dev.destructive.approve"
  modes: ["dev" | "public" | "private"]           // empty means all
  tools?: [string]                                // tool names; omitted means any
  action_classes?: [ActionClass]                  // omitted means any
  roles?: [string]                                // user roles the rule applies to
  decision: "allow" | "deny" | "needs_approval"
}
```

## Inputs and outputs

- In: the tool call, the tool's spec from the registry (name, `action_class`), identity from the
  gateway, rules from `config/policy.yaml`.
- Out: a `Decision` to `tools`; a record to `audit` when `audit` is true; a request to
  `approval` when the decision is `needs_approval`.

## Invariants

- MUST be called for every tool call, including repeats.
- Rules are evaluated in file order; the first match wins. No match means `deny` with rule
  `default.deny`.
- Effective permission = user permissions intersected with agent permissions. A rule cannot
  grant a user more than their roles allow, and an agent allowlist cannot be widened per user.
- `audit` MUST be true for every `deny` and `needs_approval`, and for every `allow` of a
  `write`, `destructive` or `external` call. It SHOULD be false for `read` allows.
- MUST be pure: the same inputs and rules give the same decision. Time-based rules are not in v1.
- Prompt or tool-result content MUST NOT influence a decision.
- Public mode: the harness MUST refuse to start if any rule would `allow` or `needs_approval` a
  `destructive` or `external` action class.
- When disabled (dev, private), every call is `allow` with rule `disabled`, the span exits
  `skipped`, and `audit` is still true for non-read classes so the bypass is on record.

## Failure behaviour

| Condition | Result |
|---|---|
| Rules file invalid at load | The harness refuses to start |
| Exception during check | Fail closed: `deny` with rule `policy.error`, audited |

## Events

`node_enter` / `node_exit` on `policy`. Edges: `tools_to_policy`, `gateway_to_policy`
(identity), `policy_to_audit`, `policy_to_approval`.

## Configuration keys (`config/policy.yaml`)

```yaml
version: 1
agent:
  allow_tools: [lookup_order, track_shipment, cancel_order, search_knowledge]
rules:
  - { id: dev.read.allow,          modes: [dev, private, public], action_classes: [read], decision: allow }
  - { id: dev.write.allow,         modes: [dev, private], action_classes: [write], decision: allow }
  - { id: dev.destructive.approve, modes: [dev, private], action_classes: [destructive], decision: needs_approval }
  - { id: external.approve,        modes: [dev, private], action_classes: [external], decision: needs_approval }
  - { id: public.deny_rest,        modes: [public], decision: deny }
```

| Key | Meaning |
|---|---|
| `policy.enabled` | Default `true`; forced true in public mode |
| `agent.allow_tools` | Agent allowlist; tools outside it are denied before rules run (rule `agent.allowlist`) |
| `rules` | Ordered rule list |
| `roles.<role>.allow_action_classes` | User side of the intersection; default role `user` allows all classes |
| `budgets.<tenant>.usd_per_day` | Read by the gateway and loop; listed here so all limits live in one file |
