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
| `turns`, `roles` | P2: a multi-turn case and the case identity's roles ("P2 additions: multi-turn cases" below) |

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

Additive (released in spec 0.2.0). A third offline suite, `model`, answers every case
from a real model and judges it with the deterministic scorers only, so "does this model pass
the golden set" has an answer that needs no judge model and is reproducible from the trace.

- **Request.** `suite: model` needs `model`: an alias from `config/models.yaml` (not an
  embedding alias) whose provider the harness can use, else `400 invalid_request` (no alias,
  unknown alias) or `422 provider_not_configured`. A `fake` alias is always usable.
- **Answering.** Every model call of a case turn is pinned to the alias: the router still
  matches its rule by the call's `when` (so a case's `checks.router_rules` keep their meaning),
  but neither the rule's own `use` list nor its `max_price_out_per_mtok` is consulted and there
  is no fallback (`router.md`, "Model suite: the pinned router"). A `judge` run that names a
  `model` pins it the same way, so the cases answer on that model. A `fake` alias answers from
  the case's fixture, so on `fake-small` the `model` suite reproduces `golden`; a case with no
  fixture is then `skipped`.
- **Scoring.** Every applicable deterministic scorer (`path`, `citations`, `tool_calls`,
  `schema`, `pii_leak`, `answer_match`, `checks`) under **model grading** (see "Grading
  modes"); no LLM judge, `judge_model` is `null`.
- **Cost.** The answers' usage is summed on the run's `cost`, as on `judge`.
- **Regressions.** The previous run of a `model` run is the last finished run of the same
  tenant, pack, suite **and model**, so a 4B run is never compared with a 9B run; the diagnose
  baseline ("the last pass") is keyed the same way. `golden` and `judge` keep their lookup.

### Grading modes

Two ways to read `tool_calls` and `answer_match`. Every other scorer reads the same in both.

| Mode | Suites | `tool_calls` | `answer_match` |
|---|---|---|---|
| exact | `golden`, `judge` | the tool names called, in order, equal `expect.tool_calls` | substrings (all of them), `answer_exact` or `answer_regex` on the reply as it is |
| model | `model` | `ordered` (below), or `exact` for a case with `expect.tool_calls_mode: exact` | the same checks on normalized text (below) |

The golden suite answers from fixtures written for its expectations, so exact is right there;
a real model words a right answer its own way and may read a skill or look a record up before
it acts, so the `model` suite grades the behaviour, not the fixture's phrasing.

`ordered` tool calls, in this order (the score's `detail` starts with the rule that decided):

1. `none expected`: `expect.tool_calls: []` fails on any call.
2. `ordered`: the expected calls must appear in order as a subsequence of the actual calls; a
   missing one fails (`value` is the share found).
3. Every other call must be to a tool of action class `read` (in the pack's tool declarations,
   `read_skill`, `read_doc` and `calculator` included); an extra call to any other tool (a write,
   destructive, external or memory tool, or an unknown one) fails.

A case may set `expect.tool_calls_mode: exact` (or `ordered`, the default) when a model run must
be strict; the `golden` suite ignores it and is always exact.

Normalized `answer_match` applies to the reply and to each expectation alike, in this order:
Unicode NFKC and case folding; markdown emphasis and code marks (`*`, `_`, backtick) removed;
thousands separators dropped inside numbers; dates to `YYYY-MM-DD` (`October 14, 2026`,
`Oct 14 2026`, `14 October 2026`, `2026-10-14`) and a date without a year to `MM-DD` (except
after `may`, too often the verb), so it matches inside a full date; times on a 12-hour clock to
24 hours (`2:30 pm` is `14:30`); amounts to `usd 6`, `eur 6`, `gbp 6` (`$6`, `6 dollars`,
`USD 6.00`, `€6`, `6 euros`, `£6`) without trailing zeros; hyphens and dashes between words or
numbers read as spaces (`2-year` is `2 year`); whitespace collapsed. An expectation that is a
number after this matches when the reply holds that number, compared numerically (`129.00`
matches `$129`). `answer_exact` compares the normalized texts; `answer_regex` runs on the reply
case-insensitively. Deterministic, no model call; a different wording of the facts ("not
found" for "could not find") still fails.

### The scorecard

`harnesswalk scorecard [--packs all|a,b] [--models a,b] [--out docs/scorecard.md]
[--json PATH] [--tenant scorecard] [--case-timeout S] [--max-usd N] [--samples N]
[--carry JSON --carry-models a,b]` runs `model`-suite runs per (pack, model), one cell at a
time, model by model, in-process and in its own tenant (default `scorecard`, so the dev
tenant's regression history is untouched). Each cell runs on the alias's provider only, with
hash-vector embeddings (`stores.embeddings.provider: fake`) so every model sees the same
retrieval, and at temperature 0 where the provider takes it (Ollama `options.temperature`;
Anthropic `temperature` when the alias runs with thinking disabled; otherwise the provider's
default, which the page says). `--samples N` runs each cell N times (default 1) and reports the
mean pass count with its min and max. A cell that cannot run is `n/a` with the reason (the
provider is not configured or not reachable, the model is not on the Ollama server, the model
takes no tools, the spend cap is reached) and the matrix goes on. `--carry` takes the
`--carry-models` columns from an earlier scorecard JSON and grades them again under model
grading from the stored case results (no model call): `tool_calls` and `answer_match` from the
stored calls and reply, `schema` from the answer the output guardrails received (the trace) with
the JSON extraction, every other score as stored; a column whose stored runs are gone keeps its
old grading, and the page says which.

The Markdown has a header (date UTC, harness and spec version, Ollama host and version, the
grading, samples and temperature, the models with their tags, parameter sizes and quantization
from `/api/show`), the pass-count matrix (packs as rows, models as columns, `passed/scored`,
with the range over samples), and per model the failed cases by scorer name, the `tool_calls`
split (a call when none was expected, no call, an expected write tool not called, an expected
read tool not called, an extra or wrong tool), the cases this model fails that at least half
the other real (non-`fake`) models pass (a case counts as failed for a model when it failed in
at least half its samples), and the tokens, cost and wall time. The JSON holds every cell's
samples: run id, totals, cost, time and, per case, its status, failing scorers, error codes and
`tool_calls` split.

## P2 additions: multi-turn cases

Additive (spec 0.2.0, `spec/packs.md` "Golden cases: `turns` and `roles`"). A case may carry
`turns: [{user, expect?, checks?}]` in place of `prompt`; `fixture` is one name or a list
aligned with the turns. The runner plays the turns in order, each a real turn through the
whole harness, in one session of the case tenant (`sess_<run_id>_<case_id>`), so turn `n`
has the earlier turns as history and compaction runs when the session slice passes its
threshold. Each turn's approvals are decided with the case's `approval`.

- **Scoring.** A turn with `expect` or `checks` is scored on that turn's events with the
  deterministic scorers but `pii_leak`, each score named `turn<n>.<scorer>` (1-based). The
  case's own `expect`, `checks` and `output_schema` are scored over the session: `path` and
  `tool_calls` are the turns' in order, `citations` every turn's, `answer_match` and `schema`
  the last turn's reply, `pii_leak` every reply against every user message, and `checks`
  every turn's events (`router_rules` in order across the turns; `retrieved` and `summary`
  from the last context). A case passes when every score passes, as before. On `judge`, the
  judge reads the last user message, the last reply and the session's retrieved context.
- **Run detail.** `actual` is the session's (the joined path, tool calls and citations, the
  last reply) with `actual.turns`, each turn's own `actual`; `turn_id` is the last turn's and
  `turn_ids` lists every turn's.
- **`checks` for long sessions.** `compacted` (bool): a context build of the turn (or of any
  turn, on the case) folded old turns into the running summary, read from the
  `context_builder` exit summary's `compacted`. `summary` (substring, compared without case): the running
  summary the last context sent the model holds it, read from the context builder's stored detail.
- **`roles`.** The case identity's roles (default `["user"]`), so a golden case can prove a
  role-scoped policy decision.

## 0.2.0: eval switched off

`eval`, `online_eval` and `diagnose` have `can_disable`. Under a profile that switches
`online_eval` off, no turn of the pack is sampled for the online judge; with `diagnose` off, a
run's regressed cases are not diagnosed on their own (`diagnoses` stays empty). `offline_eval`
is never off: golden runs, `POST /v1/evals/runs` and `POST /v1/diagnose` work under every
profile, because the golden set is how each rung is measured.

## 0.2.0: golden sets per pack, the tune lever

Additive, groundwork for the tuning loop (`tuning.md`).

- **`evals.golden_paths`** (a map of pack id to path) names a pack's golden set over
  `evals.golden_path`, so a conformance profile can give one pack a set of its own.
- **Diagnose, the tune lever.** A new evidence kind and rule between rules 5 and 6 of the table
  above: `tool_choice` (read from a failed `tool_calls` score of a `model`-suite case: an expected
  tool of a class other than `read` was not called, or a tool was called when none was expected;
  `split` is the scorecard bucket, `expected` and `actual` the calls) points at layer `model`.
  Every diagnosis gains `levers`: for `model` it is `["prompt_or_skill", "tune"]` (try the
  prompt or a skill first, then curate and tune), for the other layers the layer itself. The
  suggestion names both commands.
- **Internal runs** (not reachable from `POST /v1/evals/runs`): an eval run may carry its own
  list of cases and a candidate view (the pack's working set with some artifact bodies
  replaced, nothing stored), and its `model` may be an alias only that view defines.
- **Eval case turns' budget and timeout.** A case turn (every suite, the release gate, the
  tuning loop's runs) runs under `evals.case_budget` over the tenant's `policy.budgets` (default
  200,000 tokens per turn, 5,000,000 per hour, 5,000 tool calls per hour; its `_per_turn` keys
  replace the loop's own `loop.budget_*`, dollar limits still bind), and a model call of a
  case turn may take `evals.model_timeout_s` (default 180) instead of `model_layer.timeout_s`.
  A pinned run needs its alias's provider to be configured, not healthy (`router.md`), so one
  timed-out call fails that case, not the rest of the run.

## 0.2.0: tuning (curate, the model gate)

Additive. Contract: `tuning.md`.

- **`curate`** (expert child of `eval`, kind `code`, never switched off: it runs only when asked
  for). It runs `model`-suite runs pinned to a teacher over a pack's golden cases and teacher
  rewrites of them, keeps the case turns every deterministic scorer passed, and exports them as
  a `dataset/<name>` (`artifacts.md`). An internal run may carry its own list of cases (the
  rewrites) and a candidate view (the teacher with the control arm's addition); neither is
  reachable from `POST /v1/evals/runs`. Those runs record `purpose: curate`.
- **The model gate** (`tuning.md`, Compare) is the eval gate of a `model` artifact: model-suite
  runs of three arms (base, candidate, prompt arm) on the target and canary packs, `purpose:
  tune_gate` on each run. Its numbers are mean pass counts over the samples, so a gate of "beat
  the base by 2 cases, lose at most 1 on a canary pack" reads directly off the scorecard shape.
  A run's `model` may be an alias only the candidate view defines (the tuned alias before it is
  promoted).
