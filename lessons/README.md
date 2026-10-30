# Lessons

One chapter per node, in walk order: the order a request meets the nodes, from the gateway to
release. Each chapter will cover what the node does, what breaks without it (with a prompt that
shows the failure when the node is switched off), a walk through its code, things to try, and
further reading. Chapters arrive in P5; this index is the plan.

| # | Node | Group | In one line |
|---|---|---|---|
| 1 | `gateway` Gateway | control | Who is asking, for which tenant, and how a message becomes a turn. |
| 2 | `input_guardrails` Input guardrails | control | Size limits, PII redaction and injection checks, in code, before the model sees anything. |
| 3 | `context_builder` Context builder | context | What the model actually sees, assembled inside a token budget, stable content first. |
| 4 | `memory` Memory | context | Five stores, one retrieval interface: procedural, session, semantic, episodic, knowledge. |
| 5 | `loop` Loop | execution | Plan, act, observe, adjust, and every way the loop is made to stop. |
| 6 | `model_layer` Model layer | execution | One interface for every model: routing by rules, providers, fallback, cost. |
| 7 | `tools` Tools | execution | Typed tool calls, resolved, checked by policy and executed in a sandbox. |
| 8 | `policy` Policy | control | Allow, deny or needs approval, decided by rules on every single tool call. |
| 9 | `approval` Approval | control | A human in the loop: the turn pauses, the exact action is shown, nothing runs until someone decides. |
| 10 | `audit` Audit | control | The append-only record of consequential decisions, and why it is not the trace. |
| 11 | `output_guardrails` Output guardrails | execution | Schema, content and citation checks on the answer before it leaves. |
| 12 | `memory_writer` Memory writer | context | The one gated path that may write facts and events into memory. |
| 13 | `trace` Trace | ops | Every event of every turn, the cost receipt, and replay. |
| 14 | `eval` Eval | ops | Golden sets offline, judges and thumbs online, and diagnosis of what failed where. |
| 15 | `release` Release | ops | Prompts, skills, policy and config as versioned artifacts with an eval gate and rollback. |

The walk order is defined in `spec/diagram/diagram.json` (`walk_order`). Each node's lesson stub
is the `lesson` field of the same file; the UI shows it beside the inspector.

## Inside the nodes (expert view, chapter stubs)

The expert view opens some nodes into their parts. Each part gets a short section inside its
parent's chapter. These stubs list what each section will cover and a fixture that shows it
with the fake provider (pick it in the web app's Fixture menu, or pass `--fixture`).

| Part | Inside | Since | The section will show | Try it |
|---|---|---|---|---|
| `procedural` Procedural | Memory | P1 | The system prompt and the skills index, always in context, plus skills read on demand | `read_skill` |
| `session` Session | Memory | P1 | Earlier turns of this conversation and the rolling summary when they no longer fit | `two_turns` |
| `semantic` Semantic | Memory | P3 | Facts keyed by entity, matched by key first and cosine second, versioned with diffs; pack seeds every tenant reads, tenant facts only the writer adds | `remember_fact`, then "What is my preferred name?" |
| `episodic` Episodic | Memory | P3 | What happened before, ranked by relevance and recency; one episode per finished turn, written by the memory writer | Ask about order A-1003 |
| `knowledge` Knowledge | Memory | P3 | Documents cut into chunks, keyword and vector search fused by reciprocal rank, numbered `[n]` for the model to cite, and `read_doc` for a whole document | `trailhead_return`, `read_doc` |
| `router` Router | Model layer | P3 | Rules by step kind, risk, cost and mode pick the model; public stays local | `--model` hint in dev |
| `providers` Providers | Model layer | P1 | One adapter per provider, fallback on failure, health that a caller's own key cannot spoil | `plain_answer` |
| `registry` Registry | Tools | P1 | Typed tool definitions with an action class, what each mode offers | `tool_call` |
| `sandbox` Sandbox | Tools | P3 | A subprocess per call: scrubbed environment, limits, no network, no file change outside its directory, an OS jail | `tool_call`, `three_lookups` |
| `cost` Cost | Trace | P4 | The receipt of a turn: tokens, time and dollars per node and per model call, and what `turn_end.cost` adds up | Trace node, Turns tab, any turn |
| `offline_eval` Offline eval | Eval | P4 | The golden set, the deterministic scorers, the diff against the last run, and why a case runs in a scratch tenant | `harnesswalk eval _starter` |
| `online_eval` Online eval | Eval | P4 | Thumbs and notes on live turns, and a sampled judge whose scores attach to the nodes they judge | Good or Bad under a reply |
| `diagnose` Diagnose | Eval | P5 | Mapping a failed case to the layer that caused it (stub in P4) | |
| `artifacts` Artifacts | Release | P4 | Append-only versions of every prompt, skill and config file, the working set, and the hash chain over releases | Procedural inspector, Edit |

The P3 nodes of the simple view gain these sections in their chapters: input guardrails (PII
placeholders and the injection score, `pii_redacted`), policy (`why` and per-tenant budgets,
`three_lookups` under a budget), approval (a pause that survives a restart), audit (the hash
chain and Verify), output guardrails (schema, citations and placeholder restore) and the memory
writer (gates by mode, the review queue, writes as diffs, `remember_fact`).

## Eval and Release (P4, chapter stubs)

**Chapter 14, Eval: how you know a change made things better.** What the chapter will cover:

- A golden case is a prompt with what a good turn looks like: the node path, the tool calls,
  the citations, a schema, a substring or pattern in the answer, a rubric for a judge.
- Deterministic scorers first (path, tool calls, citations, schema, PII leak, answer match),
  judged ones second (faithfulness and relevance, 0 to 1, with a threshold), kept apart so a
  flaky judge never hides a broken path.
- A regression is a case that failed (or errored) now and passed in the previous run of the same
  pack and suite; the run lists them and diffs fixed, regressed, new and removed cases.
- Isolation: every case turn runs in a scratch tenant per run, so a run never reads or writes
  your memory and two runs start from the same state.
- Online: thumbs and notes per turn, and a judge on a sample of live turns.
- Things to try: edit the `returns` skill and run golden (the README's gate walk); run one case
  with `harnesswalk eval _starter --case returns_window`; open a regressed case in replay; rate a
  reply Bad with a note and find it in the Trace node's receipt.
- Breaks without it: a prompt edit that reads fine ships and quietly breaks returns; nothing
  says which change did it.

**Chapter 15, Release: prompts, skills, policy and config as versioned artifacts.** What the
chapter will cover:

- The working set (the latest version of every artifact), what a release freezes, and why the
  harness serves the working set in dev and the active release elsewhere.
- Append-only history: an edit is a new version, a rollback is new versions with old content,
  and a release's hash covers the release before it, so `verify` shows tampering.
- What an edit may not do from the web: add a tool entrypoint (that is new code) or change where
  provider requests and keys go (that is deployment).
- How a running server hears about a release made elsewhere (the CLI, another tab): the ops
  stream, `GET /v1/events`.
- Things to try: Create release after an edit and read its diff; roll back to `r1` and watch the
  next turn's `turn_start.summary.release`; `harnesswalk release list`, `diff`, `rollback` while
  the web app is open.
- Breaks without it: no way back to the prompt that worked, and no record of which version a
  turn ran on.
