# Contract: Evaluator

Nodes: `eval` (LLM Ops, kind `code`) and its expert children `online_eval`, `offline_eval`,
`diagnose`.

## Purpose

Tell whether a change made things better. Offline evals replay golden sets; online evals judge a
sample of live turns and collect feedback; diagnose maps a failure to the layer that caused it
and proposes a regression case and, where it fits, a memory or skill fix.

## Methods

```
interface Evaluator {
  run_offline(suite: SuiteRef, options: RunOptions) -> async EvalReport        // CLI and CI
  judge_online(tenant_id: string, turn_id: string) -> async JudgeResult        // sampled
  feedback(tenant_id: string, turn_id: string, rating: "up" | "down", note?: string) -> void
  diagnose(tenant_id: string, turn_id: string) -> async Diagnosis
  gate(report: EvalReport, baseline?: EvalReport) -> GateResult                 // used by release
}

record GoldenCase {                               // one line of datasets/<pack>/golden.jsonl
  id: string
  input: string
  expect: {
    path_includes?: [node_id]                     // deterministic
    path_excludes?: [node_id]
    tool_calls?: [string]
    must_cite?: bool
    answer_contains?: [string]
    exit_reason?: string
  }
  judge?: { rubric: string, min_score: number }   // judged
}

record EvalReport {
  suite: string
  artifact_versions: {string: string}             // what was under test
  deterministic: { cases: int, passed: int, failed: [string] }
  judged: { cases: int, mean_score: number, failed: [string] } | null
}

record Diagnosis { layer: "context" | "prompt" | "memory" | "tool" | "model" | "guardrail" | "policy", evidence: [string], regression_case: GoldenCase, proposals: [MemoryProposal] }
record GateResult { pass: bool, reasons: [string] }
```

## Inputs and outputs

- In: stored traces and their events, golden sets (`datasets/<pack>/golden.jsonl`), user feedback, a candidate release.
- Out: `EvalReport`s, `GateResult`s for `release`, `Diagnosis` records with regression cases and memory proposals.

## Invariants

- Deterministic checks and judged checks MUST be reported separately and gated separately.
- `run_offline` MUST be runnable with the `fake` provider and no network (judged checks are then
  skipped and reported as skipped).
- Deterministic checks read the event stream (node path, tool calls, citations, exit reason);
  they MUST NOT parse free text beyond `answer_contains`.
- Online judging runs on a sample (`eval.online.sample_rate`), never on every turn by default,
  and never in public mode on a BYO key.
- Diagnose proposals go to `memory_writer` and regression cases are written as new golden-set
  candidates; neither is applied automatically.
- A gate fails when any deterministic case regresses against the baseline, or the judged mean
  drops more than `eval.gate.max_judged_drop`.

## Failure behaviour

A failing case is a result, not an error. A run that cannot start (missing suite, provider down
for judged checks) fails the eval turn with `error` and an exit code for CI.

## Events

Eval runs are their own turns (`turn_start.summary.kind: eval`) with `eval` and child spans.
Edges: `trace_to_eval`, `eval_to_release`, `eval_to_memory_writer`, and expert refinements.

## Configuration keys (`config/harness.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `eval.online.sample_rate` | `0.05` | |
| `eval.online.judge_model` | `hosted-small` | Router alias |
| `eval.gate.max_judged_drop` | `0.05` | |
| `eval.suites` | `datasets/*/golden.jsonl` | |
