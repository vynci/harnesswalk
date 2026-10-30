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
| `eval.online.judge_model` | `hosted-haiku` | Router alias |
| `eval.gate.max_judged_drop` | `0.05` | |
| `eval.suites` | `datasets/*/golden.jsonl` | |

## P4 additions: what is built

The P0 interface above stays the target; P4 builds offline runs, online judging and feedback
(diagnose and `gate` arrive with P5 datasets). HTTP surface: `http_api.md`, "P4 additions".

### Golden sets

`datasets/<pack>/golden.jsonl` (path configurable as `evals.golden_path`, `{pack}` is replaced),
one case per line:

```json
{"id": "support_hours", "title": "Support hours from the system prompt",
 "prompt": "What are your support hours?", "fixture": "plain_answer",
 "expect": {"path": ["gateway", "input_guardrails", "context_builder", "memory", "loop",
            "model_layer", "output_guardrails", "gateway", "trace"],
            "tool_calls": [], "answer_contains": ["9am to 5pm"]},
 "rubric": "Says support is open 9am to 5pm Eastern, Monday to Friday."}
```

| Key | Meaning |
|---|---|
| `id`, `title`, `prompt` | Required. `id` matches `^[a-z][a-z0-9_]{0,63}$` |
| `fixture` | The `fake` fixture the `golden` suite answers with (a per-turn fixture, `http_api.md`) |
| `output_schema` | Sent with the turn; the `schema` scorer validates the reply |
| `approval` | `approve` or `deny` (default): how the runner decides an approval in this case |
| `expect.path` | Simple-view nodes that must appear in this order (subsequence) |
| `expect.citations` | `[{doc, chunk?}]` that must be in `turn_end.summary.citations` |
| `expect.tool_calls` | Tool names the turn calls, in order (equality; `[]` means no tool) |
| `expect.answer_contains`, `expect.answer_exact`, `expect.answer_regex` | `answer_match` |
| `rubric` | What a good answer says; the `relevance` judge reads it |
| `tags` | Free labels |

The P0 keys (`input`, `path_includes`, `must_cite`, `judge.rubric`) are read as aliases of
`prompt`, `path`, `citations: [{}]` (at least one) and `rubric`.

### Scorers

Fixed names, each `{value, pass, detail}`: `path`, `citations`, `tool_calls`, `schema`,
`pii_leak`, `answer_match` (deterministic, read from the case turn's events and reply only) and
`faithfulness`, `relevance` (judged). `golden` runs the deterministic ones, `judge` runs
`schema`, `pii_leak` and the judged ones. `pii_leak` runs the input guardrails' PII detector on
the reply and ignores values the user typed in the prompt (dev restores those on purpose).

### The judge

`evals.judge.model` (default the router's `judge` step, `hosted-haiku` = `claude-haiku-5-5`)
scores `faithfulness` (are the reply's claims supported by what the model was shown: the
retrieved items and tool results from the trace) and `relevance` (does it answer the prompt, by
the rubric) from 0 to 1, as a JSON reply. Pass at `evals.judge.threshold` (default 0.7). The
judge never runs on `fake`: without a configured non-fake judge a `judge` run is refused with
`judge_unavailable`, and CI never configures one. Judge calls are not turns; their cost is
summed on the run.

### Online

After a chat turn ends, with probability `evals.online.sample_rate` (default 0 in public, 0.2
in dev and private) and a configured judge, the turn is judged in the background (never on a
BYO key, never on a case turn). Scores are stored per turn and shown by `GET /v1/turns/{id}`
(`scores`) and the receipt (`scores` per node: `relevance` on `loop`, `faithfulness` on
`output_guardrails`). Feedback (`good` or `bad`, a redacted note) is stored per turn and emits
`feedback_recorded`.

### Storage and runs

`data/evals.db` (SQLite): runs, case results, online scores, feedback. A run is a sequence of
case turns in a scratch tenant (`http_api.md`), one at a time, and its progress is an ops
stream (`events.md` section 10). The diff against the previous run of the same tenant, pack
and suite lists `fixed`, `regressed`, `new` and `removed` case ids; `regression` is "failed
or error now, passed before".

CLI: `harnesswalk eval <pack> --suite golden|judge [--model ALIAS] [--case ID ...] [--json]`
prints one line per case and exits 1 when any case fails (CI runs the golden suite on the fake
provider). P4 configuration keys:

| Key | Default | Meaning |
|---|---|---|
| `evals.golden_path` | `datasets/{pack}/golden.jsonl` | Where the golden set lives |
| `evals.judge.model` | unset (router step `judge`) | Model alias of the judge |
| `evals.judge.threshold` | `0.7` | Pass mark for judged scores |
| `evals.online.sample_rate` | `0.2` dev and private, `0` public | Share of finished turns judged |
| `evals.sqlite_path` | `data/evals.db` | |
