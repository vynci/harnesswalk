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
  source: "loop" | "turn" | "diagnose"
  evidence: [SourceRef]                           // turn ids, document refs
  user_id?: string                                // scope to a user inside the tenant
}

record ProposalRecord {
  proposal_id: string
  tenant_id: string
  status: "pending" | "accepted" | "rejected"
  gate: "review" | "eval" | "auto" | "session"
  reason?: string                                 // why it was rejected
  record_id?: string                              // set when written
  version?: int
}

record WriterReport { proposed: int, written: int, rejected: int, pending: int }
```

## Where proposals come from

- **The loop.** The model calls the harness tool `remember` (`spec/conformance/harness_tools.yaml`:
  `kind: fact` with `key` and `value`, or `kind: event` with `text`; action class
  `memory.write`). It goes through policy like every tool call; running it writes nothing,
  and each successful call becomes a proposal with `source: loop`.
- **The turn.** When `episodic` is among `context.stores` and `memory_writer.auto_episodes` is
  on, every finished chat turn that reached the context builder proposes one short event
  (`kind: turn`, the question and the answer, `outcome` the turn's exit, `source: turn`).
- **The ops seam.** `propose(ctx, proposal)` queues a proposal on a turn and `process(ctx)`
  gates the queue (eval diagnosis, P4), `source: diagnose`.

## Gates

The effective gate depends on the mode first, then on `memory_writer.gate`:

| Gate | When | Behaviour |
|---|---|---|
| `session` | Always in `public` mode | Rejected with reason `public_mode`: public memory never outlives the session. The proposal record is tenant-scoped and goes with the session at TTL |
| `auto` | `memory_writer.gate: auto` (the default), in `dev`, or in `private` for tenants in `auto_approve_tenants` (`"*"` opts every tenant in) | Accepted immediately after the checks below |
| `review` | `memory_writer.gate: review`, or `auto` for a private tenant not opted in, or a policy rule that answers `needs_approval` | Proposal waits for a human `decide` (`POST /v1/memory/proposals/{id}`, the memory writer inspector lists them) |
| `eval` | `memory_writer.gate: eval` | A deterministic check accepts or rejects: duplicates and contradictions (an existing fact with the same key) are rejected |

Before any gate, a proposal is rejected for: another tenant (`cross_tenant`), a reserved
tenant (`reserved_tenant`), a bad shape (`invalid: ...`), more model proposals than
`max_proposals_per_turn` (the turn event does not count), a policy `deny` for action class
`memory.write` (the first matching rule of `config/policy.yaml`, as for tool calls), and an
exact duplicate of an existing fact (`duplicate`). A write that fails leaves the proposal
pending with reason `write_failed`.

P4 (integration): every proposal's text (a fact's `key: value`, an event's `text`, after
redaction) is scored with the same prompt-injection heuristics as the input guardrails
(`control/injection.py`). At `memory_writer.injection.block_threshold` (default 0.8) it is
rejected with reason `injection: <signals>`; at `review_threshold` (default 0.45) an `auto` or
`eval` gate becomes `review`, so a person reads text that looks like an instruction before it
can come back as retrieved memory. `memory_proposed.summary.injection` carries the score when
any signal fired. Retrieved memory still reaches the model as a system message (the context
builder's retrieved blocks); whether it should carry less authority than the system prompt is
an open question recorded in `docs/ROADMAP.md` (P6).

## Inputs and outputs

- In: `MemoryProposal`s from the `loop` (end of turn) and from eval `diagnose`; human decisions through `decide`.
- Out: versioned records in `semantic` or `episodic`; a `WriterReport` per run; pending proposals for the inspector.

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
`node_exit` with the `WriterReport` (plus `gate`, `policy_rule` and up to five `rejections`
of `{proposal_id, reason}`). A disabled writer exits `skipped` with every proposal counted
as rejected and no point events. A human `decide` outside a turn writes without events (there
is no open turn to carry them); the write is in the proposal record and the store's versions. Edges: `loop_to_memory_writer`, `eval_to_memory_writer`,
`memory_writer_to_memory` (expert: `memory_writer_to_semantic`, `memory_writer_to_episodic`).

## Configuration keys (`config/context.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `memory_writer.enabled` | `true` | When false, proposals are dropped and counted as rejected |
| `memory_writer.gate` | `auto` | `auto`, `review`, `eval` (see Gates: `auto` only applies in dev and to opted-in private tenants, never in public) |
| `memory_writer.auto_approve_tenants` | `["*"]` | Private mode opt-in list; `"*"` opts every tenant in |
| `memory_writer.max_proposals_per_turn` | `3` | Extra model proposals are rejected |
| `memory_writer.auto_episodes` | `true` | One `turn` event per finished turn (only when `episodic` is among `context.stores`) |
| `memory_writer.injection.block_threshold` | `0.8` | P4: a proposal whose injection score reaches this is rejected |
| `memory_writer.injection.review_threshold` | `0.45` | P4: at this score an `auto` or `eval` gate becomes `review` |
