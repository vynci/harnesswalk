# Conformance suite

A black-box test that any Harnesswalk implementation, in any language, can run. If your harness
passes it, the Harnesswalk web app can drive it, the hosted sandbox's lessons apply to it, and
its event stream can be traced, replayed and evaluated by the same tools. The suite is the
spec's executable half: `spec/harness.md`, `spec/events.md` and `spec/contracts/` say what a
harness must do, and these cases check that it does, over HTTP only.

## What a conforming runtime is

A runtime conforms to spec version `X.Y` when, started with `profile.yaml` and the `fake`
provider, it passes every case in `cases/` of that version, driven by
`tests/conformance/runner.py` or any runner that follows the protocol below. In practice that
means it:

- serves the HTTP API in `spec/contracts/http_api.md` that the cases use (turns, the SSE event
  stream, resume, cancel, approvals, `/healthz`, and the audit, artifact, release, eval,
  session and pack routes the later cases call);
- emits events that validate against `spec/events.schema.json` and keep the stream rules of
  `spec/events.md` section 1, with node and edge ids from `spec/diagram/diagram.json`;
- implements the `fake` provider exactly (`spec/contracts/model_provider.md`), reading the
  fixtures in `fixtures/`, and registers the tools in `tools.yaml` and `harness_tools.yaml`;
- reports `provider: fake`, its `fixture` and its `spec_version` at `GET /healthz`.

Nothing else is required: the language, the storage, the framework (or none) and any extra
nodes, summary keys or endpoints are yours. A runtime that passes a subset may say which cases
it passes; it conforms only when it passes them all. The suite version is the spec version
(`spec_version` in `profile.yaml` and in every case); `spec/VERSIONING.md` says what may change
between versions.

## Running the suite

The runner is plain Python in this repository. Clone it and install it once (`make setup`,
which is `uv sync`); the runner imports the reference harness only in the default mode.

```sh
make conformance                       # the reference harness: the default run, then every profile
uv run python -m tests.conformance.runner --all-profiles    # 0.2.0: the suite under each profile
uv run python -m tests.conformance.runner --profile minimal # one profile (repeat for more)
uv run python -m tests.conformance.runner --list            # the case ids
uv run python -m tests.conformance.runner --case tool_call  # one case (repeat --case for more)
```

It prints one line per case and a total, and exits 1 when a case fails:

```
PASS approval_approve
FAIL budget_limit turn 0: path: got [gateway, ...], expected [gateway, ...]
SKIP release_rollback changes server-wide state at /v1/releases (use --command)
21/22 cases passed, 1 skipped
```

### Against your implementation, one process per case (`--command`)

The complete run. The runner starts your harness once per case from a command template, waits
for `GET /healthz`, drives it, and stops it. A `two_process` case kills it with `SIGKILL` and
runs the same command again on the same data directory.

```sh
uv run python -m tests.conformance.runner \
  --command "./my-harness serve --port {port} --data {data_dir} --fixture {fixture} --mode {mode}"
```

The command runs from the repository root with your environment plus these variables, and the
template may use the same values as placeholders (shell-quoted; write `{{` and `}}` for a
literal brace):

| Placeholder | Variable | Value |
|---|---|---|
| `{port}` | `HARNESSWALK_PORT` | A free loopback port to listen on (`127.0.0.1`) |
| `{data_dir}` | `HARNESSWALK_DATA_DIR` | A fresh, empty directory for this case's stores; kept across a restart |
| `{fixture}` | `HARNESSWALK_FIXTURE` | The case's fixture name |
| `{mode}` | `HARNESSWALK_MODE` | `dev`, or the case's `mode` |
| `{profile}` | `HARNESSWALK_PROFILE` | The absolute path of `spec/conformance/profile.yaml` |
| `{config_json}` | `HARNESSWALK_CONFORMANCE_CONFIG` | The case's `config` overrides as a JSON object of dotted keys (`{}` for most cases) |
| `{set_args}` | | The same overrides as `--set=key=<json>` arguments, the reference CLI's spelling |
| | | 0.2.0: in a per-profile run both carry the harness profile as the dotted key `profile` (`{"profile": "minimal"}`, `--set=profile="minimal"`), so start your harness on that profile |
| `{root}`, `{python}` | | The repository root, and the Python running the runner |
| | `HARNESSWALK_PROVIDER` | Always `fake` |
| | `HARNESSWALK_SECRET` | `conformance-secret` unless your environment sets it: the reference harness refuses public mode without a cookie secret (P6) |

