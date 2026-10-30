# Harness event contract

Schema: `spec/events.schema.json` (JSON Schema 2020-12), version **0.1.0**.
Examples: `spec/examples/*.jsonl`.

The event stream is the product. Every node reports its work as typed events, and the same
stream drives the live diagram, the trace store, step mode, replay and the evals. Emit these
events correctly and any UI built on harnesswalk can draw your harness.

## 1. Rules

1. **Single source.** The stream MUST be the only source of truth for the UI, the trace store,
   step mode and replay. Nothing the UI shows about a turn may come from a side channel.
2. **One turn, one stream.** All events of a turn share `trace_id`, `turn_id`, `session_id` and
   `tenant_id`. The first event MUST be `turn_start` with `seq` 0. The last MUST be `turn_end`.
3. **Sequence.** `seq` MUST increase by exactly 1 per event, with no gaps and no repeats.
   `ts` MUST NOT decrease along `seq`.
4. **Pairing.** Every `node_enter` MUST be closed, within the same turn, by exactly one
   `node_exit` or one `error` with the same `node_id`.
5. **Nesting.** Node spans MUST be properly nested: a `node_exit` or `error` closes the most
   recently entered node that is still open. A node calls another by entering it while it is
   itself open (loop calls model_layer, tools calls policy, context_builder calls memory).
   Concurrent sibling spans are reserved for a later version.
6. **Expert children.** An implementation MUST emit events for every simple node it runs and
   MAY emit events for expert nodes. An expert node MUST only be entered while its `parent` is
   open. A UI in the simple view rolls an expert node's events up to its parent.
7. **Point events.** `approval_requested`, `approval_decided`, `memory_proposed`,
   `memory_written`, `artifact_released`, `message_delta`, `node_paused` and `node_resumed` MUST
   be emitted while their `node_id` is open. Ops events (section 10, P4) are the exception: they are
   emitted outside any turn, on streams of their own.
8. **Pausing.** `node_paused` is allowed only for two reasons: `breakpoint` (step mode) and
   `approval`. Every `node_paused` MUST be followed by a `node_resumed` for the same node before
   that node closes, unless the turn ends in error or cancellation.
9. **Edges.** `edge` events are optional. When emitted they MUST name an edge from
   `diagram.json`, and SHOULD come immediately before the `node_enter` of the edge's target, or,
   when the target is already open (a return such as `model_layer_to_loop`), at the moment
   control returns. Edges are otherwise implied by consecutive nodes.
10. **Turn end.** All open nodes MUST be closed before `turn_end`. `turn_end.status` is `done`
    when the turn produced a reply (including refusals and exit notices) and `error` otherwise.
11. **Redaction.** `summary` MUST NOT contain secrets, provider keys, unredacted input rejected or
    redacted by guardrails, or full tool results. Full records go to the trace store and are
    referenced by `detail_ref`.
    One exception (P3): when the PII restore rule is on for the mode
    (`input_guardrails.pii.restore_modes`, default `dev` only), the reply in
    `turn_end.summary.reply` and `message_delta` carries the user's own values put back in
    place of their placeholders. Nothing else does, and never in `public` mode.
12. **Size.** `summary` SHOULD serialize to at most 2 KiB. The single exception is
    `turn_end.summary.reply`, which carries the final reply text and MAY be up to 16 KiB; longer
    replies MUST be truncated there and referenced by `detail_ref`.
13. **Cost.** `cost` appears on the `node_exit` of the node that incurred it (`model_layer` for
    inference, `knowledge` for embedding at query time) and once on `turn_end` as the turn total.
    Expert children MUST NOT repeat their parent's cost. Consumers sum node costs or read the
    turn total, never both.
14. **Tenancy.** Every event carries `tenant_id`. A transport MUST NOT deliver an event to a
    viewer of another tenant.

## 2. Fields

| Field | Type | Required | Meaning |
|---|---|---|---|
| `schema_version` | string | always | `0.1.0` |
| `event_id` | string | always | Unique within the trace. SHOULD be UUIDv7 or ULID |
| `seq` | integer | always | Position in the turn, from 0, gapless |
| `ts` | string | always | RFC 3339 with offset, SHOULD be UTC `Z` |
| `trace_id` | string | always | W3C trace id, 32 lowercase hex |
| `turn_id` | string | always | Turn id |
| `session_id` | string | always | Session (conversation) id |
| `tenant_id` | string | always | Tenant id |
| `type` | enum | always | Section 3 |
| `node_id` | enum | per type | Node id from `diagram.json` |
| `edge_id` | string | edge only | Edge id from `diagram.json` |
| `status` | enum | always | `active`, `waiting`, `done`, `error`, `skipped` |
| `duration_ms` | number | `node_exit`, `turn_end`; SHOULD on `error` with a node | Time since the matching enter (or turn start), paused time included |
| `summary` | object | per type | Small, redacted, shape per node in section 6 |
| `detail_ref` | string or null | optional | Pointer to the full record, `trace://<trace_id>/<event_id>` |
| `cost` | object | optional | `input_tokens`, `output_tokens`, `usd` |
| `error` | object | `error` only | `code`, `message` |
| `ext` | object | optional | Implementation-specific; consumers ignore it |

