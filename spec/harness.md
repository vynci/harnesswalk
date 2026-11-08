# Harnesswalk harness specification

Spec version: **0.2.0**. Status: normative. 0.2.0 adds harness profiles (section 10).

This document describes what a Harnesswalk harness is, independent of language. The reference
implementation lives in `harnesswalk/` (Python). Any other implementation that passes the
conformance suite in `spec/conformance/` is a Harnesswalk harness and can drive the same diagram
and UI.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are used as in RFC 2119 and RFC 8174
when they appear in capitals.

## 1. Scope and companion files

| File | What it fixes |
|---|---|
| `spec/harness.md` | Planes, nodes, invariants, tenancy, modes (this file) |
| `spec/diagram/diagram.json` | Canonical node ids, groups, edges, lessons, walk order, paths |
| `spec/diagram/diagram.schema.json` | Shape of the diagram file |
| `spec/events.schema.json` | Shape of one harness event |
| `spec/events.md` | Event stream rules, per-node summary shapes, OpenTelemetry mapping |
| `spec/examples/*.jsonl` | Three normative example streams |
| `spec/contracts/*.md` | One interface per component, plus the HTTP API |
| `spec/conformance/` | Black-box protocol, cases, fake-provider fixtures |
| `spec/packs.md` | The dataset pack format, including the pack's `profile` (0.2.0) |

When two files disagree, the order of precedence is: `events.schema.json`, `diagram.json`,
this file, `events.md`, the contracts, the examples.

## 2. Vocabulary

- **Harness**: the runtime around a model that turns a request into a governed, observable
  answer. It is everything except the model weights.
- **Node**: one component of the harness with a stable id from `diagram.json`. Nodes are the unit
  of the diagram, the event stream, the lessons and the conformance paths.
- **Simple node / expert node**: the simple view has 15 nodes. The advanced view adds 14 child
  nodes, each with a `parent` that is a simple node.
- **Turn**: one unit of harness work with its own `turn_id` and `trace_id`. A chat request is a
  turn. A release or an eval run started outside chat is also a turn (`turn_start.summary.kind`).
- **Event**: one record in the stream, shaped by `events.schema.json`.
- **Tenant**: the isolation boundary for data, memory, documents, credentials, budgets and traces.
- **Mode**: `dev`, `public` or `private`. Section 7.
- **Profile** (0.2.0): a named harness shape, the nodes it switches off and the config it sets.
  A pack names one. Section 10.

## 3. Planes

The harness has three planes inside the runtime and one outside it. Coupling between the runtime
and ops is two arrows only: telemetry out (events to Trace) and versioned releases in (Release to
Memory).

| Group id | Plane | Nodes (simple view) | Question it answers |
|---|---|---|---|
| `control` | Control plane | gateway, input_guardrails, policy, approval, audit | Who is asking, what may they do, who must approve, what is on record |
| `context` | Context plane | context_builder, memory, memory_writer | What the model sees and how memory changes |
| `execution` | Execution plane | loop, model_layer, tools, output_guardrails | How the work gets done and checked |
| `ops` | LLM Ops | trace, eval, release | How we observe, judge and ship changes |

Expert children: `memory` contains `procedural`, `session`, `semantic`, `episodic`, `knowledge`;
`model_layer` contains `router`, `providers`; `tools` contains `registry`, `sandbox`; `eval`
contains `online_eval`, `offline_eval`, `diagnose`; `trace` contains `cost`; `release` contains
`artifacts`.

The simple view merges several boxes of the original design (`design_v2`): models into the model
layer, sandboxed execution into tools, the response box into the gateway, the system prompt into
procedural memory, versioned artifacts into release, cost into trace, diagnose into eval.

## 4. Nodes

Each node below lists: responsibility, inputs, outputs, invariants, what breaks without it, and
the events it emits. All nodes emit `node_enter` and `node_exit` (or `error`) for every
invocation; the "Events" line lists only what they add. Per-node `summary` shapes are in
`events.md` section 6. Interfaces are in `spec/contracts/`.

### 4.1 Control plane

#### `gateway` (code)

- **Responsibility**: accept a request from a channel, authenticate it, resolve tenant, user and
  session, rate-limit, normalize the message to one internal shape, start the turn, and render
  the reply back to the channel. The response box of `design_v2` is merged here.
