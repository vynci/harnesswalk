# Conformance suite

A black-box test that any harnesswalk implementation, in any language, can run. If your harness
passes it, the harnesswalk UI can drive it and the lessons apply to it.

Status: the protocol, cases and fixtures are from P0; the reference runner is
`tests/conformance/runner.py` (P1). `make conformance` starts the reference harness once per
case on a loopback port with the `fake` provider, drives it over HTTP, and prints one line per
case. It also checks that the cases, fixtures and profile are well formed and that the recorded
example streams in `spec/examples/` satisfy the matching cases. The runner touches the harness
only to start it and to read the fake provider's unused responses for the warnings, so
pointing it at another implementation means replacing `LiveHarness` with a process that serves
the same API.

## Files

| Path | What it is |
|---|---|
| `profile.yaml` | The configuration every implementation runs the suite with |
| `tools.yaml` | Three deterministic tools every implementation registers for the suite |
| `fixtures/*.yaml` | Scripted responses for the `fake` provider (`spec/contracts/model_provider.md`) |
| `fixture.schema.json` | Shape of a fixture |
| `cases/*.yaml` | One case per file: what to send and what the stream must look like |
| `case.schema.json` | Shape of a case |

## Protocol

The runner talks to the harness only through the HTTP API (`spec/contracts/http_api.md`). It
never imports the implementation.

1. **Start.** For each case, start the harness in the case's `mode` (default `dev`; P3) with `profile.yaml`, the `fake`
   provider and the case's fixture, and the case's `config` overrides applied. The reference
   variables are `HARNESSWALK_MODE=dev`, `HARNESSWALK_PROVIDER=fake`,
   `HARNESSWALK_FIXTURE=<fixture>` and `HARNESSWALK_PROFILE=spec/conformance/profile.yaml`. An
   implementation MAY instead accept the dev-only header `X-Harnesswalk-Fixture` on the first
   turn of a session, so one process can serve every case. Wait until `GET /healthz` returns
   `200` with `provider: fake` and the expected `fixture`.
2. **Isolate.** Use the tenant `conformance-<case id>` (header `X-Harnesswalk-Tenant`) and a new
   session per case. All turns of a case share that session.
3. **Send.** For each turn, `POST /v1/turns` with `send.message` and `send.options`. Then read
   `GET /v1/turns/{turn_id}/events` until `turn_end`, with a 30 second limit per turn.
4. **Act.** While reading, when an event matches an `actions[].when` matcher, perform its `do`:
   `approval: approve | deny` posts to `/v1/approvals/{summary.approval_id}`;
   `resume: step | continue` posts to `/v1/turns/{turn_id}/resume`; `cancel: true` posts `{}` as
   `application/json` to `/v1/turns/{turn_id}/cancel` and expects `202`. Each action fires once unless `repeat: true`.
   Once a turn is cancelled no further action fires for it.
   `restart: true` (P3, only in a `two_process` case) kills the harness process (`SIGKILL`),
   starts it again on the same data directory and configuration, waits for `/healthz`, and
   reopens the turn's event stream with `?after=<last seq seen>`. Actions listed after it that
   match the same event then fire against the new process.
5. **Assert**, in this order, and report the first failure per case:
   1. **Schema.** Every event validates against `spec/events.schema.json`.
   2. **Stream rules.** The rules in `spec/events.md` section 1 hold: `seq` starts at 0 and is
      gapless; `ts` never decreases; one trace, turn, session and tenant id per turn; every
      `node_enter` is closed by one `node_exit` or `error` with proper nesting; expert nodes
      only inside their parent's span; point events only inside their node's span; every
      `node_paused` followed by `node_resumed`; every `node_id` and `edge_id` exists in
      `spec/diagram/diagram.json`; `turn_start` first, `turn_end` last.
   3. **Path.** The simple-view path (the `node_id` of each `node_enter` whose node has
      `view: simple`, in `seq` order) equals `expect.path` exactly. Expert nodes are ignored
      here, so an implementation is free to emit them or not.
   4. **Statuses.** The status of the event that closed each path entry equals
      `expect.statuses` (a single value applies to all entries).
   5. **Events.** `expect.events` matchers match distinct events in order (greedy subsequence).
   6. **Absent.** No event matches any `expect.absent` matcher.
   7. **Cost.** `turn_end.cost` token totals equal `expect.cost` when given.
   8. **Reply.** `turn_end.summary.reply` contains every `expect.reply_contains` substring.
