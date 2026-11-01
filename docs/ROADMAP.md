# Roadmap

Copied from the project plan (section 10). Each phase gets its own brief, is built contract
first (the spec is the contract), and closes with an end-to-end check on the running stack.

| Phase | Deliverable | Gate |
|---|---|---|
| P0 Spec and scaffold | Name chosen, licence, repo scaffold, `spec/harness.md`, `events.schema.json`, `spec/diagram` from `diagram_v2`, contracts stubs, CI skeleton | Owner approves spec and name |
| P1 Vertical slice | Gateway, Context builder (Procedural + Session), Loop, Model layer (Ollama + one hosted), one tool, Output guardrail, Trace to SQLite; CLI runner; events emitted; compose up in one command | Canned prompt produces the expected event sequence (first conformance test) |
| P2 Web UI | Chat panel, React Flow diagram lit by SSE events, simple/expert toggle, read-only inspectors, code peek, lesson rail stub | A learner can watch a request flow end to end |
| P3 Full planes | Input guardrails, Policy + Approval (HITL card), Memory writer, Semantic, Episodic, Knowledge (pgvector), Model router rules, Audit | E-commerce "cancel order" demo pauses for approval; finance demo cites |
| P4 Ops | Trace viewer, golden-set runner (CLI + CI), online judge + thumbs, Release (versioned artifacts, diff, rollback), step mode and replay | Changing a skill creates a version; eval run flags a regression |
| P5 Datasets and lessons | Five packs generated and committed, lessons for all 14 nodes, "disable a node" demos | Each pack's golden set passes on the default model |
| P6 Sandbox and hosting | Public and private modes, limits, budgets, BYO key, auth, TTL cleanup, deploy to harnesswalk.vynci.dev, homelab model backend | Load test within ceilings; abuse checklist passes |
| P7 Community | CONTRIBUTING, conformance suite documented, adapter examples (LangGraph, MCP server), TypeScript harness port as proof of the spec | External implementation passes conformance |

Note: the plan counts 14 nodes; the spec's simple view has 15 ids because the Memory group is
itself a node. Lessons cover all 15.

## Current status

P0 (spec and scaffold) is done. P1 (vertical slice) is built: the harness runs end to end with
the `fake`, `ollama`, `openai_compatible` and `anthropic` providers, the event stream is stored
in SQLite and served over SSE, step mode and the approval pause work, and every conformance case
passes with the fake provider. Stubs that P1 registered but did not implement: `semantic`,
`episodic`, `knowledge`, `memory_writer` (built in P3), `eval` and `release` (built in P4).

P2 (web UI) is built: the chat and the live diagram in `web/`, served by the API at `/`, with
step mode, approval cards, Stop, replay, history, the inspector with a code peek, and a mock mode
for working on the UI alone. The API gained cancel, turn summaries, cursor replay, the session
turn list, the code peek and capabilities. Playwright drives the UI against the real API with
the fake provider in CI (`make e2e`).

P3 context plane (branch `p3-context`): `semantic`, `episodic` and `knowledge` are real stores
(SQLite by default, vectors in pgvector behind `storage.vectors`), with local embeddings, hybrid
keyword and vector retrieval fused by reciprocal rank, and citations `[n]` the reply carries
into `turn_end.summary.citations`. `memory_writer` is the single gated write path (the
`remember` tool, one episode per turn, gates by mode: dev and private auto, public never).
`harnesswalk ingest <pack>` indexes a pack's documents and loads its seeds.

P3 control plane (branch `p3-control`): input guardrails redact PII to placeholders and score
prompt injection; output guardrails check schema, citations and PII leaks and restore
placeholders in dev; policy explains every decision (`why`) and enforces per-tenant budgets;
approvals survive a restart; audit is a hash chain per tenant exported at `GET /v1/audit`; tool
calls run in a subprocess sandbox with an OS jail; the router picks models by step, risk and
cost.