- **Inputs**: channel request (HTTP in P1), credentials or session cookie, mode.
- **Outputs**: a normalized `Message` to `input_guardrails`; identity and tenant to `policy`;
  the rendered reply to the caller; the event stream for the turn.
- **Invariants**:
  - MUST assign `tenant_id`, `session_id`, `turn_id` and `trace_id` before any other node runs.
  - MUST emit `turn_start` as event `seq` 0 and `turn_end` as the last event of every turn.
  - MUST reject a request whose tenant cannot be resolved; it MUST NOT fall back to a shared
    tenant outside `dev` mode.
  - MUST apply rate limits before any model or tool cost is incurred.
  - SHOULD store media by reference, never inline in events.
- **Without it**: every node re-implements identity, and one forgotten check leaks across tenants.
- **Events**: `turn_start`, `turn_end`. Entered twice per chat turn: `phase: receive` first,
  `phase: reply` last.

#### `input_guardrails` (code, can be switched off in dev and private, and by a profile)

- **Responsibility**: deterministic checks before the model sees the input: size limits, PII
  redaction, prompt-injection heuristics.
- **Inputs**: normalized `Message`, guardrail config.
- **Outputs**: a `GuardrailResult` with action `pass`, `redact` or `block`, and the possibly
  redacted message.
- **Invariants**:
  - MUST run before `context_builder` on every chat turn.
  - MUST be deterministic code. It MUST NOT call a model in v1.
  - MUST NOT write the unredacted input into any event `summary`.
  - On `block` the turn MUST skip the context, execution and memory nodes and go to the gateway
    reply with a refusal. A block is a decision, not an `error` event.
- **Without it**: PII reaches the model, the trace and the memory writer; injected instructions
  arrive with the same authority as the user.
- **Events**: none beyond enter and exit.

#### `policy` (config, can be switched off in dev and private, and by a profile)

- **Responsibility**: decide whether a tool call may run, given mode, tenant, user and agent
  permissions, and the tool's action class.
- **Inputs**: a `ToolCall`, the tool's `action_class` (`read`, `write`, `destructive`,
  `external`), identity from the gateway, `config/policy.yaml`.
- **Outputs**: a `Decision`: `allow`, `deny` or `needs_approval`, with the matching rule id.
- **Invariants**:
  - MUST be evaluated for every tool call, including repeated calls in the same turn.
  - The effective permission MUST be the intersection of user permissions and agent permissions.
  - MUST default to `deny` when no rule matches.
  - MUST send to `audit` every `deny` and `needs_approval`, and every `allow` of a non-`read`
    action class. `read` allows SHOULD NOT be audited (they are in the trace). When `audit` is
    switched off (section 10) the record is sent and not kept: the `audit` span exits
    `skipped`.
  - Prompt text MUST NOT be able to change a decision.
- **Without it**: the model's judgment is the only thing between a request and a destructive
  action.
- **Events**: none beyond enter and exit.

#### `approval` (queue, can be switched off since 0.2.0)

- **Responsibility**: hold a high-risk action until a human approves or denies it, then resume.
- **Inputs**: a `needs_approval` decision with the exact tool call.
- **Outputs**: an `ApprovalDecision` (`approve`, `deny`, `expired`) back to `tools`.
- **Invariants**:
  - MUST show the exact action that will run (tool name and redacted arguments), not a paraphrase.
  - MUST emit `approval_requested`, then `node_paused` with reason `approval`, then on decision
    `approval_decided` and `node_resumed`.
  - The loop MUST checkpoint before pausing so a restart can resume the same turn.
  - An approval MUST be decided at most once. A second decision MUST be rejected.
  - `expired` MUST be treated as `deny`.
  - When `approval` is switched off (section 10), a `needs_approval` decision MUST be treated as
    `deny`: nobody is asked, nothing pauses, the call never executes, and the `approval` span
    exits `skipped` with `disabled: true` and `decision: deny`.
- **Without it**: there is no safe middle ground between "never allowed" and "always allowed".
- **Events**: `approval_requested`, `node_paused`, `approval_decided`, `node_resumed`.

#### `audit` (store, can be switched off since 0.2.0)

- **Responsibility**: append-only record of decisions with consequences: who asked, what ran,
  which rule decided, who approved, when.
- **Inputs**: records from `policy` and `approval`.
- **Outputs**: an `AuditRecord` id.
- **Invariants**:
  - MUST be append-only. There is no update or delete operation in the interface.
  - MUST be partitioned by tenant.
  - SHOULD be tamper-evident (each record carries the hash of the previous one in its tenant).
  - MUST be kept separate from the trace and MUST NOT be pruned by trace retention.