The reference harness passes this way too, which is a useful check of the runner itself:

```sh
uv run python -m tests.conformance.runner --command \
  "{python} -m harnesswalk serve --provider fake --fixture {fixture} --profile {profile} --mode {mode} --port {port} {set_args}"
```

### Against a running server (`--base-url`)

A quick check while you build. Start your harness once in `dev` mode with `profile.yaml` and
the `fake` provider, then:

```sh
uv run python -m tests.conformance.runner --base-url http://127.0.0.1:8000
make conformance BASE_URL=http://127.0.0.1:8000           # the same, then the spec checks
```

The runner checks `GET /healthz` (status 200, `provider: fake`, a `spec_version` of the same
major as the suite), then sends every turn with the case's fixture in the request body
(`fixture`, a dev-only per-turn option in `spec/contracts/http_api.md`; a turn that names its
own fixture in `options` keeps it). Each run appends a random suffix to the tenants
(`conformance-<case id>-<suffix>`, or `--tenant-suffix`), so reruns against the same server do
not see each other's audit records or memory. Cases that need a server of their own are
reported as `SKIP` with the reason: another `mode`, `config` overrides, a restart, or requests
to `/v1/artifacts` and `/v1/releases`, which change state the whole server shares. Today that
leaves 10 of the 28 cases (a case pinned to a profile also needs its own server, unless `--profile` names it); use `--command` for the rest.

For the reference harness:

```sh
HARNESSWALK_DATA_DIR=/tmp/hw-conf uv run harnesswalk serve --provider fake --profile spec/conformance/profile.yaml
```

## Files

| Path | What it is |
|---|---|
| `profile.yaml` | The configuration every implementation runs the suite with |
| `tools.yaml` | Three deterministic tools every implementation registers for the suite |
| `harness_tools.yaml` | The harness's own tools (`remember`, `read_doc`): names, action classes and argument schemas every implementation registers |
| `docs/*.md` | The documents the knowledge cases ingest (`stores.knowledge.docs_dir` in the profile) |
| `fixtures/*.yaml` | Scripted responses for the `fake` provider (`spec/contracts/model_provider.md`) |
| `fixture.schema.json` | Shape of a fixture |
| `cases/*.yaml` | One case per file: what to send and what the stream must look like |
| `case.schema.json` | Shape of a case |
| `golden.jsonl` | P4: the golden set eval runs read under the profile (`evals.golden_path`) |
| `packs/conformance_alt/pack.yaml` | P5: a second dataset pack (`packs.roots` in the profile) for the pack cases |

## Protocol

The runner talks to the harness only through the HTTP API (`spec/contracts/http_api.md`). It
never imports the implementation.

1. **Start.** For each case, start the harness in the case's `mode` (`dev` when the case does
   not say) with `profile.yaml`, the `fake` provider and the case's fixture, and the case's
   `config` overrides applied. The reference
   variables are `HARNESSWALK_MODE=dev`, `HARNESSWALK_PROVIDER=fake`,
   `HARNESSWALK_FIXTURE=<fixture>` and `HARNESSWALK_PROFILE=spec/conformance/profile.yaml`
   (`--command` sets them, with the others in the table above). Wait until `GET /healthz`
   returns `200` with `provider: fake` and the expected `fixture`. A shared server
   (`--base-url`) is started once instead, and each turn names the case's fixture in its
   request body (`fixture`, dev mode only); its `/healthz` must still say `provider: fake`.
2. **Isolate.** Use the tenant `conformance-<case id>` (header `X-Harnesswalk-Tenant`) and a new
   session per case. All turns of a case share that session. In `public` mode the header is
   not honoured: the client keeps the anonymous tenant cookie the first response sets.
3. **Send.** For each turn, `POST /v1/turns` with `send.message` and `send.options`. P5: a turn
   with `send.session_id` is sent in that session (a value saved from `POST /v1/sessions`), and
   later turns without one continue it. Then read
   `GET /v1/turns/{turn_id}/events` until `turn_end`, with a 30 second limit per turn.
