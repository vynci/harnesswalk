---
node: context_builder
try:
  pack: _starter
  fixture: trailhead_return
  prompt: "Can I return order A-1003, the Trailhead backpack?"
---

# Context builder

> What the model actually sees, assembled inside a token budget, stable content first.

## What it does

A model has no memory of its own between calls. Everything it knows about this conversation,
this customer and this shop arrives in the request, as a list of messages. The context builder
writes that list. It asks memory for what is relevant (facts, past events, document passages,
earlier turns), gives each kind its own slice of a token budget, trims the lowest-ranked items
when a slice is too big, and puts the pieces in a fixed order: the system prompt and the skills
index first, then facts, events and passages, then the conversation, then the new message.

The order is not decoration. The first part (the system prompt, the skills index, the list of
documents) is the same on every turn, so a provider that caches prompt prefixes can reuse it,
and the node reports its size as `stable_prefix_tokens`. Knowledge passages are numbered `[1]`,
`[2]` and so on, and the numbering is kept on the turn so the output guardrails can later
check that every citation in the answer points at a passage the model was really shown. When
the conversation outgrows its slice, older turns are folded into a running summary
(compaction) with pointers back to the originals.

## What breaks without it

The builder cannot be switched off: without it the model would get only your message. But you
can starve it, which shows what it was doing for you.

Pack `_starter`, fixture `trailhead_return`, prompt:

```text
Can I return order A-1003, the Trailhead backpack?
```

Send it as is. The Context builder node reports `citations=6` and a budget bar per slice; the
knowledge slice uses about 350 tokens. The reply cites `[6]` and `[5]`, and the sources under
it name the returns policy (Return window, Return labels).

Now give knowledge a budget it cannot fit in. From the command line:

```sh
uv run harnesswalk turn "Can I return order A-1003, the Trailhead backpack?" \
  --provider fake --fixture trailhead_return --set context.slices.knowledge=150
```

The knowledge store returns three chunks and the builder trims one more (`trimmed` on its
summary), so only two passages from the product sheet remain and the returns policy is gone.
The fake provider still replies with its script, citing `[6]` and `[5]`, but there are only
two passages now: the sources list is empty and the output guardrails count `invalid: 2`. A
real model would have answered without the return window at all, because it never saw it. The
builder decides what the model knows; get it wrong and every node after it works on the wrong
facts.

## The code walk

[`ContextBuilder.build`](../harnesswalk/context/builder.py#ContextBuilder.build) is the whole
node. It reads the slice budgets from `config/context.yaml`, asks
[`Memory.retrieve_all`](../harnesswalk/context/stores/__init__.py#Memory.retrieve_all) for
every store at once, and then works through four steps you can find in order: compaction of
the session, trimming each retrieved slice to its budget (popping the lowest-ranked item until
it fits), a final check against the total budget minus the reserve for the answer, and the
assembly of messages. Notice the final check drops the oldest turns first and knowledge before
episodes before facts: the cheapest loss goes first.

[`ContextBuilder._format`](../harnesswalk/context/builder.py#ContextBuilder._format) is the
text of each slice exactly as the model sees it, with a header that tells the model what the
items are. The knowledge header asks the model to cite by number and not to cite what it did
not use. Every budget is measured on this formatted text, headers included, so the numbers on
the budget bars are what was really sent.

[`ContextBuilder._compact`](../harnesswalk/context/builder.py#ContextBuilder._compact) folds
old turns into the summary. It keeps `keep_recent_turns` verbatim, uses a model to summarise
when the router has a `summarize` model, and otherwise falls back to
[`truncation_summary`](../harnesswalk/context/builder.py#truncation_summary), short previews
of each turn. That fallback is what you get on the fake provider. The summary is written to the
session store, so the next turn starts from it.

[`estimate`](../harnesswalk/context/tokens.py#estimate) is the token counter: characters
divided by four, rounded up. It is wrong for every real tokenizer and close enough to budget
with, and it has one name in config so a real tokenizer can replace it.

At the end of `build`, `span.detail` carries the full assembled messages to the trace as a
detail record. The summary on the event stream has only counts; the content goes to the trace
when `trace.capture_content` is on, so you can always answer "what did the model see?"

## Try this

1. **Read the budget bars.** Send the Trailhead prompt and open the Context builder node.
   Expected: one bar per slice; procedural is the largest fixed slice (about 195 tokens) and is
   also the `stable_prefix_tokens`.
2. **Force a compaction.** Run two turns in one session with the `two_turns` fixture
   ("Hi there", then "Where is order A-1002?"), both with
   `--session sess_c1 --set context.compaction.threshold=0.001 --set context.compaction.keep_recent_turns=0`.
   Expected: the second turn's builder says `compacted=True`, and the session's summary is
   `Turn 1. User: Hi there Assistant: Hello! ...`, written by truncation.
3. **Move the stable prefix.** In `build`, move the user message to the front of `messages`
   and run `uv run pytest tests/unit/test_context.py`. Expected: tests fail. Put it back and
   write down why a provider's prefix cache would stop working if the first message changed on
   every turn.

## Check yourself

### 1. Why does the system prompt go first in the assembled context?

- [ ] Because models read the first message more carefully.
- [x] Because it is the same on every turn, so a provider can cache that prefix.
- [ ] Because the output guardrails check it.

Answer: a stable prefix can be cached by the provider, which saves time and money. The node
reports its size as `stable_prefix_tokens`.

### 2. When the whole context is over budget, what does the builder drop first?

- [x] The oldest session turns, then knowledge, then episodes, then facts.
- [ ] The system prompt.
- [ ] The new user message.

Answer: the final check removes the cheapest loss first. The system prompt and the new message
are never dropped.

### 3. Why does the builder keep the numbering of knowledge passages on the turn?

- [ ] So the trace can sort them.
- [ ] So the next turn reuses the same passages.
- [x] So the output guardrails can check that each `[n]` in the answer points at a passage the model saw.

Answer: `ctx.citations` is what the citation check reads; a number with no passage behind it is
counted as invalid.

## Further reading

- [Anthropic documentation, Prompt caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)
- [Liu et al., Lost in the Middle: How Language Models Use Long Contexts (2023)](https://arxiv.org/abs/2307.03172)
- [`spec/contracts/context_builder.md`](../spec/contracts/context_builder.md)
