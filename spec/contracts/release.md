# Contract: Release

Nodes: `release` (LLM Ops, kind `code`) and its expert child `artifacts` (kind `store`).

## Purpose

Everything the harness loads is a versioned artifact: system prompt, skills, policy, tool
definitions, model and router config, context config. Release versions them, gates them on eval,
ships them, and rolls them back. Editing a skill in the UI is a release.

## Methods

```
interface Release {
  propose(change: ArtifactChange, author: Identity) -> Candidate
  gate(candidate_id: string) -> async GateResult                    // runs the evaluator
  ship(candidate_id: string, force?: bool) -> ArtifactVersion       // force only in dev
  rollback(artifact: string, to_version: string, author: Identity) -> ArtifactVersion
  current(artifact: string, tenant_id?: string) -> ArtifactVersion
  history(artifact: string, tenant_id?: string) -> [ArtifactVersion]
  diff(artifact: string, from: string, to: string) -> string        // unified diff
}

record ArtifactChange { artifact: string, tenant_id?: string, content: string }   // artifact: "skills/returns.md", "config/policy.yaml"
record Candidate      { candidate_id: string, change: ArtifactChange, base_version: string }
record ArtifactVersion { artifact: string, version: string, tenant_id: string | null, created_at: timestamp, author: string, gate: "passed" | "skipped" | "forced", commit?: string }
```

## Inputs and outputs

- In: an `ArtifactChange` (a UI edit, a CLI command or a diagnosis proposal), the evaluator's `GateResult`.
- Out: a new current `ArtifactVersion` that the harness loads for later turns; a history and diffs for the inspector.

## Invariants

- The harness MUST load prompts, skills, policy, tool definitions and model config only through
  `current()`. Reading those files directly from disk at runtime is a conformance failure.
- Every shipped version MUST be restorable by version id and diffable against any other.
- Tenant overrides are versioned separately (`tenant_id` set) and shadow the shared artifact
  for that tenant only.
- `ship` without a passed gate MUST be rejected except with `force` in dev mode, which records
  `gate: forced`.
- A shipped version takes effect for turns that start after it; a turn in progress keeps the
  versions it started with (they are recorded in its trace).
- Public mode: edits are session-scoped candidates that are never shipped to the shared artifact.
- v1 storage is git-backed: each ship is a commit in a local repository under the data volume,
  each rollback a new commit restoring old content. No remote is configured by the harness.
- Every ship and rollback MUST be recorded in `audit` (`artifact_released`).

## Failure behaviour

A gate failure leaves the candidate unshipped with reasons. A ship that fails mid-way leaves the
previous version current.

## Events

Release operations are their own turns (`turn_start.summary.kind: release`) with a `release`
span, optional `artifacts` child, and one `artifact_released` per shipped artifact. Edges:
`eval_to_release`, `release_to_memory` (expert: `artifacts_to_procedural`).

## Configuration keys (`config/harness.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `release.store` | `git` | |
| `release.path` | `data/artifacts` | Inside the data volume |
| `release.require_gate` | `true` | `false` only in dev |
| `release.tenant_overrides` | `true` in private | |

## P4 additions: what is built

P4 builds versioning, releases and rollback; the eval gate on ship arrives with P5 (a release
today records `gate: skipped`). Storage is SQLite (`data/releases.db`, `release.sqlite_path`),
not git: the store is append-only and every release carries a hash chained to the previous one,
which gives the same restore-by-id and tamper evidence without a repository in the data volume.
`release.store: git` is reserved for later.

- **Artifacts** are `(kind, name)` pairs: `prompt/system_prompt` (`datasets/<pack>/system_prompt.md`,
  or the profile's inline `procedural.system_prompt`), `skill/<name>` (`skills/<name>.md` or the
  inline skills), `policy/policy`, `context/context`, `models/models` (`config/*.yaml`) and
  `tools/tools` (`datasets/<pack>/tools.yaml`). `config/harness.yaml` is process
  configuration, not an artifact.
- **Versions** are integers from 1 per artifact, with `hash` (sha256 of the body),
  `updated_at`, `updated_by` (`seed`, `disk`, `user:<tenant>`, `rollback`), `note`, and
  `deleted` (a rollback past an artifact's creation). Bodies are stored whole; diffs are computed
  (unified, `difflib`).
- **Seeding and disk changes.** At start-up each source file (or inline profile value) is
  compared with the hash it was last imported at. A new artifact is seeded as version 1, a
  changed one gets a new version (`updated_by: disk`). The first start creates release
  `r1` (note "initial"). In a mode that does not serve the working set, a start-up import
  also creates a release, because the files are what was deployed.
- **current()** stays the one door: `config/loader.py` and the procedural store read every
  artifact through it, with the served set (active release, or the working set when
  `release.use_working_set`). The content stamp (`<name>@<sha256 prefix>`) is what traces show.
  Overlays (the conformance profile, `HARNESSWALK_*` variables, `--set`) apply on top of the
  served config, as they did on top of the files.
- **Releases** have `release_id` (`r1`, ...), `pack`, `created_at`, `created_by`, `note`,
  `artifacts[] {kind, name, version, hash}`, `prev_hash` and `hash` = sha256 of the canonical
  JSON of `{release_id, pack, created_at, created_by, note, artifacts, prev_hash}`. Verifying the
  chain recomputes every hash, and (P4 integration) checks that every version a release names
  still exists with that hash and a body that hashes to it; a failure names `broken_at` and the
  `artifact`. One release is active per pack; rollback changes which, and is
  logged (`activations`), not chained.
- **Applying a change.** An edit, a release or a rollback that changes what is served rebuilds
  the artifact-backed components (procedural memory, tools, policy, router and model layer,
  context builder, memory, memory writer, loop) between turns; it is refused while any turn or
  eval run is running. Providers, stores and embeddings are not rebuilt (a provider or embedder
  change needs a restart). Another process (the CLI) writing the store bumps a generation
  counter; the server reloads before its next turn.
- **Edit validation.** Besides YAML parsing, the policy rule schema and the dry run: bodies
  are capped at 200,000 characters and notes at 2,000; YAML with more than 50 aliases is
  refused (an alias bomb); a `tools` edit may not add an `entrypoint` the current version does
  not have (loading one imports and runs Python, so new code is a file change and a restart);
  a `models` edit may not change `providers` (endpoints, key variable names and `enabled` say
  where a hosted key is sent). These are `422 invalid_artifact` and are checked before anything
  from the body is loaded.
- **Events.** `artifact_changed` per edit and one `artifact_released` per release or rollback,
  as ops events (`events.md` section 10), not release turns. Turns record what they ran on in
  `turn_start.summary.release`.

CLI: `harnesswalk release list|create [--note]|rollback <release_id>|diff <release_id>`.

| Key | Default | Meaning |
|---|---|---|
| `release.sqlite_path` | `data/releases.db` | |
| `release.use_working_set` | `true` in dev, else `false` | Serve the working set instead of the active release |
| `release.lease.heartbeat_s` | `5` | `serve`'s owner lock heartbeat (`data/owner.lock`) |
