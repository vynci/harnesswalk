# Lessons

One chapter per node, in walk order: the order a request meets the nodes, from the gateway to
release. Then one chapter per expert group, for the nodes the Advanced view opens, and three
chapters on the tuning loop (0.2.0). Each chapter has
the same six parts: what the node does, what breaks without it (with the exact pack, fixture and
prompt that show the failure, verified on the fake provider), a walk through its code with links
into `harnesswalk/`, three things to try with the expected outcome, three questions to check
yourself, and further reading.

Everything runs offline on the `fake` provider: no model key, no cost, and the same result every
time. The web app shows each chapter in the Guide (the header's Guide button; a node's inspector
has "Open in Guide"), lists them all there, and offers "Disable X and try it" where a node can be
switched off.

Written for a developer who knows web services and has never built an agent runtime. Read them in
order the first time; each one assumes the ones before it.

## The walk

| # | Node | Group | Chapter | In one line |
|---|---|---|---|---|
| 1 | `gateway` Gateway | control | [01-gateway](01-gateway.md) | Who is asking, for which tenant, and how a message becomes a turn. |
| 2 | `input_guardrails` Input guardrails | control | [02-input_guardrails](02-input_guardrails.md) | Size limits, PII redaction and injection checks, in code, before the model sees anything. |
| 3 | `context_builder` Context builder | context | [03-context_builder](03-context_builder.md) | What the model actually sees, assembled inside a token budget, stable content first. |
| 4 | `memory` Memory | context | [04-memory](04-memory.md) | Five stores, one retrieval interface: procedural, session, semantic, episodic, knowledge. |
| 5 | `loop` Loop | execution | [05-loop](05-loop.md) | Plan, act, observe, adjust, and every way the loop is made to stop. |
| 6 | `model_layer` Model layer | execution | [06-model_layer](06-model_layer.md) | One interface for every model: routing by rules, providers, fallback, cost. |
| 7 | `tools` Tools | execution | [07-tools](07-tools.md) | Typed tool calls, resolved, checked by policy and executed in a sandbox. |
| 8 | `policy` Policy | control | [08-policy](08-policy.md) | Allow, deny or needs approval, decided by rules on every single tool call. |
| 9 | `approval` Approval | control | [09-approval](09-approval.md) | A human in the loop: the turn pauses, the exact action is shown, nothing runs until someone decides. |
| 10 | `audit` Audit | control | [10-audit](10-audit.md) | The append-only record of consequential decisions, and why it is not the trace. |
| 11 | `output_guardrails` Output guardrails | execution | [11-output_guardrails](11-output_guardrails.md) | Schema, content and citation checks on the answer before it leaves. |
| 12 | `memory_writer` Memory writer | context | [12-memory_writer](12-memory_writer.md) | The one gated path that may write facts and events into memory. |
| 13 | `trace` Trace | ops | [13-trace](13-trace.md) | Every event of every turn, the cost receipt, and replay. |
| 14 | `eval` Eval | ops | [14-eval](14-eval.md) | Golden sets offline, judges and thumbs online, and diagnosis of what failed where. |
| 15 | `release` Release | ops | [15-release](15-release.md) | Prompts, skills, policy and config as versioned artifacts with an eval gate and rollback. |

The walk order is `walk_order` in `spec/diagram/diagram.json`. Each node there names its chapter
(`lesson_url`) and its one-line hook (`lesson_summary`); a test keeps the hooks, the chapters and
this index in step.

## Inside the nodes (expert groups)