## 3. Types and allowed statuses

| Type | `node_id` | Status | Meaning |
|---|---|---|---|
| `turn_start` | absent | `active` | Turn accepted. `seq` 0 |
| `turn_end` | absent | `done` or `error` | Turn finished. Carries total `cost` and the reply |
| `node_enter` | required | `active` | Node started work |
| `node_exit` | required | `done` or `skipped` | Node finished. `skipped` means it ran but chose to do nothing (disabled, nothing to retrieve, tool not executed) |
| `node_paused` | required | `waiting` | Node waits on a breakpoint or an approval |
| `node_resumed` | required | `active` | Node continues |
| `edge` | absent | `done` | Data or control moved along a diagram edge |
| `approval_requested` | `approval` | `waiting` | A human decision is needed |
| `approval_decided` | `approval` | `done` | The decision: `approve`, `deny` or `expired` |
| `memory_proposed` | `memory_writer` | `waiting` | A memory proposal awaits its gate |
| `memory_written` | `memory_writer` | `done` | A proposal passed the gate and was written |
| `artifact_released` | `release` | `done` | A new artifact version is live. Since P4 an ops event: a release was created or rolled back to |
| `message_delta` | `gateway` | `active` | Optional. One chunk of the reply text, emitted inside the `phase: reply` gateway span |
| `eval_run_started` | `eval` | `active` | Ops event (P4): an eval run began |
| `eval_case_finished` | `eval` | `done` | Ops event (P4): one case of a run was scored |
| `eval_run_finished` | `eval` | `done` or `error` | Ops event (P4): a run ended |
| `feedback_recorded` | `eval` | `done` | Ops event (P4): thumbs and a note on a turn |
| `artifact_changed` | `release` | `done` | Ops event (P4): a working-set artifact got a new version |
| `error` | optional | `error` | Failure. With `node_id` it closes that node's span; without, it is a turn-level failure followed by `turn_end` with status `error` |

## 4. Error codes

| Code | Raised by | Meaning |
|---|---|---|
| `invalid_request` | gateway | Malformed request |
| `unauthenticated` | gateway | No valid identity or tenant |
| `rate_limited` | gateway | Rate or budget limit before work started |
| `provider_error` | model_layer | Provider failed and no fallback succeeded |
| `provider_timeout` | model_layer | Provider timed out and no fallback succeeded |
| `fixture_exhausted` | model_layer | The `fake` provider has no scripted response left |
| `fixture_mismatch` | model_layer | A `fake` response's `expect` did not match the request |
| `tool_error` | tools | The tool raised; the loop receives an error observation |
| `tool_timeout` | tools | The sandbox timeout fired |
| `store_unavailable` | memory, trace, audit | A store could not be reached |
| `cancelled` | any | The caller cancelled the turn |
| `restarted` | any | The harness restarted while the turn was running; recovery closed it (P3) |
| `internal` | any | Anything else |

A policy `deny`, a guardrail `block` and a loop exit on a limit are decisions, not errors. They
appear in `node_exit` summaries.

A tool error does not have to end the turn. `tools` MAY close with `error`, after which the loop
receives an error observation and continues.

## 5. Ordering of a chat turn

The simple-view node path of a turn is the `node_id` of every `node_enter` whose node is a
simple node, in `seq` order. The canonical paths are in `diagram.json` under `paths`.

```
turn_start
gateway                      (phase: receive)
input_guardrails
context_builder
  memory
    [procedural, session, semantic, episodic, knowledge]   expert, optional
loop
  model_layer
    [router, providers]                                     expert, optional
  tools                                                     once per tool call
    [registry]
    policy
    audit                                                   when policy must record
    approval                                                when policy says needs_approval
    audit                                                   records the decision
    [sandbox]                                               skipped when denied
  model_layer                                               next step
output_guardrails
gateway                      (phase: reply)
memory_writer                                               only when the loop proposed memories
trace
turn_end
```

