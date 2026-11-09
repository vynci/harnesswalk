# Contract: Artifacts

Node: `artifacts` (expert child of `release`, kind `store`). The store behind release:
`release.md` has the methods, releases, the eval gate and rollback. This file lists the artifact
kinds and, since 0.2.0, the three tuning kinds (`tuning.md`).

## Kinds

| Kind | Scope | Body | Loaded by the harness |
|---|---|---|---|
| `prompt/system_prompt` | pack | Markdown | procedural memory |
| `skill/<name>` | pack | Markdown with front matter | procedural memory |
| `tools/tools` | pack | YAML | tool registry |
| `policy/policy` | global | YAML | policy, approval |
| `context/context` | global | YAML | context builder, stores, memory writer |
| `models/models` | global | YAML | router, model layer |
| `dataset/<name>` (0.2.0) | tune | JSON manifest | no (curate writes it, tune reads it) |
| `adapter/<name>` (0.2.0) | tune | JSON manifest | no (tune writes it) |
| `model/<name>` (0.2.0) | tune | JSON manifest | through `models/models`, once promoted |

A pack artifact belongs to one pack and a global one to every pack; both are part of each pack's
working set and releases (`release.md`). A **tune** artifact belongs to no pack: it is versioned,
hashed, diffed and listed like any artifact, but it is not part of a pack's working set or
served set, so creating a dataset never changes what a turn runs on. What makes a tuned model
served is the `models/models` edit that promote makes (`tuning.md`), and that edit is in the
pack's release chain like any other.

Names of tune artifacts match `^[a-z][a-z0-9_]{0,63}$`. Every version keeps the whole body and its
sha256 and is never updated in place, as for every kind.

## Tune manifests

Bodies are canonical JSON (sorted keys, two-space indent). Each one names what it was made from by
`{name, version, hash}`, so the three kinds form a chain of custody: a model names its adapter,
the adapter names its dataset, and `verify` (`GET /v1/tune/verify`) checks that every named
version exists with that hash.

```
record DatasetManifest {
  kind: "dataset", name: string, created_at: timestamp
  teacher: string, teacher_model: string, teacher_prompt_arm: bool
  packs: [string], scenarios: { golden: int, paraphrase: int, variants: int, seed: int }
  filters: [string]                       // "every deterministic scorer passed (model grading)", "dedupe"
  counts: { turns: int, kept: int, dropped: {scorer: int}, deduplicated: int, train: int, val: int,
            by_pack: {pack: int} }
  files: { train: FileRef, val: FileRef, scenarios: FileRef }   // FileRef { path, sha256, bytes }
  eval_run_ids: [string]
  synthetic: true, source: string         // "dataset packs only (synthetic); no customer data"
  regenerate: string                      // the command that rebuilds it
}
record AdapterManifest {
  kind: "adapter", name: string, created_at: timestamp, run_id: string
  dataset: Ref, trainer: string, student: string
  base_model: { repo: string, revision: string }
  config: { method: "lora" | "qlora" | "fake", rank: int, alpha: int, dropout: number,
            target_modules: [string], epochs: number, learning_rate: number,
            max_seq_len: int, batch_size: int, grad_accum: int, seed: int, ... }
  metrics: { steps: int, train_loss: number, eval_loss?: number, wall_s: number, tokens?: int }
  weights: { location: string, sha256: string, bytes?: int }      // never the weights themselves
}
record ModelManifest {
  kind: "model", name: string, created_at: timestamp
  adapter: Ref, alias: string                                      // the alias it is served under
  alias_config: object                                             // its models/models entry
  serve: { provider: "ollama" | "fake", tag?: string, digest?: string, quantization?: string,
           method: "merged_gguf" | "adapter" | "fake", gguf_sha256?: string }
  modelfile: string                                                // the Ollama Modelfile text
  gate: GateVerdict | null                                         // set by compare (tuning.md)
}
record Ref { name: string, version: int, hash: string }
```

The weights, the merged model and the GGUF files live under `tune.artifacts_dir` or on the
trainer's machine; a manifest names them by sha256 and location only.

## Events

Creating a tune artifact version emits `artifact_changed` (kind `dataset`, `adapter` or `model`,
no `pack`) on the tenant's `ops` stream, as an edit does.
