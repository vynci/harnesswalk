# Contract: Context builder

Node: `context_builder` (context plane, kind `code`).

## Purpose

Assemble what the model sees, inside a token budget, from the stores, the session and the new
input. Make the result inspectable: a budget bar per slice and the exact assembled text.

## Methods

```
interface ContextBuilder {
  build(ctx: TurnContext, input: Message, budget?: Budget) -> AssembledContext
  fetch_on_demand(ctx: TurnContext, ref: OnDemandRef) -> Slice      // exposed to the model as a tool
  last(ctx: TurnContext) -> AssembledContext                          // for the inspector
}

record Budget { total_tokens: int, per_slice: {SliceName: int}, reserve_output_tokens: int }

SliceName = "procedural" | "session" | "semantic" | "episodic" | "knowledge" | "user"

record Slice {
  name: SliceName
  tier: "always" | "retrieved" | "on_demand"
  tokens: int
  items: [SliceItem]                             // each with source ids for citations and pointers
  truncated: bool
}

record AssembledContext {
  messages: [Message]                            // what the model layer sends
  slices: [Slice]                                // in order
  used_tokens: int
  budget: Budget
  stable_prefix_tokens: int                      // tokens before the first per-turn slice
  compaction?: { turns_compacted: int, summary_tokens: int, pointers: [string] }
  on_demand_index: [OnDemandRef]                 // skills and documents the model may fetch by name
}
```

## Order (stable first)

1. `procedural` always tier: system prompt (skill zero), then the skills index (names and one
   line each, not bodies).
2. `semantic` facts, then `episodic` events, then `knowledge` chunks (retrieved tier).
3. `session`: running summary, then recent turns verbatim.
4. `user`: the new, guardrail-cleaned input.

## Invariants

- `used_tokens` MUST be at most `budget.total_tokens - budget.reserve_output_tokens`.
- Each slice MUST be at most its `per_slice` budget. Overflow is handled per slice: session by
  compaction into the running summary with pointers to the original turns; retrieved slices by
  dropping the lowest-ranked items; `procedural` overflow is a configuration error at load time.
- Truncation and compaction MUST be visible in the exit summary (`compacted`, per-slice tokens).
- Every store call MUST pass `ctx.tenant_id`.
- MUST count tokens with the tokenizer of the routed model when known, otherwise with the
  configured estimator (`context.token_estimator`), and say which in the detail record.
- The full `AssembledContext` MUST be written to the trace (via `detail_ref`), never inline in
  the event summary.
- Deterministic for the same stores, input and config.

## Failure behaviour

| Condition | Result |
|---|---|
| A retrieved store fails | That slice is empty, the span exits `done` with `degraded: [store]`; the turn continues |
| `procedural` fails | `error` `store_unavailable`; the turn ends with an error notice (no prompt, no answer) |

## Events

`node_enter` (summary `budget_tokens`) and `node_exit` (summary in `events.md`) on
`context_builder`; it opens `memory` inside its span. Edges: `input_guardrails_to_context_builder`,
`memory_to_context_builder`, `context_builder_to_loop`.

## Configuration keys (`config/context.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `context.total_tokens` | `16000` | Total budget |
| `context.reserve_output_tokens` | `1024` | Kept free for the answer |
| `context.slices.procedural` | `3000` | |
| `context.slices.semantic` | `800` | |
| `context.slices.episodic` | `800` | |
| `context.slices.knowledge` | `4000` | |
| `context.slices.session` | `5000` | |
| `context.compaction.threshold` | `0.8` | Fraction of the session slice that triggers compaction |
| `context.compaction.keep_recent_turns` | `4` | Verbatim turns kept |
| `context.retrieval.top_k` | `{semantic: 5, episodic: 5, knowledge: 6}` | |
| `context.token_estimator` | `chars_div_4` | Fallback token counter |