- **Without it**: you cannot answer "who approved this" after the trace has been sampled away.
- **Events**: none beyond enter and exit.

### 4.2 Context plane

#### `context_builder` (code)

- **Responsibility**: assemble the model input for each turn within a token budget.
- **Inputs**: clean input, session id, `config/context.yaml`, retrievals from `memory`.
- **Outputs**: an `AssembledContext`: ordered slices with token counts, plus pointers to anything
  compacted or left on demand.
- **Invariants**:
  - MUST enforce a token budget per slice and in total. Overflow MUST be resolved by compaction
    or truncation recorded in the summary, never by exceeding the budget silently.
  - MUST order slices stable-first (system prompt, skills index, then retrieved, then session,
    then the new input) so prefix caching works.
  - Tiers: `always` (system prompt, skills index), `retrieved` (facts, episodes, documents),
    `on_demand` (skill bodies or documents the model asks for through a tool).
  - MUST pass `tenant_id` on every store call.
  - SHOULD make the assembled context retrievable through `detail_ref` for the inspector.
- **Without it**: context grows until it overflows, important instructions fall off the end, and
  nobody can see what the model was actually shown.
- **Events**: none beyond enter and exit; the exit summary carries tokens per slice.

#### `memory` (store, group) and its five stores

- **Responsibility**: the read side of every store, behind one retrieval interface. The group
  node is entered once per retrieval pass; the stores are its expert children.
- **Stores**:

  | Store | Holds | Ranking rule | Written by |
  |---|---|---|---|
  | `procedural` | System prompt (skill zero) and skills | Always tier for prompt and index; bodies on demand by name | `release` only |
  | `session` | Recent turns, running summary, loop checkpoints | Recency, newest turns verbatim | `loop`, `gateway` |
  | `semantic` | Durable facts | Entity match first, then similarity; high precision | `memory_writer` only |
  | `episodic` | Dated events | Recency blended with relevance | `memory_writer` only |
  | `knowledge` | Documents (RAG) | Hybrid keyword and vector search, then rerank | ingestion via `release` |

- **Invariants**:
  - Every call MUST carry `tenant_id`; a store MUST NOT return records of another tenant.
  - Retrieval MUST respect the budget the context builder passes in.
  - `semantic` and `episodic` MUST NOT be written by any path other than `memory_writer`.
  - Since 0.2.0 `semantic`, `episodic` and `knowledge` can be switched off (section 10). A store
    that is off is not retrieved (its span exits `skipped` with `disabled: true`), takes no
    write, and `read_doc` is neither offered nor run while `knowledge` is off. `procedural` and
    `session` are always on.
- **Without it**: one undifferentiated memory where facts, chat and documents compete with the
  same ranking rule and nobody controls what gets written.
- **Events**: none beyond enter and exit.

#### `memory_writer` (code, can be switched off in dev and private, and by a profile)

- **Responsibility**: the single gated write path into `semantic` and `episodic`.
- **Inputs**: `MemoryProposal`s from the loop and from eval diagnosis.
- **Outputs**: written, versioned records, or rejections.
- **Invariants**:
  - MUST be the only writer of `semantic` and `episodic`.
  - MUST gate every proposal: human review, an eval check, or auto-approve. Auto-approve MUST
    only be possible in `private` mode with the tenant setting on, or in `dev`.
  - Writes MUST be versioned, diffable and scoped to one tenant (and user where applicable).
  - In `public` mode a proposal MUST NOT be written to durable memory (gate `session`).
  - MUST emit `memory_proposed` for every proposal and `memory_written` for every write.
  - In a chat turn it runs only when the loop produced proposals, after the gateway reply and
    before `trace`, so a slow gate never delays the answer.
- **Without it**: one hallucinated "fact" becomes permanent context for every later turn.
- **Events**: `memory_proposed`, `memory_written`.

### 4.3 Execution plane

#### `loop` (code)

- **Responsibility**: plan, act, observe, adjust. Call the model, run requested tools, feed back
  observations, repeat until an answer or an exit.
