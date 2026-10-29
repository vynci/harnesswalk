# Component contracts

One file per interface. Each file gives: purpose, method signatures in language-neutral
pseudocode, inputs, outputs, invariants, failure behaviour, events emitted and configuration
keys. The HTTP surface of the gateway is in `http_api.md`.

| Contract | Node ids | File |
|---|---|---|
| Gateway | `gateway` | `gateway.md` |
| Guardrail (input and output) | `input_guardrails`, `output_guardrails` | `guardrail.md` |
| Policy | `policy` | `policy.md` |
| Approval queue | `approval` | `approval_queue.md` |
| Audit | `audit` | `audit.md` |
| Context builder | `context_builder` | `context_builder.md` |
| Store (five kinds) | `memory`, `procedural`, `session`, `semantic`, `episodic`, `knowledge` | `store.md` |
| Memory writer | `memory_writer` | `memory_writer.md` |
| Loop | `loop` | `loop.md` |
| Model provider | `providers`, `model_layer` | `model_provider.md` |
| Router | `router`, `model_layer` | `router.md` |
| Tool registry | `registry`, `tools` | `tool_registry.md` |
| Sandbox | `sandbox`, `tools` | `sandbox.md` |
| Tracer | `trace`, `cost` | `tracer.md` |
| Evaluator | `eval`, `online_eval`, `offline_eval`, `diagnose` | `evaluator.md` |
| Release | `release`, `artifacts` | `release.md` |
| HTTP API | `gateway` | `http_api.md` |

## Notation

```
interface Name {
  method(arg: Type, opt?: Type) -> Result        // ? marks optional
  method_that_waits(...) -> async Result          // may suspend (I/O, approval)
}
record Type { field: Type, list: [Type], map: {string: Type}, either: "a" | "b" }
```

Types are structural. An implementation may use classes, dicts or structs as long as the fields
and semantics match. Names in `snake_case` are normative; language bindings may adapt casing.

## Shared types

```
record TurnContext {
  tenant_id: string            // required on every call that touches data
  session_id: string
  turn_id: string
  trace_id: string             // 32 lowercase hex
  user_id: string | null
  mode: "dev" | "public" | "private"
  emit: Emitter                // the only way a component reports events
  deadline: timestamp          // wall-clock limit for the turn
}

interface Emitter {
  enter(node_id, summary?) -> Span
  point(type, node_id, status, summary) -> void   // approval_*, memory_*, artifact_released, node_paused, node_resumed
  edge(edge_id) -> void
}

interface Span {
  exit(status: "done" | "skipped", summary?, cost?, detail?) -> void
  fail(code: string, message: string, summary?) -> void       // emits error and closes the span
}

record Message {
  role: "system" | "user" | "assistant" | "tool"
  content: string
  name?: string                // tool name for role "tool"
  tool_call_id?: string
  tool_calls?: [ToolCall]      // on assistant messages
  attachments?: [MediaRef]     // by reference only
}

record ToolCall    { call_id: string, name: string, arguments: object }
record Observation { call_id: string, outcome: "ok" | "denied" | "error" | "timeout", content: string }
record Usage       { input_tokens: int, output_tokens: int, cached_tokens?: int, usd: number }
record Cost        { input_tokens: int, output_tokens: int, usd: number }

ActionClass = "read" | "write" | "destructive" | "external" | "memory.write"   // memory.write since P3
```

## Conventions for every contract

- Every method that reads or writes data takes `ctx: TurnContext` first. A missing `tenant_id`
  is a programming error and MUST raise, never default.
- Components report through `ctx.emit` only. They MUST NOT write to the trace store directly.
- A component that is disabled (dev and private modes, nodes with `can_disable`) still enters
  and exits its span, with status `skipped` and `summary.disabled: true`, so the diagram shows
  that it was bypassed.
- Configuration keys are dotted paths. They are loaded only through `release` and live in four
  files: `config/harness.yaml` (gateway, guardrails, loop, trace, eval, release),
  `config/policy.yaml` (policy, approval), `config/context.yaml` (context builder, stores, memory
  writer), `config/models.yaml` (router, providers, prices).
