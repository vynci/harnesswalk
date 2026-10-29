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
| `GET /healthz` | Readiness | P1 |

## Conventions

- Base path `/v1`. `GET /healthz` sits outside it.
- Identity: in `dev` mode every request is the `dev` tenant unless the header
  `X-Harnesswalk-Tenant` names another (dev only; ignored in other modes). In `public` mode a
  signed, HTTP-only session cookie carries the anonymous tenant. In `private` mode a session
  cookie or bearer token from sign-in.
- BYO key (public and private): header `X-Harnesswalk-Provider-Key` on `POST /v1/turns` only.
  It is used for that turn and dropped. It is never echoed, stored or logged.
- Errors use one shape, with HTTP status codes below:

```json
{ "error": { "code": "turn_not_found", "message": "No turn turn_123 for this tenant." } }
```

- A resource of another tenant is `404`, never `403`, so ids cannot be probed.
- A `POST` that takes a body (`/v1/turns`, `resume`, `approvals`) MUST send
  `Content-Type: application/json` (parameters such as `charset` are allowed); anything else,
  or none, is `415 unsupported_media_type` before the body is read. A `text/plain` or form body
  is a request a browser sends cross-site without a CORS preflight, so this keeps a foreign page
  from starting turns. `cancel` takes no body and needs no content type.

| Status | When |
|---|---|
| `400` | `invalid_request` (body or parameters) |
| `401` | `unauthenticated` |
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

Errors (before the stream starts, as JSON): `400` (a cursor, query or `Last-Event-ID`, that is
not an integer; an empty `Last-Event-ID` counts as absent), `401`, `404`.

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

Stop a running or paused turn. No body.

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

Errors: `401`, `404`, `409 turn_finished` when the turn already ended. The `409` body carries the
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
  are served, after resolving links and `..`; anything else, and a node whose file does not exist
  yet, is `404 source_not_found`.
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
    "step_mode": true, "approvals": true, "message_delta": false, "cancel": true,
    "byo_key": false, "disable_nodes": true
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
| `limits.max_message_chars` | `gateway.max_message_chars`, the transport cap; input guardrails may block shorter messages |
| `limits.max_steps` | `loop.max_steps` |
| `limits.session_ttl_seconds` | `gateway.session_ttl_s` in `public` mode, else `null` |

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

## Known limits

Accepted for P2 and scheduled, so a UI author does not mistake them for the contract:

- `GET /v1/sessions/{session_id}/turns` reads each listed turn's full event list to build its
  summary, one read per turn (up to 200). Fine at teaching scale; P4 (ops) adds an index or a
  summary table so the list costs one query.
- After a restart cut a turn off (no `turn_end`), that turn never reached the session store, so
  the next turn of the session can reuse its `turn_index`, and two summaries can share one.
  P3 (restart resume) finishes or closes such turns, which keeps `turn_index` unique.

## Not yet

Uploads, sign-in endpoints, eval and release endpoints, feedback (`POST /v1/turns/{id}/feedback`
arrives with online eval in P4), WebSocket transport.
