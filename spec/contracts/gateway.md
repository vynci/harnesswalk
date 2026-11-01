# Contract: Gateway

Node: `gateway` (control plane, kind `code`). HTTP surface: `http_api.md`.

## Purpose

The front door and the exit. The gateway turns a channel request into a turn: it resolves
identity, tenant and session, applies rate limits, normalizes the message, starts the event
stream, runs the turn, and renders the reply back to the channel. The response box of the
original design is merged here.

## Methods

```
interface Gateway {
  start_turn(request: ChannelRequest) -> async TurnHandle
  events(turn_id: string, after_seq?: int, viewer: Identity) -> async stream<Event>
  render_reply(ctx: TurnContext, answer: FinalAnswer) -> ChannelReply
  cancel(turn_id: string, viewer: Identity) -> void
}

interface ChannelAdapter {                     // one per channel; P1 ships "web" (HTTP + SSE)
  channel: string
  parse(raw_request) -> ChannelRequest
  render(reply: ChannelReply) -> raw_response
}

record ChannelRequest {
  channel: string                              // "web", "cli", later "slack"
  credentials: Credentials | null              // cookie, bearer token, BYO key header (never stored)
  session_id?: string                          // continue a session, else a new one is created
  message: string
  attachments?: [MediaRef]
  options?: { step_mode?: bool, dataset?: string, disabled_nodes?: [string],
              provider?: string, fixture?: string }   // provider and fixture: dev only
}

record Identity   { tenant_id: string, user_id: string | null, roles: [string], anonymous: bool }
record TurnHandle { turn_id: string, trace_id: string, session_id: string, tenant_id: string }
record FinalAnswer { text: string, citations: [Citation], exit_reason: string }
record ChannelReply { text: string, citations: [Citation], turn_id: string }
```

## Inputs and outputs

- In: `ChannelRequest` from a channel adapter.
- Out: `Message` (role `user`) to `input_guardrails`; `Identity` to `policy`; `ChannelReply`
  to the caller; the turn's event stream to subscribers.

## Invariants

- MUST resolve `Identity` (tenant, user) before emitting `turn_start`. Unresolvable identity
  fails with `unauthenticated` and no turn is created.
- MUST allocate `turn_id` and a fresh W3C `trace_id` per turn; MUST create or validate
  `session_id` and check that the session belongs to the tenant.
- MUST emit `turn_start` (`seq` 0) first and `turn_end` last, and close all open spans before
  `turn_end`.
- MUST apply rate limits before any cost is incurred; over-limit requests fail with
  `rate_limited` before `turn_start`.
- Enters its span twice per chat turn: `phase: receive` at the start, `phase: reply` at the end.
- `events()` MUST filter by tenant: a viewer of another tenant gets `404`, not an empty stream.
- `events()` MUST support resumption from `after_seq` and MUST replay a finished turn in full.
- A BYO key in `credentials` MUST be passed to the model layer for this turn only and MUST NOT be
  logged, stored, or placed in any event.
- One turn per session at a time. A second `start_turn` on a session with an open turn MUST fail
  with `409 turn_in_progress` (HTTP) rather than interleave.
- In-process turn state is bounded. A finished turn's state MAY be evicted after
  `gateway.turn_state_ttl_seconds`, or once more than `gateway.turn_state_max` finished turns are
  kept (oldest finish first); a running or paused turn MUST NOT be evicted. Eviction MUST NOT
  change what a client sees: summaries, the session turn list and the event stream read the trace
  store, and `cancel` or `resume` on an evicted turn answer `409` (`turn_finished`,
  `turn_not_paused`) as for any finished turn, never `404`.

## Failure behaviour

| Condition | Result |
|---|---|
| Malformed request | `invalid_request`, no turn |
| No identity or wrong tenant for the session | `unauthenticated`, no turn |
| Over rate limit | `rate_limited`, no turn |
| A downstream node fails | The turn continues to the reply with an error notice; `turn_end.status` is `error` only when no reply could be produced |
| Client disconnects from the stream | The turn continues; events stay available for resumption |
| `cancel` while paused, or at a later breakpoint, before the `phase: reply` span closes | Open spans close with `error` code `cancelled`, then `turn_end` with status `error` and `summary.exit: cancelled` |
| `cancel` while running | The loop stops at its next step boundary with `exit_reason: interrupted` and its notice; `turn_end.status` `done`, `summary.exit: cancelled` |
| `cancel` after the `phase: reply` span closed | Not honoured: the reply is recorded, any breakpoint is released, and the turn ends as it would have |
| The trace store cannot give the receipt while a failed turn closes | `turn_end` is still emitted (status `error`), with a zero `cost`, so every stream closes |
| A failure after the reply was recorded | `turn_end.status` `error`, but the session keeps the recorded turn; it is never rewritten |

## Events

`turn_start`, `turn_end`, `node_enter` / `node_exit` for `gateway` twice per chat turn, and
`edge` `gateway_to_input_guardrails`, `output_guardrails_to_gateway`.

## Configuration keys (`config/harness.yaml`)

| Key | Default | Meaning |
|---|---|---|
| `mode` | `dev` | `dev`, `public`, `private` (also env `HARNESSWALK_MODE`) |
| `gateway.channels` | `[web]` | Enabled channel adapters |
| `gateway.max_message_chars` | `8000` | Hard cap before guardrails |
| `gateway.rate_limit.per_ip_per_hour` | `30` | Public mode |
| `gateway.rate_limit.per_tenant_per_hour` | `120` | |
| `public.session_ttl_s` | `7200` | Public mode TTL of an anonymous tenant (P6: moved from `gateway.session_ttl_s`) |
| `gateway.max_open_streams` | `50` | SSE streams open at once (every mode); the next is a `429` (P6) |
| `gateway.trust_proxy` | `false` | Read the client IP and scheme from proxy headers (env `HARNESSWALK_TRUST_PROXY=1`, P6) |
| `gateway.dev_tenant` | `dev` | Tenant used in dev mode when none is given |
| `gateway.allow_tenant_header` | `true` in dev | Accept `X-Harnesswalk-Tenant` (dev only, ignored elsewhere) |
| `gateway.stream_reply_chars` | `24` | Above 0, the reply is also sent as `message_delta` events of this many characters inside the `phase: reply` span; `0` turns streaming off |
| `gateway.turn_state_max` | `500` | Finished turns whose in-process state is kept; the oldest beyond this are evicted |
| `gateway.turn_state_ttl_seconds` | `3600` | A finished turn's in-process state is evicted this long after it ended |

## P6 additions: public mode

- **Identity.** An anonymous tenant per browser, in a cookie that carries its expiry and is
  signed with `HARNESSWALK_SECRET` (http_api.md, P6 additions). The HTTP surface refuses to
  start in public mode without the secret.
- **Model.** A turn's `model` is validated against `public.models` (400), a spent budget (409
  `budget_exhausted` with `resets_at`) and a missing key (422 `provider_not_configured`)
  before the rate limits count it.
- **Rate limits.** Per IP and per tenant, hourly sliding windows in a table bounded by
  `public.rate_limit_max_keys`; a refusal is a 429 with `Retry-After`.
- **Expiry.** `start_turn` registers the tenant with its expiry; the TTL sweeper
  (`ops/sweep.py`, `harnesswalk sweep`) deletes every row of an expired tenant without a
  running turn, and the gateway forgets its finished turns and rate windows.
- **Notices.** A turn's notices (`TurnContext.notices`, `budget_exhausted` when the budget moved
  a call to the fake model) are listed on `turn_end.summary.notices`.