- **Inputs**: `AssembledContext`, tool schemas, loop limits.
- **Outputs**: a final answer to `output_guardrails`, memory proposals to `memory_writer`.
- **Invariants**:
  - MUST checkpoint after every step to the session store.
  - MUST enforce every exit: `max_steps`, `budget_tokens`, `budget_usd`, `timeout`, `stuck`
    (the same tool with the same arguments repeated `stuck_repeats` times), `interrupted`.
  - A step is one model call plus the tool calls it requested. Before starting step `k` the loop
    MUST exit with `max_steps` if `k > max_steps`.
  - After `max_steps` or `stuck` the loop MAY make one final model call with tools disabled
    (`loop.final_call_on_exit`, default true). After a budget, timeout or interrupt exit it MUST
    NOT call the model again and MUST answer with a fixed notice.
  - MUST honour breakpoints (step mode) by emitting `node_paused` with reason `breakpoint`.
- **Without it**: no tool use, or tool use with no ceiling.
- **Events**: none beyond enter and exit; the exit summary carries `exit_reason`.

#### `model_layer` (model)

- **Responsibility**: one interface in front of every model: routing, provider call, fallback,
  caching, structured output.
- **Inputs**: a `ModelRequest` (messages, tools, step, risk, modality, output schema).
- **Outputs**: a `ModelResponse` (text and or tool calls, finish reason, usage).
- **Invariants**:
  - MUST route by rules first (`router`); a model MAY choose only among options the rules allow.
  - MUST report token usage and cost on its `node_exit`.
  - Fallback to another provider MUST be visible in the summary (`fallback: true`).
  - The `fake` provider MUST be available in every implementation (contract `model_provider`).
- **Without it**: provider details leak into the loop, and swapping a model means a rewrite.
- **Events**: none beyond enter and exit; `cost` is set on its exit.

#### `tools` (code)

- **Responsibility**: resolve, check and execute tool calls.
- **Inputs**: a `ToolCall` from the loop.
- **Outputs**: an `Observation` back to the loop (result, error, or denial).
- **Invariants**:
  - MUST resolve the tool in the `registry`; unknown tools return an error observation, they
    never execute.
  - MUST call `policy` before execution on every call. `deny` returns a denial observation
    without executing. `needs_approval` goes to `approval` and waits.
  - MUST validate arguments against the tool's JSON schema before execution.
  - MUST execute in the `sandbox` with a timeout.
  - The node shows two sub-states: policy check, then execute.
- **Without it**: the model's tool call string is the program.
- **Events**: none beyond enter and exit.

#### `output_guardrails` (code, can be switched off in dev and private, and by a profile)

- **Responsibility**: check the answer before it leaves: schema validation, content checks,
  citations when the answer draws on `knowledge`.
- **Inputs**: final answer, the context slices it was built from.
- **Outputs**: a `GuardrailResult` with action `pass`, `repair` or `block`.
- **Invariants**:
  - MUST run on every answer before the gateway reply.
  - MUST require at least one citation when the context contained `knowledge` chunks and the
    answer makes a claim from them (config `output_guardrails.citations`).
  - MUST be deterministic code in v1.
- **Without it**: a malformed or uncited answer reaches the user and nobody notices.
- **Events**: none beyond enter and exit.

### 4.4 LLM Ops

#### `trace` (store)

- **Responsibility**: persist every event of every turn under its `trace_id`; produce the per-turn
  cost receipt (`cost` child).
- **Inputs**: the event stream.
- **Outputs**: stored traces, replay streams, cost per turn, node and tenant.
- **Invariants**:
  - MUST store events exactly as emitted (same `event_id`, `seq` and payload).
  - MUST partition by tenant.
  - Replay of a stored trace MUST produce the same events in the same order.
  - The trace node is entered once at the end of each chat turn, after the gateway reply and
    before `turn_end`, to flush the turn and compute the receipt.
- **Without it**: you cannot debug, replay, evaluate or price a turn.
- **Events**: none beyond enter and exit.

#### `eval` (code)

- **Responsibility**: offline golden sets, online judging and feedback, diagnosis of failures.
- **Inputs**: stored traces, golden sets per dataset pack, user feedback.
- **Outputs**: scores, regression cases, proposed fixes (to `memory_writer`), an eval gate result
  for `release`.
- **Invariants**:
  - Deterministic checks (node path, citation present, schema) and judged checks MUST be reported
    separately.
  - Offline eval MUST be runnable with the `fake` provider in CI.
  - Online eval MUST run on a sample, never on every turn by default.
  - Since 0.2.0 `eval`, `online_eval` and `diagnose` can be switched off (section 10): no turn
    of that pack is sampled or judged, and failed cases are not diagnosed on their own.
    `offline_eval` is never switched off: the golden set is how every rung is measured, and
    `POST /v1/evals/runs` and `POST /v1/diagnose` still answer, since they are asked for from
    outside the harness.
