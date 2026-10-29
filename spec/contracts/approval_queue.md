# Contract: Approval queue

Node: `approval` (control plane, kind `queue`).

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
