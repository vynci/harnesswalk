# Changelog

Every notable change to Harnesswalk, the reference harness, its web app and its packs. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the package follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Changes to the language-agnostic
spec (`spec/`) are recorded in [spec/CHANGELOG.md](spec/CHANGELOG.md) under their own version
rules ([spec/VERSIONING.md](spec/VERSIONING.md)).

`make release-prep VERSION=x.y.z` moves the Unreleased section under a new version heading.

## [Unreleased]

### Added

- **The tuning loop (P3).** `harnesswalk tune curate | run | compare | promote | rollback |
  status`, and the same under `/v1/tune`: a teacher model answers a pack's golden prompts and
  rewrites of them through the whole harness, the turns every deterministic scorer passed
  become a versioned dataset (`dataset/<name>`, chat examples with the exact context, packs
  only, files under `data/tune`), a trainer turns it into an adapter and the model Ollama will
  serve (`adapter/<name>`, `model/<name>`, hashes only, never weights), the model gate compares
  base, tuned model and a prompt-only control arm on target and canary packs, and promote
  canaries it through the router (`router.canary`, a split by turn id) before rollback or the
  whole pack. Trainers in `config/trainers.yaml`: `fake` (instant, CI and the Guide),
  `homelab-trl` (a GPU worker over ssh, `deploy/homelab/`), `local-mlx` (stub). Aliases
  `local-27b` (the in-family teacher, in no router rule) and `fake-student` (a scripted student
  that asks for approval in prose). Diagnose names the `tune` lever on a `model` failure.
  Public mode replays a recorded run (`config/fixtures/tune_replay.json`) and refuses every
  tuning action.
- **The generator (P4a): `harnesswalk init`.** The third verb: from a profile (the node set),
  a pack (the content) and a generator manifest (`--provider ollama|fake`, `--transport
  http|cli`, `--storage sqlite|files`, the file release door), `init` writes a repository you own
  with no runtime dependency on Harnesswalk: plain Python (the standard library plus `pyyaml`),
  one file per node in `harness/core/`, the pack's prompt, skills, policy and tools in folders
  you own, its golden set and a CI job, the conformance suite vendored with its runner, a
  compose file and Modelfile for a local model, `harness.lock`, pristine copies and
  `GENERATION_NOTES.md`. Template-first: `harnesswalk/gen/templates/python/` (a TypeScript set
  would sit beside it), with `#%` directives and one `off_node` seam for every switched-off
  node, so the walk keeps its shape. This first set covers the `minimal` rung: a generated
  helpdesk_lite repo (about 2,300 lines in `harness/`) passes 2 of 2 applicable conformance
  cases (the rest need switched-off nodes or public mode, never generated) through the vendored
  runner and this repository's `--command` runner, and 21 of 21 golden cases, on the fake
  provider. Drift tests tie the template set to the spec version, the diagram's nodes and the
  event schema. `docs/generator.md`.
- **Groundwork for the tuning loop (P3).** The router's canary rule: `router.canary`, a rule
  with `split: {base_alias, candidate_alias, fraction}` instead of `use`, evaluated right after
  `public`, sends a share of a pack's turns (`when.pack`) to a candidate alias by the hash of
  the turn id; eval case turns and pinned runs are never split, and the `router` span reports
  `arm` and `bucket`. Scripted students for the fake provider (`params.fake_style`: `ask_first`
  asks for approval in prose instead of calling a write tool, `over_call` calls a tool when
  none is needed) and the alias `fake-student`; the teacher alias `local-27b` (qwen3.5:27b,
  thinking off, in no router rule). Candidate views (`Harness.candidate_view`: a pack's
  working set with artifact bodies replaced, nothing stored) that internal eval runs can use,
  with their own case list. `evals.golden_paths`. Diagnose's `tool_choice` evidence and
  `levers` (`["prompt_or_skill", "tune"]` for a `model` failure).

### Fixed

