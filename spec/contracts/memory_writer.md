# Contract: Memory writer

Node: `memory_writer` (context plane, kind `code`, can be disabled in dev and private modes).

## Purpose

The single, gated write path into `semantic` and `episodic` memory. It takes proposals from the
loop and from eval diagnosis, gates them, versions them and scopes them to one tenant.

## Methods

```
interface MemoryWriter {
  propose(ctx: TurnContext, proposal: MemoryProposal) -> ProposalRecord
  process(ctx: TurnContext) -> WriterReport                 // run the gate on this turn's proposals
  decide(proposal_id: string, decider: Identity, decision: "accept" | "reject", reason?: string) -> ProposalRecord
  pending(tenant_id: string) -> [ProposalRecord]
  history(tenant_id: string, record_id: string) -> [Version]
}

record MemoryProposal {
  store: "semantic" | "episodic"
  content: object                                 // a fact or an event, per store.md
  source: "loop" | "diagnose"
  evidence: [SourceRef]                           // turn ids, document refs
  user_id?: string                                // scope to a user inside the tenant
}

record ProposalRecord {
  proposal_id: string
  tenant_id: string
  status: "pending" | "accepted" | "rejected"
  gate: "review" | "eval" | "auto"
  record_id?: string                              // set when written
  version?: int
}

record WriterReport { proposed: int, written: int, rejected: int, pending: int }
```

## Gates

| Gate | When | Behaviour |
|---|---|---|
| `review` | Default in every mode | Proposal waits for a human `decide` (the UI shows it in the memory inspector) |
| `eval` | `memory_writer.gate: eval` | A deterministic check (schema, duplicate, contradiction with an existing fact) accepts or rejects |
| `auto` | Only `dev`, or `private` with the tenant setting on | Accepted immediately after schema and duplicate checks |

## Invariants

- MUST be the only component holding a `WriterToken` for `semantic` and `episodic`.
- Every proposal MUST be scoped to `ctx.tenant_id`; cross-tenant proposals MUST be rejected.
- `auto` MUST NOT be possible in `public` mode, whatever the configuration says.
- Writes MUST be versioned: an update to an existing fact creates a new version with the
  previous one kept and diffable.
- Proposal content MUST pass input-style PII redaction before it is stored.
- In a chat turn, `process` runs after the gateway reply and before `trace`, only when the turn
  produced proposals.

## Failure behaviour

A failed write leaves the proposal `pending` and is reported in the summary; it never fails the
turn. A store refusing the writer token is a programming error and raises.

## Events

`node_enter`, one `memory_proposed` per proposal, one `memory_written` per accepted write,
`node_exit` with the `WriterReport`. Edges: `loop_to_memory_writer`, `eval_to_memory_writer`,
`memory_writer_to_memory` (expert: `memory_writer_to_semantic`, `memory_writer_to_episodic`).

## Configuration keys (`config/context.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `memory_writer.enabled` | `true` | When false, proposals are dropped and counted as rejected |
| `memory_writer.gate` | `review` | `review`, `eval`, `auto` |
| `memory_writer.auto_approve_tenants` | `[]` | Private mode opt-in list |
| `memory_writer.max_proposals_per_turn` | `3` | Extra proposals are rejected |
