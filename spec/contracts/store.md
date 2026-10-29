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
| `semantic` | Durable facts: `{id, tenant_id, user_id, key, value, entities, source, created, updated, version}` (a subject and predicate fold into `key`, the object is `value`) | Exact entity or key match first (score 1.0), then lexical or vector similarity; drop below `min_score`; high precision over recall | `memory_writer` | Yes |
| `episodic` | Dated events: `{id, tenant_id, session_id, ts, kind, text, outcome, turn_id, entities, source}` | `score = w_rel * relevance + w_rec * exp(-age_days / half_life_days)`, relevance 1.0 on an entity match; events under `min_relevance` are left out | `memory_writer` | Yes (append-only, version 1) |
| `knowledge` | Document chunks with document id, title, position | Hybrid: keyword (BM25 or full-text) and vector similarity fused by reciprocal rank, then rerank top `rerank_k`; every hit carries a citable `SourceRef` | `release` (ingestion of a dataset pack or upload) | Yes, by document version |

### Shared pack scope

Content released with a dataset pack (its documents, seed facts and seed events) is read by
every tenant that runs the pack, like the generic skills of `harness.md` section 5. It is
stored under the reserved scope `_pack` with the pack name, never under a real tenant. A store
reads the caller's tenant scope plus the `_pack` scope of the active pack; a tenant's own fact
with the same key shadows the seed. Tenant ids starting with `_` are reserved: the memory
writer rejects proposals from them. Seeds still go through the memory writer (`seed`), which
is the only holder of the writer token.

### Knowledge hits and citations

A knowledge `Hit` carries `{doc, chunk, score, citation}`: the document id (the file stem),
the chunk position in the document from 0, the fused score, and a citation string `"<title>,
<section heading>"` (`"<title>, page N"` for a PDF page, the title alone before the first
heading). The `SourceRef` is `{store: knowledge, id: "<doc>#<chunk>", title, uri:
"knowledge://<pack>/<doc>#<chunk>", version}`. Chunking is by heading (`#` to `###`) first,
then by size (`chunk_tokens`, `chunk_overlap`).

## Inputs and outputs

- In: a `Query` from `memory` (built by the context builder) or a `write` from an allowed writer.
- Out: ranked `Hit`s with `SourceRef`s; record ids for writes; records for the inspector.

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
| `stores.backend` | `sqlite` | Records: `sqlite` (the reference keeps records, versions and the keyword index in SQLite) |
| `storage.vectors` (`config/harness.yaml`) | `sqlite` | Vector index: `sqlite` (brute-force cosine) or `pgvector` (Postgres from `HARNESSWALK_DATABASE_URL` or `POSTGRES_*`; env `HARNESSWALK_VECTORS`) |
| `stores.memory.sqlite_path` | `data/memory.db` | The file behind semantic, episodic, knowledge and the memory writer |
| `stores.ingest_on_start` | `true` | Index the pack's documents and load its seeds at start-up when they changed |
| `stores.embeddings.provider` | `auto` | `auto` (fake on the fake provider, else Ollama when it has the model, else fake), `ollama`, `fake` (hash embeddings for tests); env `HARNESSWALK_EMBEDDINGS` wins |
| `stores.embeddings.model` | `nomic-embed-text` | |
| `stores.embeddings.dim` | `256` | Dimensions of the hash embeddings |
| `stores.semantic.min_score` | `0.35` | |
| `stores.episodic.half_life_days` | `30` | |
| `stores.episodic.weights` | `{relevance: 0.7, recency: 0.3}` | |
| `stores.episodic.min_relevance` | `0.2` | An event less relevant than this is left out however recent |
| `stores.knowledge.docs_dir` | `datasets/<pack>/docs` | Where the pack's markdown and PDF documents are |
| `stores.seeds_dir` | `datasets/<pack>` | Where `facts.jsonl` and `episodes.jsonl` are |
| `stores.knowledge.chunk_tokens` | `400` | |
| `stores.knowledge.chunk_overlap` | `40` | |
| `stores.knowledge.rrf_k` | `60` | Reciprocal rank fusion constant |
| `stores.knowledge.rerank_k` | `20` | Candidates per list, and passed to the reranker |
| `stores.knowledge.reranker` | `none` | `none`, or a reranker registered by name (`cross_encoder` and `model` are reserved names; none ships) |
| `stores.knowledge.min_vector_score` | `0.45` | A chunk found only by vector search needs this cosine |
| `stores.knowledge.min_keyword_coverage` | `0.5` | A keyword hit needs this share of the query's words, and at least two of them |