4. **Act.** While reading, when an event matches an `actions[].when` matcher, perform its `do`:
   `approval: approve | deny` posts to `/v1/approvals/{summary.approval_id}`;
   `resume: step | continue | step_over` posts to `/v1/turns/{turn_id}/resume`; `cancel: true` posts `{}` as
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
5a. **Requests** (P4). A turn may list `before` and `after` requests (`GET`, `POST` or `PUT`
   with a JSON body): `before` runs before the turn's message is sent, `after` once the turn
   ended and its `http` checks ran. Each must answer `status` (default 200) and match `match`;
   with `wait: {timeout_s}` the runner repeats it until both hold. `save: {name: dotted.path}`
   keeps a value from the response, and any later string in the case may name it as
   `${name}` (requests, messages, options, matchers).
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
| `knowledge_cited` | `knowledge_cited` | With `knowledge` among the stores, the `knowledge` child retrieves the returns policy, the context numbers it `[1]`, and `turn_end.summary.citations` names the chunk the reply cites |
| `memory_written` | `memory_written` | A `remember` call (class `memory.write`) is allowed and audited, the memory writer runs after the reply and writes the fact (`memory_proposed`, `memory_written`), and the next turn retrieves it from `semantic` |
| `memory_denied_public` | `memory_denied_public` | In `public` mode the same call is proposed with gate `session` and rejected: no `memory_written` |
| `eval_golden_regression` | `eval_golden_regression` | P4: a golden run over `spec/conformance/golden.jsonl` passes; after a skill edit (`PUT /v1/artifacts/skill/returns`) the next run fails the case that pinned the skill and lists it in `regression_cases` |
| `release_rollback` | `release_rollback` | P4: an edited prompt reaches the model after a release; after `POST /v1/releases/{id}/rollback` to the first release, the next turn's model sees the old prompt (the fixture's `system_contains`) and the working set is clean |
| `step_over` | `step_over` | P4: step mode with `resume: step_over` at `memory`: the expert children `procedural` and `session` do not pause, the next pause is `loop`. P5: each `node_resumed` names the resume `mode` (`step_over` at `memory`, `continue` at `loop`) |
| `restart_keeps_schema` | `restart_keeps_schema` | P4, two-process: a turn with an `output_schema` and a `remember` call pauses on an approval, the harness is killed and restarted, the approval is decided, and the reply is still schema-checked (`schema: valid`), the pre-pause proposal is written and `checkpoint.restored` is true |
| `pack_switch` | `pack_switch` | P5: one tenant, two sessions made with `POST /v1/sessions` on two packs: a fact remembered on `_starter` is not retrieved on `conformance_alt` (`semantic` hits 0) and is retrieved back on `_starter`; `turn_start.summary.pack` and the turn and session summaries name the pack. Per-turn fixtures `pack_switch_other` and `pack_switch_recall` |
| `diagnose_skill_change` | `diagnose_skill_change` | P5: a skill edit regresses the golden case; the run diagnoses it on its own (`diagnoses: {skill_index: prompt_or_skill}`), `POST /v1/diagnose` names the same layer against the passing run as baseline, a passed case has nothing to diagnose, and a chat turn with bad feedback is diagnosed and carries its `diagnosis` |
| `release_gate` | `release_gate` | P5, with `release.gate.enabled: true`: a release on a passing golden set records `gate: passed`; after an edit that breaks the case `POST /v1/releases` is `409 gate_failed` with the failing case and the active release stays; `force: true` releases with `gate: skipped, forced: true` |
| `public_model_allowlist` | `public_model_allowlist` | P6, public mode: `GET /v1/capabilities` carries `public`; a turn naming a model outside `public.models` is `400 "model not available in public mode"`; `model: fake-small` runs on it through the router's `public` rule |
| `profile_minimal` | `plain_answer` | 0.2.0, under `minimal` only: a plain answer walks the same path, with `input_guardrails` and `output_guardrails` exiting `skipped` with `disabled: true` and `turn_start.summary.profile` naming `minimal` |
| `profile_minimal_tool` | `tool_call` | 0.2.0, under `minimal` only: a read tool runs with `policy` exiting `skipped` (rule `disabled`, allow) and nothing audited |
| `approval_off` | `approval_deny` | 0.2.0: with `approval` disabled for the turn, a destructive call that needs approval is denied without asking anyone: the `approval` span exits `skipped` (`decision: deny`), audit records the deny, the tool never runs |
| `public_budget_fall_through` | `public_budget_fall_through` | P6, public mode with a priced fake alias (`fake-priced`, profile) first: the first call spends the day's whole budget (`public.hosted_budget_usd_per_day: 0.01`), the second call of the same turn runs on `fake-small` with `notice: budget_exhausted`, `turn_end.summary.notices` says so, capabilities show `budget.status: exhausted`, and a turn naming the priced alias is `409 budget_exhausted` |