- **Hosted scorecard cells.** The scorecard no longer sends `temperature` to Anthropic (the
  5.5 models answer HTTP 400 "temperature is deprecated for this model", which scored
  `hosted-haiku` 0/232); the page's Temperature column says the provider default is used. A
  provider's HTTP error now carries the API's own message (`error.message`, capped at 200
  characters, key headers and key-shaped strings masked) instead of a bare status. The
  scorecard raises the Anthropic and OpenAI-compatible call timeout to at least
  `--case-timeout` unless a `--set` names it. A passive health mark expires after
  `router.health_check_s` when no probe refreshes it, and a pinned eval call's failure no
  longer marks its provider unhealthy for other tenants.
- Eval case turns no longer end on a chat tenant's budget or a chat turn's timeout: they run
  under `evals.case_budget` (200,000 tokens per turn, 5,000,000 per hour by default) and
  `evals.model_timeout_s` (180 s), and a pinned run keeps its alias after one timed-out call
  (it used to answer "the pinned model has no configured provider" for the rest of the run).
  The Ollama provider's own timeout is 300 s.
- **Four packs on the empty rungs (P2).** `device_fleet` (Kestrelway Field Services,
  `governed`): a telematics fleet desk on a real platform's API shapes, twelve tools split by
  risk, approvals on power-off, SMS clear and purge, a viewer's and an operator's reboot denied
  by policy (new `viewer` and `operator` roles and rule `fleet.admins_only` in
  `config/policy.yaml`), and its business rules in `skills/policy.md`. `gov_services`
  (Aldervale Services Desk, `grounded`): English and French programme rules cited in the
  language of the question, a SIN or a street address in a sentence redacted, read-only tools.
  `long_session` (Fernhollow Journeys, `remembered`): long multi-turn chats that cross the
  compaction threshold, checked with `checks.compacted` and `checks.summary`, and a remembered
  preference recalled in a later session. `helpdesk_lite` (Pebblebrook IT Helpdesk, `minimal`):
  one read-only lookup per question, so the lowest rung has a pack a small local model can
  pass. The Guide's domain demos gain the new packs.
- **Multi-turn golden cases and pack format additions (P2).** A golden case may script a
  whole conversation (`turns`), run in one session so memory and compaction are testable,
  with expectations on any turn and on the case; `roles` runs a case as a given role;
  `checks.compacted` and `checks.summary` check what survives compaction; a pack may ship
  `skills/policy.md`, its business rules in words; `pack.yaml` `provenance` may name the
  generator model. `tools/gen_pack.py` gains `--model haiku` and `--only`, writes the
  generator block and resolves citations turn by turn.
- **The model ladder scorecard.** `harnesswalk scorecard` runs every pack's golden set on each
  model alias you list (one `model`-suite eval run per cell, one model at a time) and writes a
  pack x model pass matrix with a failure breakdown by scorer to `docs/scorecard.md`, plus the
  cell data as JSON. A model that cannot run is `n/a` with the reason. The published matrix
  covers `fake-small`, five local models on a homelab Ollama and two hosted models.
- **The `model` eval suite.** `harnesswalk eval <pack> --suite model --model <alias>` (and
  `POST /v1/evals/runs` with `suite: model`) answers every case on that alias, pinned for every
  model call, and scores it with the deterministic scorers only. Its regression diff compares
  runs of the same model.
- Model aliases `local-2b` (qwen3.5:2b), `local-4b` (qwen3.5:4b), `local-9b` (qwen3.5:9b) and
  `local-e4b` (gemma4:e4b), with thinking off. The Ollama adapter sends an alias's `params`
  (for example `think: false`), and `models.providers.ollama.base_url_env` names the variable
  that overrides `base_url` (`HARNESSWALK_OLLAMA_URL`, as before).
- **Harness profiles and spec 0.2.0.** A profile is a named harness shape: the nodes it
  switches off and the config that goes with it. Six ship in `config/profiles/` as a ladder,
  `minimal`, `guarded`, `grounded`, `remembered`, `governed` and `operated` (every node on, the
  0.1 harness and the default). Each pack names its profile in `pack.yaml` (`profile`, now
  required): `healthcare` and `sports` run on `grounded`, `ecommerce` and `logistics` on
  `governed`, `finance` and `_starter` on `operated`, each measured as the lowest rung its golden
  set passes on (or higher). `HARNESSWALK_PROFILE` or `--profile` (a name or a path) runs every
  pack on one profile. Spec 0.2.0 is the first minor release of the spec: see
  `spec/CHANGELOG.md`.
