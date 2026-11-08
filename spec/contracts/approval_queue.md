# Contract: Approval queue

Node: `approval` (control plane, kind `queue`; can be switched off since 0.2.0).

## Purpose

Hold a high-risk action until a human decides. The exact action is shown, the loop is
checkpointed, the turn pauses, and it resumes on approve, deny or expiry.

## Methods

```
interface ApprovalQueue {
  request(ctx: TurnContext, call: ToolCall, tool: ToolSpec, decision: Decision) -> async ApprovalDecision
  decide(approval_id: string, decider: Identity, decision: "approve" | "deny", reason?: string) -> ApprovalRecord
  pending(tenant_id: string) -> [ApprovalRecord]
  get(approval_id: string, viewer: Identity) -> ApprovalRecord
}

record ApprovalRecord {
  approval_id: string
  tenant_id: string
  turn_id: string
  tool: string
  action_class: ActionClass
  args_preview: object                           // redacted arguments, exactly what will run
  reason: string                                 // why approval is needed (from the policy rule)
  status: "pending" | "approved" | "denied" | "expired"
  requested_at: timestamp
  expires_at: timestamp
  decided_by?: string
  decided_at?: timestamp
}

record ApprovalDecision { approval_id: string, decision: "approve" | "deny" | "expired", decided_by: string | null }
```

## Inputs and outputs

- In: a `needs_approval` decision with the tool call (from `tools` via `policy`).
- Out: an `ApprovalDecision` back to `tools`; a record to `audit`.

## Invariants

- MUST persist the request and the loop checkpoint before emitting `node_paused`, so a restart
  can resume the same turn.
- `args_preview` MUST be the arguments that will be executed, redacted only of secrets, never a
  model-written paraphrase.
- A record is decided at most once. A second `decide` MUST fail (`409` over HTTP).
- The decider MUST belong to the same tenant. In public mode the decider is the session owner.
- Expiry after `approval.timeout_s` MUST be treated as `deny`, with decision `expired`.
- The decision MUST be sent to `audit` before the tool executes.
- Event order inside the `approval` span: `approval_requested`, `node_paused` (reason
  `approval`), then after the decision `approval_decided`, `node_resumed`, `node_exit`.

## Failure behaviour

| Condition | Result |
|---|---|
| Unknown `approval_id` or other tenant | `404` |
| Already decided | `409` |
| Turn cancelled while pending | Record becomes `expired` at once (decided by `system`), so a later decision is `409`; the span closes with `error` `cancelled` |
| Approved, then the turn is cancelled before the tool runs | Record stays `approved`; the audit record carries `extra: {executed: false}` with a note; the tool does not run and the `tools` span closes with `error` `cancelled` |
| Process restart while pending | Pending record and checkpoint survive; a later decision resumes the turn |

## Events

`node_enter`, `approval_requested`, `node_paused`, `approval_decided`, `node_resumed`,
`node_exit` on `approval`. Edges: `policy_to_approval`, `approval_to_audit`, `approval_to_tools`.

## Configuration keys (`config/policy.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `approval.timeout_s` | `900` | Pending approvals expire after this |
| `approval.deciders` | `[session_owner]` | Who may decide: `session_owner`, `role:<name>` |
| `approval.show_args` | `true` | Include `args_preview` in the card and event |

## P3 additions: restart and expiry

- **Persisted state.** The approval record, the loop checkpoint (with the pending call) and the
  turn's events up to `node_paused` are durable before the turn waits.
- **Boot.** A harness that serves the HTTP API (`harnesswalk serve`) recovers at start-up:
  - Each turn whose stream has no `turn_end` and is paused on a pending approval is
    re-attached: the gateway rebuilds the turn state and the open spans (`loop`, `tools`,
    `approval`) from the trace, continues `seq` where the stream stopped, and waits for the
    decision again with the time left until `expires_at`. `GET /v1/turns/{id}` reports it
    `paused` with its `pending_approval_id`, the event stream follows it live, and cancel works.
  - When the decision arrives the stream goes on exactly as it would have without the restart:
    `approval_decided`, `node_resumed`, the approval's audit record, the tool, then the loop
    from the checkpoint (the next model call reads the next scripted response with the fake
    provider).
  - A pending approval past `expires_at` at boot is decided `expired` (by `system`) and its
    turn resumes with that decision, which counts as a deny.
  - Every other turn without `turn_end` (running, or paused on a breakpoint, when the process
    stopped) is closed: its open spans close with `error` code `restarted`, then a turn-level
    `error` and `turn_end` with `status: error`, `summary.exit: error`. The session records it
    so `turn_index` stays unique. Its pending approvals become `expired`.
- **Graceful shutdown** leaves turns that wait on an approval as they are (no closing events,
  the record stays `pending`), so the next boot resumes them. Running turns are cancelled.
- **Expiry sweeper.** A background task (every `approval.sweep_s`, default 30) decides every
  pending approval past `expires_at` as `expired` and wakes its waiter.
- Breakpoints do not survive a restart: a re-attached turn continues without step mode.

| Key | Default | Meaning |
|---|---|---|
| `approval.sweep_s` | `30` | Expiry sweeper period |

## 0.2.0: approval switched off

When `approval` is off for a turn (a profile, or the request's `disable` in dev and private), a
`needs_approval` decision is a deny and the queue is not used: the `approval` span opens and
exits `skipped` with `disabled: true`, `tool` and `decision: deny`; no `approval_requested`, no
`node_paused`, no approval record. The tools node then audits an `approval_decision` with
`decision: deny`, `actor: system`, `executed: false` and the note `denied: approval is off, so
nobody could approve` (when audit is on), skips the sandbox and returns a denial observation.
Conformance case `approval_off`.