When `input_guardrails` blocks, the path is `gateway, input_guardrails, gateway, trace`.

## 6. Summary shapes per node

Keys are listed for `node_exit` unless stated. All keys are optional unless marked (required).
Values are small: counts, ids, enums, short previews. Implementations MAY add keys.

| Node | Summary keys |
|---|---|
| `turn_start` | `kind` (`chat`, `release`, `eval`, `replay`), `channel`, `mode`, `step_mode`, `message_chars`, `disabled` (node ids, P3, so a restart can restore them), `release` (P4: `{release_id, working_set, artifacts_hash}` the turn runs on), `eval_run_id` (P4, on a case turn), `output_schema` (P4: `true` when the turn asked for a structured reply) |
| `turn_end` | `exit` (`answered`, `blocked`, `limit`, `error`, `cancelled`), `steps`, `reply`, `citations` (when the reply cites knowledge: `[{n, doc, chunk, citation}]` in first-cited order), `byo_key_dropped` (P4: the turn had a BYO key and resumed after a restart on the configured provider) |
| `gateway` | `phase` (`receive`, `reply`), `channel`, `authenticated`, `rate_limit_remaining`, `reply_chars` |
| `input_guardrails` | `action` (`pass`, `redact`, `block`), `checks`, `redactions` (counts by kind), `injection_score`, `injection_signals` (counts by category), `warnings`, `classifier_score`, `reason` |
| `policy` | `tool`, `action_class`, `decision` (`allow`, `deny`, `needs_approval`), `rule`, `why`, `budget` (`name`, `limit`, `used`, when a budget decided) |
| `approval` | `approval_id`, `decision`, `waited_ms` |
| `audit` | `record_id`, `action` (`policy_decision`, `approval_decision`, `tool_executed`, `memory_written`), `tool`, `decision`, `decided_by`, `executed`, `prev_hash`, `hash` |
| `context_builder` | enter: `budget_tokens`. exit: `budget_tokens`, `used_tokens`, `slices` (tokens per slice), `compacted`, `stable_prefix_tokens`, `retrieved` (items per retrieved slice), `citations` (knowledge chunks numbered), `trimmed` (items dropped per slice), `degraded` (stores that failed) |
| `memory` | `stores_queried`, `hits` (per store), `degraded` |
| `procedural` | `prompt_version` (a content stamp, `<name>@<sha256 prefix>`, so a rollback shows the old stamp again), `prompt_artifact_version` (P4, the working-set version number), `skills_indexed`, `tokens` |
| `session` | `turns`, `summary_tokens`, `tokens` |
| `semantic`, `episodic` | `hits`, `tokens`, `top_score`, `entity_hits` (semantic), `enabled` (false when the store is not in `context.stores`) |
| `knowledge` | `hits`, `top_score`, `tokens`, `docs` (document ids of the hits), `embedder`, `reranker`, `enabled` |
| `memory_writer` | `proposed`, `written`, `rejected`, `pending`, `gate`, `policy_rule`, `rejections` (`[{proposal_id, reason}]`) |
| `loop` | enter: `max_steps`, `budget_tokens`, `budget_usd`. exit: `steps`, `exit_reason` (`answer`, `max_steps`, `budget_tokens`, `budget_usd`, `budget_tool_calls`, `timeout`, `stuck`, `interrupted`), `tool_calls`, `budget_source` |
| `model_layer` | enter: `step`. exit: `step`, `provider`, `model`, `finish_reason` (`stop`, `tool_calls`, `length`), `tool_calls`, `fallback`, `tools_enabled`, `cached_tokens`, `cascade` |
| `router` | `rule`, `model`, `alternatives` |
| `providers` | `provider`, `model`, `attempt` |
| `tools` | enter: `tool`, `call_id`. exit: `tool`, `call_id`, `outcome` (`ok`, `denied`, `error`, `timeout`), `executed` |
| `registry` | `tool`, `found`, `action_class`, `timeout_ms` |
| `sandbox` | `tool`, `call_id`, `executed`, `timeout_ms`, `result_chars`, `level`, `network`, `jail`, `outcome` |
| `output_guardrails` | `action` (`pass`, `repair`, `block`), `checks`, `citations` (`required`, `present`, `invalid`, `uncited_claims`), `schema`, `leaks`, `restored`, `reasons` |
| `trace` | `events`, `persisted`, `store` |
| `cost` | `input_tokens`, `output_tokens`, `usd` |
| `eval`, `online_eval`, `offline_eval` | `suite`, `cases`, `passed`, `failed`, `judged` |
| `diagnose` | `layer` (`context`, `prompt`, `memory`, `tool`, `model`), `case_id` |
| `release`, `artifacts` | `artifact`, `version`, `previous_version`, `gate` |
| `node_paused` | `reason` (required), `approval_id` |
| `node_resumed` | `reason`, `approval_id` |
| `approval_requested` | `approval_id` (required), `tool` (required), `action_class` (required), `args_preview`, `reason`, `expires_at` |
| `approval_decided` | `approval_id` (required), `decision` (required), `decided_by`, `reason` |
| `memory_proposed` | `proposal_id` (required), `store` (required), `gate` (`review`, `eval`, `auto`, `session`), `preview` (redacted), `source` (`loop`, `turn`, `diagnose`), `redactions` (counts by kind), `injection` (P4, the proposal text's injection score when any signal fired) |
| `memory_written` | `proposal_id` (required), `store` (required), `record_id` (required), `version`, `op` (`create`, `update`) |
| `artifact_released` | In a turn (P0 shape): `artifact` (required), `version` (required), `previous_version`, `gate`. As an ops event (P4): `release_id` (required), `artifacts` (required, `[{kind, name, version, hash}]`), `hash`, `prev_hash`, `note`, `rollback_of` (on a rollback: the release that was active and is undone; `release_id` is then the release made active again), `pack` |
| `artifact_changed` | `kind` (required), `name` (required), `version` (required), `previous_version`, `hash`, `pack`, `note_chars` |
| `eval_run_started` | `run_id` (required), `pack`, `suite`, `model`, `total` (cases in the run) |
| `eval_case_finished` | `run_id` (required), `case_id` (required), `status` (required), `index`, `total`, `scores` (`{name: pass}`), `regression`, `turn_id` |
| `eval_run_finished` | `run_id` (required), `status`, `totals`, `regressions`, `regression_cases`, `previous_run_id` |
| `feedback_recorded` | `rating` (required, `good` or `bad`), `note_chars` |
| `message_delta` | `delta` (required, string), `index` (required, int from 0) |

### 6.1 Streaming the reply (`message_delta`)

`message_delta` lets a channel render the reply while it is produced. It is optional: an
implementation MAY emit it and the conformance suite never requires it, so a consumer MUST NOT
depend on it to get the reply (the full text is always in `turn_end.summary.reply`).

- It MUST only be emitted while the `gateway` span entered with `phase: reply` is open, after
  output guardrails have passed the text, so a streamed chunk is never one the guardrails would
  have blocked.
- `summary.index` starts at 0 and increases by 1 per delta within the turn. Concatenating every
  `summary.delta` in `index` order MUST give exactly `turn_end.summary.reply` (before its 16 KiB
  truncation).
- Each delta is subject to rule 12: a chunk SHOULD serialize to at most 2 KiB.
- It carries no `cost`; the model layer already reported it.

## 7. Transport

- Over HTTP the stream is Server-Sent Events (`spec/contracts/http_api.md`). Each event is one
  SSE message: `id` is `seq`, `event` is `type`, `data` is the event JSON on one line.
- On disk (trace export, fixtures, examples) the stream is JSON Lines, one event per line, in
  `seq` order.

## 8. Mapping to OpenTelemetry GenAI semantic conventions

A node span is the interval from `node_enter` to its `node_exit` or `error`. An exporter can turn
the stream into OpenTelemetry spans without extra information. The GenAI conventions are still
marked as in development upstream; this mapping targets the attribute names current in late
2026 and will be revised with the spec.

| Event field | OpenTelemetry | Notes |
|---|---|---|
| `trace_id` | Span context trace id | Same 32-hex format |
| node span | Span | `node_enter` starts it, `node_exit` or `error` ends it; nesting gives the parent span |
| `node_id` | Span name, and `harnesswalk.node.id` | For `model_layer`: span name `chat {model}`; for `tools`: `execute_tool {tool}`; for `loop`: `invoke_agent` |
| (span kind) | `gen_ai.operation.name` | `chat` on `model_layer`, `execute_tool` on `tools`, `invoke_agent` on `loop`, `embeddings` on `knowledge` when it embeds |
| `ts` | Span start or end time, event time | `node_enter.ts` is start, the closing event's `ts` is end |
| `duration_ms` | Derived from span start and end | Not exported as an attribute |
| `status` | Span status | `error` maps to `ERROR`; others to `UNSET` or `OK` |
| `error.code` | `error.type` | |
| `error.message` | Span status description | |
| `session_id` | `gen_ai.conversation.id` | |
| `tenant_id` | `harnesswalk.tenant.id` | No standard attribute |
| `turn_id` | `harnesswalk.turn.id` | |
| `event_id`, `seq` | `harnesswalk.event.id`, `harnesswalk.event.seq` | On span events |
| `cost.input_tokens` | `gen_ai.usage.input_tokens` | On the `model_layer` span |
| `cost.output_tokens` | `gen_ai.usage.output_tokens` | |
| `cost.usd` | `harnesswalk.cost.usd` | No standard attribute |
| `summary.provider` (model_layer) | `gen_ai.provider.name` | Older collectors use `gen_ai.system` |
| `summary.model` (model_layer) | `gen_ai.request.model`, `gen_ai.response.model` | |
| `summary.finish_reason` | `gen_ai.response.finish_reasons` | As a one-element array |
| `summary.tool` (tools) | `gen_ai.tool.name` | |
| `summary.call_id` (tools) | `gen_ai.tool.call.id` | |
| `edge`, `approval_*`, `memory_*`, `artifact_released`, `node_paused`, `node_resumed` | Span events on the open span | Name is the event `type`, summary keys become attributes prefixed `harnesswalk.` |
| `turn_start` to `turn_end` | Root span `harnesswalk.turn` | Optional; parent of the first node span |
| `detail_ref` | `harnesswalk.detail_ref` | |

Content stays out of telemetry (P4 integration): the summary keys that carry text a person or
the model wrote (`reply`, `delta`, `text`, `message`, `preview`, `args_preview`, `note`,
`query`, `content`) are never exported as attributes, and `message_delta` events are not
exported at all. Summaries are redacted for the UI, but in dev the reply carries the user's
own values back, and the GenAI conventions keep message content out unless a deployment opts
in (an opt-in is not offered yet). Secrets never appear in events, so none can reach an
exporter.

Message content (`gen_ai.input.messages`, `gen_ai.output.messages`) is not in the stream. An
exporter MAY attach it from the trace store when the deployment allows content capture.

## 9. Examples

| File | Scenario | Simple path |
|---|---|---|
| `spec/examples/plain_answer.jsonl` | Plain question, no tool, expert children for memory and model layer | `diagram.json` `paths.plain_answer` |
| `spec/examples/tool_call.jsonl` | One read-only tool call, allowed by policy, not audited | `paths.tool_call` |
| `spec/examples/approval_pause.jsonl` | Destructive tool call: policy needs approval, audit, pause, approve, audit, execute | `paths.approval_pause` |

All three were produced with the `fake` provider, so `usd` is 0. `tests/spec/test_events.py`
checks every line against the schema and every stream against the rules in section 1.

## 10. Ops events (P4)

Some events belong to no turn: an eval run's progress, feedback on a finished turn, an edited
artifact, a release. They use the same envelope and schema, with these differences:

- They live on **ops streams**, never in a turn's stream. An eval run has its own stream (the
  run's events, `seq` from 0 per run, one `trace_id` per run). Feedback, artifact and release
  events go to the tenant's `ops` stream (`seq` from 0 per tenant).
- `turn_id` and `session_id` are `null`, except on `feedback_recorded`, which carries the turn's
  `turn_id`, `session_id` and `trace_id`. `trace_id` is otherwise new per eval run or per event.
- `node_id` names the node the event is about (`eval` or `release`), which is not open: rule 7
  does not apply, and no `node_enter` or `turn_start` precedes them.
- `summary` follows the table in section 6; the same redaction and size rules apply (a feedback
  note is never in the event, only its length).
- Over HTTP an eval run's stream is SSE at `GET /v1/evals/runs/{id}/events`; the `ops` stream is
  read with `GET /v1/trace/ops-events`; `GET /v1/events` follows every ops stream of a tenant at
  once (P4 integration, `http_api.md`). OpenTelemetry exports each ops event as a span of its own
  (name `harnesswalk.<type>`), with summary keys as `harnesswalk.` attributes.

| Type | Stream | `node_id` | `status` |
|---|---|---|---|
| `eval_run_started` | the run | `eval` | `active` |
| `eval_case_finished` | the run | `eval` | `done` |
| `eval_run_finished` | the run | `eval` | `done`, or `error` when the run could not finish |
| `feedback_recorded` | `ops` | `eval` | `done` |
| `artifact_changed` | `ops` | `release` | `done` |
| `artifact_released` | `ops` | `release` | `done` |
