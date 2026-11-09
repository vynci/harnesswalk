# Contract: Tuning (curate, tune, compare, canary)

Nodes: `curate` (LLM Ops, expert child of `eval`, kind `code`) and `tune` (LLM Ops, expert child
of `release`, kind `code`). Additive, spec 0.2.0. Artifacts: `artifacts.md`. Events: `events.md`
section 14. HTTP: `http_api.md`, "0.2.0 additions: tuning".

## Purpose

Close the loop from what the ops plane already measures to the model itself:

```
traces + model-suite results -> curate -> dataset/<name> -> tune (a trainer) -> adapter/<name>
  -> model/<name> (the served model) -> compare (the eval gate: beat the base, no forgetting)
  -> promote (a canary router rule, then the whole pack) -> rollback when the canary fails
```

Fine-tuning is the fourth improvement lever beside memory (facts and episodes), procedural text
(prompt and skills) and configuration (policy, router, budgets). Every step is an explicit
action (CLI or HTTP), never automatic: a trainer owns GPUs on a machine the harness does not
manage. The prompt-optimisation control arm runs beside every comparison, so the cheaper lever
is always measured on the same cases.

## Methods

```
interface Tuning {
  curate(req: CurateRequest, author: Identity) -> async Curation        // node curate
  run(req: TuneRequest, author: Identity) -> async TuneRun              // node tune
  compare(req: CompareRequest, author: Identity) -> async Comparison    // the model eval gate
  promote(req: PromoteRequest, author: Identity) -> CanaryState         // router rule + releases
  rollback(author: Identity) -> CanaryState
  canary(tenant_id: string) -> CanaryState                              // the rule and both arms
}

record CurateRequest { name: string, packs: [string], teacher: string, variants?: int, seed?: int,
                       teacher_prompt_arm?: bool }
record TuneRequest   { dataset: string, trainer: string, student: string, name?: string,
                       config?: {string: any} }
record CompareRequest { candidate: string /* model/<name> */, against: string /* base alias */,
                        packs: [string], canary_packs: [string], samples?: int, prompt_arm?: bool }
record PromoteRequest { candidate: string, packs?: [string], stage: "canary" | "full",
                        fraction?: number, force?: bool }
```

## Curate (`curate`)

Generates traffic, keeps what passes, exports it as supervised examples.

1. **Scenarios.** For each pack in `packs`: every golden case (`source: golden`), plus `variants`
   rewrites of each case's prompt written by the teacher (`source: paraphrase`), with the same
   expectations (the same records, so the same tool calls and facts). The rewrite call is not a
   turn: one model call per case, temperature 0, `seed` passed where the provider takes it, asking
   for a JSON list of `variants` rewrites that keep every identifier. A `fake` teacher rewrites
   from fixed templates (a greeting before, a thanks after), so CI and the public replay are
   deterministic. The scenarios are stored with the dataset so it can be regenerated exactly.
2. **Teacher traffic.** The scenarios run as `model`-suite eval runs pinned to the teacher
   (`evaluator.md`), one per pack, through the whole harness, approvals decided by each case's
   `approval`. The scenarios run in chunks of about `tune.curate.chunk` (24) cases, each chunk an
   eval run in a fresh scratch tenant, so a long curation stays inside the per-tenant hourly
   budgets (a case and its rewrites, and cases linked by `after`, stay together). With
   `teacher_prompt_arm` (default `true`) the teacher runs on a candidate view
   whose system prompt carries the control arm's addition (Compare, below); the exported examples
   then use the pack's own system prompt (context distillation: the student learns the behaviour
   without the instruction).
3. **Filter.** A turn is kept only when every deterministic scorer passed under model grading.
   Dropped turns are counted by their first failing scorer.
4. **Export.** One example per kept turn, read from the trace: the last `model_layer` call's
   request messages (system and context exactly as the harness built them, the user message,
   every assistant tool call and tool result) plus its reply as the final assistant message, and
   the JSON schemas of the tools that call offered. Roles and shapes follow the common chat
   format (`tool_calls: [{type: function, function: {name, arguments}}]`, `role: tool` with
   `tool_call_id` and `name`). Examples are deduplicated by the sha256 of their canonical JSON;
   `val_fraction` (default 0.1) of them, chosen by that hash, form the validation split.
