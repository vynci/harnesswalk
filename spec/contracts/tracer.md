# Contract: Tracer

Nodes: `trace` (LLM Ops, kind `store`) and its expert child `cost`.

## Purpose

Persist every event of every turn, serve live and stored streams, and compute the cost receipt.
The live diagram, the trace viewer, step mode, replay and evals all read from here.

## Methods

```
interface Tracer {
  record(event: Event) -> void                               // called by the emitter for every event
  subscribe(tenant_id: string, turn_id: string, after_seq?: int) -> async stream<Event>
  get_turn(tenant_id: string, turn_id: string) -> [Event]
  get_detail(tenant_id: string, detail_ref: string) -> object | null
  put_detail(ctx: TurnContext, event_id: string, record: object) -> string   // returns detail_ref
  list_turns(tenant_id: string, filter?: TurnFilter, page?: Page) -> [TurnSummary]
  replay(tenant_id: string, turn_id: string) -> async stream<Event>
  receipt(tenant_id: string, turn_id: string) -> Receipt
  export_otel(tenant_id: string, turn_id: string) -> [OtelSpan]              // optional
}

record TurnSummary { turn_id: string, trace_id: string, session_id: string, started_at: timestamp, status: string, path: [string], cost: Cost }
record Receipt     { turn_id: string, total: Cost, by_node: {node_id: Cost}, by_model: {string: Cost}, latency_ms: {node_id: number} }
```

## Inputs and outputs

- In: every event from the emitter; detail records from nodes (assembled context, prompts, tool results).
- Out: live and stored event streams, replays, turn listings, cost receipts, optional OpenTelemetry spans.

## Invariants

- `record` MUST validate each event against `events.schema.json` in dev mode and in tests, and
  MUST reject events whose `tenant_id`, `turn_id` or `trace_id` differ from the turn's.
- Events are stored exactly as emitted. Stored order is `seq` order.
- `subscribe` delivers events in `seq` order with no gaps; after `turn_end` the stream completes.
- Every read method takes `tenant_id` and never crosses it.
- `replay` emits stored events without calling any node. Timing MAY be compressed.
- `receipt` sums `cost` from node exits only (never adds `turn_end.cost` to them); the result MUST
  equal `turn_end.cost`.
- Detail records may contain full prompts and tool results; they follow the same tenant
  partitioning and the mode's retention (public TTL hard delete).
- The `trace` node span runs once at the end of each chat turn to flush and compute the receipt.
  Events emitted after the `trace` span closes (only `turn_end`) are still recorded.

## Failure behaviour

If the store is unavailable, events are buffered in memory up to `trace.buffer_events` and the
turn continues; overflow drops detail records first, never stream events. The `trace` span exits
with `persisted: false`.

## Events

`node_enter` / `node_exit` on `trace` (summary `events`, `persisted`, `store`); optional `cost`
child (summary `input_tokens`, `output_tokens`, `usd`). Edges: `loop_to_trace`, `trace_to_eval`,
`trace_to_cost`.

## Configuration keys (`config/harness.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `trace.backend` | `sqlite` | `sqlite` or `postgres` |
| `trace.retention_days` | `30` | Private and dev; public follows the session TTL |
| `trace.buffer_events` | `10000` | In-memory buffer when the store is down |
| `trace.capture_content` | `true` in dev | Store full prompts and tool results in detail records |
| `trace.otel.endpoint` | unset | Optional OTLP export |

## P4 additions

- **Turn index.** The store keeps one row per turn (ids, kind, channel, status, exit, steps,
  cost, duration, the simple-view path and every node entered), written at `turn_start` and
  completed at `turn_end`, backfilled for turns stored before P4. `list_turns` filters by
  session, node, status, kind and start time and pages with a `turn_id` cursor
  (`GET /v1/trace/turns`).
- **Receipt.** Adds `nodes` (ms, count and cost per node, every node) and `calls` (one row per
  `model_layer` exit: step, provider, model, tokens, usd, ms, fallback), plus online scores and
  feedback. `total` still equals `turn_end.cost`.
- **Ops events** (`events.md` section 10) are stored in the same database, per tenant and
  stream, validated against the schema like turn events.
- **OpenTelemetry export** (`export_otel`): `harnesswalk/ops/otel.py` turns a finished turn into
  spans following `events.md` section 8 (root `harnesswalk.turn`, one span per node span, point
  events as span events, span ids derived from the `event_id` of the opening event) and exports
  them over OTLP when `HARNESSWALK_OTLP_ENDPOINT` or `trace.otel.endpoint` is set. Needs the
  optional extra `otel` (`opentelemetry-sdk`, `opentelemetry-exporter-otlp`). Export runs off the
  event loop and never fails a turn.

## Payloads

Every content-bearing hop of a chat turn records what it actually carried as a typed detail
record, when `trace.capture_content` is on (no detail is stored when it is off). The event
keeps only its small `summary` and a `detail_ref`; the one summary key payloads add is
`has_payload: true` on an event whose detail holds a payload, so a reader knows which events to
fetch without fetching every detail.
Implementation: `harnesswalk/ops/payloads.py`; the UI shows them on the inspector's Payload
tab.

**Where.** The `node_enter` and the `node_exit` of `gateway`, `input_guardrails`,
`context_builder`, `model_layer` and `output_guardrails` each carry a `detail_ref`. The enter
record is the node's input, stored before a step-mode breakpoint pauses on it, so a reader sees
what is about to be sent before pressing Next; the exit record is the whole hop. A detail
record is an object with a `payload` key (beside the keys a node already kept there, such as
the context builder's `items` and `messages`, or the model layer's `route` and `request`):

