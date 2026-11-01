# Contract: HTTP API (gateway, P1 and P2)

The gateway's HTTP surface. The web UI, the CLI runner and the conformance runner all use it.
JSON in and out, UTF-8, except the event stream, which is Server-Sent Events.

P1 defined turns, the event stream, resume, approvals, the diagram, node inspectors and health.
P2 adds what the web UI needs: cancel, turn summaries, cursor replay with `follow`, the session
turn list, a code peek per node, capabilities, per-turn options, serving the built web app and
dev-mode CORS.

| Method and path | Purpose | Since |
|---|---|---|
| `POST /v1/turns` | Start a turn | P1, options P2 |
| `GET /v1/turns/{turn_id}` | Turn summary | P2 |
| `GET /v1/turns/{turn_id}/events` | Event stream, replay and reconnect | P1, `after` and `follow` P2 |
| `POST /v1/turns/{turn_id}/resume` | Release a breakpoint | P1 |
| `POST /v1/turns/{turn_id}/cancel` | Stop a running or paused turn | P2 |
| `GET /v1/sessions/{session_id}/turns` | Turn list for chat history | P2 |
| `POST /v1/approvals/{approval_id}` | Decide a pending approval | P1 |
| `GET /v1/diagram` | The diagram with implementation flags | P1 |
| `GET /v1/nodes/{node_id}` | What an inspector shows | P1 |
| `GET /v1/nodes/{node_id}/source` | Code peek | P2 |
| `GET /v1/capabilities` | What this build can do | P2 |
| `GET /v1/audit` | Audit export for the caller's tenant | P3 |
| `GET /healthz` | Readiness | P1 |
| `GET /v1/memory/proposals` | Memory proposals of the caller (`?status=pending` waiting at the review gate, default; `all` the last ones with their outcome and diff) | P3 |
| `POST /v1/memory/proposals/{id}` | Decide a proposal at the review gate: `{"decision": "accept" \| "reject", "reason"?}` | P3 |
| `GET /v1/knowledge/docs` | The documents the caller can retrieve from (pack and own) | P3 |
| `GET /v1/knowledge/docs/{doc_id}` | One document with its chunks and their citation strings | P3 |

## Conventions

- Base path `/v1`. `GET /healthz` sits outside it.
- Identity: in `dev` mode every request is the `dev` tenant unless the header
  `X-Harnesswalk-Tenant` names another (dev only; ignored in other modes). Ids starting with
  `evalrun-` are reserved for eval runs' scratch tenants and are `400` (P4 integration). In `public` mode a
  signed, HTTP-only session cookie carries the anonymous tenant. In `private` mode a session
  cookie or bearer token from sign-in.
- BYO key (public and private): header `X-Harnesswalk-Provider-Key` on `POST /v1/turns` only.
  It is used for that turn and dropped. It is never echoed, stored or logged.
- Errors use one shape, with HTTP status codes below:

```json
{ "error": { "code": "turn_not_found", "message": "No turn turn_123 for this tenant." } }
```

- A resource of another tenant is `404`, never `403`, so ids cannot be probed.
- Every `POST` (`/v1/turns`, `resume`, `cancel`, `approvals`) MUST send
  `Content-Type: application/json` (parameters such as `charset` are allowed); anything else,
  or none, is `415 unsupported_media_type` before the body is read. A `text/plain` or form body,
  or a POST with no body at all, is a request a browser sends cross-site without a CORS
  preflight, so this keeps a foreign page from starting or stopping turns. `cancel` reads no
  fields: its body may be empty or `{}`, but the content type is still required.

| Status | When |
|---|---|
| `400` | `invalid_request` (body or parameters) |
| `401` | `unauthenticated` |
| `403` | `forbidden_in_mode`: an endpoint this mode does not offer (P3: `GET /v1/audit` in public mode) |
| `404` | Unknown id, or another tenant's |
| `409` | Conflict: turn already running in the session, approval already decided, turn not paused, turn already finished |
| `415` | `unsupported_media_type`: a body that is not declared `application/json` |
| `422` | Valid JSON that breaks a rule (for example disabling a node in public mode) |
| `429` | `rate_limited`, with `Retry-After` |
| `503` | Not ready (`/healthz` only reports it) |

## `POST /v1/turns`

Start a turn. Returns as soon as the turn is accepted; progress is on the event stream.

Request:

```json
{
  "message": "Please cancel order A-1002.",
  "session_id": "sess_demo",
  "step": false,
  "disable": [],
  "options": { "dataset": "ecommerce" }
}
```

| Field | Required | Meaning |
|---|---|---|
| `message` | yes | User text, at most `gateway.max_message_chars` |
| `session_id` | no | Continue a session; omitted creates one |
| `step` | no | Boolean. Start with a breakpoint on every node (section 8 of `harness.md`) |
| `provider` | no | Dev mode only: run this turn on one provider (`fake`, `ollama`, `anthropic`, `openai_compatible`). It must be `configured` in `/v1/capabilities` |
| `fixture` | no | Dev mode only: the `fake` provider's fixture for this turn. Implies `provider: fake` |
| `disable` | no | Node ids with `can_disable`, disabled for this turn, the same as the CLI `--disable`. Dev and private only |
| `options.step_mode` | no | P1 spelling of `step`; either one turns step mode on |
| `options.dataset` | no | Dataset pack for this session; fixed after the first turn |
| `options.disabled_nodes` | no | P1 spelling of `disable`; the two lists are merged |

`step`, `provider`, `fixture` and `disable` are also accepted inside `options`; a top-level value
wins. Mode checks:

| Field | `dev` | `private` | `public` |
|---|---|---|---|
| `step` | yes | yes | yes |
| `provider`, `fixture` | yes | `400 invalid_request` | `400 invalid_request` |
| `disable` | yes | yes | `422 invalid_request` |

An unknown provider, an unknown fixture, a fixture file that cannot be parsed, or a fixture with
a provider other than `fake` is `400 invalid_request`. A fixture name is 1 to 64 letters, digits,
`_` or `-`, starting with a letter or digit. A known provider that is not configured is `422 provider_not_configured`. A node
without `can_disable` is `422 invalid_request`, as in P1.

A per-turn fixture follows the fake provider's rule (`model_provider.md`), counted over the turns
of the session that ran on that same fixture: the first turn on a fixture reads `turns[0]`,
whatever the session's turn number. A fixture set at start-up (`HARNESSWALK_FIXTURE`) keeps the
plain rule, turn `n` of the session reads `turns[n]`. That includes `provider: fake` without a
`fixture` on a server started with another provider: the turn runs the start-up fixture
(`models.providers.fake.fixture`, `plain_answer` by default) with the plain rule.

Response `202 Accepted`:

```json
{
  "turn_id": "turn_approval_pause",
  "trace_id": "5b8aa5a2d2c872e8321cf37308d69df2",
  "session_id": "sess_demo",
  "events_url": "/v1/turns/turn_approval_pause/events"
}
```

Errors: `400`, `401`, `409 turn_in_progress`, `415`, `422`, `429`.

## `GET /v1/turns/{turn_id}/events`

The turn's event stream as Server-Sent Events (`Content-Type: text/event-stream`).

Query `?after=<seq>&follow=<bool>`.

- Each event is one SSE message: `id:` is `seq`, `event:` is the event `type`, `data:` is the
  event JSON on a single line. Every message carries `id:`, so `Last-Event-ID` works.
- Cursor: the query `after=N` (P1 spelling `after_seq=N`) or the standard `Last-Event-ID`
  header returns events with `seq > N`. When more than one is given the largest wins, so a
  browser that reconnects to the same URL with `Last-Event-ID` never sees an event twice.
  Without any, the stream starts at `seq` 0; `after=-1` means the same.
- `follow` (default `true`): for a running turn the server sends stored events, then live events
  as they happen, and closes the stream after `turn_end`. A cursor past `turn_end` still closes
  the stream when the turn ends, without sending anything. With `follow=false` it sends what is
  stored and closes, running or not.
- For a finished turn it sends the stored events and closes. This is also how replay works in
  the UI.
- While a turn is paused (approval or breakpoint) the server sends an SSE comment
  (`: keepalive`) every 15 seconds.

```
id: 37
event: approval_requested
data: {"schema_version":"0.1.0","event_id":"evt_approval_pause_037","seq":37,...}

```

