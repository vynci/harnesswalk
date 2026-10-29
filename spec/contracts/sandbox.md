# Contract: Sandbox

Node: `sandbox` (expert child of `tools`, kind `code`).

## Purpose

Where a tool call actually runs: isolated from the harness process state, time-limited, with
idempotent side effects. In public mode only pure dataset functions run here.

## Methods

```
interface Sandbox {
  execute(ctx: TurnContext, spec: ToolSpec, call: ToolCall) -> async ExecResult
}

record ExecResult {
  outcome: "ok" | "error" | "timeout"
  content: string                                // observation text, before truncation
  data?: object                                  // structured result, to the trace only
  duration_ms: int
  idempotency_key: string                        // sha256(tenant_id, turn_id, call_id)
}
```

## Invariants

- MUST enforce `spec.timeout_ms`; on expiry the call is abandoned and `outcome` is `timeout`.
- MUST pass `ctx.tenant_id` to the tool and resolve per-tenant credentials at execution time.
  Credentials MUST NOT appear in arguments, results, events or context.
- Side-effecting tools MUST receive `idempotency_key`; executing the same key twice (for example
  after resume from a checkpoint) MUST NOT repeat the side effect.
- Isolation levels, by mode:

  | Level | Used for | Mechanism (reference implementation) |
  |---|---|---|
  | `inprocess` | Pure dataset functions | Direct call with a timeout, no I/O allowed by convention and review |
  | `subprocess` | Local functions with I/O in dev and private | Child process, resource limits, no inherited secrets |
  | `container` | Shell or untrusted code (later phase) | One-shot container, no network unless the tool declares `external` |

- Public mode MUST use `inprocess` for pure tools only and MUST refuse any other level.
- Exceptions inside a tool become `outcome: error` with a short message; stack traces go to the
  trace only.

## Failure behaviour

Timeouts and tool errors are results, not harness errors. A sandbox that cannot start (for
example the container runtime is missing) fails the `tools` span with `error` `tool_error`.

## Events

Optional `node_enter` / `node_exit` on `sandbox` inside the `tools` span; summary `tool`,
`call_id`, `executed`, `timeout_ms`, `result_chars`. Status `skipped` when the call was denied.
Edge: `registry_to_sandbox`.

## Configuration keys (`config/harness.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `sandbox.level` | `inprocess` | Default level for tools that do not declare one |
| `sandbox.max_timeout_ms` | `10000` | Ceiling over any tool's own timeout |
| `sandbox.subprocess.memory_mb` | `256` | |
| `sandbox.container.image` | unset | Later phase |
