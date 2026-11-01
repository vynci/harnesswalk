---
node: trace
try:
  fixture: tool_call
  prompt: "Where is order A-1001?"
---

# Trace

> Every event of every turn, the cost receipt, and replay.

## What it does

Every node in the harness runs inside a span, and every span emits events: `node_enter` when it
starts, `node_exit` (or `error`) when it ends, `edge` when a request moves along an arrow of the
diagram, and a few point events such as `approval_requested` or `message_delta`. Each event has a
`seq` number within its turn, the turn's `trace_id`, the tenant, and a short summary of counts and
decisions. The trace store subscribes to the event bus and writes each event to SQLite the
moment it is emitted, exactly as emitted. The diagram you are watching is drawn from the same
stream, live; the history in the chat, replay, step mode, the eval scorers and the receipts all
read it back from the store.

The Trace node itself runs once at the end of each turn, after the reply and before
`turn_end`. It confirms the turn was persisted (`persisted=True`) and computes the cost receipt
from the node exits (its child `cost`, chapter 19). Summaries never carry the message or matched
values; when `trace.capture_content` is on, heavier content such as the assembled context or a
model request goes to separate detail records that an event points to with `detail_ref`. The
store is the harness's memory of itself: what happened, in what order, and what it cost.

## What breaks without it

The node cannot be switched off, but its store is one file, so you can take it away.

Pack `_starter`, fixture `tool_call`, prompt `Where is order A-1001?`. Run a scratch server:

```sh
HARNESSWALK_DATA_DIR=/tmp/hw-trace uv run harnesswalk serve --port 8813 --provider fake
```

Send the prompt (or post it with curl) and note the session and turn ids. `GET
/v1/sessions/<session_id>/turns` lists the turn, `GET /v1/turns/<turn_id>` has its summary and
cost, and the Replay button under the reply plays it back on the diagram. Now stop the server,
move `/tmp/hw-trace/trace.db` out of the way, and start it again. The session list answers
`[]`, the turn summary answers `404 turn_not_found`, and replay has nothing to play, even though
`session.db` still holds the conversation and the model would answer the next turn as before. The
harness still works; it has lost every record of how it worked. No replay, no receipt, no eval of
past turns, nothing to debug yesterday's bad answer with. Move the file back and everything
returns.

## The code walk

[`node`](../harnesswalk/nodes.py#node) is where events are born. Every component wraps its work
in `async with node(ctx, "<id>") as span`, which emits `node_enter`, gives the component a
`Span` to fill with `span.set(...)` or `span.skip(...)`, and emits `node_exit` or `error` on the
way out, with the duration. Because every node uses this one helper, no node can forget to
report.

[`TurnEmitter`](../harnesswalk/events.py#TurnEmitter) builds the events. [`TurnEmitter._emit`](../harnesswalk/events.py#TurnEmitter._emit)
assigns `seq`, timestamps and ids, refuses anything after `turn_end`, validates the event
against `spec/events.schema.json` when `trace.validate_events` is on, and publishes it on the
[`EventBus`](../harnesswalk/events.py#EventBus). The emitter also enforces nesting: a span must
close before its parent does, and [`TurnEmitter._check_node`](../harnesswalk/events.py#TurnEmitter._check_node)
refuses a node id that is not in `spec/diagram/diagram.json`. That is what makes the indented
walk in the CLI output possible, and why the diagram cannot show a node the code does not have.

[`Tracer.record`](../harnesswalk/ops/trace.py#Tracer.record) is the bus subscriber. It inserts
the event, and if SQLite refuses (a locked file), it buffers the event instead of dropping it:
stream events are never lost. It also feeds live readers and keeps a one-row-per-turn index for
the trace query.

[`Tracer.subscribe`](../harnesswalk/ops/trace.py#Tracer.subscribe) serves `GET
/v1/turns/{id}/events`: stored events after the cursor, then live ones, until `turn_end`. A
browser that reconnects with `Last-Event-ID` never sees an event twice.
[`Tracer.replay`](../harnesswalk/ops/trace.py#Tracer.replay) is the same read without the live
part, which is all replay needs: no node runs and no model is called.

[`run_trace_node`](../harnesswalk/ops/trace.py#run_trace_node) is the Trace span at the end of a
turn, and [`full_receipt`](../harnesswalk/ops/trace.py#full_receipt) is what `GET
/v1/trace/turns/{id}/receipt` returns: time and cost per node, and one line per model call.

## Try this

1. **Dump a turn.** Run the `tool_call` prompt with the CLI, then
   `uv run harnesswalk events <turn_id> | head -3`. Expected: JSON Lines starting with
   `turn_start` at `seq` 0, then `node_enter` for `gateway` with `phase: receive`; the last line
   is `turn_end`. The CLI said how many were stored (72 for this turn).
2. **Replay without a model.** In the web app, press Replay under a finished reply, then drag the
   slider. Expected: the diagram animates the turn again, node by node, and no new turn appears in
   the history. Stop the server and start it with another provider: the replay is the same.
3. **Query the store.** With a few turns stored, call
   `curl -s 'http://127.0.0.1:8000/v1/trace/turns?node=policy&status=done'`. Expected: only turns
   that ran a tool (and so entered policy) are listed, each with its receipt totals. Open one in
   the Trace node's Turns tab and read its per-node receipt.

## Check yourself

### 1. How does the diagram know which node is active?

- [ ] It polls each node for its state.
- [x] It reads the same event stream the trace stores: `node_enter` and `node_exit` per span.
- [ ] The gateway sends it a status message after each step.

Answer: the animation, the store, replay and the evals all read one stream, so the picture
cannot disagree with what was recorded.

### 2. What happens to an event when SQLite is briefly locked?

- [ ] It is dropped and counted.
- [x] It is buffered and written on the next successful insert.
- [ ] The turn fails.

Answer: `Tracer.record` keeps stream events in a buffer rather than lose them; only detail
records are sacrificed under pressure.

### 3. Why does replay never call the model?

- [ ] Because the fake provider is used for replay.
- [ ] Because replay re-runs the nodes in a sandbox.
- [x] Because it only reads the stored events of the turn and plays them back.

Answer: `Tracer.replay` yields stored events; no node runs. That is why a replay is free and
identical whatever provider is configured.

## Further reading

- [OpenTelemetry semantic conventions for generative AI](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [W3C Trace Context](https://www.w3.org/TR/trace-context/), where the 32-hex-digit `trace_id` comes from
- [`spec/events.md`](../spec/events.md) and [`spec/contracts/tracer.md`](../spec/contracts/tracer.md)
