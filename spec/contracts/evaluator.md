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
| `checks` | P5: `{router_rules?: [rule id per model call, in order], retrieved?: {semantic \| episodic \| knowledge: substring}}`, scored by `checks` |
| `modes` | P5 integration: the harness modes the case applies in (`dev`, `public`, `private`); absent means every mode. Outside them the case is `skipped` with a `detail`, and a skipped case does not count toward a pass rate |
| `after` | P5 integration: the id of an earlier case whose memory writes this case reads (a remember and recall pair). Load fails when it is not an earlier case |

The P0 keys (`input`, `path_includes`, `must_cite`, `judge.rubric`) are read as aliases of
`prompt`, `path`, `citations: [{}]` (at least one) and `rubric`.

### Scorers

Fixed names, each `{value, pass, detail}`: `path`, `citations`, `tool_calls`, `schema`,
`pii_leak`, `answer_match`, `checks` (deterministic, read from the case turn's events, stored
details and reply only) and `faithfulness`, `relevance` (judged). `golden` runs the
deterministic ones, `judge` runs `schema`, `pii_leak` and the judged ones, and `model` (see
"Model suite and the scorecard") runs the deterministic ones on a real model's answers. `checks` (P5
integration) runs when the case has `checks`: the `router` node's `summary.rule` of each model
call must equal `router_rules`, and each `retrieved` store's items in the context builder's
stored detail must include the substring.

Case order: a run plays its cases one at a time in the golden file's order, all in one case
tenant, so a case with `after` sees what the earlier case's memory writer wrote. A run of
selected `case_ids` keeps that order but does not add the earlier case; the UI says so on such
a case. `pii_leak` runs the input guardrails' PII detector on
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

CLI: `harnesswalk eval <pack> --suite golden|judge|model [--model ALIAS] [--case ID ...] [--json]`
prints one line per case and exits 1 when any case fails (CI runs the golden suite on the fake
provider). P4 configuration keys:

| Key | Default | Meaning |
|---|---|---|
| `evals.golden_path` | `datasets/{pack}/golden.jsonl` | Where the golden set lives |
| `evals.judge.model` | unset (router step `judge`) | Model alias of the judge |
| `evals.judge.threshold` | `0.7` | Pass mark for judged scores |
| `evals.online.sample_rate` | `0.2` dev and private, `0` public | Share of finished turns judged |
| `evals.sqlite_path` | `data/evals.db` | |

## P5 additions: diagnose, packs, the release gate

Additive. Node `diagnose` (expert child of `eval`) is implemented
(`harnesswalk/ops/eval/diagnose.py`). It reads stored traces only: no model call, so it runs on
the fake provider and in CI.

### Diagnose

Input: a failed eval case (`{run_id, case_id}`, status `failed` or `error`) or a turn
(`{turn_id}`) of the caller's tenant that has `bad` feedback or ended with an exit other than
`answered`. A case turn id of one of the caller's runs is read as that case. Anything else has
nothing to diagnose (`422 nothing_to_diagnose`).

Output, a `Diagnosis`:

```
record Diagnosis {
  diagnosis_id: string                 // dg_<n>
  target: "case" | "turn"
  run_id?: string, case_id?: string, turn_id: string, pack: string
  layer: "retrieval" | "prompt_or_skill" | "tool" | "model" | "guardrail" | "budget" | "unknown"
  evidence: [Evidence]                 // trace facts, in the order they were found
  suggestion: string                   // what to look at first, one or two sentences
  baseline: { run_id, release_id, artifacts_hash } | null   // the last pass compared against
  automatic: bool, created_at: timestamp
}
record Evidence { kind: string, fact: string, ...details }
```

The baseline ("the last pass") is, for a case, the newest earlier finished run of the same
tenant, pack and suite in which that case passed; for a turn, the newest finished `golden` run
of the turn's pack with no failed case. What each side ran on is the served set named by
`turn_start.summary.release.artifacts_hash` (the release store keeps every served set it ever
stamped, by that hash), so a diagnosis compares artifact versions, not file contents on disk.

Evidence kinds and the layer each points at. The layer is the first rule that matches, in this
order:

| Order | Evidence `kind` | Read from | Layer |
|---|---|---|---|
| 1 | `guardrail_block` | `input_guardrails` or `output_guardrails` exit with `action: block` (with `reason`), or a `policy` exit with `decision: deny` (with `rule`) | `guardrail` |
| 2 | `budget_exit` | `loop` exit with `exit_reason` other than `answer` | `budget` |
| 3 | `tool_error` | a `tools` exit with `outcome` `error` or `timeout`, or an `error` event at `tools`, `registry` or `sandbox` | `tool` |
| 4 | `artifact_changed` | an artifact whose hash differs between the baseline's served set and the failing turn's (`artifact`, `from_version`, `to_version`, `baseline_run_id`) | by kind: `prompt` and `skill` are `prompt_or_skill`, `tools` is `tool`, `policy` is `guardrail`, `context` is `retrieval`, `models` is `model` (the first changed kind in that order wins) |
| 5 | `model_error` | an `error` event at `model_layer`, `router` or `providers` (`code`, for example `fixture_mismatch`, `provider_error`) | `model` |
| 6 | `retrieval_empty`, `citation_missing` | the case expected citations and `knowledge` returned no hit or none of the expected documents; or every queried memory store returned no hit | `retrieval` |
| 7 | none of the above | | `unknown` |