- **Without it**: every change is a guess.
- **Events**: none beyond enter and exit (eval runs are their own turns).

#### `release` (code)

- **Responsibility**: version every artifact the harness loads (system prompt, skills, policy,
  tool definitions, model config, context config), gate releases on eval, ship, roll back.
- **Inputs**: a candidate artifact change, an eval gate result.
- **Outputs**: a new artifact version loaded by the harness.
- **Invariants**:
  - The harness MUST load prompts, skills, policy and model config only through release.
  - Every version MUST be diffable against its predecessor and restorable by version id.
  - MUST emit `artifact_released` for each released artifact. Since P4 this is one ops event per
    release (or rollback) listing every artifact in it (`events.md` section 10).
  - Since 0.2.0 `release` can be switched off (section 10). Off means ungated: a release still
    versions every artifact and still loads through the release store (`artifacts` is never
    switched off), but the eval gate is `skipped` with `reason: profile`.
- **Without it**: a prompt edit in production is untracked and cannot be undone.
- **Events**: `artifact_released`.

## 5. Multi-tenant rule

This rule is structural. It MUST NOT depend on prompt text.

| Shared across tenants | Per tenant | Never crosses tenants |
|---|---|---|
| Harness code, control plane, model layer and routing rules, eval framework, generic skills | Identity and permissions, memories, knowledge index, session state, prompt and skill overrides, tool credentials, budgets, audit retention | Data, memory, documents, credentials, traces |

- Every store call MUST take `tenant_id` as a required argument. There is no default.
- Every event MUST carry `tenant_id`. The gateway MUST NOT stream an event to a viewer whose
  tenant differs.
- Credentials for tools MUST be scoped per tenant and resolved at execution time, never placed in
  context or events.
- Traces, audit records and memory MUST be partitioned so that a query without `tenant_id` is a
  programming error, not an empty result.

## 6. The diagram cannot drift

The diagram is data, and it is derived from the harness, not drawn beside it.

1. `spec/diagram/diagram.json` is the canonical list of node ids, groups and edges.
2. An implementation MUST keep a node registry: each component registers under a node id from
   the diagram. Registering an id that is not in the diagram MUST fail at startup.
3. `GET /v1/diagram` MUST be generated from the registry merged with `diagram.json`. Nodes that
   are in the diagram but not registered MUST be served with `implemented: false`, never hidden.
4. Every `node_id` and `edge_id` in an event MUST exist in `diagram.json`. An unknown id is a
   conformance failure.
5. The reference implementation MUST have a test that compares the generated diagram with
   `diagram.json` (ids, groups, parents, edges) and fails on any difference.
6. Referential rules checked by `tests/spec/test_diagram.py`: ids unique; every expert node has
   a simple-view parent in the same group; every edge endpoint exists; every `refines` names a
   simple edge; `walk_order` lists every simple node exactly once; every path uses known ids.

## 7. Modes

| | `dev` | `public` | `private` |
|---|---|---|---|
| Purpose | Local learning and building | Anonymous, short-lived sandbox | Signed-in, persistent tenant |
| Tenant | Default `dev` tenant; tenant header allowed | One tenant per anonymous session | One tenant per account |
| Persistence | Local volumes | Hard delete after the session TTL | Persistent, export and delete-my-data |
| Disable nodes (`can_disable`) | Allowed | Forbidden for a request; a profile switches off only nodes outside the controls (section 10) | Allowed |
| Edit prompts and skills | Allowed (new artifact version) | Session-scoped only, discarded at TTL | Allowed (new artifact version) |
| Memory writer auto-approve | Allowed | Forbidden | Allowed when the tenant enables it |
| Tools | All registered tools, subject to policy | Dataset pure functions and knowledge search only; no shell, no network, no file writes | Registered tools, subject to policy; no shell |
| Uploads | Allowed | Forbidden in v1 | Allowed with caps |
| Source and config inspectors | Full | Read-only | Read-only, own overrides editable |
| Provider keys | Env vars | Bring-your-own key in browser memory only, never stored or logged | Bring-your-own key or tenant key |
| Limits | Config | Per-IP and global rate limits, loop caps, daily hosted budget | Higher caps |

