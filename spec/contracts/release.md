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
