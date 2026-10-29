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
`episodic`, `knowledge`, `memory_writer` (P3), `eval` and `release` (P4). Next: P2, the web UI.