Normative rules:

- The mode MUST be fixed at process start and reported by `GET /healthz`.
- In `public` mode the harness MUST refuse to start if any tool with action class
  `destructive` or `external`, or any shell or network tool, is registered with a policy other
  than `deny`.
- In `public` mode disabling a node MUST be rejected by the API, not only hidden in the UI.
- A bring-your-own key MUST NOT appear in any event, trace, audit record or log line.
- Proposed `public` defaults (PLAN section 8): 30 requests per IP per hour, 8 loop steps, 16K
  context, 2 hour TTL, hosted-model budget 5 USD per day. They are configuration, not spec.

## 8. Step mode and replay

- Step mode sets a breakpoint on every node. After each `node_enter`, the harness MUST emit
  `node_paused` (reason `breakpoint`) and wait for `POST /v1/turns/{id}/resume`, then emit
  `node_resumed` and continue.
- Replay re-emits a stored trace without calling any model or tool. A replayed stream MUST be
  identical to the stored one except for an `ext.replay: true` marker if the implementation adds
  one.
- Breakpoints and approval are the only two reasons a node may pause.
- Step over (P4): resuming with `step_over` releases the breakpoint and skips the breakpoints of
  expert nodes until the next simple-view node is entered, where the harness pauses again.

## 9. Versioning

- The spec, the event schema and the diagram share one version (`0.2.0`). Since 0.2.0 the
  schema accepts any `0.x.y` `schema_version`, so 0.1.0 streams still validate.
- Adding an optional field, a node summary key or an expert node is a minor change. Removing or
  renaming a node id, edge id, event type, field or status is a major change.
- Events carry `schema_version`. A consumer MUST reject events with an unknown major version.

## 10. Profiles (0.2.0)

A **profile** is a named harness shape: which nodes are switched off, and which configuration
goes with that. A pack names the profile it runs with (`spec/packs.md`, `profile`), so each
pack shows a rung of the same diagram. The diagram does not change: a node a profile switches
off is **dimmed, never hidden**, and the rule of section 6 holds for every profile.

### 10.1 The profile file

| Key | Type | Meaning |
|---|---|---|
| `id` | string `^[a-z][a-z0-9_]{0,31}$` | The profile id; the file name when loaded by name |
| `title` | string | A short name for the UI |
| `summary` | string | One sentence: what this shape is for |
| `rung` | integer 1 to 6 | Its place on the ladder (10.4) |
| `disable` | node ids | The nodes switched off. Each MUST have `can_disable: true` in `diagram.json` |
| `stores` | store ids | The context stores that exist: the five of section 4.2 minus the disabled store nodes |
| `overrides` | map of dotted keys | Configuration set on top of the config files, only under `context.`, `loop.`, `input_guardrails.`, `output_guardrails.`, `memory_writer.` and `approval.timeout_s`. Never the router, the models or the policy rules |

No other keys. Loading a profile MUST fail (the harness refuses to start) when a key is unknown,
a `disable` id is not a diagram node or has no `can_disable`, a disabled node has a child with
`can_disable` that is not disabled too, `policy`, `approval` and `audit` are not all on or all
off, `stores` is not exactly the stores that are not disabled, or an override key is outside the
allowed prefixes.

### 10.2 Which profile a turn runs with

The harness profile of a pack is, first found: a server-wide choice (the dotted override
`profile`, or `HARNESSWALK_PROFILE` / `--profile` when it names a harness profile, by name or by
path; a file with a `rung` is a harness profile, a file without one is a config overlay such as
`spec/conformance/profile.yaml`), the pack's `pack.yaml` `profile`, then `operated`. A switch of
pack is a new session (P5), so a profile never changes inside a session.

For each turn the set of switched-off nodes is the union of the profile's (as the mode allows,
10.5) and the request's `disable` (dev and private). `turn_start.summary.profile` records the
shape: `{ id, rung, off }`, with `off` the profile's switched-off nodes in this mode
(`events.md` section 6). A turn restored after a restart keeps it.

### 10.3 What "off" means, node by node

Every node a turn would have entered is still entered when it is off: its span opens where it
would run and exits with status `skipped` and `summary.disabled: true`, so the walk keeps its
shape and the diagram shows the node faded. Nodes that run outside a turn (the ops plane)
simply do nothing for that pack. No event type is added.