Errors (before the stream starts, as JSON): `400` when a cursor (`after`, `after_seq` or
`Last-Event-ID`) is not an integer of at most 18 digits with an optional leading `-` (the rule
`-?\d{1,18}`, surrounding spaces allowed), so every cursor fits a 64-bit integer; an empty
cursor counts as absent. Also `401`, `404`.

## `GET /v1/turns/{turn_id}`

The turn summary, for a reload, the chat history and a Stop button that knows whether to show.
Built from the turn's stored events (rule 1 of `events.md`), so it reads the same live and after
a restart.

Response `200`:

```json
{
  "turn_id": "turn_approval_pause",
  "session_id": "sess_demo",
  "tenant_id": "dev",
  "trace_id": "5b8aa5a2d2c872e8321cf37308d69df2",
  "turn_index": 0,
  "status": "paused",
  "exit": null,
  "reply": null,
  "message": null,
  "started_at": "2026-10-29T14:10:00.000Z",
  "ended_at": null,
  "cost": { "input_tokens": 980, "output_tokens": 24, "usd": 0.0 },
  "event_count": 37,
  "paused_at_node": "approval",
  "pending_approval_id": "apr_0001"
}
```

| Field | Meaning |
|---|---|
| `status` | `running`, `paused` (a `node_paused` without its `node_resumed`) or `done` (`turn_end` stored) |
| `exit` | `turn_end.summary.exit`, `null` until done |
| `reply` | `turn_end.summary.reply`, `null` until done |
| `message` | The user text as the session stored it after input guardrails; `null` while running, for blocked input, and for a turn that ended with `turn_end.status` `error` (those are not session history) |
| `started_at`, `ended_at` | `ts` of `turn_start` and of `turn_end` (`null` until done) |
| `cost` | `turn_end.cost` when done; while running, the sum of node costs so far |
| `event_count` | Events stored for the turn |
| `paused_at_node` | The paused node, else `null` |
| `pending_approval_id` | An `approval_requested` without its `approval_decided`, else `null` |
| `trace_id`, `turn_index` | Extra, for linking a trace and ordering a session |

Errors: `401`, `404`.

## `POST /v1/turns/{turn_id}/cancel`

Stop a running or paused turn. The body is empty or `{}`, sent as `application/json` (see
Conventions).

Response `202`: `{ "turn_id": "...", "cancel_requested": true }`. The turn ends on its own; the
client keeps reading the event stream until `turn_end` with `summary.exit: cancelled`, and the
turn summary's `status` becomes `done`.

| The turn is | Effect |
|---|---|
| Running, between steps | The loop stops at the next step boundary (before a model call, before each tool call) with `exit_reason: interrupted`, answers with its fixed notice, and the turn ends normally: `turn_end.status` `done`, `summary.exit` `cancelled` |
| Paused on a breakpoint or an approval, or reaching one after the cancel, up to and including the `phase: reply` gateway span | Stops at once. The paused node and every open node close with `error` code `cancelled` (rule 8 of `events.md`), a pending approval becomes `expired`, then `turn_end` with status `error` and `summary.exit` `cancelled` (`gateway.md`) |
| Inside a model or tool call | The call finishes, then one of the rows above applies |
| Past the loop (output guardrails, reply, trace) without a breakpoint | Nothing is left to stop; the turn ends as it would have |
| After the `phase: reply` gateway span closed (memory writer, trace), paused on a breakpoint or not | The reply is already in the session, so nothing is left to stop: the cancel is not honoured. It is still `202`, any breakpoint is released (as `continue`), and the turn ends as it would have, with its own `summary.exit` |

A `202` asks; it does not promise `summary.exit: cancelled`. When the cancel lands inside a
model call whose response has no tool calls, the response is the answer, the loop exits
`answer`, and the turn ends `answered` with that reply. The same holds after the reply phase
(row above). The turn summary and `turn_end` always say which way it went.

The session records a cancelled turn like any other, so turn numbers stay gapless. Cancelling
twice before the turn ends is `202` again.

Errors: `401`, `404`, `415`, `409 turn_finished` when the turn already ended. The `409` body carries the
final state next to the error: `{ "error": {...}, "turn": <turn summary> }`. A turn this process
is not running and that has no `turn_end` (cut off by a restart) is `409 turn_not_running`.

## `GET /v1/sessions/{session_id}/turns`

Query `?limit=<n>` (default 50, at most 200).

Response `200`: a JSON array of turn summaries (the shape of `GET /v1/turns/{turn_id}`), newest
first. Tenant-scoped: an unknown session, or another tenant's, is `404 session_not_found`.

Errors: `400` (bad `limit`), `401`, `404`.

## `POST /v1/approvals/{approval_id}`

Decide a pending approval.

Request:

```json
{ "decision": "approve", "reason": "Customer confirmed by phone." }
```

`decision` is `approve` or `deny`; `reason` is optional.

Response `200`:

```json
{
  "approval_id": "apr_0001",
  "turn_id": "turn_approval_pause",
  "decision": "approve",
  "decided_by": "user:dev",
  "decided_at": "2026-10-29T14:10:41.475Z"
}
```

Errors: `400`, `401`, `404`, `409 approval_already_decided`, `415`.

The turn resumes on its own; the client keeps reading the same event stream.

## `POST /v1/turns/{turn_id}/resume`

Step mode: release the current breakpoint.

Request:

```json
{ "mode": "step" }
```

| `mode` | Effect |
|---|---|
| `step` | Continue to the next breakpoint (the next `node_enter`) |
| `continue` | Clear all breakpoints for the rest of the turn |

Response `200`: `{ "turn_id": "...", "resumed_node_id": "context_builder", "mode": "step" }`.

Errors: `401`, `404`, `409 turn_not_paused` (not at a breakpoint; an approval pause is not
resumed here), `415`.

## `GET /v1/diagram`

The diagram, generated from the node registry merged with `spec/diagram/diagram.json`
(`harness.md` section 6).

Response `200`: the `diagram.json` document with these additions per node:

| Field | Meaning |
|---|---|
| `implemented` | `true` when a working component is registered under this id |
| `stub` | Optional. `true` when the id is registered by a placeholder that only reports `skipped`; such a node has `implemented: false` |
| `disabled` | `true` when the node is disabled for the caller's session |

Query `?view=simple|expert` filters nodes and edges by `view` (expert includes simple). Default
returns everything.

## `GET /v1/nodes/{node_id}`

What an inspector shows for one node. The payload depends on the node's `kind`.

Response `200`:

```json
{
  "id": "policy",
  "label": "Policy",
  "kind": "config",
  "group": "control",
  "lesson": "The rules that decide whether a tool call may run...",
  "implemented": true,
  "can_disable": true,
  "inspector": {
    "type": "config",
    "path": "config/policy.yaml",
    "version": "policy@3",
    "format": "yaml",
    "content": "version: 1\nrules:\n  - ...",
    "editable": true
  },
  "source": { "path": "harnesswalk/control/policy.py", "language": "python", "content": "..." },
  "recent": [ { "turn_id": "turn_approval_pause", "status": "done", "summary": { "decision": "needs_approval" } } ]
}
```

| `kind` | `inspector.type` | `inspector` contents |
|---|---|---|
| `code` | `source` | Same as `source`, plus the node's last summary |
| `config` | `config` | Current config artifact (path, version, format, content, editable) |
| `prompt` | `prompt` | System prompt and skills: list of `{name, version, content}`, editable |
| `store` | `store` | Record count, the last few records (redacted, tenant-scoped), store settings |
| `queue` | `queue` | Pending items for the caller's tenant (approvals, proposals) |
| `model` | `model` | Configured models, router rules, the last routing decision |

- `source` is always present: the read-only source of the node's module (`source_hint`), so every
  node has a code peek.
- `recent` holds the caller's last `node_exit` summaries for this node (default 5, `?recent=N`).
- `editable` is false in public mode for shared artifacts. Edits go through release (P4), not
  through this endpoint.
- P4: every inspector that shows a release artifact names it as `artifact: {kind, name, version,
  url}` (`url` is its `/v1/artifacts/{kind}/{name}`; `null` when the artifact was removed): a
  `config` inspector for its file, each `prompt` item for the system prompt or its skill, and
  (P4 integration) the inspector of any node whose `artifact_hint` is a config file, such as
  `context_builder` (`context/context`) and `model_layer` (`models/models`). A UI edits that
  artifact with `PUT /v1/artifacts/{kind}/{name}` instead of guessing it from the node.

Errors: `401`, `404` (unknown node id).