```
payload = { node, shape, at: "enter" | "exit", version: 1, ...fields per shape }
Text    = { text: string, chars: int, truncated: bool }   // text is cut at 32 000 characters
```

`shape` names the fields, so a reader renders by shape and never by node id (`message` for the
gateway, `input_checks`, `context`, `model_call`, `output_checks` in the order of the table
below). `chars` is the full length and `truncated` says whether `text` was cut. Tool-call arguments
whose JSON is longer than the cap come as `arguments: null` with `arguments_text: Text`.

| Node | `at` | Shape |
|---|---|---|
| `gateway` | enter, exit (`phase: receive`) | `phase`, `channel`, `received: Text \| null`, `withheld`, `chars` |
| `gateway` | enter (`phase: reply`) | `phase`, `reply: Text` |
| `gateway` | exit (`phase: reply`) | `phase`, `channel`, `received: Text \| null`, `withheld`, `redacted: Text \| null` (the text the model saw; null when the input guardrails blocked), `blocked`, `placeholders: {count, kinds: {kind: n}, tokens: ["[EMAIL_1]"]}`, `reply: Text` |
| `input_guardrails` | enter | `input: Text \| null`, `withheld`, `chars` |
| `input_guardrails` | exit | `input`, `withheld`, `chars`, `output: Text \| null` (the clean text; null when blocked), `action`, `blocked`, `checks: [Check]`, `warnings`, `placeholders`, `disabled` (when skipped) |
| `context_builder` | enter | `input: Text` (the clean input), `budget_tokens` |
| `context_builder` | exit | `sections: [Section]`, `total_tokens`, `messages`, `used_tokens`, `budget_tokens`, `limit_tokens`, `reserve_output_tokens`, `token_estimator` |
| `model_layer` | enter | `step`, `request: Request` |
| `model_layer` | exit | `step`, `provider`, `model`, `request: Request`, `reply: {...Text, tool_calls: [ToolCall], finish_reason}` |
| `output_guardrails` | enter | `before: Text` (the loop's answer) |
| `output_guardrails` | exit | `before: Text`, `after: Text` (the reply that goes on), `action`, `repairs` (the repair kinds: `length`, `citations`, `pii`), `reasons`, `checks: [Verdict]`, `restored` (placeholders put back), `restore_mode`, `disabled` (when skipped) |

```
Check    = { check, verdict: "pass" | "redact" | "warn" | "block", looked_at: {stage, chars},
             findings: {kind: n}, change: string | null, reason: string | null, score? }
Section  = { index, message, role: "system" | "user" | "assistant" | "tool",
             kind: "system" | "skill" | "retrieved" | "history" | "user" | "assistant" | "tool",
             label, sources: [{label, store?, id?, doc?, chunk?, n?, score?, call_id?}],
             tokens, ...Text, tool_calls?: [ToolCall] }
Request  = { sections: [Section], total_tokens, messages, tools: [tool name] }
ToolCall = { call_id, name, arguments }
Verdict  = { check, verdict: "pass" | "repair" | "block" | "skipped", note }
```

- `looked_at.stage` is `received` or `after <check>`: the text a check read, after the checks
  before it changed it. `findings` are counts by kind, never a matched value; `change` says
  what the check did in words ("replaced 1 email with placeholders").
- The sections are the assembled context in order. `message` is the index of the model
  message a section belongs to; sections of one message, joined by a blank line, are that
  message's content (the system message is the system prompt, the skills index, the
  documents index and the data rule; the retrieved-data message is one section per quoted
  block). `kind` `retrieved` sections list their `sources` (facts and events by id, knowledge
  passages as `[n] citation` with `doc` and `chunk`); `history` is earlier turns and the
  running summary; `skill` is the skills index. `tokens` is the configured estimator's count
  of the section's text and `total_tokens` their sum; `used_tokens` is the builder's own count
  (the slices), `budget_tokens` the total budget, `limit_tokens` the budget minus the output
  reserve.
- A model request reuses the context builder's sections for the messages it assembled; the
  messages the loop added after them (the model's earlier steps with their tool calls, the
  tool results) are sections of kind `assistant` and `tool`.

**Tenancy.** Details are stored per tenant (`put_detail` takes the turn's tenant, `get_detail`
reads inside one) and served by `GET /v1/trace/turns/{turn_id}/details/{event_id}`
(`http_api.md`, Trace), which answers only for a turn of the caller's own tenant (or a case
turn of one of its eval runs, as the receipt does): any other turn is `404 turn_not_found`, an
event without a stored detail `404 detail_not_found`.

**Public mode.** A payload holds the user's own texts in their own tenant, with their personal
data already replaced by the input guardrails' placeholders, plus the model's replies: nothing
a reader of the turn has not already sent or been shown, so it adds no exposure. The text as
typed, before redaction (`received`, `input`), is kept only when the guardrails would not
change it, or in the modes of the PII restore rule (`input_guardrails.pii.restore_modes`,
default `dev`, never `public`); elsewhere it is `null` with `withheld: true`, and the redacted
text stands in for it. The placeholder map is never stored, only its count, kinds and tokens.
Details follow the mode's retention (the public TTL sweeper hard deletes them with the tenant).
