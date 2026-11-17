---
node: eval
covers: [curate]
try:
  pack: _starter
  fixture: cancel_order
  prompt: "Please cancel order A-1002."
also:
  - pack: ecommerce
    fixture: cancel_approved_qf20430
    prompt: "Please cancel order QF-20430."
    note: "The kind of turn curation keeps: a lookup, a cancel paused for approval, an honest reply."
---

# Curate: where training data comes from

> Where training data comes from: teacher traffic through the harness, kept only when it passes.

## What it does

Chapters 20 and 21 showed how the harness measures a change and ships it. Curate is the first
step of the fourth lever, changing the model itself (chapter 23 trains it, chapter 24 rolls it
out). It is an expert child of Eval because it is built from eval: it runs the pack's golden set
as `model`-suite runs and keeps what passes.

### Curate

A curation takes packs, a **teacher** model alias, a number of rewrites and a seed. For every
golden case it asks the teacher for `variants` rewrites of the prompt that keep every identifier
(the fake teacher uses fixed templates, so CI is deterministic). The originals and the rewrites
run through the whole harness: guardrails, context builder, policy, approval decided by each
case's `approval`, tools, output guardrails. A turn is **kept only when every deterministic
scorer passed**; a dropped turn is counted by its first failing scorer.

Each kept turn becomes one chat example, read from the trace: the last model call's request
exactly as the context builder built it (system prompt, skills, retrieved chunks, the user
message, every tool call and tool result) plus the reply, and the JSON schemas of the tools that
call offered. So the student learns from the context it will really get.

**Context distillation.** By default the teacher runs with the control arm's text
(`config/tune/prompt_arm.md`, "call the write tool, the harness asks for approval") appended to
the system prompt, and the exported examples use the pack's own prompt. The student learns the
behaviour without the instruction.

Examples are **deduplicated** by the sha256 of their canonical JSON, and the ones whose hash is
lowest (`val_fraction`, 0.1) form the **validation split**, so the split is stable when you rerun.
The result is a `dataset/<name>` artifact: a manifest with the teacher, the scenarios, the
filters, every count, the eval run ids and the files by sha256, plus `synthetic: true` and the
statement that only dataset packs were used. A curation generates its own traffic in scratch
tenants and never reads a real tenant's turns. The JSONL files live under `data/tune/` (outside
git and the artifact store); the manifest names them by hash.

## What breaks without it

The filter is what makes the data worth training on. Run the same curation with two teachers,
offline (a scratch data directory, the fake provider, a few seconds each):

```sh
export HARNESSWALK_DATA_DIR=/tmp/hw-tune
uv run harnesswalk tune curate --pack ecommerce --teacher fake-small --variants 2 --out dataset/approvals
uv run harnesswalk tune curate --pack ecommerce --teacher fake-student --variants 2 --out dataset/student_only
```

`fake-small` answers every ecommerce case like its fixture: 72 turns (24 golden, 48 rewrites),
72 kept, 69 train and 3 validation, and 18 of the examples call `cancel_order` and 15 call
`issue_refund`. `fake-student` is the scripted small model chapter 23 tunes: it answers in
words where a cancel or refund needs a tool. The same command keeps 39 of 72, with
`"dropped": {"path": 33}`, and its 39 examples (35 train, 4 validation) hold no `cancel_order`
or `issue_refund` call at all.

Without the filter, those 33 turns would be in the dataset, and you would be training the
student to do exactly what it already does wrong. Without the trace export you would train on
prompts that do not match what the harness sends, and without the manifest nobody could say what
a tuned model learned from.

## The code walk

[`Tuning._curate`](../harnesswalk/ops/tune/__init__.py#Tuning._curate) is the whole flow: the
rewrites, one candidate view per pack with the control arm's text, the `model`-suite runs, the
keep or drop decision per case, dedupe, the split and the manifest.

[`keeps_ids`](../harnesswalk/ops/tune/curate.py#keeps_ids) rejects a rewrite that lost an order
id, so a rewrite keeps the case's expectations true. [`FAKE_TEMPLATES`](../harnesswalk/ops/tune/curate.py#FAKE_TEMPLATES)
are the fake teacher's rewrites.

[`example_of`](../harnesswalk/ops/tune/curate.py#example_of) reads one kept turn back from the
trace and removes the control arm's text from the system prompt: the distillation step.
[`content_hash`](../harnesswalk/ops/tune/curate.py#content_hash) and
[`is_val`](../harnesswalk/ops/tune/curate.py#is_val) are the dedupe and the split.

[`SOURCE`](../harnesswalk/ops/tune/curate.py#SOURCE) is the sentence every manifest carries about
where the data came from.

## Try this

1. **Read the manifest.** After the first command, run `uv run harnesswalk tune status` (same
   `HARNESSWALK_DATA_DIR`) and look at `dataset/approvals`. Expected: `teacher: fake-small`, `packs: [ecommerce]`, counts
   `turns 72, kept 72, train 69, val 3`, and a `regenerate` command that rebuilds it.
2. **Read an example.** `head -c 2000 /tmp/hw-tune/tune/datasets/approvals/v1/train.jsonl`.
   Expected: a `messages` list starting with the pack's system prompt (no "Actions that change
   something" section), tool calls in the common chat format, and a `tools` list of schemas.
3. **Curate again.** Rerun the first command. Expected: `dataset/approvals` v2 with the same
   counts and the same sha256 for all three files (fixed seed, fake teacher, each chunk of cases
   in a fresh scratch tenant, a split chosen by hash), and both versions in the Tune tab's
   Datasets history. A dataset can be rebuilt and checked, not just trusted.

## Check yourself

### 1. Which turns does curation keep?

- [ ] Every turn the teacher answered.
- [x] Turns where every deterministic scorer passed.
- [ ] Turns a human thumbed up.

Answer: the filter is the eval scorers under model grading; a dropped turn is counted by its first failing scorer.

### 2. Why does the teacher run with the control arm's text while the examples leave it out?

- [x] So the student learns the behaviour without needing the instruction in its prompt.
- [ ] To save tokens at training time.
- [ ] Because the student cannot read Markdown.

Answer: that is context distillation: the instruction shapes the teacher's answers, and the student learns from those answers with the plain prompt.

### 3. Where are the training files kept?

- [ ] In the artifact store, beside the prompts.
- [ ] In the pack's folder in git.
- [x] Under `data/tune/`, named in the manifest by sha256.

Answer: the manifest is the versioned artifact; the files stay outside git and the store, and the hashes tie the two together.

## Further reading

- [Askell et al., A General Language Assistant as a Laboratory for Alignment (2021)](https://arxiv.org/abs/2112.00861), where context distillation is introduced
- [Wang et al., Self-Instruct (2022)](https://arxiv.org/abs/2212.10560), generating and filtering training data with a model
- [`spec/contracts/tuning.md`](../spec/contracts/tuning.md)