**P3 is done** (integration pass, 2026-10-29). Both planes are merged on `main` and drawn in the
web UI: citation chips that open the document at the cited chunk, the memory writer's writes as
diffs with Accept and Reject for the review gate, store inspectors with scope tags and the
knowledge document list, the context builder's budget per slice, guardrail badges and the
placeholder reveal, policy `why` and budget use on the node and in the status bar, sandbox
level, network and jail on every tool line, the audit chain with Verify, and notices for limits
and restarts. The integration review hardened the sandbox (no file change outside the work
directory, no raw spawns, a macOS write wall), kept public mode to pure tools, stopped a
caller's key from marking a provider down, routed the writer's gate through policy with roles,
audited memory writes, and paged the audit export by record. Gate met: the e-commerce cancel
pauses for approval (and survives a restart), and the starter pack's Trailhead question cites
the returns policy. The finance pack arrives with the other packs in P5. Next: P4, ops.

Deferred from P3, and where each stands after P4:

| Item | Phase | Status |
|---|---|---|
| Read walls in the sandbox (Linux mount or landlock over the data directory and `/proc`) | P6 | open |
| Turn ownership, so a second process on the same data directory is not mistaken for a crashed one | P4 | **closed**: `serve` holds `data/owner.lock` (pid and heartbeat), a second `serve` refuses or `--takeover`s |
| Injection scoring of memory proposals | P4 | **closed**: proposals are scored like input; block at 0.8, review at 0.45 (`memory_writer.injection`) |
| Retrieved memory carries system authority (retrieved blocks are system messages) | P6 | open, recorded below |
| TTL cleanup of public proposals | P6 | open |
| A resumed turn keeping its output schema, BYO key and pre-pause proposals | P4 | **closed**: loop checkpoints keep the schema, the proposals, the numbered knowledge and the BYO-key flag (the key itself is never stored; `byo_key_dropped`) |
| Memory proposals made before a restart were lost | P4 | **closed**, with the checkpoints above (conformance `restart_keeps_schema`, and a live two-process test) |
| Budget ledger charged per call rather than per turn | P6 | open |

**P4 is done** (integration pass, 2026-10-30). Ops and web merged on `main`, then wired: the
trace query and receipts, OpenTelemetry export, golden and judge eval runs with a diff against
the previous run, feedback and a sampled online judge, versioned artifacts with releases, a hash
chain and rollback, step-over and restart-proof checkpoints, all drawn in the web app on the
AI Elements patterns (Test Results for runs, Trace Commit diffs for releases, Checkpoint markers
for saves and releases, Good and Bad message actions). The integration added `GET /v1/events`,
one live stream of a tenant's ops events, so a release made with the CLI or in another tab shows
up at once; replays eval case turns from their `events_url`; lists them with `kind=eval` in the
trace query; names every artifact behind an inspector (`inspector.artifact`); and moved the UI
onto the real response shapes. The review fixed: tool `entrypoint`s and model `providers` can no
longer be changed by a web edit (one would import code, the other could send a hosted key to
another host), YAML alias bombs and long notes are refused, release verification checks the
stored bodies too, eval scratch tenants cannot be named by a caller, and reply text and message
deltas stay out of OpenTelemetry. Gate met: changing the `returns` skill creates version 2, the
next golden run flags `returns_window` as a regression, and a rollback serves the old version to
the next turn, in unit tests, the `eval_golden_regression` and `release_rollback` conformance
cases, and the browser (`make e2e`). Next: P5, datasets and lessons.

Deferred from P4 (owner phase):

