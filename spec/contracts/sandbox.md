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

## Inputs and outputs

- In: a validated, policy-approved `ToolCall` and its `ToolSpec` from `tools`; per-tenant credentials resolved at execution.
- Out: an `ExecResult` back to `tools`; the full result as a trace detail record.

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

## P3 additions: the subprocess level

The reference implementation runs each tool call at `subprocess` level by default in dev and
private modes (`sandbox.level`), and keeps `inprocess` for tests and for tools that declare it
(tools that read harness memory, such as `read_skill`). Public mode forces `inprocess` and pure
tools only.

A subprocess call:

- runs `python -I -B -m harnesswalk.execution.sandbox_child` with a scrubbed environment (no
  inherited variables beyond `PATH` and a locale; `HOME` and `TMPDIR` are the work directory),
  in its own process group, in a work directory per turn (`sandbox.subprocess.workdir`/<tenant>/
  <turn_id>) that is removed when the turn ends;
- gets the tool by entrypoint (`module:function` or `path/to/file.py:function`, declared in
  `tools.yaml` as `entrypoint`; lookup-table tools use the built-in table entrypoint);
- is killed (`SIGKILL` to the group) when `timeout_ms` passes (`outcome: timeout`), when its
  result is over `sandbox.subprocess.output_cap_bytes` (`outcome: error`), or when its resident
  memory passes `sandbox.subprocess.memory_mb` (`outcome: error`);
- has no network unless the tool's class is `external` and policy allowed the call. The block
  is two layers: a Python audit hook in the child that refuses sockets, process spawning,
  `ctypes` loading and file writes outside the work directory, and, where available, an OS
  jail (`sandbox-exec` with a deny-network profile on macOS, `unshare --net` on Linux).

| Key | Default | Meaning |
|---|---|---|
| `sandbox.subprocess.output_cap_bytes` | `65536` | Larger results are an error |
| `sandbox.subprocess.workdir` | `data/sandbox` | Parent of the per-turn directories |
| `sandbox.subprocess.os_jail` | `auto` | `auto` or `off` |

`tools.yaml` keys added: `sandbox` (`inprocess` or `subprocess`, overrides `sandbox.level`),
`entrypoint`, `audit.before` (see `audit.md`).

The `sandbox` exit summary adds `level`, `network` (`blocked` or `allowed`), `jail` (the OS
layer used, or `none`) and `outcome`.