- Switched-off nodes are dimmed, never hidden: the diagram keeps every node in place, faded with
  a dashed border and an "off" chip, the edges to them fade too, and the tooltip names the
  profile. The profile chip beside the pack picker shows the id and the rung; the pack menu
  names each pack's profile. A turn still enters a switched-off node where it would run and
  shows it `skipped`.
- 13 nodes can be switched off, up from 4: approval (fail closed, a needed approval is a
  deny), audit, the knowledge, semantic and episodic stores, eval, online eval, diagnose and
  release (ungated) join the guardrails, policy and the memory writer.
- `GET /v1/profiles`, `GET /v1/diagram?pack=`, `profile` on `GET /v1/packs` items and
  `turn_start.summary.profile`. Guide chapters declare `requires`, and a chapter whose node is
  off under the current pack's profile is greyed with the rung that has it.
- The pack validator checks a pack against its profile (demonstrated nodes on, golden cases
  reachable, no tool that needs a human without approval), and the conformance runner runs the
  suite under each profile (`--profile`, `--all-profiles`; cases declare `requires` and are
  skipped, not failed). `make conformance` and CI run every profile.
- **Model grading.** The `model` suite grades `tool_calls` as ordered (the expected calls in
  order, extra calls to read tools allowed; `expect.tool_calls_mode: exact` keeps a case
  strict) and `answer_match` on normalized text (case, Unicode, whitespace, dates, times,
  amounts, numbers). The `golden` suite stays exact.
- **Scorecard samples and carried columns.** `--samples N` repeats each cell (mean, min and
  max); every cell runs at temperature 0 where the provider takes it; the page adds a
  `tool_calls` split and the cases a model fails that most other models pass; `--carry` with
  `--carry-models` re-grades columns of an earlier scorecard from their stored results without
  calling the model. The published scorecard re-runs the local columns with two samples and
  carries the hosted ones from 2026-11-08, re-graded.

### Fixed

- A structured reply (`output_schema`) written as prose and a fenced JSON block, as real models
  do after a tool call, is repaired to its valid JSON value instead of replaced with "I could
  not produce a reply in the required format."
- `harnesswalk eval --suite judge --model <alias>` answers on that alias. A hint outside the
  matched router rule's aliases was ignored, so `--model local-4b` answered on `local-8b`.
- A model pinned by an eval run is no longer moved to another router rule by a rule's price
  cap, so a hosted model's after-tool call reports the `lookup` rule as it should.

- The Ollama adapter asks for the alias's `context_tokens` as `options.num_ctx`, so a model is
  no longer loaded with its full declared context (up to 262144 tokens) and several models fit
  on the GPUs at once. An alias without `context_tokens` keeps Ollama's default.

### Changed

- The README is a short front door; the detail moved to `docs/quickstart.md`, `docs/walkthrough.md`, `docs/architecture.md` and `docs/repository.md`, with `docs/README.md` as the index.

## [0.1.1] - 2026-11-02

### Added

- The empty chat shows a card per dataset pack; picking one starts a session on it (the header picker still works).
- A Replay label on the chat action, and a Replay button on the status bar for a finished turn.
- **Use your own data.** `docs/your-own-data.md` builds a pack from your own documents and
  functions, from copying the starter to a gated release, linked from the README. The pack
  menu ends with Add your own dataset, disabled ("Feature coming soon.") with a link to that
  guide.