| Item | Phase |
|---|---|
| The eval gate on release: a release records `gate: skipped`; P5 runs golden before it ships | P5, **closed** |
| `diagnose` (map a failed case to the layer that caused it) is a stub node | P5, **closed** |
| Retrieved memory still reaches the model as system text; whether it should be a lower-authority message (and how the fake fixtures and lessons show it) is open. Proposals are now scored, which closes the write side | P6 |
| Which release is active (`activations`) is logged but not hash-chained | **closed in P5** (hash-chained per pack, `verify.activations`) |
| Per-tenant artifact overrides; config overlays (`--set`, profiles) still win over edited artifacts | P6 |
| The tenant ops stream polls the store once a second for other processes' writes; a notify channel for hosted deployments | P6 |
| An opt-in to export message content to OpenTelemetry (off and not offered) | P6 |
| `node_resumed` does not say which resume mode released it (step, continue, step_over) | P5, **closed** (`node_resumed.summary.mode`) |
| The judge suite and the online judge were hand-run once in the ops pass; the integration pass made no hosted call | owner |

**P5 is done** (integration pass, 2026-11-01). Three builders merged on `main`, then joined
up. Datasets: five CC0 packs with provenance cards, each demonstrating one feature
(healthcare: PII redaction and refusal; ecommerce: a destructive action behind approval;
logistics: multi-step tool use; sports: the cheap `lookup` route; finance: citations and the
memory writer), 22 to 25 golden cases each, a validator and `harnesswalk packs`. Lessons: 21
chapters in walk order with a "what breaks" demonstration each, the Lessons tab and drawer,
and Check yourself questions. Ops: the eval gate on release (409 `gate_failed`, `force`
audited in dev), `diagnose` mapping a failed case or a bad turn to the layer at fault,
`node_resumed.mode`, sessions on a pack and tenant data per pack.

The integration pass fixed a cross-pack bug found at verification: BM25 over one FTS5 table
for every pack let other packs' documents reorder a pack's hits, so citation cases passed on a
fresh data directory and failed after other packs had run; each pack now has its own table
(an old data directory migrates without a re-ingest), and a regression test runs two packs'
suites in one directory and reruns the first. It also closed the seams the builders listed:
one pack module (`harnesswalk/packs.py`, `pack_index.py` deleted); name cues in the PII
detector are case-insensitive in the cue words only, so healthcare redacts names again;
lookup answers no longer get "(No sources were cited)"; golden `checks` are a scorer, cases
gain `modes` (dev-only restore cases) and `after` (remember and recall pairs, run in file
order); a router rule whose price cap leaves nothing falls through; `GET /v1/packs/{id}` with a
file tree, `?pack=` on memory proposals, `repo_url` in capabilities. In the web app: a pack
picker, the Suggestion row and fixture menu per pack, File Tree browsers in the Procedural and
Knowledge inspectors, every pack's golden suite from the Eval inspector, the gate in the release
panel with "Release anyway", diagnosis chips, the resume mode in replay, and lesson
demonstrations that name their pack (five also run on a domain pack). The content of the five
packs was reviewed (names, brands, places, advice) and their cards say so.

Gate met: every pack's golden set passes on the fake provider in one data directory that
already holds the other packs' runs, in CI (`packs` job), in `tests/unit/test_p5_integration.py`
(each domain pack released through the gate) and in the browser (`make e2e`).

Deferred from P5:

| Item | Phase |
|---|---|
| Tenant uploads would share their pack's FTS5 statistics with other tenants of the pack (no uploads exist yet) | P6 |
| A case with `after` run on its own does not pull in the case it reads; the UI says so | open |
| Six healthcare and two finance cases (restored values) and the starter's `pii_redacted` are dev-only, so a gate outside dev runs fewer cases | open |
| An uncited answer on a turn that ran tools is not required to cite, even when part of it came from the documents; a model-based claim check would tell them apart | P6 |
| `Diagnosis.regression_case` and memory proposals from a diagnosis | P6 |
| Ollama `llama3.1:8b` passes 4 to 10 of each pack's cases structurally (it writes tool calls as text); not a gate | P6 |
| The owner's read of the 21 chapters and of the pack content reviews (done by the integration builder, an agent) | owner |
| The judge suite and a hosted smoke run on the new packs (this pass made no hosted call) | owner |
