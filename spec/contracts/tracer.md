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
