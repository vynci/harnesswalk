---
node: eval
covers: [offline_eval, online_eval, diagnose]
requires: [eval, online_eval, diagnose]
try:
  pack: _starter
  fixture: tool_call
  prompt: "Where is order A-1001?"
---

# Inside eval: offline, online and diagnose

> Golden runs in a scratch tenant, thumbs and a sampled judge on live turns, and the layer at fault.

## What it does

Chapter 14 used eval as a gate. The Advanced view opens it into three parts that answer three
different questions: did this change break a known case, how are real turns going, and when
something failed, where is the fix?

### Offline eval

A run plays every golden case as a real turn, one at a time, through the whole harness from
gateway to trace. Each case runs in its own session inside a scratch tenant created for the run
(`evalrun-<run_id>`), so a run never reads or writes your memory, and two runs always start from
the same state. The `golden` suite answers each case from its fake fixture and uses only
deterministic scorers; the `judge` suite answers from a real model and adds a judge's scores.
Approvals inside a case are decided by the case (deny by default). Progress streams as
`eval_run_started`, one `eval_case_finished` per case, and `eval_run_finished`.

### Online eval

Live turns get two kinds of signal. People press Good or Bad under a reply and may add a note,
which is PII-redacted before it is stored. And with `evals.online.sample_rate` (0.2 in dev and
private, 0 in public) a judge model scores a sample of finished turns in the background:
relevance lands on the Loop node and faithfulness on Output guardrails, so the scores sit on the
nodes they judge. Judging never affects the turn, and never runs on eval case turns or on turns
that used a caller's own key.

### Diagnose

Diagnose takes a failed case, or a turn rated Bad, and reads its stored events to name the layer
at fault, by the first rule that matches: a guardrail or policy blocked it, a budget or step limit
stopped it, a tool errored, an artifact changed since the last run where the case passed
(`prompt_or_skill` for a skill), a model call failed, retrieval came back empty, or `unknown`. It
returns the evidence, a suggestion and the baseline it compared with. It needs no model, it runs
automatically on every regression, and it only proposes: it never changes anything.

## What breaks without it

Pack `_starter`. The golden set has a case, `remember_name`, whose turn asks the agent to remember
"Sam" and writes a fact. Start a server and run the golden suite for a tenant of your own:

```sh
curl -s -X POST http://127.0.0.1:8000/v1/evals/runs -H 'X-Harnesswalk-Tenant: evaldemo' \
  -H 'Content-Type: application/json' -d '{"pack": "_starter", "suite": "golden"}'
```

The run finishes with 16 passed. The `remember_name` case passed, so a fact was written, yet
`GET /v1/nodes/semantic` for `evaldemo` lists no tenant facts, and `GET /v1/turns/<case turn>` is
`404`: the case turn and its memory live in the run's scratch tenant, reachable only through the
case's `events_url`. Without that isolation, every eval run would plant its test facts and
episodes in the memory of whoever ran it, the next run would start from a different state, and
two runs of the same pack could disagree for reasons that have nothing to do with your change.

Now ask for the judge suite with `"suite": "judge"` on the fake provider. The answer is `422
judge_unavailable`: "no judge model is configured (evals.judge.model)". The deterministic layer
works offline and for free; the judged layer needs a model, which is exactly why the two are kept
in separate suites.

## The code walk

[`OfflineRunner.start`](../harnesswalk/ops/eval/offline.py#OfflineRunner.start) checks the
request and creates the run; [`OfflineRunner._turn_request`](../harnesswalk/ops/eval/offline.py#OfflineRunner._turn_request)
builds each case's turn with the [`scratch_tenant`](../harnesswalk/ops/eval/offline.py#scratch_tenant)
identity, its own session id, the fixture or model, and `channel="eval"`.
[`OfflineRunner._play`](../harnesswalk/ops/eval/offline.py#OfflineRunner._play) runs it through
the real gateway and collects its events.

[`scorers.py`](../harnesswalk/ops/eval/scorers.py#DETERMINISTIC) holds one function per check.
[`score_citations`](../harnesswalk/ops/eval/scorers.py#score_citations) compares the cited chunks
with the expected ones, and [`score_pii`](../harnesswalk/ops/eval/scorers.py#score_pii) fails a
reply that contains personal data the prompt did not.

[`Judge.judge`](../harnesswalk/ops/eval/judge.py#Judge.judge) asks a judge model for scores
between 0 and 1 with a short rubric, and [`context_of`](../harnesswalk/ops/eval/judge.py#context_of)
gives it what the model was shown, read from the trace's detail records. With
`trace.capture_content` off, the judge sees "(none)", so faithfulness cannot be judged.

[`decide`](../harnesswalk/ops/eval/diagnose.py#decide) is diagnose's rule order and
[`trace_facts`](../harnesswalk/ops/eval/diagnose.py#trace_facts) the evidence it reads.
[`OnlineEval.after_turn`](../harnesswalk/ops/eval/online.py#OnlineEval.after_turn) decides
whether a finished turn is sampled, and [`OnlineEval.feedback`](../harnesswalk/ops/eval/online.py#OnlineEval.feedback)
redacts the note, stores the rating and emits `feedback_recorded` with only the note's length.
[`SCORE_NODES`](../harnesswalk/ops/eval/online.py#SCORE_NODES) is the map from a score to the node
it judges.

## Try this

1. **Rate a reply.** Send any prompt, press Bad, and add the note "Wrong window, mail me at
   ana@example.com". Expected: the turn's `feedback` reads `{"rating": "bad", "note": "Wrong
   window, mail me at [EMAIL]"}`, and another tab of the same session shows the rating at once.
2. **Replay a case.** Open the Eval node's Runs tab, open a case of the last run and follow its
   turn into replay. Expected: the chat header names the eval replay, and the diagram plays the
   case turn, which no chat session of yours contains.
3. **Diagnose a regression.** Edit the `returns` skill to 14 days, run golden (or try to
   release, which runs it as the gate), then `POST /v1/diagnose` with the run id and
   `{"case_id": "returns_window"}`. Expected: `layer: prompt_or_skill`, evidence that
   `skill/returns` is v2 where the last passing run ran v1, and the suggestion to diff the two or
   roll back. The fixture mismatch is listed too, as evidence rather than the cause.

## Check yourself

### 1. Why does each eval run use a scratch tenant?

- [ ] To make runs faster.
- [x] So a run never touches your memory and every run starts from the same state.
- [ ] So the judge cannot see the cases.

Answer: case turns can write facts and episodes; in a tenant of their own, those writes cannot
leak into your conversations or into the next run.

### 2. Where does an online faithfulness score appear?

- [ ] On the Eval node only.
- [ ] On the Model layer node.
- [x] On the Output guardrails node, the check that the answer stays with its sources.

Answer: `SCORE_NODES` puts relevance on Loop and faithfulness on Output guardrails, so a score
shows beside the layer it judges.

### 3. What does diagnose produce for a failed case?

- [x] The layer at fault, with evidence from the trace, a suggestion and the baseline run it compared with.
- [ ] A rewritten prompt that it ships automatically.
- [ ] A new judge score.

Answer: diagnose reads the stored events and points the fix at the right layer; it proposes and
never changes anything itself.

## Further reading

- [Anthropic documentation, Create strong empirical evaluations](https://docs.anthropic.com/en/docs/test-and-evaluate/develop-tests)
- [Liu et al., G-Eval: NLG Evaluation using GPT-4 with Better Human Alignment (2023)](https://arxiv.org/abs/2303.16634)
- [`spec/contracts/evaluator.md`](../spec/contracts/evaluator.md)
