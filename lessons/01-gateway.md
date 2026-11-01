---
node: gateway
try:
  fixture: tool_call
  prompt: "Where is order A-1001?"
---

# Gateway

> Who is asking, for which tenant, and how a message becomes a turn.

## What it does

The gateway is the front door and the exit. A request arrives over HTTP (or from the CLI),
and the gateway decides who is asking, which tenant they belong to, and whether they may start
a turn right now. It checks the body, the size of the message and the per-turn options, finds
or creates the session, and refuses a second turn in a session that already has one running.
Then it gives the turn its ids (`turn_id`, `trace_id`), emits `turn_start`, and walks the
request through the harness: input guardrails, context builder, loop, output guardrails, and
back to itself to send the reply.

You will see the gateway twice on the diagram for every turn: once with `phase=receive` at the
start and once with `phase=reply` at the end. That second visit is where the reply is streamed
as `message_delta` events, the turn is written to the session store, the memory writer runs
and the trace is closed. Every other node can assume one internal shape (a `TurnContext` with a
tenant, a session and a mode) and never needs to know about HTTP, cookies or headers.

## What breaks without it

The gateway cannot be switched off, because nothing could run without it. What you can do is
watch the rules it enforces, and imagine each node having to enforce them on its own.

Pack `_starter`, fixture `tool_call`, prompt `Where is order A-1001?`. Start a server with
`make run PROVIDER=fake`, send the prompt from the web app, and copy the session id from the
header menu (Copy session id). Now pretend to be another tenant. In dev mode the
`X-Harnesswalk-Tenant` header names the tenant:

```sh
curl -s http://127.0.0.1:8000/v1/turns/<turn_id> -H 'X-Harnesswalk-Tenant: mallory'
curl -s http://127.0.0.1:8000/v1/turns -H 'X-Harnesswalk-Tenant: mallory' \
  -H 'Content-Type: application/json' \
  -d '{"message": "What did I ask before?", "session_id": "<session_id>"}'
```

Both answer `404`: `turn_not_found` and `session_not_found`. Your own requests to the same
turn answer `200`. Notice it is `404`, not `403`: a stranger cannot even learn that the id
exists. Without one place that maps a request to a tenant and checks every session and turn
against it, each store would have to repeat the check, and the first one that forgot would
leak one tenant's conversation into another's.

Two more refusals are worth seeing. Send the same POST with `Content-Type: text/plain` and you
get `415 unsupported_media_type` before the body is read, which stops a foreign web page from
starting turns in your browser. Send two turns into one session at once and the second gets
`409 turn_in_progress`.

## The code walk

[`Identities.resolve`](../harnesswalk/control/http_api.py#Identities.resolve) is the only
place that knows about cookies and headers. In dev mode the tenant is `dev` unless the header
names another; ids that start with `evalrun-` are refused because eval runs use them as
scratch tenants. Public mode mints an anonymous tenant and signs it into an HTTP-only cookie
with an HMAC, so a visitor cannot pick someone else's tenant by editing the cookie. Private
mode answers `401` until sign-in arrives in a later phase.

[`Gateway._validate`](../harnesswalk/control/gateway.py#Gateway._validate) checks the request
itself: a non-empty message under the hard cap, only nodes with `can_disable` in `disable`,
and no disabling at all in public mode. Per-turn provider and fixture choices are allowed in
dev mode only, in [`Gateway._validate_provider`](../harnesswalk/control/gateway.py#Gateway._validate_provider).

[`Gateway.start_turn`](../harnesswalk/control/gateway.py#Gateway.start_turn) applies the rate
limits (per IP in public mode, per tenant outside dev), then calls
[`SessionStore.ensure_session`](../harnesswalk/context/stores/session.py#SessionStore.ensure_session),
which creates the session or raises when it belongs to another tenant. That exception becomes
the `404`. Next come the ids, the `TurnContext` that every node receives, and `turn_start`
with the release stamp, so a turn always records which versions of the prompts it ran on.

[`Gateway.run_turn`](../harnesswalk/control/gateway.py#Gateway.run_turn) is the walk in one
function. Read it top to bottom and you have the simple view of the diagram: receive, input
guardrails (and the early refusal on a block), context, loop, then `_check_and_reply`. The
`except` branches matter as much: a failure anywhere still ends the turn with a notice and a
closed trace, never a hung request.

[`Gateway._reply_and_close`](../harnesswalk/control/gateway.py#Gateway._reply_and_close) is
the exit. It streams the reply in chunks, stores what the model saw (placeholders, not restored
personal data) in the session, hands episodes and proposals to the memory writer, and closes
the trace with the cost receipt.

## Try this

1. **Read the walk in the events.** Run
   `uv run harnesswalk turn "Where is order A-1001?" --provider fake --fixture tool_call`.
   Expected: the first line of the walk is `gateway ... phase=receive` and the last gateway line
   is `phase=reply reply_chars=70`, with everything else in between.
2. **Hit the size cap.** Add `--set gateway.max_message_chars=20` to the same command.
   Expected: it stops with `GatewayError: message is over 20 characters` and stores nothing,
   because the gateway refuses the request before a turn exists. Over HTTP the same check
   answers `400 invalid_request`.
3. **Find the tenant on every row.** Open `harnesswalk/context/stores/session.py` and
   `harnesswalk/control/audit.py` and list every query that does not filter by `tenant_id`.
   Expected: only lookups by a globally unique id that then compare the owner, like
   `ensure_session` and `owner`. Ask yourself what would break if one of them forgot.

## Check yourself

### 1. Why does a request for another tenant's turn answer 404 rather than 403?

- [ ] Because the turn really does not exist.
- [x] Because a 403 would confirm the id exists, which lets a stranger probe for ids.
- [ ] Because 403 is reserved for rate limits.

Answer: the API treats another tenant's resource exactly like a missing one, so nothing about
other tenants can be learned from status codes.

### 2. Why is the gateway entered twice in every turn?

- [ ] Because the loop calls it after every tool call.
- [ ] Because the second visit retries a failed first one.
- [x] Because it receives the request first and sends the reply last.

Answer: `phase=receive` opens the turn; `phase=reply` streams the answer, writes the session,
runs the memory writer and closes the trace.

### 3. A browser page on another site posts a form to `/v1/turns`. What stops it?

- [ ] The rate limit.
- [x] The gateway requires `Content-Type: application/json`, which a plain form cannot send without a CORS preflight.
- [ ] The input guardrails.

Answer: a form or `text/plain` body is a request a browser sends cross-site without asking,
so the gateway answers `415` before reading the body.

## Further reading

- [OWASP API Security Top 10 (2023), API1: Broken Object Level Authorization](https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/)
- [Fetch Standard, CORS-safelisted request headers](https://fetch.spec.whatwg.org/#cors-safelisted-request-header)
- [`spec/contracts/gateway.md`](../spec/contracts/gateway.md) and [`spec/contracts/http_api.md`](../spec/contracts/http_api.md)
