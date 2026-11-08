# Changelog

Every notable change to Harnesswalk, the reference harness, its web app and its packs. The
format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the package follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Changes to the language-agnostic
spec (`spec/`) are recorded in [spec/CHANGELOG.md](spec/CHANGELOG.md) under their own version
rules ([spec/VERSIONING.md](spec/VERSIONING.md)).

`make release-prep VERSION=x.y.z` moves the Unreleased section under a new version heading.

## [Unreleased]

### Added

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