5. **Record.** A `dataset/<name>` artifact version (`artifacts.md`) whose manifest carries the
   teacher, the scenario source and seed, the filters, the counts (turns, kept, dropped by
   scorer, deduplicated, train, validation, per pack), the files with their sha256, the eval run
   ids, and `synthetic: true` with the statement that only dataset packs were used.

Invariants: a curation MUST NOT read turns of real tenants: it generates its own traffic in
scratch tenants. Only packs are sources. The dataset files live under `tune.artifacts_dir`, not
in the artifact store; the manifest names them by hash. Progress is on the stream
`curate:<curation_id>` with the eval run event types (`events.md` section 14).

## Tune (`tune`)

A **trainer** is an adapter behind one interface, like a provider, configured in
`config/trainers.yaml` under an alias:

| Kind | Alias shipped | What it does |
|---|---|---|
| `fake` | `fake` | Deterministic and instant (or `step_delay_s` per step). A fixed loss curve from the dataset hash, a fake weights hash, and a `model` whose alias is a `fake` provider alias. Its `outcome` (`good`, default, or `forgets`) decides whether that model answers like the fixtures (passes every case) or calls a tool when none is needed (fails the canary packs), so the gate can be shown both ways |
| `ssh` | `homelab-trl` | A remote worker over ssh: copies the dataset and the worker scripts in, starts the run, polls a status file, reads the result back. The worker trains a LoRA adapter, merges it, converts and quantizes the merge to GGUF, and creates the Ollama model (`deploy/homelab/README.md`) |
| `stub` | `local-mlx` | Documented, not implemented: a run is refused with `422 trainer_unavailable` |

A run's `student` is a model alias; `trainers.yaml` `students` maps it to the base weights the
trainer starts from (repository and revision) and the serving recipe (Ollama renderer and
parser). A run:

- emits `tune_run_started`, then `tune_step` per optimizer step (step, total steps, epoch, loss,
  validation loss when measured, elapsed and estimated seconds left), then `tune_run_finished`
  on the stream `tune:<run_id>`;
- on success records an `adapter/<name>` version (training config, base model and revision,
  trainer alias, metrics, sha256 of the weights) and a `model/<name>` version (the Modelfile text,
  the served tag and digest, the alias configuration it will be served under) chained to it;
- never edits a router rule or a served artifact: a model is served only after compare and
  promote.

One tuning activity (a curation, a run or a comparison) runs at a time per harness:
`409 tune_running` otherwise. Weights, merges and GGUF files MUST NOT enter the artifact store
or a repository; only their hashes do.

## Compare: the eval gate for a `model`

Compare runs the `model` suite (`evaluator.md`) on every pack in `packs` (the target) and
`canary_packs` (the forgetting canary), `samples` times each (default `tune.gate.samples`, 2),
for three arms:

| Arm | Answers on | Served set |
|---|---|---|
| `base` | `against` (for example `local-4b`) | the pack's working set |
| `candidate` | the model artifact's alias | the working set with that alias added to `models/models` (a candidate view, nothing is edited) |
| `prompt_arm` | `against` | the working set with the control arm's addition appended to `prompt/system_prompt` |

The arms run on the same harness, the same day, the same grading and temperature 0 where the
provider takes it, so the base is re-measured beside the candidate. The verdict:

- `passed` when `gain = sum over packs of (candidate mean - base mean)` is at least
  `tune.gate.min_gain_cases`, and on every canary pack `base mean - candidate mean` is at most
  `tune.gate.canary_tolerance_cases`;
- `failed` otherwise (`reason`: `below_min_gain`, `canary_regressed`, or `run_error` when a run
  did not finish).

The prompt arm gets the same two numbers (`prompt_arm.gain`, `prompt_arm.canary_ok`), reported,
not gating. The comparison also carries each arm's `tool_calls` split (`evaluator.md`, the
scorecard) so "write tool not called" and "called when none expected" are visible before and
after. The verdict is appended to the model artifact as a new version (`gate`), so the hash chain
covers it, and emitted as `release_gated` (`suite: model`, `events.md` section 14).

## Promote, canary, rollback

Promotion goes through the router and the release plane; nothing else changes what is served.