## `GET /v1/nodes/{node_id}/source`

The code peek for the inspector: the file named by the node's `source_hint`. Available in every
mode, since the code is open source.

Response `200`:

```json
{
  "node_id": "policy",
  "path": "harnesswalk/control/policy.py",
  "language": "python",
  "line_count": 212,
  "source": "\"\"\"Policy: ...",
  "truncated": false,
  "symbols": [ { "name": "Policy", "line": 40, "kind": "class" } ]
}
```

- `path` is relative to the repository root. Only regular files under the `harnesswalk/` package
  are served, after resolving links and `..` in both the file and the package directory (so a
  package installed as a link still has a code peek); anything else, and a node whose file does
  not exist yet, is `404 source_not_found`.
- `source` is capped at 200 KiB of UTF-8; a longer file is cut at a line end and has
  `truncated: true`. `line_count` counts the whole file.
- `language` comes from the extension (`python`, `yaml`, `markdown`, `json`, else `text`).
- `symbols` lists the top-level `class` and `def` (`kind`: `class`, `function`) of a Python file
  with their 1-based line, from the syntax tree; it is empty for other files.
- No identity is needed: the code is the same for every tenant.

Errors: `404`.

## `GET /v1/capabilities`

What this build can do, so the UI adapts instead of guessing. No identity is needed, so a
signed-out UI can read the mode first.

Response `200`:

```json
{
  "mode": "dev",
  "version": "0.1.0",
  "spec_version": "0.1.0",
  "providers": [
    { "name": "fake", "configured": true, "default": true, "models": ["fake-small"] },
    { "name": "ollama", "configured": true, "default": false, "models": ["local-8b", "local-small"] }
  ],
  "fixtures": ["approval_approve", "plain_answer"],
  "features": {
    "step_mode": true, "approvals": true, "message_delta": true, "cancel": true,
    "byo_key": false, "disable_nodes": true, "knowledge": true, "memory_review": true
  },
  "limits": { "max_message_chars": 32000, "max_steps": 8, "session_ttl_seconds": null }
}
```

| Field | Meaning |
|---|---|
| `providers` | Every known provider. `configured` is true when a turn could use it: the one selected at start-up, `fake` in dev mode, `ollama` with a base URL, a hosted provider only with `enabled: true` and its key or URL. `default` marks the provider the default route lands on. `models` are the chat model aliases of `config/models.yaml` for that provider |
| `fixtures` | Fixture names a turn may ask for (files the `fake` provider finds whose name is a valid fixture name), dev mode only, else empty |
| `features.message_delta` | True when `gateway.stream_reply_chars` is above 0 |
| `features.byo_key` | True in `public` and `private` mode (the `X-Harnesswalk-Provider-Key` header) |
| `features.disable_nodes` | True outside `public` mode |
| `features.knowledge` | True when `knowledge` is among `context.stores`, so replies may carry citations |
| `features.memory_review` | True outside `public` mode: proposals at the review gate can be decided (`POST /v1/memory/proposals/{id}`) |
| `limits.max_message_chars` | `gateway.max_message_chars`, the transport cap; input guardrails may block shorter messages |
| `limits.max_steps` | `loop.max_steps` |
| `limits.session_ttl_seconds` | `public.session_ttl_s` in `public` mode, else `null` (P6: the key moved from `gateway.session_ttl_s`) |

## `GET /healthz`

