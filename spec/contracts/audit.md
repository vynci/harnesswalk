# Contract: Audit

Node: `audit` (control plane, kind `store`).

## Purpose

An append-only, tamper-evident record of decisions with consequences: which rule decided, who
asked, who approved, what ran, when. It is separate from the trace, kept longer, and never
sampled.

## Methods

```
interface AuditLog {
  append(ctx: TurnContext, entry: AuditEntry) -> AuditRecord
  list(tenant_id: string, filter?: AuditFilter, page?: Page) -> [AuditRecord]
  verify(tenant_id: string) -> VerifyResult                 // walks the hash chain
}

record AuditEntry {
  action: "policy_decision" | "approval_decision" | "tool_executed" | "memory_written" | "artifact_released"
  actor: string                                  // "user:<id>", "agent", "system"
  tool?: string
  action_class?: ActionClass
  decision?: string
  rule?: string
  approval_id?: string
  args_digest?: string                           // sha256 of the canonical arguments, not the arguments
  turn_id: string
  trace_id: string
}

record AuditRecord {
  record_id: string
  tenant_id: string
  ts: timestamp
  entry: AuditEntry
  prev_hash: string                              // hash of the previous record in this tenant
  hash: string                                   // sha256(prev_hash + canonical(entry, ts, record_id))
}

record VerifyResult { ok: bool, records: int, first_bad_record?: string }
```

## Inputs and outputs

- In: `AuditEntry` records from `policy` (decisions) and `approval` (decisions), and later from `release` and `memory_writer`.
- Out: an `AuditRecord` with its id and chain hash; listings and chain verification for the inspector and for compliance export.

## Invariants

- There is no update or delete method. Storage MUST reject updates (for example a trigger or a
  write-once table).
- Partitioned by tenant; `list` and `verify` take `tenant_id` and never cross it.
- Each record chains to the previous record of the same tenant through `prev_hash`.
- Arguments are recorded as a digest; the full call is in the trace under the same `trace_id`.
- Retention is independent of the trace (`audit.retention_days`). Public-mode TTL deletion
  removes the tenant's audit records with the tenant, and records that it did so in the
  operator log.

## Failure behaviour

| Condition | Result |
|---|---|
| Store unavailable on a `needs_approval` or non-read `allow` | Fail closed: the tool does not execute; the span closes with `error` `store_unavailable` and the loop gets a denial observation |
| Hash chain broken on `verify` | `ok: false` with the first bad record |

## Events

`node_enter` / `node_exit` on `audit`. Edges: `policy_to_audit`, `approval_to_audit`.

## Configuration keys (`config/harness.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `audit.backend` | `sqlite` | `sqlite` or `postgres` |
| `audit.retention_days` | `365` | Private mode; public mode follows the session TTL |
| `audit.record_read_allows` | `false` | Debug switch: also audit `read` allows |