The knowledge and memory cases need the expert children `knowledge` and `semantic`: an
implementation that implements those stores emits their spans, so these cases assert them.

## Profiles (0.2.0)

A harness runs with a profile (`spec/harness.md` section 10). The default run is the suite as
before: each pack runs with the profile its card names (the conformance packs name
`operated`, so every node is on). A per-profile run forces one profile for every case:

- The runner starts the harness with the dotted override `profile: <id>` (in-process and
  `--command`: part of the case's overrides; `--base-url`: start the server with
  `HARNESSWALK_PROFILE=<id>` and pass `--profile <id>`, the runner trusts it).
- A case lists the nodes it needs switched on in `requires`. Under a profile whose `off` set
  (for the case's mode: public mode keeps the controls on) contains one of them, the case is
  `SKIP` with the reason, never `FAIL`. Most cases assert `statuses: done` on the guardrails,
  so they require them; the `requires` lists were measured by switching each node group off
  in turn.
- A case with its own `profile` runs only under it: in the default run with that profile, and
  skipped in a run under another one (`profile_minimal`, `profile_minimal_tool`).
- The runner prints one total per profile, then a `per profile:` line. The suite passes when
  every case that ran passed under every profile.

The reference passes every profile: on `minimal` 5 cases run (the two `profile_minimal` cases
and the three public-mode cases, since public mode keeps the controls on), on `operated` all
but the two minimal-only cases.

## The fixture protocol

The model is the one part of a harness a black-box test cannot pin, so the suite replaces it.
Every case names a fixture in `fixtures/`, and the implementation's `fake` provider replays it
(`spec/contracts/model_provider.md`, "The `fake` provider"):

- A fixture has `turns`, one per chat turn of a session, and each turn has `responses`, one per
  model call in that turn, in order: `text`, `tool_calls` (with optional `call_id`), or both,
  and `usage` token counts.
- Each session keeps its own cursor: turn `n` of the session reads `turns[n]`. A per-turn
  fixture (`fixture` in the request, dev only) counts only the session's turns on that fixture.
- A response may carry `expect`, checked against what the harness sent the model:
  `tools_enabled`, `last_message_role`, `last_message_contains` and `system_contains`. A
  mismatch fails the call with `fixture_mismatch`, so a fixture also asserts what the model
  saw: the placeholder that replaced an email, a tool observation, the prompt version a
  rollback restored.
- A call past the script fails with `fixture_exhausted`; responses left unread are reported as
  warnings by the in-process reference run.
- No network, no clock (except `latency_ms`, a delay for step and timeout cases) and no
  randomness: the same fixture and inputs give the same events every time, on every machine.
  Embeddings are the deterministic `fake_embed` hash, so knowledge retrieval is the same too.

`fixture.schema.json` is the shape. The knowledge cases ingest `docs/`, the eval cases read
`golden.jsonl`, and the pack cases add `packs/conformance_alt`, all through `profile.yaml`.

## Adding a case

1. Write the fixture first: what the model will say, call by call, with `expect` where the
   case depends on what the model was shown.
2. Write the case in `cases/<id>.yaml` (`id` equals the file stem; `case.schema.json` is the
   shape): the message, the actions, and the smallest set of assertions that proves the
   behaviour. Prefer `path` plus a few `events` matchers over long matcher lists, and never
   assert what the spec leaves open (see "What the suite does not check").
3. If the case needs configuration, override only keys listed in `profile.yaml`
   `overridable`; adding a key there is a spec change (record it in `spec/CHANGELOG.md`).
4. Add a row to the Cases table above, and run `make conformance` (or
   `uv run python -m tests.conformance.runner --case <id>`) until the reference passes it.
   `make test` runs it too, along with the checks that the cases, fixtures and profile are
   well formed and that `spec/examples/` streams satisfy their cases.
5. A case that asserts new behaviour is a spec change: it lands with the spec text it tests,
   and under `spec/VERSIONING.md` it is added in a minor version.

Cases and fixtures must read the same under YAML 1.1 and 1.2 parsers. Quote, or avoid, bare
`on`, `off`, `yes`, `no`, `y` and `n` as keys and values, since YAML 1.1 reads them as booleans.