- **Canary** (`stage: canary`): needs the model artifact's latest version to carry `gate.status:
  passed`, else `409 gate_failed` (dev only: `force`, audited as `tune_forced`). It edits
  `models/models` in the working set: adds the model's alias when missing, and sets
  `router.canary` (`router.md`, "0.2.0: the canary rule") to the packs, the base alias (the
  comparison's `against`), the candidate alias and `split.fraction` = `fraction` (default
  `tune.canary.fraction`, 0.1). Then each pack in `packs` (default the comparison's target
  packs) is released (`release.md`, its golden gate as usual).
- **Full** (`stage: full`): needs the canary gate to pass (below), else `409 canary_failed`
  (dev: `force`). Sets the fraction to 1.0 and releases the packs again.
- **Rollback**: sets the fraction to 0 and releases the packs. The alias stays in
  `models/models` (its history is the record); no traffic reaches it. Rolling back the release
  chain (`POST /v1/releases/{id}/rollback`) is the other way back and restores whatever
  `models/models` that release had.

**The canary gate** reads live signals of the canary window (turns of the rule's packs routed by
the `canary` rule since it was last set), per arm: turns, failed turns (status `error`, exit
`error` or `limit`, or `bad` feedback). It passes when the candidate arm has at least
`tune.canary.min_turns` turns and its failure rate exceeds the base arm's (0 when the base arm
has no turns) by at most `tune.canary.max_failure_increase`. Counts are per tenant over HTTP.

## Diagnose: the `tune` lever

A failed `model`-suite case whose trace shows no guardrail, budget, tool error, changed artifact
or provider error, but whose `tool_calls` score failed because an expected tool of a class other
than `read` was not called, or a tool was called when none was expected, is a `model` diagnosis
with evidence `kind: tool_choice` (`evaluator.md`, "0.2.0: the tune lever"). Its `levers` are
`["prompt_or_skill", "tune"]`, cheaper first: the control arm, then the curate and tune flow.

## Public mode

A public server never curates, trains, compares, promotes or rolls back: every `POST` under
`/v1/tune` is `403 forbidden_in_mode`, and the `GET` routes answer from a recorded run
(`tune.replay_fixture`), marked `replay: true`. The sandbox never trains.

## Configuration

`config/harness.yaml` (process configuration, not an artifact):

| Key | Default | Meaning |
|---|---|---|
| `tune.sqlite_path` | `data/tune.db` | Curations, runs, comparisons, the canary ledger |
| `tune.artifacts_dir` | `data/tune` | Dataset files and anything a trainer brings back; never in git |
| `tune.teacher` | `local-27b` | Default teacher alias |
| `tune.curate.variants` | `6` | Teacher rewrites per golden case |
| `tune.curate.seed` | `7` | |
| `tune.curate.val_fraction` | `0.1` | |
| `tune.curate.teacher_prompt_arm` | `true` | The teacher runs with the control arm's addition |
| `tune.curate.chunk` | `24` | Scenarios per eval run (one scratch tenant each) |
| `tune.prompt_arm` | `config/tune/prompt_arm.md` | The control arm's system prompt addition |
| `tune.gate.samples` | `2` | Model-suite runs per arm and pack |
| `tune.gate.min_gain_cases` | `2` | Cases the candidate must gain over the base, summed over the target packs |
| `tune.gate.canary_tolerance_cases` | `1` | Cases a canary pack may lose (mean of the samples) |
| `tune.canary.fraction` | `0.1` | The first split |
| `tune.canary.min_turns` | `5` | Candidate turns the canary gate needs |
| `tune.canary.max_failure_increase` | `0.1` | |
| `tune.replay_fixture` | `config/fixtures/tune_replay.json` | What a public server serves |

`config/trainers.yaml`: `trainers` (alias to `{kind, ...}`) and `students` (alias to `{hf,
revision, renderer, parser, chat_template}`). Like `providers`, it says where data goes, so it is
not an artifact and is not editable over HTTP.

## CLI

```
harnesswalk tune curate --pack ecommerce --pack logistics --teacher local-27b --out dataset/approvals
harnesswalk tune run --dataset approvals --trainer homelab-trl --student local-4b [--name approvals_4b]
harnesswalk tune compare --candidate model/approvals_4b --against local-4b \
    --packs ecommerce,logistics --canary-packs sports,_starter [--samples 2] [--json PATH]
harnesswalk tune promote --candidate model/approvals_4b [--fraction 0.1] [--full] [--force]
harnesswalk tune rollback
harnesswalk tune status            # artifacts, runs, the canary rule and both arms
```
