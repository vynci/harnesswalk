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