| Node | Off means | Invariants that still hold |
|---|---|---|
| `input_guardrails` | The message passes unchanged; no redaction, no block | Deterministic; it still runs before `context_builder` |
| `output_guardrails` | The answer passes unchanged; no repair, no citation check | It still runs before the gateway reply |
| `knowledge` | Not retrieved, no documents index in context, `read_doc` not offered or run | Tenant scoping; the store is still ingested |
| `semantic`, `episodic` | Not retrieved; the memory writer drops proposals to it | Only `memory_writer` writes them |
| `memory_writer` | Every proposal is dropped (counted rejected); the turn's episode is still proposed when `episodic` is configured, so the span shows | Nothing else writes `semantic` or `episodic` |
| `policy` | Every call is allowed with rule `disabled`; non-`read` calls are still sent to audit | Prompt text still cannot change a decision; `approval` is never reached |
| `approval` | A `needs_approval` decision is a `deny`: nobody is asked, nothing pauses, the call never runs | Nothing that needs a human runs without one (fail closed) |
| `audit` | Records a turn would make are not kept; the span exits skipped | Append-only and tenant-partitioned for what is kept; out-of-turn records (memory review decisions, forced releases) are still kept |
| `eval`, `online_eval`, `diagnose` | No sampling, no online judging, no automatic diagnosis for the pack | Offline eval stays runnable with the fake provider |
| `release` | The eval gate is skipped (`reason: profile`) | Artifacts still load only through release, versioned and restorable |

`policy`, `approval` and `audit` are on or off together in a profile: approval without audit
cannot answer "who approved this", and policy without audit loses its denials. From `governed`
up all three are on. A profile below `governed` has no human in the loop, so a pack MAY run on
it only when none of its tools needs one: no tool of class `destructive` or `external`, and none
a policy rule sends to `needs_approval` (the pack validator checks this, `spec/packs.md`).

Nodes that are never switched off, and why:

| Node | Why it stays on |
|---|---|
| `gateway`, `trace`, `cost` | No turn, no stream, no diagram without them |
| `context_builder`, `memory`, `procedural`, `session` | The model always needs the system prompt and the session; a profile reduces what the context builder retrieves, not whether it runs |
| `loop`, `model_layer`, `providers` | There is no harness without the model call |
| `router` | `model_layer` MUST route by rules first; one model is a router rule, not a missing router, and the rules are not a profile's to change |
| `tools`, `registry`, `sandbox` | Every rung keeps the tool loop; a chat-only shape is a later addition |
| `offline_eval` | The golden set is how every rung is measured |
| `artifacts` | The harness MUST load its artifacts only through release |

### 10.4 The ladder

Six profiles ship in `config/profiles/`. Each rung adds to the one below it.

| Node | `minimal` 1 | `guarded` 2 | `grounded` 3 | `remembered` 4 | `governed` 5 | `operated` 6 |
|---|---|---|---|---|---|---|
| `input_guardrails`, `output_guardrails` | off | on | on | on | on | on |
| `knowledge` | off | off | on | on | on | on |
| `memory_writer`, `semantic`, `episodic` | off | off | off | on | on | on |
| `policy`, `approval`, `audit` | off | off | off | off | on | on |
| `eval`, `online_eval`, `diagnose`, `release` | off | off | off | off | off | on |
| every other node | on | on | on | on | on | on |

Overrides: the three lowest rungs set `context.compaction.summarize: false` (an old session is
truncated, never summarised by a model). `operated` is the harness of 0.1.0, and the default.

### 10.5 Public mode

A public server never switches off `input_guardrails`, `output_guardrails`, `policy`,
`approval` or `audit`, whatever the profile says; the other nodes of a profile are off as
anywhere else. The served diagram, `GET /v1/profiles` (`off`) and `turn_start.summary.profile`
report what the server actually switches off. A request's `disable` stays forbidden in public
mode (section 7).

### 10.6 What implementations serve

- `GET /v1/profiles` lists the profiles with their `disable` sets and what is `off` in this mode;
  `GET /v1/packs` gives each pack's `profile`; `GET /v1/diagram?pack=<id>` serves every node with
  `disabled: true` for the ones that pack's profile switches off (`contracts/http_api.md`).
- The conformance suite runs under every profile (`spec/conformance/README.md`, Profiles): a
  case declares the nodes it `requires` and is skipped, not failed, under a profile that
  switches one of them off.
- A per-profile layout of the diagram is out of scope for 0.2.0: every profile uses the same
  layout, dimmed.

