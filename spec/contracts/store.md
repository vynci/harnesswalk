# Contract: Store (five kinds)

Nodes: `memory` (group), `procedural`, `session`, `semantic`, `episodic`, `knowledge` (context
plane, kind `store`; `procedural` is kind `prompt`).

## Purpose

One retrieval interface over five stores that differ in what they hold, how they rank and who
may write them. The `memory` node is the read side seen by the context builder; writes arrive
through `memory_writer`, `release`, `loop` and `gateway` only, as listed below.

## Methods

```
interface Store {
  kind: "procedural" | "session" | "semantic" | "episodic" | "knowledge"
  retrieve(ctx: TurnContext, query: Query) -> [Hit]
  get(ctx: TurnContext, id: string) -> Record | null
  write(ctx: TurnContext, records: [Record], writer: WriterToken) -> [string]   // ids
  list(ctx: TurnContext, page?: Page) -> [Record]                                 // inspector
}

interface Memory {                                   // the group node
  retrieve_all(ctx: TurnContext, query: Query, budgets: {kind: int}) -> {kind: [Hit]}
}

record Query { text: string, entities?: [string], now: timestamp, top_k: int, max_tokens: int }
record Hit   { id: string, kind: string, text: string, score: number, tokens: int, source: SourceRef }
record SourceRef { store: string, id: string, title?: string, uri?: string, version?: string }
record WriterToken { writer: "memory_writer" | "release" | "loop" | "gateway", proposal_id?: string }
```

`tenant_id` comes from `ctx` and is applied as a filter by the store, not by the caller.

## The five kinds

| Kind | Holds | Retrieval and ranking | Allowed writers | Versioned |
|---|---|---|---|---|
| `procedural` | System prompt (skill zero), skills (markdown with a name and a one-line description) | Prompt and skills index always included; a skill body is fetched by exact name, on demand | `release` | Yes, artifact versions |
| `session` | Turns, running summary, loop checkpoints, per session | Newest turns first, verbatim; summary ahead of them; no similarity ranking | `gateway` (turns), `loop` (checkpoints, summary) | No |
| `semantic` | Durable facts: `{subject, predicate, object, source, confidence}` | Exact entity match first (score 1.0), then lexical or vector similarity; drop below `min_score`; high precision over recall | `memory_writer` | Yes |
| `episodic` | Dated events: `{when, what, entities, source}` | `score = w_rel * relevance + w_rec * exp(-age_days / half_life_days)` | `memory_writer` | Yes |
| `knowledge` | Document chunks with document id, title, position | Hybrid: keyword (BM25 or full-text) and vector similarity fused by reciprocal rank, then rerank top `rerank_k`; every hit carries a citable `SourceRef` | `release` (ingestion of a dataset pack or upload) | Yes, by document version |

## Invariants

- Every method MUST take `ctx` with a `tenant_id`; a store MUST NOT return or write records of
  another tenant. A call without `tenant_id` MUST raise.
- `write` MUST check `writer` against the allowed writers above and reject anything else. This
  is how "semantic and episodic are written only by the memory writer" is enforced in code.
- Retrieval MUST be deterministic for the same data, query and config (ties broken by id).
- Retrieval MUST honour `max_tokens`; the store trims its own hits.
- Hits MUST carry a `SourceRef` so the output guardrails can check citations.
- Embeddings are computed locally by default (`stores.embeddings.provider`).

## Failure behaviour

A failing store returns an error to the caller; `memory.retrieve_all` records the failure, skips
that kind, and the context builder marks the slice degraded. Writes fail loudly (no silent drop).

## Events

`node_enter` / `node_exit` on `memory`, and optionally on each kind as expert children inside
the `memory` span. `knowledge` reports embedding cost on its exit when it embeds at query time.
Edges: `memory_to_context_builder` and its expert refinements `<kind>_to_context_builder`.

## Configuration keys (`config/context.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `stores.backend` | `sqlite` | `sqlite` (tiny profile) or `postgres` (pgvector) |
| `stores.embeddings.provider` | `ollama` | `ollama`, `fake` (hash embeddings for tests) |
| `stores.embeddings.model` | `nomic-embed-text` | |
| `stores.semantic.min_score` | `0.35` | |
| `stores.episodic.half_life_days` | `30` | |
| `stores.episodic.weights` | `{relevance: 0.7, recency: 0.3}` | |
| `stores.knowledge.chunk_tokens` | `400` | |
| `stores.knowledge.chunk_overlap` | `40` | |
| `stores.knowledge.rrf_k` | `60` | Reciprocal rank fusion constant |
| `stores.knowledge.rerank_k` | `20` | Candidates passed to the reranker |
| `stores.knowledge.reranker` | `none` | `none`, `cross_encoder`, `model` |
