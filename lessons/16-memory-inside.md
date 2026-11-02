---
node: memory
covers: [procedural, session, semantic, episodic, knowledge]
try:
  pack: _starter
  fixture: trailhead_return
  prompt: "Can I return order A-1003, the Trailhead backpack?"
---

# Inside memory: the five stores

> What each store holds, how it ranks, and who may write it.

## What it does

Chapter 4 showed the shared read path. Turn on the Advanced view and Memory opens into five
stores. They answer the same query shape, and each answers it differently.

### Procedural

How the agent should behave: the system prompt (skill zero) and an index of the skills, one line
each. That text is always in context and forms the stable prefix. A skill's full body is not:
the model reads it on demand with the `read_skill` tool, so ten skills cost ten lines until one
is needed. Skills are artifacts, edited and versioned through Release.

### Session

The conversation so far: earlier turns verbatim, a running summary once they outgrow their slice
(compaction, chapter 3), and the loop's checkpoints. Only the gateway writes turns and only the
loop side writes the summary; each write checks a writer name.

### Semantic

Durable facts, such as "order A-1003 item: one Trailhead 28 backpack in moss, delivered on 20
October". Retrieval favours precision: a fact whose entity (A-1003) or whole key appears in the
question scores 1.0, otherwise it needs a cosine similarity of at least `min_score`. Pack seed
facts have scope `pack`; facts the memory writer adds have scope `tenant`.

### Episodic

Dated events, such as "support explained the 30 day return window". The score blends relevance
with recency (`0.7` and `0.3`, with a 30 day half-life), so last week's event can outrank a
slightly closer match from last year, and an irrelevant event never gets in however recent.

### Knowledge

Documents cut into chunks by heading and size, searched two ways at once: keywords (SQLite FTS5)
and vectors (local embeddings). The two rankings are fused by reciprocal rank, an optional
reranker reorders them, and each chunk carries a citation string such as "Returns policy, Return
window". `read_doc` reads a whole document on demand.

## What breaks without it

Pack `_starter`, fixture `trailhead_return`, prompt `Can I return order A-1003, the Trailhead
backpack?` Take one retrieving store away at a time with `--set 'context.stores=[...]'` and read
the context builder's summary:

| Left out | What the model loses | What the walk shows |
|---|---|---|
| `semantic` | That A-1003 is the moss backpack delivered on 20 October | `retrieved.semantic: 0`; the slice drops from 39 tokens to 0 |
| `episodic` | That support already explained the 30 day window to this customer | `retrieved.episodic: 0` |
| `knowledge` | The returns policy itself, and the documents index | `knowledge: 0`, procedural 151 tokens instead of 195, citations `invalid: 2`, no sources |

Each store fills a different gap, which is why "add a vector database" is not a memory design.
Without semantic, the agent does not know the order's facts; without episodic, it does not know
what already happened; without knowledge, it cannot back its answer with the policy, and the
output guardrails notice.

## The code walk

[`ProceduralStore.always_text`](../harnesswalk/context/stores/procedural.py#ProceduralStore.always_text)
is skill zero plus [`ProceduralStore.index_text`](../harnesswalk/context/stores/procedural.py#ProceduralStore.index_text),
and [`ProceduralStore.skill_body`](../harnesswalk/context/stores/procedural.py#ProceduralStore.skill_body)
is what `read_skill` returns. That split is the "always" and "on demand" tiers in two methods.

[`SessionStore.turns`](../harnesswalk/context/stores/session.py#SessionStore.turns) reads answered
turns only (blocked ones are not history), always filtered by tenant and session.
[`SessionStore.checkpoint`](../harnesswalk/context/stores/session.py#SessionStore.checkpoint)
holds the loop's state per step.

[`SemanticStore.retrieve`](../harnesswalk/context/stores/semantic.py#SemanticStore.retrieve)
does entity or key match first, similarity second, and marks each hit `match: entity` or
`similarity`. [`SemanticStore.write`](../harnesswalk/context/stores/semantic.py#SemanticStore.write)
refuses any writer but the memory writer and keeps the previous version on update.

[`EpisodicStore.score`](../harnesswalk/context/stores/episodic.py#EpisodicStore.score) is the
whole recency formula in three lines; [`EpisodicStore.retrieve`](../harnesswalk/context/stores/episodic.py#EpisodicStore.retrieve)
applies `min_relevance` before recency can help.

[`KnowledgeStore.retrieve`](../harnesswalk/context/stores/knowledge.py#KnowledgeStore.retrieve)
runs [`keyword`](../harnesswalk/context/stores/knowledge.py#KnowledgeStore.keyword) and
[`vector`](../harnesswalk/context/stores/knowledge.py#KnowledgeStore.vector), fuses them with
[`rrf`](../harnesswalk/context/records.py#rrf), and builds each hit's citation. Chunking is in
[`chunking.py`](../harnesswalk/context/chunking.py), embeddings in
[`embeddings.py`](../harnesswalk/context/embeddings.py): always local, hash vectors on the fake
provider, Ollama `nomic-embed-text` otherwise.

## Try this

1. **Session history.** Send "Hi there" and then "Where is order A-1002?" in one session with
   fixture `two_turns`. Expected: on the second turn Session reports `turns=1`, and the scripted
   model's check that the last message mentions A-1002 passes.
2. **A document in full.** Send "Tell me about the Trailhead 28 backpack warranty." with fixture
   `read_doc`. Expected: Knowledge retrieves chunks as usual, then the model calls `read_doc` on
   `trailhead_backpack` (`tools ... outcome=ok`) and answers with the 2 year warranty.
3. **Pack scope and tenant scope.** Run the `remember_fact` prompt, then open Semantic in the
   advanced view. Expected: the six seed facts with scope `pack` and `preferred name: Sam` with scope
   `tenant`. Send the same prompt as another tenant (`X-Harnesswalk-Tenant: other` with curl) and
   list that tenant's facts. Expected: the seeds, and no Sam.

## Check yourself

### 1. Why is a skill's body not in the context of every turn?

- [ ] Because skills are secret.
- [x] Because only the index is always included; the model reads a body on demand with `read_skill`.
- [ ] Because the context builder trims it every time.

Answer: the index costs one line per skill. The body enters as a tool result only when the model
decides it needs it.

### 2. How does knowledge combine keyword and vector search?

- [ ] It keeps whichever list is longer.
- [ ] It averages BM25 scores and cosines.
- [x] It fuses the two rankings with reciprocal rank fusion, then may rerank.

Answer: rank fusion needs no score calibration between the two methods; a chunk ranked well by
either list rises.

### 3. Why does an old but relevant event sometimes lose to a newer one?

- [x] Episodic scores blend relevance with an exponential recency decay.
- [ ] Old events are deleted after 30 days.
- [ ] The context builder sorts events by date.

Answer: `0.7 * relevance + 0.3 * recency`, where recency halves every 30 days by default.

## Further reading

- [Cormack, Clarke and Buettcher, Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods (SIGIR 2009)](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)
- [SQLite FTS5 extension](https://www.sqlite.org/fts5.html)
- [`spec/contracts/store.md`](../spec/contracts/store.md)