5b. **HTTP checks** (P3). After a turn ends, each `http` entry is a `GET` whose status must be
   `status` (default 200) and whose JSON must match `match` (recursive subset, as matchers).
6. **Report.** One line per case: `PASS` or `FAIL <assertion>: <detail>`. Unused fixture responses
   are reported as warnings. The suite passes when every case passes.

### Matchers

A matcher is an object with any of `type`, `node_id`, `edge_id`, `status`, `summary`, `error`.
It matches an event when every key given is equal. `summary` and `error` match as a recursive
subset: for objects every given key must match, scalars and arrays must be equal. Keys the case
does not mention are ignored, so implementations may add summary keys freely.

### What the suite does not check

Timestamps beyond ordering, `event_id` format beyond the schema, `duration_ms` values, `ext`,
expert-node events (other than nesting and ids), edge events (other than ids), and anything the
model would say. The fake provider fixes what the model says.

## Cases

| Case | Fixture | What it proves |
|---|---|---|
| `plain_answer` | `plain_answer` | The canonical path for a plain question; no tool, policy or audit node runs |
| `tool_call` | `tool_call` | A read tool goes through policy, is allowed, runs, and is not audited |
| `approval_approve` | `approval_approve` | A destructive tool is audited, pauses for approval, resumes on approve, then runs |
| `approval_deny` | `approval_deny` | The same call is denied: it never executes and the model is told why |
| `loop_max_steps` | `loop_max_steps` | `loop.max_steps: 3` stops a tool-happy model, then one tools-off call answers |
| `step_mode` | `step_mode` | With `options.step_mode` every node pauses on a breakpoint after its enter and resumes on `resume: step` |
| `guardrail_block` | `guardrail_block` | An oversized input is blocked by `input_guardrails`; no context, loop or model call; `exit: blocked` |
| `cancel_running` | `cancel_running` | A step-mode turn cancelled while paused at `tools`: the open spans close with `error` `cancelled`, no policy or tool runs, `exit: cancelled` |
| `pii_redacted` | `pii_redacted` | An email in the prompt reaches the model as `[EMAIL_1]` (the fixture's `expect` checks it) and is restored in the reply in dev |
| `injection_blocked_public` | `injection_blocked_public` | Public mode: an instruction-override message scores over the threshold and is blocked before any model call |
| `budget_limit` | `budget_limit` | A tool-call budget of 2 per turn: the third call is denied by policy (rule `budget.tool_calls_per_turn`), audited, and the turn ends `exit: limit` |
| `approval_resume_after_restart` | `approval_resume_after_restart` | Two-process: the harness is killed while the turn waits on an approval, restarted, the approval is decided, and the same turn resumes and answers |
| `audit_chain` | `audit_chain` | An approved destructive call then a denied one: five audit records with an unbroken hash chain (`GET /v1/audit`) |

## Adding a case

1. Write the fixture first: what the model will say, call by call.
2. Write the case: the message, the actions, and the smallest set of assertions that proves the
   behaviour. Prefer `path` plus a few `events` matchers over long matcher lists.
3. Run `make conformance`. In P0 this validates the files; from P1 it runs the case.

Cases and fixtures must read the same under YAML 1.1 and 1.2 parsers. Quote, or avoid, bare
`on`, `off`, `yes`, `no`, `y` and `n` as keys and values, since YAML 1.1 reads them as booleans.