Response `200` when ready, `503` when a required dependency is down:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "spec_version": "0.1.0",
  "mode": "dev",
  "provider": "fake",
  "fixture": "plain_answer",
  "checks": { "trace_store": "ok", "provider": "ok" }
}
```

`fixture` is present only when the provider is `fake`.

## Serving the web app

- When the built web app exists (directory from `HARNESSWALK_WEB_DIST`, default `web/dist`,
  relative paths resolved against the repository root), the API serves it at `/`. Any `GET` path
  that is not `/v1`, `/healthz`, `/docs` or `/openapi.json` or under one of them, not under
  `/assets/`, and not an existing file falls back to `index.html`, so client-side routes survive
  a reload. A missing file under `/assets/` is a JSON `404`, never `index.html`.
- `/v1/*` and `/healthz` always win: an unknown `/v1/...` path is a JSON `404` for every method,
  never the app; a known path with a method it does not take is a JSON `405`.
- Without the directory, `/` is a JSON `404` and the API works as before.

## CORS

In `dev` mode the API sends CORS headers for `http://127.0.0.1:5173` and `http://localhost:5173`
(the Vite dev server), with credentials, for `GET` and `POST` and the `Content-Type`,
`Last-Event-ID` and `X-Harnesswalk-*` request headers. Other modes send none: the app is served
from the same origin.

## Event stream guarantees the UI relies on

Restated from `events.md`, so a UI author finds them here:

- `approval_requested.summary` carries `approval_id`, `tool`, `action_class`, `args_preview` and
  `expires_at`; the decision goes to `POST /v1/approvals/{approval_id}`.
- `node_paused.summary` carries `reason` (`breakpoint` or `approval`, with `approval_id`). No
  resume token is needed: a breakpoint is released with `POST /v1/turns/{turn_id}/resume` and
  `{ "mode": "step" | "continue" }`.
- `message_delta` events, when enabled, arrive inside the `phase: reply` gateway span in
  `summary.index` order.
- `turn_end.summary` carries `reply`, `exit` and `steps`; the turn's total cost is the
  `turn_end.cost` field (rule 13), not a summary key.

## `GET /v1/audit` (P3)

Dev and private modes. Query `?since=<RFC 3339 timestamp>` (records with `ts` at or after it;
default all), `?limit=<n>` (default 500, at most 5000).

Response `200`:

```json
{
  "tenant_id": "dev",
  "verify": { "ok": true, "records": 3 },
  "records": [
    { "record_id": "aud_...", "tenant_id": "dev", "ts": "2026-10-29T14:10:41.475Z",
      "entry": { "action": "policy_decision", "actor": "agent", "tool": "cancel_order",
                 "action_class": "destructive", "decision": "needs_approval",
                 "rule": "dev.destructive.approve", "args_digest": "...",
                 "turn_id": "...", "trace_id": "..." },
      "prev_hash": "000...", "hash": "..." }
  ],
  "next_since": null
}
```

Records come oldest first, in chain order. `verify` walks the whole chain of the tenant, not
only the page. `next_since` is the `ts` to ask for next when the page was full, else `null`.
Errors: `400` (bad `since` or `limit`), `401`, `403 forbidden_in_mode` in public mode.

P3 integration (additive): `?after=<record_id>` starts after that record (exclusive, in chain
order) and the response carries `next_after`, the last `record_id` of a full page, else `null`.
Page with `next_after`: several records can share a millisecond `ts`, so following
`next_since` can return the boundary records again. `since` must carry an offset (`Z` or
`+hh:mm`); a bare local time is `400`. An `after` that names no record of the caller is `400`.

## Per-turn options added in P3

`POST /v1/turns` also takes `output_schema` (a JSON Schema; the answer must be JSON valid
against it or the output guardrails block it) and, in dev mode, `model` (a model alias hint for
the router, honoured only when the matched rule allows it).

## Recovery after a restart (P3)

`harnesswalk serve` recovers at start-up (`approval_queue.md`, P3 additions): turns paused on an
approval are re-attached and wait again, other unfinished turns are closed with `error` code
`restarted`. The P2 limits below about restarts no longer apply.

## Known limits

Accepted for P2 and scheduled, so a UI author does not mistake them for the contract:

- `GET /v1/sessions/{session_id}/turns` reads each listed turn's full event list to build its
  summary, one read per turn (up to 200). Fine at teaching scale; P4 (ops) adds an index or a
  summary table so the list costs one query.
- After a restart cut a turn off (no `turn_end`), that turn never reached the session store, so
  the next turn of the session can reuse its `turn_index`, and two summaries can share one.
  P3 (restart resume) finishes or closes such turns, which keeps `turn_index` unique.

## Not yet

Uploads, sign-in endpoints, WebSocket transport. (Eval, release, artifact, trace and feedback
endpoints arrived in P4, in the section "P4 additions" below; public mode arrived in P6, see
"P6 additions"; private mode stays deferred.)

## Context plane routes (P3)

All four are tenant-scoped like every other route.

- `GET /v1/memory/proposals?status=pending|all&limit=<n>` returns `{"proposals": [...]}`. Each
  item has `proposal_id`, `store`, `content` (redacted), `source`, `evidence`, `gate`,
  `status`, `reason`, `record_id`, `version`, `created_at`, `decided_at`, `decided_by`; with
  `all`, accepted items add `diff: {op, before, after}`.
- `POST /v1/memory/proposals/{id}` with `{"decision": "accept" | "reject", "reason"?}`: `200`
  with the decided record; `400` for another decision value; `403` in public mode (public
  memory is never written); `404` for a proposal of another tenant or none; `409`
  `proposal_not_pending` when it is not waiting at the review gate (decided at most once).
- `GET /v1/knowledge/docs` returns `{"pack", "docs": [{doc_id, title, path, kind, version,
  chunks, scope}]}` (`scope` is `pack` or `tenant`).
- `GET /v1/knowledge/docs/{doc_id}` returns `{doc_id, title, path, kind, version, scope, text,
  chunks: [{position, heading, text, tokens, citation}]}`, or `404` `doc_not_found`. A
  citation chip (`turn_end.summary.citations`) opens here at `chunks[chunk]`.

The store inspectors (`GET /v1/nodes/semantic`, `episodic`, `knowledge`, `memory`) return
`counts`, `last_records` (tenant-scoped; pack seeds and documents marked `scope: pack`) and
`settings` (embedder, vector backend, ranking settings). `GET /v1/nodes/memory_writer` adds
`last_writes` (the last proposals with their outcome and, for writes, a diff), `pending` and
`settings` (including `gate_in_this_mode`).

## P4 additions: LLM Ops (evals, feedback, releases, artifacts, trace, step-over, checkpoints)

Additive. Every existing endpoint keeps its shape; new fields are listed where they appear.
`PUT` joins `POST` under the content-type rule of the Conventions (`415` unless
`application/json`), and the dev-mode CORS allow list gains `PUT`. "Dev and private only" means
`403 forbidden_in_mode` in public mode.

| Method and path | Purpose | Since |
|---|---|---|
| `POST /v1/evals/runs` | Start an eval run | P4 |
| `GET /v1/evals/runs` | List runs | P4 |
| `GET /v1/evals/runs/{run_id}` | Run detail with per-case results and the diff against the previous run | P4 |
| `GET /v1/evals/runs/{run_id}/events` | Run progress stream (SSE) | P4 |
| `GET /v1/evals/runs/{run_id}/cases/{case_id}/events` | The stored event stream of one case's turn (SSE) | P4 |
| `GET /v1/evals/golden` | The golden set of a pack | P4 |
| `POST /v1/turns/{turn_id}/feedback` | Thumbs and an optional note on a turn | P4 |
| `GET /v1/artifacts` | Working set listing | P4 |
| `GET /v1/artifacts/{kind}/{name}` | One artifact with its body and history | P4 |
| `PUT /v1/artifacts/{kind}/{name}` | Edit an artifact (a new working-set version) | P4 |
| `GET /v1/releases` | List releases | P4 |
| `GET /v1/releases/{release_id}` | Release detail with the artifact diff | P4 |
| `POST /v1/releases` | Snapshot the working set as a release | P4 |
| `POST /v1/releases/{release_id}/rollback` | Make a release active again | P4 |
| `GET /v1/trace/turns` | Turn list with receipts, filtered | P4 |
| `GET /v1/trace/turns/{turn_id}/receipt` | Per-turn receipt | P4 |
| `GET /v1/trace/ops-events` | Point events outside turns (feedback, artifact changes, releases) | P4 |
| `GET /v1/events` | Every ops event of the tenant, live (SSE) | P4 integration |

### Evals

`POST /v1/evals/runs` (dev and private). Body:

```json
{ "pack": "_starter", "suite": "golden", "model": null, "cases": ["support_hours"] }
```

| Field | Required | Meaning |
|---|---|---|
| `pack` | yes | The loaded dataset pack; another pack is `422 invalid_request` |
| `suite` | yes | `golden` (every case on the `fake` provider with its fixture; deterministic scorers) or `judge` (judged scorers) |
| `model` | no | `judge` only: a model alias from `config/models.yaml` whose provider is configured, used to answer the cases. Without it the cases answer from their fixtures and only the scoring is judged. On `golden` it must be absent or a `fake` alias |
| `cases` | no | Case ids to run; default every case. An unknown id is `400 invalid_request` |

`202` `{ "run_id": "run_0003", "events_url": "/v1/evals/runs/run_0003/events" }`. A `judge` run
needs a configured non-fake judge model (`evals.judge.model`, router step `judge`), else
`422 judge_unavailable`. A run is refused with `409 eval_running` while another run of the
same tenant is running.

`GET /v1/evals/runs?pack=&limit=` returns `{ "runs": [...] }`, newest first (`limit` default 20,
at most 200). Each run summary:

```json
{
  "run_id": "run_0003", "pack": "_starter", "suite": "golden", "model": null,
  "judge_model": null, "started_at": "2026-10-29T15:00:00.000Z",
  "ended_at": "2026-10-29T15:00:02.410Z", "status": "done",
  "totals": { "cases": 13, "passed": 12, "failed": 1, "skipped": 0 },
  "regressions": 1, "previous_run_id": "run_0002",
  "release_id": "r1", "working_set": true, "artifacts_hash": "9c1f...",
  "cost": { "input_tokens": 0, "output_tokens": 0, "usd": 0.0 }
}
```

`status` is `running`, `done` or `error` (the run could not finish; `error` carries
`{code, message}`). `release_id`, `working_set` and `artifacts_hash` say what was under test.
`cost` is the judge's spend plus the hosted answers' spend (0 on `golden`). `totals.failed`
counts `failed` and `error` cases.

`GET /v1/evals/runs/{run_id}` returns the summary plus:

| Field | Meaning |
|---|---|
| `cases[]` | `case_id`, `title`, `status` (`passed`, `failed`, `skipped`, `error`), `scores` (`{name: {value, pass, detail}}`), `expected`, `actual` (`reply`, `path[]`, `citations[]`, `tool_calls[]`, `exit`), `turn_id`, `events_url`, `previous_status`, `regression` (bool), `error` (when `error`) |
| `regression_cases` | Case ids with `regression: true`, in case order |
| `diff` | Against `previous_run_id`: `{ "fixed": [ids], "regressed": [ids], "new": [ids], "removed": [ids] }` (`new` and `removed` by case id) |

Scorers (names fixed): `path` (expected node path is a subsequence of the actual simple-view
path), `citations` (every expected citation present in `turn_end.summary.citations`, by `doc`
and, when given, `chunk`), `tool_calls` (the tool names the turn called, in order, equal the
expected list), `schema` (the reply validates against the case's `output_schema`), `pii_leak`
(no personal data in the reply other than values the user typed), `answer_match` (substring,
exact or regex, when the case gives one), `faithfulness` and `relevance` (judge, 0 to 1, pass
at `evals.judge.threshold`, default 0.7). A scorer is applicable when the case gives what it
checks (`pii_leak` always applies). `golden` applies `path`, `citations`, `tool_calls`,
`schema`, `pii_leak`, `answer_match`; `judge` applies `schema`, `pii_leak`, `faithfulness`,
`relevance`, so deterministic and judged results stay apart (`evaluator.md`). A case passes
when every applicable scorer passes; a case with no fixture is `skipped` on `golden`.
`regression` is true when the case is `failed` or `error` now and was `passed` in
`previous_run_id` (the last finished run of the same tenant, pack and suite).

Case turns run on the harness like any turn (`turn_start.summary.channel: eval`,
`summary.eval_run_id`), each in its own session, in a scratch tenant per run
(`evalrun-<run_id>`) so a run never reads or writes the caller's memory and two runs see the
same starting state. Their streams are served to the caller at `events_url`
(`/v1/evals/runs/{run_id}/cases/{case_id}/events`, the same SSE format as a turn's stream,
stored events only). Approvals in a case are decided by the runner with the case's `approval`
(default `deny`).

`GET /v1/evals/runs/{run_id}/events`: SSE of the run's ops events, stored first then live,
`eval_run_started`, one `eval_case_finished` per case, `eval_run_finished`; closes after
`eval_run_finished`. `id:` is the event's `seq` in the run stream; `after` and `Last-Event-ID`
work as on turn streams. Each message's `data` is the ops event (`type`, `tenant_id`,
`turn_id: null`, `ts`, `summary`, ...) with `run_id` added at the top level for the reader's
convenience (the stored event keeps the schema's envelope; `run_id` is also in `summary`).
Summaries: `eval_run_started {run_id, pack, suite, model, total}`, `eval_case_finished
{run_id, case_id, status, regression, index, total, scores, turn_id}`, `eval_run_finished
{run_id, status, totals, regressions, regression_cases, previous_run_id}`.

`GET /v1/evals/golden?pack=` returns `{ "pack": "_starter", "path": "datasets/_starter/golden.jsonl", "cases": [...] }`,
read-only, as stored.

### Feedback

`POST /v1/turns/{turn_id}/feedback` body `{ "rating": "good" | "bad", "note"?: string }` (a note
of at most 2000 characters, PII-redacted before it is stored). `200` with the stored record
`{ turn_id, rating, note, at, by }`. Tenant-scoped (another tenant's turn is `404`). A later
rating replaces the current one; earlier ones stay in the history. Emits `feedback_recorded`
(an ops event with the turn's `turn_id`, `session_id` and `trace_id`). Allowed in every mode.

`GET /v1/turns/{turn_id}` (and each item of `GET /v1/sessions/{id}/turns`) gains:

| Field | Meaning |
|---|---|
| `feedback` | `{ rating, note, at }` or `null` |
| `scores` | Online judge scores `{ name: value }` when the turn was sampled, else `null` |
| `checkpoint` | `{ step, at, restored }` from the last loop checkpoint, else `null`; `restored` is true when the turn was re-attached after a restart |
| `release` | `{ release_id, working_set, artifacts_hash }` the turn ran on (`turn_start.summary.release`) |

### Artifacts and releases

Artifacts and their names: `prompt/system_prompt` (the loaded pack's system prompt),
`skill/<name>` (one per skill, for example `skill/order_status`, `skill/returns`),
`policy/policy`, `context/context`, `models/models`, `tools/tools` (the loaded pack's tool
file). Each has `kind, name, version (int from 1), hash
(sha256 of the body), updated_at, updated_by, note, body`. The working set is the latest
version of each; a release is a frozen snapshot of the working set with its own `release_id`,
`created_at`, `note`, `hash` chained to the previous release (`prev_hash`), and
`artifacts[] {kind, name, version, hash}`.

| Method and path | Notes |
|---|---|
| `GET /v1/artifacts?pack=` | `{ pack, artifacts: [...] (no bodies), release: {active_release_id, working_set_dirty, use_working_set} }` |
| `GET /v1/artifacts/{kind}/{name}?version=` | With `body`, `latest_version` and `history[] {version, hash, updated_at, updated_by, note}`; default the latest version. Unknown kind or name `404 artifact_not_found` |
| `PUT /v1/artifacts/{kind}/{name}` | Body `{body, note?}`. Dev and private only. Creates version n+1, `200` with the new artifact (with body), `previous_version` and a unified `diff` against n. A body equal to the latest is `200` with `unchanged: true` and no new version. `409 turn_running` while any turn or eval run is running in the process (artifacts are shared, so this is process-wide). `422 invalid_artifact` when validation fails: YAML parse for `policy`, `context`, `models`, `tools`, the policy rule schema, and a dry run that builds the harness components from the candidate (router rules, tool specs, the procedural token budget). A new `skill` name creates version 1; other kinds only edit names that exist. Emits `artifact_changed` |
| `GET /v1/releases?pack=` | `{ pack, releases: [...] newest first, each with active, active_release_id, working_set_dirty, verify: {ok, releases} }` |
| `GET /v1/releases/{release_id}` | The release plus `diff_from_previous[] {kind, name, from_version, to_version, diff}` (added artifacts have `from_version: null`, removed ones `to_version: null`) |
| `POST /v1/releases` | Body `{note?}`. Dev and private only. Snapshots the working set, makes it active, `201`. Emits `artifact_released` |
| `POST /v1/releases/{release_id}/rollback` | Dev and private only. Makes that release active and resets the working set to its artifact versions by appending new versions (history is append-only); an artifact absent from the release is marked deleted. `200` with the release (as `GET /v1/releases/{id}`) plus `rollback_of` (the release that was active), `reset: [{kind, name, from_version, to_version}]`, `active_release_id`, `working_set_dirty`. `409 turn_running` as for `PUT`. Emits `artifact_released` with `rollback_of` |

Loading rule: the harness serves turns from the active release. With `release.use_working_set`
(default `true` in dev, `false` elsewhere) it serves the working set instead, so an edit
applies to the next turn; `working_set_dirty` is true when the working set differs from the
active release (the status bar shows "working set (unreleased changes)"). A turn in progress
keeps what it started with. The first start seeds the working set from the pack and config
files and creates release `r1` ("initial"; release ids are `r1`, `r2`, ...); a later start that finds a file changed on
disk appends a working-set version (`updated_by: disk`), and in a mode that serves the release
it also creates a release for it.

`GET /v1/capabilities` gains `features.evals` (dev and private), `features.judge` (a judge
model is configured), `features.release` (true), `features.artifact_edit` (dev and private),
`features.feedback` (true), `features.step_over` (true) and `release: {active_release_id,
working_set_dirty, use_working_set}`.

### Trace

`GET /v1/trace/turns?tenant=&session=&node=&status=&kind=&since=&until=&limit=&after=`:
the caller's turns, newest first. `tenant` must be the caller's own (else `404
tenant_not_found`); `node` keeps turns that entered that node (simple or expert); `status` is
`running`, `paused`, `done` or `error` (`turn_end.status`); `kind` is `chat` or `eval` (case
turns); `since` and `until` bound `started_at` (RFC 3339 with an offset); `limit` default 50,
at most 200; `after` is a `turn_id` from the previous page. Response
`{ "turns": [...], "next_after": "<turn_id>" | null }`, each item
`{ turn_id, session_id, trace_id, started_at, ended_at, status, exit, cost: {input_tokens,
output_tokens, usd}, steps, duration_ms, path, kind, channel }` where `path` is the simple-view
path (`steps` and `duration_ms` are `null` while the turn runs). Served from a turn index the
trace store keeps (one query per page).

P4 integration: each item also carries `tenant_id`. Case turns of eval runs live in a scratch
tenant per run, so `kind=eval` lists the case turns of the caller's own runs (their
`tenant_id` is `evalrun-<run_id>`), each with `run_id`, `case_id` and `events_url` (the case's
stored stream, as in the run detail); without `kind` only the caller's own tenant is listed.
The receipt route answers for those case turns too.

`GET /v1/trace/turns/{turn_id}/receipt`: `{ turn_id, total, by_node, by_model, latency_ms,
nodes: {node_id: {ms, count, input_tokens, output_tokens, usd}}, calls: [{seq, step, provider,
model, input_tokens, output_tokens, usd, ms, fallback}], scores: {node_id: {name: value}} |
null, feedback }`. `total` equals `turn_end.cost` (`tracer.md`).

`GET /v1/trace/ops-events?type=&after=&limit=`: the caller's ops events (`events.md` section 10)
from the `ops` stream, oldest first, `{ "events": [...], "next_after": <seq> | null }`.

OpenTelemetry: when `HARNESSWALK_OTLP_ENDPOINT` (or `trace.otel.endpoint`) is set and the
optional extra `otel` is installed, every turn is exported as spans when it ends, and every ops
event as a span of its own, with the attributes of `events.md` section 8. Off by default; a
missing extra logs one warning and exports nothing.

### The tenant's ops events, live (P4 integration)

`GET /v1/events?tenant=&after=&follow=`: SSE of every ops event of the caller's tenant
(`events.md` section 10), from every ops stream at once: the `ops` stream (`feedback_recorded`,
`artifact_changed`, `artifact_released`) and every eval run's stream (`eval_run_started`,
`eval_case_finished`, `eval_run_finished`), oldest first in the order they were stored. This is
how a UI hears about a release made with the CLI, or an eval run another browser started,
without polling.

- `tenant` must be the caller's own (else `404 tenant_not_found`), as on `/v1/trace/turns`.
- `id:` is a cursor over all of the tenant's ops events (an integer, not the event's `seq`,
  which counts per stream); `event:` is the type. Each message's `data` is the stored event
  plus two keys for the reader: `cursor` (the same value as `id:`) and `stream` (`ops` or
  `eval:<run_id>`).
- `after` (or `Last-Event-ID`, the larger wins) starts after that cursor; `after=-1` replays
  everything stored. Without either, the stream starts at the tenant's newest event, so a fresh
  page sees only what happens from then on.
- `follow` (default `true`) keeps the stream open: the server sends new events as they are
  stored, including events another process wrote to the same data directory (it polls the store
  about once a second), and a `: keepalive` comment after 15 seconds of quiet. `follow=false`
  sends what is stored and closes.
- The stream never closes on its own while following. Errors: `400` (a cursor that is not an
  integer), `401`, `404`.

### Step-over and checkpoints

`POST /v1/turns/{turn_id}/resume` takes `mode: "step" | "continue" | "step_over"`. `step_over`
releases the current breakpoint and runs to the next simple-view node, skipping breakpoints on
expert children on the way (from `memory` it pauses next at `loop`, not at `procedural`).

After a restart a turn paused on an approval keeps its `output_schema`, its memory proposals
from before the pause, the knowledge chunks numbered for it (so citations still check) and
whether a BYO key was present (the key itself is never stored; the turn resumes on the
configured provider and `turn_end.summary.byo_key_dropped` is `true`). A turn paused on a
breakpoint is still closed with `restarted` (step mode is interactive and cheap to redo).

`harnesswalk serve` holds `data/owner.lock` (pid and a heartbeat every
`release.lease.heartbeat_s`, default 5 seconds). A second `serve` on the same data directory
refuses to start while the lock is held by a live process with a fresh heartbeat, unless it is
started with `--takeover`, which asks the holder to stop and waits for it before recovering
turns. A lock whose pid is gone or whose heartbeat is older than three periods is stale.

## P5 additions: packs, the release gate, diagnose

Additive. Every existing endpoint keeps its shape; new fields are listed where they appear.

| Method and path | Purpose | Since |
|---|---|---|
| `GET /v1/packs` | The dataset packs this harness can load | P5 |
| `GET /v1/packs/{pack_id}` | One pack's card and file tree | P5 integration |
| `GET /v1/packs/{pack_id}/files/{path}` | One file of a pack, as text | P5 integration |
| `POST /v1/sessions` | Start a session on a pack | P5 |
| `GET /v1/sessions/{session_id}` | One session: its pack and turn count | P5 |
| `POST /v1/diagnose` | Map a failed eval case or a bad turn to the layer at fault | P5 |
| `GET /v1/lessons` | The lesson chapters in order, with their metadata | P5 |
| `GET /v1/lessons/{slug}` | One chapter: metadata, markdown, code links, try | P5 |
| `GET /v1/lessons/{slug}/source?path=` | A file the chapter's code walk links to | P5 |

### Packs

`GET /v1/packs` (every mode, tenant-free like the diagram) returns `{ "default": "_starter",
"packs": [...] }`, one item per pack directory under the configured roots (`packs.roots`,
default `[datasets]`): a directory with a `pack.yaml`, or else one with a `system_prompt.md`.

| Field | Meaning |
|---|---|
| `id` | The directory name (`^[a-z_][a-z0-9_-]{0,63}$`) |
| `title`, `domain`, `feature` | From `pack.yaml` (`title` defaults to the id; the others are `null` without it) |
| `prompts` | Suggested prompts: `pack.yaml` `prompts`, else the list items of `prompts.md`, at most 10 |
| `counts` | `{docs, facts, episodes, skills, tools, golden}` counted from the pack's files |
| `default` | True for the configured `dataset` |
| `loaded` | True when this process has built the pack (a session ran on it) |
| `fixtures` | P5 integration: the fake fixtures the pack ships (`fixtures/fake/`), dev mode only, else `[]`. `capabilities.fixtures` still lists every name a turn may ask for |

`GET /v1/packs/{pack_id}` (every mode, tenant-free; P5 integration): the item above plus
`summary`, `tier`, `demonstrates`, `licence`, `tools` (names), `size_bytes` and `files`, every
file under the pack but hidden ones and caches, `[{path, bytes, role}]` sorted by path, where
`role` is `procedural` (`system_prompt.md`, `skills/`), `knowledge` (`docs/`),
`knowledge_source` (`sources/`, the markdown a PDF was rendered from), `tools`, `guardrails`,
`semantic` (`facts.jsonl`), `episodic` (`episodes.jsonl`), `golden`, `fixture`, `card`
(`pack.yaml`, `provenance.md`, `prompts.md` and other top-level files) or `other`. `404
pack_not_found` for an unknown pack.

`GET /v1/packs/{pack_id}/files/{path}`: `{pack, path, bytes, role, kind, text}` for a path the
file tree lists (anything else, `..` included, is `404 file_not_found`); `kind` is `text`, or
`pdf_text` with the text extracted from a PDF. Files over 512 KiB are `413 file_too_large`, a
file that is not UTF-8 is `415 not_text`.

`GET /v1/memory/proposals` takes `?pack=` (P5 integration): only that pack's proposals, `422
invalid_request` for an unknown pack; the response then echoes `pack`. Each proposal carries
`pack`, the pack whose memory writer made it (a row from before this field is placed by its
session's pack).

`GET /v1/capabilities` gains `repo_url` (P5 integration), `project.repo_url` in
`config/harness.yaml` without a trailing slash: the repository the lessons' code links open.

`POST /v1/sessions` (every mode; the content-type rule applies) body `{ "pack": "ecommerce" }`
(`pack` optional, default the configured `dataset`). `201` `{ session_id, pack, tenant_id,
created_at }` and the session cookie, as `POST /v1/turns` sets it. An unknown pack is `422
invalid_request`. The pack is fixed for the session: a later turn naming another pack in
`options.pack` (or the P1 spelling `options.dataset`) is `422 invalid_request`, as in P1.
`POST /v1/turns` without a session still creates one on the pack its options name, or on the
default.

`GET /v1/sessions/{session_id}`: `{ session_id, pack, tenant_id, created_at, turns }`, `404
session_not_found` for an unknown session or another tenant's.

The session's pack drives the turn: its system prompt and skills, knowledge documents, seed
facts and episodes, tools, guardrail patterns and golden set. Tenant data is kept per tenant
and pack: a fact remembered in a session on one pack is not retrieved by a session of the same
tenant on another pack (conformance case `pack_switch`). A memory proposal is decided against
the pack of the session that proposed it.

Turn summaries (`GET /v1/turns/{id}`, the session turn list) gain `pack`
(`turn_start.summary.pack`). `GET /v1/capabilities` gains `features.packs` (true),
`features.diagnose` (dev and private) and `packs: { default, ids }`.

`?pack=` selects the pack on `GET /v1/artifacts`, `GET` and `PUT /v1/artifacts/{kind}/{name}`,
`GET /v1/releases`, `GET /v1/evals/golden`, `GET /v1/knowledge/docs` and
`GET /v1/knowledge/docs/{doc_id}`, and `GET /v1/nodes/{node_id}` (the inspectors show that
pack's prompt, skills, tools and stores). Default: the configured `dataset`. A pack the harness
cannot find is `422 invalid_request`. `POST /v1/evals/runs` takes any pack of `GET /v1/packs`.
A release id is unique across packs, so `GET /v1/releases/{id}` and its rollback need no pack.

### Releases and the eval gate

`POST /v1/releases` body `{ note?, pack?, force? }` (`release.md`, P5 additions). The gate runs
first; the request returns when it is done.

- `201`: the release as before plus `gate: { status, reason, suite, min_pass_rate, pass_rate,
  run_ids, failed_cases, forced }` (`status` `passed` or `skipped`). Every release in `GET
  /v1/releases` and `GET /v1/releases/{id}` carries its `gate` (`null` before P5).
- `409 gate_failed`: no release was made. The body is the error plus the gate result and the
  failing cases, so a UI can show them next to a "release anyway" control:

```json
{ "error": { "code": "gate_failed", "message": "The golden gate failed: 1 of 13 cases failed." },
  "gate": { "status": "failed", "pass_rate": 0.9231, "min_pass_rate": 1.0, "run_ids": ["run_0004"],
            "failed_cases": ["returns_window"], "suite": "golden", "forced": false, "reason": "below_min_pass_rate" },
  "cases": [ { "case_id": "returns_window", "title": "...", "status": "failed", "run_id": "run_0004",
               "events_url": "...", "scores": { "answer_match": { "pass": false, "detail": "..." } },
               "diagnosis": { "layer": "prompt_or_skill", "suggestion": "..." } } ] }
```

- `force: true` (dev only, else `403 forbidden_in_mode`) skips the gate and is audited.
- `409 turn_running` as before when the pack does not serve the working set and a turn runs.

### Diagnose

`POST /v1/diagnose` (dev and private; `403 forbidden_in_mode` in public) body `{ "run_id"?,
"case_id" }` or `{ "turn_id" }`. Without `run_id`, `case_id` names the case in the caller's
newest run where it failed. `200` with the `Diagnosis` (`evaluator.md`, P5 additions). Errors:
`400 invalid_request` (neither or both targets), `404 run_not_found`, `case_not_found`,
`turn_not_found`, `422 nothing_to_diagnose` (the case passed, or the turn answered with no bad
feedback). Emits `diagnose_finished`.

`GET /v1/evals/runs/{id}` cases gain `diagnosis` (the latest for that case, or `null`), the run
summary gains `diagnoses: {case_id: layer}`, `purpose` (`release_gate` or `null`) and
`case_tenant`. `GET /v1/turns/{id}` gains `diagnosis`. The `diagnose` node's inspector lists
the caller's last diagnoses (`last_diagnoses`).

### Resume mode in the stream

`node_resumed.summary.mode` (`events.md` section 11) says which control released a pause:
`step`, `continue`, `step_over` or `approval`.

### Lessons

The chapters in `lessons/` (`lessons/README.md` has the format), served by
`harnesswalk/control/http_lessons.py`. Every mode serves them and no identity is needed: the
lessons, like the code, are the same for every tenant.

- `GET /v1/lessons`: `{ lessons: [{ slug, n, title, hook, node_id, covers, group, words, url }],
  walk_order }`, in chapter order. `slug` is the file stem (`02-input_guardrails`), `node_id` the
  node the chapter is about (the parent for an expert group chapter, `group: true`), `covers` the
  nodes it teaches, `words` the length without link targets.
- `GET /v1/lessons/{slug}`: the list item plus `markdown` (front matter removed), `try`
  (`{ pack, fixture?, prompt, disable? }` or `null`; `pack` is `_starter` when the chapter does
  not name one, P5 integration), `also` (P5 integration: the same demonstration on a domain
  pack, `[{ pack, fixture?, prompt, disable?, note }]`, possibly empty), `code_links: [{ path, symbol, line, node_id }]`
  (`line` resolved from the file's syntax tree, `null` when the symbol is gone; `node_id` is the
  node whose code peek is that file, else `null`), `prev` and `next`. Unknown slug:
  `404 lesson_not_found`.
- `GET /v1/lessons/{slug}/source?path=harnesswalk/...`: the code peek's shape (`path`, `language`,
  `line_count`, `source`, `truncated`, `symbols`) for a file the chapter links to, under the same
  containment rule as `GET /v1/nodes/{id}/source`. Any other path: `404 source_not_found`.
- Each node of `spec/diagram/diagram.json` names its chapter (`lesson_url`, a repo path with a
  `#section` anchor for an expert node) and its one-line hook (`lesson_summary`).

## P6 additions: public mode (harnesswalk.vynci.dev)

Public mode only; private mode (sign-in, uploads, persistence, export, delete) is deferred and
its endpoints keep refusing in public mode as before. Configuration is the `public:` block of
`config/harness.yaml`; the environment adds `HARNESSWALK_MODE=public`, `HARNESSWALK_SECRET`
(required: the server refuses to start in public mode without it), `HARNESSWALK_PUBLIC_BUDGET_USD`
(overrides `public.hosted_budget_usd_per_day`), `HARNESSWALK_TRUST_PROXY=1`
(`gateway.trust_proxy`), `HARNESSWALK_LOG_FORMAT=json` (`logging.format`) and the site's own
model key in the provider's key variable (`ANTHROPIC_API_KEY`), read from the environment,
never logged.

```yaml
public:
  models: [hosted-haiku, hosted-sonnet, fake-small]   # the only aliases a public turn may use
  default_model: hosted-haiku     # what a public turn uses without a hint
  hosted_budget_usd_per_day: 15   # shared by every priced alias, per UTC day
  session_ttl_s: 7200             # anonymous tenant lifetime (was gateway.session_ttl_s)
  sweep_interval_s: 300           # TTL sweeper cadence; 0 disables it
  rate_limit_max_keys: 10000      # bound on the in-memory rate-limit table
  cookie_secure: auto             # auto (Secure when the request came over HTTPS), always, never
  fake_fixture: public_demo       # fake-small's fixture unless one is given (config/fixtures)
```

`public.models` implies the providers: a public server builds exactly the providers behind
those aliases (a hosted one whatever its `enabled` flag says) and no other. Whether a hosted
alias can answer is decided per turn: it needs the site's key or the turn's BYO key, a healthy
provider, and, when the alias has a price, room in the shared daily budget (a BYO key bypasses
the budget). A hosted provider built without the site's key serves BYO-key turns only and is
not probed. `fake-small` runs the `public_demo` fixture, which never runs out (`cycle: true`,
model_provider.md P6 additions).

### Identity: the anonymous tenant cookie

`harnesswalk_tenant` is `anon-<16 hex>.<expires, Unix seconds>.<HMAC-SHA256 with
HARNESSWALK_SECRET, 32 hex>`, `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` the time left,
and `Secure` when the request came over HTTPS (directly, or `X-Forwarded-Proto: https` from a
trusted proxy; `public.cookie_secure` can force it either way). Any route that resolves the
identity mints a tenant when the cookie is missing, tampered, foreign or expired, and that
response sets the cookie; a bad cookie is never an error. `GET /v1/capabilities` resolves it,
so the session clock starts when the page loads. A turn registers the tenant with its expiry
for the sweeper. A fresh tenant ignores the old `harnesswalk_session` cookie (it named the old
tenant's session); a body or header `session_id` that belongs to another tenant is still a 404.

### `GET /v1/capabilities`: `public` (every mode)

```json
{
  "public": {
    "session_expires_at": "2026-11-01T20:38:46Z",
    "ttl_s": 7200,
    "limits": { "per_ip_per_hour": 30, "remaining_this_hour": 27, "max_steps": 8,
                "budget_tokens": 16000, "max_message_chars": 8000 },
    "models": [
      { "alias": "hosted-haiku", "label": "Claude Haiku 5.5", "provider": "anthropic",
        "default": true, "available": true, "reason": null },
      { "alias": "hosted-sonnet", "label": "Claude Sonnet 5.5", "provider": "anthropic",
        "default": false, "available": true, "reason": null },
      { "alias": "fake-small", "label": "Fake model (scripted)", "provider": "fake",
        "default": false, "available": true, "reason": null }
    ],
    "budget": { "status": "ok", "resets_at": "2026-11-02T00:00:00Z", "spent_fraction": 0.42 },
    "byo_key_bypasses_budget": true,
    "fixtures": [
      { "name": "plain_answer", "description": null, "packs": ["_starter"] },
      { "name": "public_demo", "description": "The scripted model a public server offers as fake-small (public.fake_fixture).", "packs": [] }
    ],
    "default_fixture": "public_demo"
  }
}
```

| Field | Meaning |
|---|---|
| `session_expires_at` | When the caller's anonymous tenant and every row of it are deleted (RFC 3339, seconds, `Z`). `null` outside public mode |
| `ttl_s` | `public.session_ttl_s` |
| `limits.per_ip_per_hour` | `gateway.rate_limit.per_ip_per_hour` |
| `limits.remaining_this_hour` | Turns the caller's IP may still start this hour; outside public mode (no IP limit there) the full `per_ip_per_hour` |
| `limits.max_steps`, `limits.budget_tokens` | The loop's caps (`loop:`); never overridable per turn in public mode |
| `limits.max_message_chars` | The smaller of `gateway.max_message_chars` and the input guardrails' size limit: longer messages are refused (400) or blocked |
| `models` | Public: `public.models` in its order. Elsewhere: every non-embedding alias. `label` is `models.<alias>.label` (else the alias) |
| `models[].default` | The alias a turn without a `model` would run on now: `public.default_model` when it can answer, else the first alias of `public.models` (default first) that can. Elsewhere the default route's model |
| `models[].available`, `reason` | Whether a site-paid turn can use the alias now. `reason`: `budget_exhausted`, `provider_unconfigured` (provider not built, or hosted with no site key and no BYO key), `provider_unhealthy` (P6 addition to the contract's list: the health probe or a failure marked the provider down), or `null` |
| `budget` | Public: `status` (`ok` or `exhausted`), `resets_at` (the next 00:00 UTC) and `spent_fraction` (0 to 1). No dollar amount leaves the server. Elsewhere `{status: "ok", resets_at: null, spent_fraction: 0}` |
| `byo_key_bypasses_budget` | Always `true`: a BYO key makes the hosted aliases available whatever the budget |
| `fixtures` | The fake model's fixtures a turn may name (P6 picker fixes), every mode: `{name, description, packs}` sorted by name, the same names as the dev-mode top-level `fixtures` (every pack's own and the shared ones; a turn may use its session pack's own and the shared ones). `description` is the first sentence of the fixture file's `description` on one line (at most 140 characters, a leading phase mark such as `P5.` dropped), `null` for a file without one (every JSONL fixture). `packs`: the packs whose own `fixtures/fake` ships the name, `[]` for a shared one (the picker lists the session pack's own, then the shared ones; `GET /v1/packs` `fixtures` stays dev only). No path leaves the server. The top-level `fixtures` stays empty outside dev mode |
| `default_fixture` | The fixture a fake-model turn without `fixture` plays: `models.providers.fake.fixture` (`--fixture`, else `public.fake_fixture` in public mode), else `plain_answer` |

The top-level `providers[].configured` in public mode is true exactly for the providers the
server built from `public.models`.

### `POST /v1/turns` in public mode

- `model` is accepted when it is one of `public.models`; any other value is
  `400 invalid_request "model not available in public mode"`.
- A priced alias while the shared budget is spent and the turn has no BYO key:
  `409 {"error": {"code": "budget_exhausted", "message", "resets_at"}}`.
- A hosted alias with no site key and no BYO key: `422 provider_not_configured`.
- Without `model` the router's `public` rule picks (default model first); when the budget
  moves a call off a priced alias the turn still runs, on the next alias, with a notice.
- BYO key: `X-Harnesswalk-Provider-Key: <key>` (the P3 header), or `X-Harnesswalk-Api-Key`
  (the P6 contract's spelling); the first present wins. It reaches only the routed model's
  provider for that request, is never persisted, never in events, traces, audit rows, logs or
  error messages, and BYO-paid calls never charge the shared ledger. The waiver is per call
  (P6 review): a call is BYO-paid only when it is sent with the key (the turn's internal calls,
  compaction and the injection classifier, use it too); any other priced call is gated by the
  budget, so a header that is not a usable key waives nothing.
- Running site-paid turns on a priced alias (P6 review): at most
  `public.max_running_hosted_turns` (default 10) at once on the server and
  `public.max_running_hosted_turns_per_ip` (default 2) per client; the next one is
  `429 rate_limited` with `retry_after: 10`, `limit: "running_turns"` and `Retry-After`. BYO-key
  turns and turns on an unpriced alias are not counted.
- Reservations (P6 review): before a priced site-paid call the model layer reserves its worst
  case (the input estimate plus `max_output_tokens` at the alias price) on the ledger and settles
  the real amount after; a reservation is refused once today's spend plus the open reservations
  reach the limit, and the call falls through like a spent budget (same notice). The day
  overshoots by at most one call's worst case however many turns run at once.
- A public turn is cancelled (exit `cancelled`) once it has existed for `public.max_turn_wall_s`
  (default 600) seconds of wall-clock time, paused time included (P6 review), so a step-mode
  turn cannot hold its session and stream forever.
- `fixture` (P6 picker fixes): accepted when the turn runs on the scripted model, an unpriced
  alias of the `fake` provider (`fake-small`): named by `model`, or picked by the router without
  a `model` (the alias a turn without a hint would run on now, `models[].default`, for example
  while the shared budget is spent or no site key is set). The name must be one of the fixtures
  of the session's pack and the shared ones, else `400 invalid_request "no fixture named
  '<name>'"` (no path in the message). Any other case, a `fixture` on a hosted or priced alias
  (named, or picked by the router while the budget lasts) and a `provider` in any case, keeps the
  dev-only refusal `400 invalid_request "provider and fixture can only be chosen in dev mode"`.
  The turn's fake model counts turns per session on that fixture (as in dev mode) and the
  turn's `turn_start.summary.fixture` names it.
- `max_steps`, `budget_tokens`, `budget_usd`, `timeout_s` and `stuck_repeats`, at the top level
  or in `options`, are `400 invalid_request "<key> cannot be changed in public mode"`.
- Rate limits (per IP from `gateway.rate_limit.per_ip_per_hour`, per tenant from
  `per_tenant_per_hour`): `429 {"error": {"code": "rate_limited", "message", "retry_after"}}`
  with `Retry-After` in seconds (until the oldest request of the window leaves it). The IP is
  the peer address, or with `gateway.trust_proxy` `X-Harnesswalk-Viewer-Ip` (the CloudFront
  viewer-ip function overwrites it), else the first `X-Forwarded-For` hop (Caddy writes a single
  one); never `CloudFront-Viewer-Address`, which a viewer can send through the distribution
  (P6 review). IPv6 addresses count by their /64. The table
  holds at most `public.rate_limit_max_keys` keys; the least recently used goes first.
- New tenants and sessions (P6 review): every anonymous tenant minted (any route, a request
  without a valid cookie) and every `POST /v1/sessions` counts in the client's hourly creation
  bucket, `public.creations_per_ip_per_hour` (default 60); past it the request is
  `429 rate_limited` with `Retry-After`.
- Mid-turn exhaustion: when the ledger crosses the line between two calls of a turn, the next
  call falls through to `fake-small` (the next alias of the rule), its `model_layer` exit
  summary carries `notice: budget_exhausted` and `downgraded_from: <alias>`, the `loop` exit
  carries `notices: ["budget_exhausted"]`, and `turn_end.summary.notices` is
  `[{reason: "budget_exhausted", message}]` with the text of `loop.notices.budget_exhausted`
  (events.md, P6 additions).

### Event streams are bounded (every mode)

At most `gateway.max_open_streams` (default 50) SSE streams are open at once across
`GET /v1/turns/{id}/events`, `GET /v1/evals/runs/{id}/events` and `GET /v1/events`; the next
one is `429 rate_limited` with `retry_after: 5` and `limit: "streams"`. A slot comes back when
its stream ends. In public mode (P6 review) a slot also counts against the client
(`public.max_streams_per_ip`, default 4, IPv6 by /64) and the tenant
(`public.max_streams_per_tenant`, default 4), both checked before the server-wide cap;
`GET /v1/events` draws on its own pool of `public.max_ops_streams` (default 20) so it never
starves the turn streams; and a stream ends after `public.max_stream_s` (default 600) seconds
(the browser's EventSource reconnects from its cursor).

### `GET /readyz`

Unauthenticated, cheap: `200 {"status": "ready", "checks": {...}, "default_model"}` when every
store file answers (`store.trace`, `store.session`, `store.audit`, `store.approvals`,
`store.budgets`, `store.memory`, `store.evals`), the default model can answer
(`default_model`: in public mode the alias a turn without a hint would use) and, in public
mode, `secret` is present; else `503` with `status: not_ready` and the failing check
`unavailable` or `missing`. `GET /healthz` stays the process-up probe. The web app's fallback
never answers `/readyz`.

### `GET /v1/ops/budget` (dev mode only)

Today's row of the shared hosted ledger for the owner: `{date, spent_usd, calls, updated_at,
limit_usd, exhausted, resets_at}`. `403 forbidden_in_mode` in other modes: public mode never
exposes a dollar amount.

### The TTL sweeper

Every `public.sweep_interval_s` in public mode, and once with `harnesswalk sweep [--now
<RFC 3339>] [--json]`, every expired anonymous tenant (its registered expiry passed, or an
`anon-` tenant whose first session is older than the TTL) without a running turn is hard
deleted: every row with its `tenant_id` (or `scope`) in every store file, its eval runs and
their scratch tenants, its vectors, its whole audit chain (audit.md, P6 additions) and its
sandbox work directories. Other tenants' audit chains still verify.

### Structured logs

With `logging.format: json` every request is one line on stdout:
`{ts, level, logger: "harnesswalk.http", msg: "request", request_id, tenant, method, route,
status, duration_ms}`. `tenant` is the first 12 hex of the tenant id's SHA-256, `route` the
route template (never the path's ids or the query), `request_id` the caller's `X-Request-Id`
when it is a plain token, else a new one, and echoed in the `X-Request-Id` response header (every
mode). No line ever holds a message, a header or a key. uvicorn's access log is off then.
