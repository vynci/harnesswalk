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
  approver?: string                              // P3: who decided an approval ("user:<id>" or "system")
  executed?: bool                                // P3: whether the action ran
  outcome?: string                               // P3: tool_executed: ok | error | timeout
  before?: object                                // P3: writes: the state before (see below)
  after?: object                                 // P3: writes: the state after
  extra?: object
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

## P3 additions

- **One record per consequence.** Every tool call of a class other than `read` (and
  `memory.write`, whose call only proposes: the memory writer records the write) produces, in
  order: `policy_decision` (before anything runs), `approval_decision` when it needed approval
  (before it runs), and `tool_executed` after the sandbox ran it (`executed`, `outcome`,
  `before`, `after`). A denied or unapproved call has `executed: false` on its last record.
- **Before and after.** A tool MAY declare `audit: { before: <read tool> }` in `tools.yaml`;
  the harness runs that read tool with the same arguments just before execution and records
  its result as `before`, and the tool's own result as `after`. Both are JSON values when the
  result parses, capped at 1 KiB each, and PII-redacted with typed placeholders.
- **Chain.** Appends to one tenant are serialized (a write transaction), so `prev_hash` always
  names the latest record of that tenant, across processes.
- **Export.** `GET /v1/audit?since=<RFC 3339>&limit=<n>` (dev and private modes) returns the
  caller's tenant's records in chain order with `verify` (see `http_api.md`).
- The `audit` exit summary adds `prev_hash` and `hash` (first 12 hex characters each) and
  `executed` for `tool_executed` records.

## P6 additions

- **`purge_tenant(tenant_id)`.** The one deletion the append-only triggers let through, for the
  public-mode TTL sweeper: in one write transaction it drops the delete trigger, deletes every
  record of that tenant, and creates the trigger again. A chain links only its own tenant's
  records, so every other tenant's chain still verifies. Nothing calls it outside the sweeper.
