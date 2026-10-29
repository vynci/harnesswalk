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
| P6 Sandbox and hosting | Public and private modes, limits, budgets, BYO key, auth, TTL cleanup, deploy to aiharness.vynci.dev, homelab model backend | Load test within ceilings; abuse checklist passes |
| P7 Community | CONTRIBUTING, conformance suite documented, adapter examples (LangGraph, MCP server), TypeScript harness port as proof of the spec | External implementation passes conformance |

Note: the plan counts 14 nodes; the spec's simple view has 15 ids because the Memory group is
itself a node. Lessons cover all 15.

## Current status

P0 (spec and scaffold) is done. P1 (vertical slice) is built: the harness runs end to end with
the `fake`, `ollama`, `openai_compatible` and `anthropic` providers, the event stream is stored
in SQLite and served over SSE, step mode and the approval pause work, and every conformance case
passes with the fake provider. Stubs that P1 registers but does not implement: `semantic`,
`episodic`, `knowledge`, `memory_writer` (P3), `eval` and `release` (P4).

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

Deferred from P3 (owner phase): read walls in the sandbox (Linux mount or landlock over the
data directory and `/proc`, P6); turn ownership so a second process on the same data directory
is never mistaken for a crashed one (P4); retrieved memory sent as untrusted data rather than
system text, and injection scoring of proposals (P4); TTL cleanup of public proposals (P6); a
resumed turn keeping its output schema, BYO key and pre-pause proposals (P4, with checkpoints);
budget ledger charged per call rather than per turn (P6).
