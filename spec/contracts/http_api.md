# Contract: HTTP API (gateway, P1)

The gateway's HTTP surface. The web UI, the CLI runner and the conformance runner all use it.
JSON in and out, UTF-8, except the event stream, which is Server-Sent Events.

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

| Status | When |
|---|---|
| `400` | `invalid_request` (body or parameters) |
| `401` | `unauthenticated` |
| `404` | Unknown id, or another tenant's |
| `409` | Conflict: turn already running in the session, approval already decided, turn not paused |
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
  "options": { "step_mode": false, "dataset": "ecommerce", "disabled_nodes": [] }
}
```

| Field | Required | Meaning |
|---|---|---|
| `message` | yes | User text, at most `gateway.max_message_chars` |
| `session_id` | no | Continue a session; omitted creates one |
| `options.step_mode` | no | Breakpoint on every node (section 8 of `harness.md`) |
| `options.dataset` | no | Dataset pack for this session; fixed after the first turn |
| `options.disabled_nodes` | no | Node ids with `can_disable`; `422` in public mode |

Response `202 Accepted`:

```json
{
  "turn_id": "turn_approval_pause",
  "trace_id": "5b8aa5a2d2c872e8321cf37308d69df2",
  "session_id": "sess_demo",
  "events_url": "/v1/turns/turn_approval_pause/events"
}
```

Errors: `400`, `401`, `409 turn_in_progress`, `422`, `429`.

## `GET /v1/turns/{turn_id}/events`

The turn's event stream as Server-Sent Events (`Content-Type: text/event-stream`).

- Each event is one SSE message: `id:` is `seq`, `event:` is the event `type`, `data:` is the
  event JSON on a single line.
- Resumption: the standard `Last-Event-ID` header, or the query `?after_seq=N`, returns events
  with `seq > N`. Without either, the stream starts at `seq` 0.
- For a running turn the server sends stored events, then live events as they happen, and
  closes the stream after `turn_end`.
- For a finished turn it sends all stored events and closes. This is also how replay works in
  the UI.
- While a turn is paused (approval or breakpoint) the server sends an SSE comment
  (`: keepalive`) every 15 seconds.

```
id: 37
event: approval_requested
data: {"schema_version":"0.1.0","event_id":"evt_approval_pause_037","seq":37,...}

```

Errors (before the stream starts, as JSON): `401`, `404`.

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

Errors: `400`, `401`, `404`, `409 approval_already_decided`.

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
resumed here).

## `GET /v1/diagram`

The diagram, generated from the node registry merged with `spec/diagram/diagram.json`
(`harness.md` section 6).

Response `200`: the `diagram.json` document with two additions per node:

| Field | Meaning |
|---|---|
| `implemented` | `true` when a component is registered under this id |
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

## Not in P1

Uploads, sign-in endpoints, eval and release endpoints, feedback (`POST /v1/turns/{id}/feedback`
arrives with online eval in P4), WebSocket transport.
