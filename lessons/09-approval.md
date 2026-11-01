---
node: approval
try:
  pack: _starter
  disable: [policy]
  fixture: cancel_order
  prompt: "Please cancel order A-1002."
---

# Approval

> A human in the loop: the turn pauses, the exact action is shown, nothing runs until someone decides.

## What it does

When policy answers `needs_approval`, the Tools node hands the call to Approval, and the turn
stops. Approval writes a record to its own store (the tool, its action class, the exact arguments
that will run, the reason policy gave, and when the request expires), lets the loop save a
checkpoint with the pending call, and emits `approval_requested` and `node_paused`. In the web
app that event becomes a card in the chat: "Run cancel_order?", the arguments, a countdown, and
Deny then Approve. This is the only time a request pauses on its own; on the diagram the node
shows as waiting, in amber.

A decision arrives on `POST /v1/approvals/{id}` (the card's buttons, or the CLI prompt). A record
can be decided once; a second decision is a `409`. Approve and the call goes on to the sandbox,
deny and the model gets an observation saying it was not approved. If nobody decides before
`approval.timeout_s` (fifteen minutes by default), the sweeper decides it as `expired`, which
counts as a deny. Every decision is audited, with who made it. And because the record, the
checkpoint and the events up to the pause are on disk before the turn waits, a server that
crashes mid-wait picks the turn back up when it starts again.

## What breaks without it

Approval only runs when policy asks for it, so the way to remove it is to switch policy off.
Pack `_starter`, fixture `cancel_order`, prompt:

```text
Please cancel order A-1002.
```

With everything on, the turn pauses: Approval is amber, the card shows `cancel_order` with
`{"order_id": "A-1002"}`, and the turn summary says `status: paused` with a
`pending_approval_id`. Nothing has run yet. Press Deny and the sandbox span is drawn as skipped
with "approval deny", and the order is untouched.

Now open Policy, switch on "Disable on the next turn", and send the same prompt. The card never
appears and Approval never lights. `cancel_order` runs at once, the audit log records a
`tool_executed` with `before: processing` and `after: cancelled`, and the reply comes back as if
someone had agreed. The human checkpoint is gone, and a destructive action ran on the strength of
one model response.

The other way to lose the human is time. Start a server with a short timeout and let a request
sit:

```sh
uv run harnesswalk serve --port 8812 --provider fake \
  --set approval.timeout_s=3 --set approval.sweep_s=1
```

Send the cancel prompt to it and wait. Within a few seconds the card's countdown ends, the
stream carries `approval_decided` with `decision: expired`, and the order is not cancelled.
Expiry fails safe: silence is a no.

## The code walk

[`ApprovalQueue.request`](../harnesswalk/control/approval.py#ApprovalQueue.request) is the
pause. Read the order of its steps, because it is the whole trick: insert the record, call
`before_pause` so the loop checkpoints, register the waiter, and only then emit
`approval_requested` and `node_paused`. A decision that arrives the instant the card appears
still finds a waiter, and a crash after the events finds everything it needs on disk.

[`ApprovalQueue.wait`](../harnesswalk/control/approval.py#ApprovalQueue.wait) waits with a
timeout. A timeout decides `expired`. A cancelled turn decides `expired` too, unless the server
is shutting down, in which case the record stays pending for the next boot. The time spent
waiting is added to `paused_s`, so it does not count against the loop's wall-clock timeout.

[`ApprovalQueue.decide`](../harnesswalk/control/approval.py#ApprovalQueue.decide) and
[`_record`](../harnesswalk/control/approval.py#ApprovalQueue._record) make a decision happen once.
The `UPDATE ... WHERE status = 'pending'` is a compare-and-set in SQL: if two people press
Approve at the same moment, one update changes a row and the other changes none and gets
`ApprovalAlreadyDecided`. `get` raises not-found for another tenant's id, like every other
lookup.

[`Tools.after_approval`](../harnesswalk/execution/tools.py#Tools.after_approval) is what happens
next: audit the decision with the approver, then run the call or skip the sandbox with the
reason. An approval that lands after the user cancelled the turn is audited as approved but not
run.

[`Gateway.recover`](../harnesswalk/control/gateway.py#Gateway.recover) runs once at start-up. It
re-attaches every turn that was waiting on an approval and closes every other unfinished turn as
`restarted`, which is why a hard restart loses nothing.

## Try this

1. **Answer from the CLI.** Run the cancel prompt with
   `uv run harnesswalk turn "Please cancel order A-1002." --provider fake --fixture cancel_order`
   in a terminal. Expected: it prints `approve cancel_order {"order_id": "A-1002"}? [y/N]`; `y`
   runs it, anything else denies it, and the walk shows `approval ... decision=approve` or `deny`.
2. **Decide twice.** Pause a turn in the web app, approve it, then send the same decision again:
   `curl -s -X POST http://127.0.0.1:8000/v1/approvals/<id> -H 'Content-Type: application/json' -d '{"decision": "deny"}'`.
   Expected: `409` with an already-decided error; the first decision stands.
3. **Survive a crash.** Start `harnesswalk serve --provider fake`, pause a cancel turn, kill the
   server with `kill -9`, start it again, and approve the request by id. Expected: the start-up
   log says "1 turn(s) waiting on an approval re-attached", and the turn finishes `done` with exit
   `answered`.

## Check yourself

### 1. Why does Approval save the record and the checkpoint before it emits `node_paused`?

- [ ] So the card renders faster.
- [x] So a decision that arrives at once, or a crash during the wait, finds everything already on disk.
- [ ] Because events cannot be emitted inside a span.

Answer: the order of operations makes the pause durable. After a restart the gateway re-attaches
the turn from the stored record, checkpoint and events.

### 2. Nobody answers an approval before it expires. What happens to the call?

- [ ] It runs, because no one objected.
- [x] It is decided `expired`, which counts as a deny, and nothing runs.
- [ ] The turn stays paused forever.

Answer: expiry fails safe. The sweeper decides the record as expired and the model is told the
action was not approved.

### 3. Two people press Approve on the same card at the same moment. What stops a double run?

- [ ] The browser disables the button.
- [ ] The audit log rejects the second record.
- [x] The decision is an `UPDATE` that only matches a pending row, so the second one changes nothing and fails.

Answer: the compare-and-set in `_record` lets exactly one decision win; the other gets
`ApprovalAlreadyDecided` and a `409`.

## Further reading

- [NIST AI 600-1, Artificial Intelligence Risk Management Framework: Generative AI Profile (2024)](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf), on human oversight of AI actions
- [`spec/contracts/approval_queue.md`](../spec/contracts/approval_queue.md)