| # | Chapter | Opens | Covers | In one line |
|---|---|---|---|---|
| 16 | [Inside memory: the five stores](16-memory-inside.md) | Memory | procedural, session, semantic, episodic, knowledge | What each store holds, how it ranks, and who may write it. |
| 17 | [Inside the model layer: router and providers](17-model_layer-inside.md) | Model layer | router, providers | Rules choose the model, adapters speak each provider's API, and a fixture can stand in for any of them. |
| 18 | [Inside tools: registry and sandbox](18-tools-inside.md) | Tools | registry, sandbox | What a tool is, what each mode offers, and the walls around the code that runs it. |
| 19 | [Inside the trace: cost](19-trace-inside.md) | Trace | cost | The receipt of a turn and the budgets that read it. |
| 20 | [Inside eval: offline, online and diagnose](20-eval-inside.md) | Eval | offline_eval, online_eval, diagnose | Golden runs in a scratch tenant, thumbs and a sampled judge on live turns, and the layer at fault. |
| 21 | [Inside release: artifacts](21-release-inside.md) | Release | artifacts | Append-only versions of every prompt, skill and config file, and the hash chain over releases. |

## The tuning loop (0.2.0)

Three chapters about changing the model itself, the fourth lever beside memory, procedural text
and configuration. Each opens its node in the Advanced view: Curate inside Eval, Tune inside
Release. Their demonstrations run the whole loop offline on the fake trainer, in one scratch
data directory, chapter after chapter. The tuning commands work under every profile, so no
chapter is greyed out on the governed packs (ecommerce, logistics) it uses.

| # | Chapter | Opens | Covers | In one line |
|---|---|---|---|---|
| 22 | [Curate: where training data comes from](22-curate.md) | Eval | curate | Where training data comes from: teacher traffic through the harness, kept only when it passes. |
| 23 | [Tune: train the model, then gate it](23-tune.md) | Release | tune | A dataset in, an adapter and a served model out, gated like any release, canaried through the router. |
| 24 | [Canary and rollback](24-canary.md) | Release | tune, router | A passed gate earns a share of real turns, not all of them: the router splits, live signals decide, rollback closes it. |

## The node switches

Four nodes can be switched off for one turn, in dev and private mode only: input guardrails,
policy, output guardrails and the memory writer. Their chapters (2, 8, 11, 12) and the approval
chapter (9) open with that switch. The other nodes cannot run without themselves, so their
chapters take away what the node depends on instead (a store, a budget, a file, a price) and show
what changes.

Every demonstration runs on the `_starter` pack, so the walk tells one story. The five switch
chapters also show the same failure on the domain packs whose feature it is (`also` below):
healthcare and gov_services for input guardrails, healthcare for output guardrails, ecommerce
and device_fleet for policy and approval, finance and long_session for the memory writer. The
Guide chapter gives each its own button.

## Chapter format

Each chapter is `NN-<name>.md` with YAML front matter, then markdown:

```yaml
---
node: input_guardrails            # the node the chapter is about (the parent, for a group)
covers: [procedural, session]     # group chapters only: the expert nodes, one ### section each
requires: [input_guardrails]      # 0.2.0: nodes that must be on; the Guide greys the chapter under a profile without them
try:                              # optional: what the "try it" button sets up in the web app
  pack: _starter                  # the pack it runs on (a new session when it is not the current)
  disable: [input_guardrails]     # nodes with can_disable, switched off for the next turn
  fixture: tool_call              # a fake fixture (dev mode)
  prompt: "My email is ..."       # put in the composer
also:                             # optional: the same demonstration on a domain pack
  - pack: healthcare              # each gets its own button, with its note
    disable: [input_guardrails]
    fixture: tool_clinic_hours
    prompt: "My health number is ..."
    note: "What changes, in one line."
---
```

The body starts with `# Title` and a `> hook` line, then the six `##` sections in order. Code
walk links are relative links into the package, `[text](../harnesswalk/<path>#<Symbol>)`, with a
top-level name or `Class.method`; the API resolves each to a line. Check yourself holds three
`### N. Question` blocks, each with three options as a task list (`- [x]` marks the right one)
and an `Answer:` paragraph. `GET /v1/lessons` and `GET /v1/lessons/{slug}` serve the chapters;
`tests/unit/test_http_lessons.py` checks the structure, the length (600 to 1200 words), that every
code link still resolves and that every node links its chapter.
