---
node: audit
try:
  fixture: cancel_order
  prompt: "Please cancel order A-1002."
---

# Audit

> The append-only record of consequential decisions, and why it is not the trace.

## What it does

The audit log is the record you would show someone who asks "who let this happen?" It holds
decisions with consequences: every policy denial and approval request, every allowed call that
is not a plain read, every human decision on an approval (with who made it), every non-read call
that actually ran (with its outcome, and the state before and after for writes), and every
memory write. A plain lookup like "where is my order?" leaves no audit record at all, which is
why the Audit node stays dark on a simple question and lights three times on a cancellation.

Two properties make it an audit log and not just a table. It is append-only, enforced by the
storage itself: SQLite triggers reject every `UPDATE` and `DELETE`. And it is hash-chained per
tenant: each record stores the hash of the record before it, and its own hash covers that, its
id, its time and its content. Change one byte of an old record, or remove one, and every hash
after it stops matching. Arguments are kept only as a SHA-256 digest; the full call lives in the
trace under the same `trace_id`. The trace is for debugging and carries content; the audit log
is for accountability and is kept.

## What breaks without it

The node cannot be disabled, and the triggers stop casual edits. What the chain gives you is the
ability to notice when someone goes around them, so that is the demonstration.

Pack `_starter`, fixture `cancel_order`, prompt `Please cancel order A-1002.` Approve the card.
The walk shows three audit spans: `policy_decision` (needs approval), `approval_decision`
(approve, with `decided_by`) and `tool_executed` (executed, with before and after). Open the
Audit node and press Verify: `ok`, with the number of records.

Now play the insider who wants the record to say no human was asked. Run a server on a scratch
data directory (`HARNESSWALK_DATA_DIR=/tmp/hw-audit uv run harnesswalk serve --provider fake`), do
the cancellation there, and try to rewrite history:

```sh
sqlite3 /tmp/hw-audit/audit.db \
  "UPDATE audit SET entry = replace(entry, 'needs_approval', 'allow') WHERE entry LIKE '%needs_approval%'"
```

SQLite refuses: `audit is append-only`. Drop the trigger first (`DROP TRIGGER audit_no_update;`)
and the same update goes through. Press Verify again, or ask the API:

```sh
curl -s 'http://127.0.0.1:8000/v1/audit?limit=1' | python3 -m json.tool | head -8
```

`verify` now reads `ok: false` with `first_bad_record` naming the edited row. Without the chain
the edit would be invisible. Without the log at all, the only record of who approved the refund
would be the trace, which holds content you may not want to keep, can be switched off with
`trace.capture_content`, and is not built to prove anything.

## The code walk

[`AuditLog`](../harnesswalk/control/audit.py#AuditLog) is the store. Its
[`SCHEMA`](../harnesswalk/control/audit.py#SCHEMA) is worth reading: one table and two triggers
whose only job is to raise `audit is append-only`. Putting the rule in the storage means no code
path, however careless, can update a record.

[`AuditLog.append`](../harnesswalk/control/audit.py#AuditLog.append) reads the tenant's last
hash and inserts the new record inside one write transaction, so a server and a CLI writing the
same file cannot both chain onto the same parent and fork the chain. A storage error becomes
`store_unavailable`, and the Tools node turns that into a denied call: an action nobody could
record does not run.

[`_hash`](../harnesswalk/control/audit.py#_hash) is the chain: SHA-256 over the previous hash
plus a canonical JSON of the record (sorted keys). [`AuditLog.verify`](../harnesswalk/control/audit.py#AuditLog.verify)
recomputes it from the genesis value and returns the first record whose stored hash disagrees.

[`args_digest`](../harnesswalk/control/audit.py#args_digest) keeps a fingerprint of the
arguments instead of the arguments. You can prove which call ran by hashing the call in the trace,
without the audit log holding personal data. [`AuditEntry`](../harnesswalk/control/audit.py#AuditEntry)
lists every field a record can carry.

[`record`](../harnesswalk/control/audit.py#record) is the `audit` span the diagram draws, with
the short hashes on its summary. The callers are in [`Tools._invoke`](../harnesswalk/execution/tools.py#Tools._invoke)
(policy decisions), [`Tools.after_approval`](../harnesswalk/execution/tools.py#Tools.after_approval)
and [`Tools._execute`](../harnesswalk/execution/tools.py#Tools._execute). The memory writer
appends its `memory_written` records directly, without a span, so they are in the chain even
though the node stays dark on those turns.

## Try this

1. **Stay dark.** Send "Where is order A-1001?" with fixture `tool_call`. Expected: no `audit`
   span in the walk. Then set `audit.record_read_allows: true` in `config/harness.yaml`, restart,
   and send it again. Expected: one `policy_decision` record for the read.
2. **Read a chain.** After a cancellation, run
   `curl -s 'http://127.0.0.1:8000/v1/audit?limit=5'`. Expected: records oldest first, each
   with a `prev_hash` equal to the `hash` of the one before it, and `verify.ok: true`.
3. **Delete instead of edit.** On a scratch data directory, drop `audit_no_delete` and delete one
   record in the middle of the chain. Expected: Verify reports `ok: false`, and the first bad
   record is the one right after the deleted row, because its `prev_hash` no longer matches.

## Check yourself

### 1. Why does a plain order lookup leave no audit record?

- [ ] Because the audit store is too slow for reads.
- [x] Because only decisions with consequences are audited; read-only allows go to the trace only.
- [ ] Because policy skips read calls.

Answer: policy still decides every read, but an allowed read changes nothing, so it is recorded
in the trace and not in the audit log (unless `record_read_allows` is on).

### 2. Someone drops the trigger and edits an old record. How is it noticed?

- [ ] The trigger restores the old value.
- [x] Verify recomputes the hash chain and reports the first record that no longer matches.
- [ ] The trace overwrites the audit log.

Answer: each hash covers the previous one, so any change breaks the chain from that record on.

### 3. Why does the audit log store a digest of the arguments rather than the arguments?

- [x] So it can prove which call ran without keeping personal data in a record that is never deleted.
- [ ] Because arguments are too large to store.
- [ ] Because the model may change them.

Answer: the full call is in the trace under the same `trace_id`; hashing it reproduces the
digest, so the proof survives without the content.

## Further reading

- [Crosby and Wallach, Efficient Data Structures for Tamper-Evident Logging (USENIX Security 2009)](https://www.usenix.org/legacy/event/sec09/tech/full_papers/crosby.pdf)
- [SQLite documentation, CREATE TRIGGER and RAISE()](https://www.sqlite.org/lang_createtrigger.html)
- [`spec/contracts/audit.md`](../spec/contracts/audit.md)