Every failing scorer is also listed (`kind: scorer_failed`, `scorer`, `detail`), and a turn's
feedback (`kind: feedback`, `rating`, `note_chars`), so the evidence explains the failure even
when the layer is `unknown`. A fake-provider `fixture_mismatch` after an edited skill is rule 4
before rule 5: the scripted model saw a different prompt, which is the change, not the model.

Diagnose is never applied: it proposes. The suggestion names the changed artifact and the
release to compare with, the failing tool, or the store that returned nothing.

- **Storage.** `data/evals.db` (`diagnoses`). A case's diagnosis is also stored on the case:
  run detail `cases[].diagnosis`; a turn's on the turn: `GET /v1/turns/{id}` gains `diagnosis`
  (the latest, or `null`).
- **Automatic on regression.** When a run finishes with regressions, each regressed case is
  diagnosed (`automatic: true`) before `eval_run_finished`, and the run summary gains
  `diagnoses: {case_id: layer}`. Off with `evals.diagnose.auto: false`.
- **Events.** Each diagnosis emits `diagnose_finished` on the tenant's `ops` stream
  (`events.md` section 11).

### Packs

A run names any pack the harness can load (`GET /v1/packs`), not only the configured one; its
case turns run on that pack (procedural memory, knowledge, tools, guardrails and golden set of
the pack). `evals.golden_path` keeps `{pack}` for the pack id; its default is the pack's own
`golden.jsonl`.

### The release gate

`release.md`, P5 additions. A gate run is an ordinary golden run with `purpose: release_gate`
on its summary and in `eval_run_started`.

### Case turns and the caller's tenant

`evals.scratch_tenant` (default `true`) keeps P4's scratch tenant per run. With `false`, case
turns run under the caller's tenant (each in its own session), so they read the caller's
memory and their memory writes are the caller's; the run summary's `case_tenant` says which
tenant held the case turns either way.

| Key | Default | Meaning |
|---|---|---|
| `evals.diagnose.auto` | `true` | Diagnose regressed cases when a run finishes |
| `evals.scratch_tenant` | `true` | Case turns in a scratch tenant per run (P4) or in the caller's tenant |

## Model suite and the scorecard

Additive (spec 0.1.0, no version change). A third offline suite, `model`, answers every case
from a real model and judges it with the deterministic scorers only, so "does this model pass
the golden set" has an answer that needs no judge model and is reproducible from the trace.

- **Request.** `suite: model` needs `model`: an alias from `config/models.yaml` (not an
  embedding alias) whose provider the harness can use, else `400 invalid_request` (no alias,
  unknown alias) or `422 provider_not_configured`. A `fake` alias is always usable.
- **Answering.** Every model call of a case turn is pinned to the alias: the router still
  matches its rule by the call's `when` (so a case's `checks.router_rules` keep their meaning),
  and a rule's `max_price_out_per_mtok` still applies to the pinned alias (a rule that prices it
  out passes to the next matching rule, as an unpinned rule whose models are all priced out
  does), but the rule's own `use` list is not consulted and there is no fallback
  (`router.md`, "Model suite: the pinned router"). A `fake` alias answers from the case's
  fixture, so on `fake-small` the `model` suite reproduces `golden`; a case with no fixture is
  then `skipped`.
- **Scoring.** Every applicable deterministic scorer (`path`, `citations`, `tool_calls`,
  `schema`, `pii_leak`, `answer_match`, `checks`); no LLM judge, `judge_model` is `null`.
  `answer_match` substrings are written for the fixture replies, so a model that words a right
  answer differently fails it: read the reply before calling it wrong.
- **Cost.** The answers' usage is summed on the run's `cost`, as on `judge`.
- **Regressions.** The previous run of a `model` run is the last finished run of the same
  tenant, pack, suite **and model**, so a 4B run is never compared with a 9B run; the diagnose
  baseline ("the last pass") is keyed the same way. `golden` and `judge` keep their lookup.

### The scorecard

`harnesswalk scorecard [--packs all|a,b] [--models a,b] [--out docs/scorecard.md]
[--json PATH] [--tenant scorecard] [--case-timeout S] [--max-usd N]` runs one `model`-suite
run per (pack, model), one cell at a time, model by model, in-process and in its own tenant
(default `scorecard`, so the dev tenant's regression history is untouched). Each cell runs on
the alias's provider only, with hash-vector embeddings (`stores.embeddings.provider: fake`) so
every model sees the same retrieval. A cell that cannot run is `n/a` with the reason (the
provider is not configured or not reachable, the model is not on the Ollama server, the model
takes no tools, the spend cap is reached) and the matrix goes on. The Markdown has a header
(date UTC, harness and spec version, Ollama host and version, the models with their tags,
parameter sizes and quantization from `/api/show`), the pass-count matrix (packs as rows,
models as columns, `passed/scored`), and per model the failed cases by scorer name, the three
failing cases the most models also fail, and the tokens, cost and wall time. The JSON holds
every cell's totals and, per case, its status, failing scorers and error codes.

