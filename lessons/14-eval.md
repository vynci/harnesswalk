---
node: eval
try:
  pack: _starter
  fixture: golden_returns_window
  prompt: "How long do I have to return a shipped order?"
---

# Eval

> Golden sets offline, judges and thumbs online, and diagnosis of what failed where.

## What it does

An agent changes every time you edit a prompt, a skill, a policy rule or a model, and most of
those changes look harmless when you read them. Eval is how you find out whether a change made
things better or worse before your users do. Offline, a pack carries a golden set
(`datasets/<pack>/golden.jsonl`): each case is a prompt plus what a good turn looks like, such as
the node path, the tool calls, the citations, a schema, text the answer must contain, and a rubric
for a judge. A run plays every case as a real turn through the whole harness and scores it.
Online, people rate live replies with Good and Bad, and a judge model can score a sample of live
turns.

Deterministic scorers come first: did the turn take the expected path, call the expected tools,
cite the expected chunks, return valid JSON, avoid leaking personal data, and say what it should?
They need no model and they never flake, and they are the whole `golden` suite. Judged scores
(faithfulness and relevance, from 0 to 1) run in a separate `judge` suite, so a noisy judge can
never hide a broken path, and every score is reported on its own. Each run is
compared with the previous run of the same pack and suite: a case that passed then and fails now
is a regression, and the run says which cases were fixed, regressed, added or removed. The same
golden run is the gate on every release (chapter 15). Chapter 20
opens the node into its parts.

## What breaks without it

Pack `_starter`. Start from a clean checkout and a scratch data directory, and run the golden set:

```sh
export HARNESSWALK_DATA_DIR=/tmp/hw-eval
uv run harnesswalk eval _starter
```

Expected: `16 passed, 0 failed, 0 skipped of 16` for `run_0001`. Now make a change that reads
fine: in `datasets/_starter/skills/returns.md`, change "within 30 days of delivery" to "within 14
days of delivery". (In the web app: Advanced view, Procedural, Prompt tab, Edit on `returns`.) Run
the golden set again:

```text
FAILED  returns_window  The returns skill sets the 30 day window (edit the skill and this case regresses)  REGRESSION
          error: fixture_mismatch at providers: fixture golden_returns_window: last message does not contain 'within 30 days of delivery'
run run_0002 (golden, pack _starter): 15 passed, 1 failed, 0 skipped of 16; 1 regression(s) against run_0001
```

One case caught it, named the skill, and marked it a regression against the last run. On the
fake provider the failure shows as a fixture mismatch, because the fixture checks that the skill
it depends on still says 30 days; on a real model the same case would fail on `answer_contains`
when the reply says 14. Without eval, nothing fails: the edit goes live, the next customer is told
14 days, and when someone notices, nothing says which of last week's changes did it. Put the
skill back with `git checkout datasets/_starter/skills/returns.md`.

## The code walk

[`Evals`](../harnesswalk/ops/eval/__init__.py#Evals) is what the harness holds: the store in
`data/evals.db`, the judge, and the two runners. It is the node, and it is deliberately thin.

[`load_golden`](../harnesswalk/ops/eval/golden.py#load_golden) and
[`GoldenCase`](../harnesswalk/ops/eval/golden.py#GoldenCase) read the golden set. Open
[`datasets/_starter/golden.jsonl`](../datasets/_starter/golden.jsonl) beside them: each line is
one case, and the `expect` keys map one to one to the scorers.

[`OfflineRunner._case`](../harnesswalk/ops/eval/offline.py#OfflineRunner._case) runs one case.
It builds a turn request from the case (its fixture on the golden suite, a model on the judge
suite), plays it through the gateway, reads the facts off the events with
[`actual_of`](../harnesswalk/ops/eval/scorers.py#actual_of), and scores them. Any exception
makes the case `error`, which counts as failed.

[`deterministic`](../harnesswalk/ops/eval/scorers.py#deterministic) applies the scorers the case
asked for. Read [`score_path`](../harnesswalk/ops/eval/scorers.py#score_path): it compares the
simple-view path, so a change in which nodes ran is a failure even when the answer is right.

[`OfflineRunner._finish`](../harnesswalk/ops/eval/offline.py#OfflineRunner._finish) totals the
run and computes the diff against the previous one: `fixed`, `regressed`, `new` and `removed`.
[`EvalStore.previous_run`](../harnesswalk/ops/eval/store.py#EvalStore.previous_run) is what
"previous" means: the last finished run of the same tenant, pack and suite.

## Try this

1. **Run one case.** `uv run harnesswalk eval _starter --case returns_window --json`. Expected: a
   run with one case, its `expected` and `actual` side by side, and a score per scorer, all
   `pass: true` on an unedited pack.
2. **Fix it and read the diff.** After the regression above, put the skill back and run golden
   again. Expected: `16 passed`, and the run's `diff.fixed` lists `returns_window`.
3. **Write a case.** Add a line to `golden.jsonl`: id `hours_lesson`, prompt "What are your
   support hours?", fixture `plain_answer`, and `expect` with `answer_contains: ["8am"]` and
   `tool_calls: []`. Run golden. Expected: 17 cases, yours fails on `answer_match`, the diff lists
   it under `new`, and the run has 0 regressions, because it never passed before. Change the
   expected text to "9am to 5pm" and run again. Expected: 17 passed and `diff.fixed` lists
   `hours_lesson`. Remove the line afterwards.

## Check yourself

### 1. Why are deterministic scorers kept apart from judged ones?

- [ ] Because judged scores are cheaper.
- [x] So a noisy judge can never hide a broken path, tool call or schema.
- [ ] Because the judge cannot read the trace.

Answer: the golden suite needs no model at all, and the judge suite reports each judged score
with its own threshold, so you can always tell a broken path from a low judge score.

### 2. What makes a failed case a regression?

- [ ] It failed twice in a row.
- [ ] It failed with an error rather than a wrong answer.
- [x] It failed in this run and passed in the previous run of the same pack and suite.

Answer: a regression is defined against history, which is why a new case that fails is listed
as new, not regressed.

### 3. The returns skill edit reads fine. Why does the golden set catch it?

- [x] A case depends on the 30 day window, and its turn no longer produces it.
- [ ] The scorer reads the skill file and compares it with the last version.
- [ ] Policy refuses the edited skill.

Answer: the case checks the behaviour, not the text of the skill: the turn that relied on the
window now fails, so the run fails.

## Further reading

- [Liang et al., Holistic Evaluation of Language Models (2022)](https://arxiv.org/abs/2211.09110)
- [Zheng et al., Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena (2023)](https://arxiv.org/abs/2306.05685)
- [`spec/contracts/evaluator.md`](../spec/contracts/evaluator.md)
