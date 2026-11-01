---
node: memory
try:
  pack: _starter
  fixture: trailhead_return
  prompt: "Can I return order A-1003, the Trailhead backpack?"
---

# Memory

> Five stores, one retrieval interface: procedural, session, semantic, episodic, knowledge.

## What it does

"Memory" in an agent is not one thing. How the agent should behave (the system prompt and its
skills) is memory. What was said earlier in this conversation is memory. A durable fact about
a customer, a dated event from last week and a page of the returns policy are memory too. They
differ in what they hold, how they are ranked and who may write them, so harnesswalk keeps them
in five stores: procedural, session, semantic, episodic and knowledge. The simple view draws
them as one node; the expert view opens it into the five, and chapter 16 walks each one.

What they share is the read side. The context builder enters Memory once per turn with the
user's message, and Memory asks each store in a fixed order with the same query shape: the
text, the time, how many hits to return (`top_k`) and a token budget. Each store answers with
hits of the same shape: an id, the text, a score, a token count and a source reference. Pack
content (the shop's documents and seed facts) lives in a shared scope every tenant reads;
anything learned about a tenant lives under that tenant only. If one store fails, Memory marks
it degraded and the turn goes on without it.

## What breaks without it

Memory cannot be switched off as a node, but which stores it reads is one line of config,
`context.stores` in `config/context.yaml`. Leave out the three retrieving stores and the agent
keeps its instructions and the conversation, and loses everything it knows.

Pack `_starter`, fixture `trailhead_return`, prompt:

```text
Can I return order A-1003, the Trailhead backpack?
```

With all five stores, four memory badges light (session joins from the second turn): procedural, a seed fact
("Order A-1003 is one Trailhead 28 backpack in moss, delivered on 20 October"), a seed event
(support explained the 30 day window), six knowledge passages, and the reply cites `[6]` and
`[5]` from the returns policy. Now run it with only procedural and session:

```sh
uv run harnesswalk turn "Can I return order A-1003, the Trailhead backpack?" \
  --provider fake --fixture trailhead_return --set 'context.stores=[procedural, session]'
```

The walk shows `semantic`, `episodic` and `knowledge` as `skipped`, Memory reports
`stores_queried: [procedural, session]`, and the context shrinks from about 650 tokens to 164.
Even the procedural slice is smaller (151 tokens instead of 195), because the list of documents
the model may read is gone too. The scripted reply still cites `[6]` and `[5]`, but no passage
backs them (the output guardrails count `invalid: 2`), the delivery date came from nowhere, and
the memory writer does not run at all, since there is no episodic store to write the turn to.
A real model in this position either guesses or says it does not know.

In the web app the same change is an edit: open Context builder, the Config tab, Edit, and
remove the three names from `stores`. Put them back afterwards (or roll back in Release).

## The code walk

[`Memory`](../harnesswalk/context/stores/__init__.py#Memory) is the group node. Its
constructor takes the stores that exist; `semantic`, `episodic` and `knowledge` are `None` when
`context.stores` leaves them out, and [`Memory.enabled`](../harnesswalk/context/stores/__init__.py#Memory.enabled)
lists the ones that are on. The wiring is in
[`PackParts._build_artifact_components`](../harnesswalk/runtime.py#PackParts._build_artifact_components),
which reads `context.stores` every time the config is reloaded.

[`Memory.retrieve_all`](../harnesswalk/context/stores/__init__.py#Memory.retrieve_all) is the
one retrieval pass. Procedural and session always run (an agent without instructions or its
own conversation is not an agent). The three retrieving stores each get a `Query` with their
`top_k` and slice budget. Notice the `except Exception` around each store: a failure is
recorded in `degraded` and the turn continues with an empty slice, which the context builder
then reports.

[`records.py`](../harnesswalk/context/records.py#Hit) holds the shared shapes.
[`Query`](../harnesswalk/context/records.py#Query) and [`Hit`](../harnesswalk/context/records.py#Hit)
are the request and the answer every store speaks. [`trim_to_budget`](../harnesswalk/context/records.py#trim_to_budget)
keeps the best hits that fit both limits; it skips a hit that is too big rather than stopping,
so a small, lower-ranked hit can still fit after a large one is left out.

[`PACK_SCOPE`](../harnesswalk/context/records.py#PACK_SCOPE) is the most important line in the
file. Pack content is stored under the reserved scope `_pack`, never under a real tenant, and
a tenant id starting with `_` may not write. That is how one pack's documents are shared while
memories stay private. [`require_tenant`](../harnesswalk/context/records.py#require_tenant)
makes a store call without a tenant a programming error, not a silent global read.

## Try this

1. **Count the hits.** Send the Trailhead prompt and open the Memory node. Expected: its last
   summary lists `hits` per store, with `semantic: 1`, `episodic: 1` and `knowledge: 6`.
2. **Edit the config from the web.** Remove `semantic`, `episodic` and `knowledge` from
   `stores` in the Context builder's Config tab and send the prompt again. Expected: the three
   stores are grey on the diagram, the memory badges stay dark, and Memory writer is missing
   from the walk. Restore the line afterwards.
3. **Break a store on purpose.** Add `raise RuntimeError("demo")` as the first line of
   `EpisodicStore.retrieve` in `harnesswalk/context/stores/episodic.py`, run the Trailhead turn
   from the CLI with `--json`, then undo the change. Expected: the turn still answers; the
   episodic span ends in error `store_unavailable`, and Memory and the context builder both
   report `degraded: ["episodic"]`.

## Check yourself

### 1. Why are pack documents stored under `_pack` instead of under each tenant?

- [ ] To save disk space.
- [x] So every tenant on the pack can read them while nothing a tenant writes lands in the shared scope.
- [ ] Because documents have no owner.

Answer: shared content and private memories need different rules. A reserved scope that no
tenant may write keeps the two apart.

### 2. Which stores does Memory always read, whatever `context.stores` says?

- [ ] Semantic and knowledge.
- [ ] None of them.
- [x] Procedural and session.

Answer: the instructions and the conversation so far are needed on every turn; only the three
retrieving stores can be switched off.

### 3. What happens when one retrieving store raises an error during a turn?

- [x] It is marked degraded, its slice is empty, and the turn goes on.
- [ ] The turn fails with an error.
- [ ] Memory retries it until it succeeds.

Answer: `retrieve_all` catches the failure per store, records it in `degraded`, and the context
builder reports it on its summary.

## Further reading

- [Sumers et al., Cognitive Architectures for Language Agents (2023)](https://arxiv.org/abs/2309.02427), the source of the procedural, semantic and episodic split
- [Lewis et al., Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks (2020)](https://arxiv.org/abs/2005.11401)
- [`spec/contracts/store.md`](../spec/contracts/store.md)
