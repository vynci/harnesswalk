# Contract: Tool registry and tools node

Nodes: `tools` (execution plane, kind `code`) and its expert child `registry` (kind `config`).
Execution is in `sandbox.md`.

## Purpose

Know which tools exist, describe them to the model with typed schemas, and run every call through
the same path: resolve, validate, policy, approval when needed, sandbox, observation.

## Methods

```
interface ToolRegistry {
  register(spec: ToolSpec, impl: ToolImpl) -> void          // at startup only
  get(name: string) -> ToolSpec | null
  list(ctx: TurnContext) -> [ToolSpec]                      // tools visible to this tenant and mode
  schemas(ctx: TurnContext) -> [ToolSchema]                 // what the model sees
}

interface Tools {                                            // the tools node
  invoke(ctx: TurnContext, call: ToolCall) -> async Observation
}

record ToolSpec {
  name: string                                   // ^[a-z][a-z0-9_]{0,63}$
  description: string
  parameters: JSONSchema                         // arguments, draft 2020-12
  action_class: ActionClass
  timeout_ms: int
  idempotent: bool
  source: "local" | "mcp" | "dataset"
  pure: bool                                     // no I/O beyond the dataset; required in public mode
}

record ToolSchema { name: string, description: string, parameters: JSONSchema }
```

## The `invoke` path (normative)

```
spec = registry.get(call.name)            -> unknown: Observation(outcome "error", "unknown tool")
validate(call.arguments, spec.parameters) -> invalid: Observation(outcome "error", validation message)
decision = policy.check(ctx, identity, call, spec)
if decision.audit: audit.append(...)
if decision == "deny": return Observation(outcome "denied", decision.reason)
if decision == "needs_approval":
    d = approval.request(ctx, call, spec, decision)          // pauses the turn
    audit.append(approval decision)
    if d != "approve": return Observation(outcome "denied", "not approved")
result = sandbox.execute(ctx, spec, call)
return Observation(outcome result.outcome, content result.content)
```

## Inputs and outputs

- In: `ToolSpec`s at startup (dataset `tools.yaml`, code, later MCP); a `ToolCall` from the `loop` at run time.
- Out: tool schemas for the model; an `Observation` back to the `loop` for every call.

## Invariants

- Registration happens at startup. The registry is immutable during a turn.
- Every tool MUST declare `action_class` and `timeout_ms`; registration without them fails.
- Arguments MUST be validated before policy is consulted, so policy and audit see valid calls only.
- Policy MUST be consulted on every call. There is no cache of decisions.
- In public mode, only tools with `pure: true` or `source: dataset` and action class `read` are
  listed; others are not even shown to the model.
- Observations returned to the model are truncated to `tools.max_observation_chars`; the full
  result goes to the trace.
- MCP servers (later phase) register their tools through the same `register` call with
  `source: mcp`; nothing downstream changes.

## Failure behaviour

Unknown tool, invalid arguments, denial, tool error and timeout all become observations. The
`tools` span closes `done` with `outcome` set; it closes with `error` only when the harness
itself failed (for example the audit store was unavailable).

## Events

`node_enter` (summary `tool`, `call_id`), optional `registry` and `sandbox` children, nested
`policy`, `audit` and `approval` spans, `node_exit` (summary `tool`, `call_id`, `outcome`,
`executed`). Edges: `loop_to_tools`, `tools_to_policy`, `approval_to_tools`, `tools_to_loop`,
`registry_to_sandbox`.

## Configuration keys

Tools come from `datasets/<pack>/tools.yaml` and from code. Settings in `config/harness.yaml`:

| Key | Default | Meaning |
|---|---|---|
| `tools.packs` | `[active pack]` | Dataset packs whose tools are registered |
| `tools.max_observation_chars` | `4000` | |
| `tools.mcp_servers` | `[]` | Later phase |

`tools.yaml` entry:

```yaml
- name: cancel_order
  description: Cancel an order that has not shipped and refund it.
  action_class: destructive
  timeout_ms: 2000
  idempotent: true
  pure: true
  parameters:
    type: object
    required: [order_id]
    properties: { order_id: { type: string, pattern: "^A-[0-9]{4}$" } }
```