- **The document reader shows the original file.** A Document view beside the chunk list: a
  pack PDF in the browser's own viewer (with an open and download fallback where the browser
  has none), a markdown document rendered with a rule naming the chunks at each boundary, any
  other text as code. A citation scrolls to its section, or opens the PDF at its page. The API
  serves a pack document's file at `GET /v1/packs/{pack}/files/docs/{name}?raw=1`
  (`application/pdf` or `text/markdown`, inline, every mode, only from the pack's `docs/`), and
  the knowledge document routes name it in `file`.

### Fixed

- The document reader is a fixed-height panel whose body scrolls inside it: a long document no
  longer runs past the dialog's bottom edge. The Legend dialog scrolls the same way.

## [0.1.0] - 2026-11-02

The first public release, built in eight phases (P0 to P7).

### Added

- **Spec and scaffold (P0).** `spec/harness.md` (planes, 15 simple-view nodes and 14 expert
  nodes, invariants, the tenant rule, modes), `spec/diagram/diagram.json` as the single source
  of node and edge ids, the event contract (`spec/events.schema.json`, `spec/events.md`) with
  its OpenTelemetry mapping, one contract per component plus the HTTP API, recorded example
  streams, and the first conformance cases.
- **The vertical slice (P1).** Gateway, context builder with procedural and session memory, the
  loop, the model layer with the `fake`, `ollama`, `openai_compatible` and `anthropic`
  providers, tools and the registry, policy, approval, audit, output guardrails and the trace in
  SQLite. Every node emits typed events over Server-Sent Events. The `harnesswalk` command
  (`serve`, `turn`, `events`), the conformance runner and a Docker compose stack.
- **The web app (P2).** A chat beside a live React Flow diagram lit by the event stream, simple
  and advanced views, step mode with Next and Continue, Stop, approval cards, replay of any
  finished turn, an inspector per node with its lesson, config, events and code peek, and a mock
  mode that plays the recorded streams with no API.
- **The full planes (P3).** Input guardrails (size, PII placeholders, injection scoring),
  per-tenant budgets and policy explanations, approvals that survive a restart, a hash-chained
  audit per tenant, a subprocess sandbox for tools, the router (step, risk, cost, health), the
  semantic, episodic and knowledge stores (SQLite or pgvector) with local embeddings, hybrid
  retrieval and citations, and the memory writer as the one gated write path.
- **LLM Ops (P4).** The trace query and per-node receipts, OpenTelemetry export, golden and
  judge eval runs diffed against the previous run, thumbs and a sampled online judge, versioned
  artifacts with hash-chained releases and rollback, step over, restart-proof checkpoints and
  `GET /v1/events`, one live stream of a tenant's ops events.
- **Datasets and lessons (P5).** Five synthetic CC0 domain packs (healthcare, ecommerce,
  logistics, sports, finance) with provenance cards and golden sets, a pack validator and
  `harnesswalk packs`, sessions on a pack with per-pack memory, the eval gate on release with a
  forced override, `diagnose` mapping a failure to the layer at fault, and 21 lesson chapters
  in walk order with "what breaks" demonstrations in the web app.
- **The hosted sandbox (P6).** Public mode hardened for
  [harnesswalk.vynci.dev](https://harnesswalk.vynci.dev): Claude Haiku, Claude Sonnet and the
  fake model, a shared daily budget for the hosted models (a bring-your-own key bypasses it),
  per-IP limits and anonymous sessions that expire. Private mode stays visible but disabled.
- **Community (P7).** `CONTRIBUTING.md`, a code of conduct, a security policy, issue and pull
  request templates, the conformance suite documented as a product with
  `tests/conformance/runner.py --base-url` for any HTTP implementation, `spec/VERSIONING.md`,
  a wheel that runs on its own (`pip install harnesswalk && harnesswalk serve --provider
  fake`), a release workflow (PyPI trusted publishing, a multi-arch image on
  `ghcr.io/vynci/harnesswalk`, a GitHub Release), `make release-prep`, and a fresh-clone check
  in a clean container that CI runs.

### Fixed

- A turn that fails now records a failed episode and writes no fact from it, and the command
  line prints each failing node's error message and the first error under the reply.
- Golden citation cases no longer depend on which other packs a data directory has run: each
  pack keeps its own keyword index.
- On Linux, the tool sandbox's memory cap measures the child's own resident memory: it read
  `ru_maxrss`, which keeps the parent's high-water mark across exec, so every subprocess tool
  call of a server that had once grown past the cap failed with "went over the memory cap".
- `make web-build` and the other web targets run pnpm from inside `web/`, so corepack uses the
  pinned pnpm on a fresh machine instead of failing with `ERR_PNPM_BAD_PM_VERSION`.

[Unreleased]: https://github.com/vynci/harnesswalk/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/vynci/harnesswalk/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/vynci/harnesswalk/releases/tag/v0.1.0
