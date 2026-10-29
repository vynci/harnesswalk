# harnesswalk

**Walk through an AI harness, one node at a time.**

Status: **pre-alpha, P2 web UI.** The harness runs: a request walks the gateway, guardrails,
context builder, memory, loop, model layer, tools, policy, approval, audit and trace, and every
step is an event you can stream, store and replay. The web UI shows it: a chat beside a live
diagram, step mode, approvals, replay and a code peek per node. See
[docs/ROADMAP.md](docs/ROADMAP.md).

![A finished turn: the chat on the left, every node it visited lit on the diagram](docs/screenshots/turn-done.png)

## What this is

An agent is a model inside a harness. The harness is everything else: the gateway that decides
who is asking, the guardrails that check what goes in and out, the policy that decides which
tools may run, the context builder that chooses what the model sees, the memory it reads and the
gate that controls what it writes, the loop, the tools, the trace, the evals, the release
process. Most teaching material shows the loop. The harness is where most of the engineering is.

harnesswalk is an open-source, local-first harness built to be read. One command brings up
a working agent and a web page with two panels: a chat, and a live systems diagram of the
harness. Send a message and watch it travel. Every node lights up as the request passes through
it. Click a node to see what is behind it: the code for a guardrail, the rules for policy, the
skills in procedural memory, the assembled context with a budget bar per slice, the timeline in
the trace.

## Walk through it one node at a time

Turn on step mode and the harness stops at every node, like a debugger. Press Next and the
request moves one hop. The inspector of the current node stays open beside a short lesson: what
the node does, what breaks without it, and something to try. Replay does the same over a past
turn without calling the model again.

The lessons follow the same order as the walk, so reading the course and stepping through a
request are the same activity.

## The diagram

Fifteen nodes in four groups in the simple view. The expert view expands memory, the model
layer, tools, trace, eval and release into 14 more nodes. The diagram is data
([spec/diagram/diagram.json](spec/diagram/diagram.json)) and the running harness serves it from
its registered components, so the picture cannot drift from the code.

```mermaid
flowchart LR
    subgraph control["Control plane"]
        gateway["Gateway"]
        input_guardrails["Input guardrails"]
        policy["Policy"]
        approval["Approval"]
        audit["Audit"]
    end
    subgraph context["Context plane"]
        context_builder["Context builder"]
        memory[("Memory<br/>procedural, session, semantic,<br/>episodic, knowledge")]
        memory_writer["Memory writer"]
    end
    subgraph execution["Execution plane"]
        loop["Loop"]
        model_layer["Model layer"]
        tools["Tools"]
        output_guardrails["Output guardrails"]
    end
    subgraph ops["LLM Ops"]
        trace["Trace"]
        eval["Eval"]
        release["Release"]
    end

    gateway -->|message| input_guardrails
    gateway -.->|identity + tenant| policy
    input_guardrails -->|clean input| context_builder
    memory -->|retrieve + rank, within budget| context_builder
    context_builder -->|assembled context| loop
    loop -->|inference| model_layer
    model_layer -->|completion| loop
    loop -->|tool call| tools
    tools -.->|policy check per call| policy
    policy -->|record decision| audit
    policy -.->|needs approval| approval
    approval -->|record decision| audit
    approval -.->|approve / deny, resume| tools
    tools -->|observation| loop
    loop -->|final answer| output_guardrails
    output_guardrails -->|reply via channel| gateway
    loop -->|checkpoint per step| memory
    loop -->|proposed memories| memory_writer
    memory_writer -->|gated writes| memory
    loop -.->|telemetry| trace
    trace -.->|sample + feedback| eval
    eval -->|proposed fixes, facts and skills| memory_writer
    eval ==>|eval gate| release
    release ==>|loaded on release| memory
```

Solid arrows carry requests and data, dotted arrows carry control and telemetry, thick arrows
carry releases.

## Principles

1. **Glass box.** Every component is small, readable and inspectable from the UI.
2. **The event stream is the product.** Every node emits typed events. The animation, the trace
   store, step mode, replay and the evals all read the same stream. Emit the events from any
   language and you get the UI.
3. **Code for controls, text for intent.** Guardrails, policy and budgets are code and config.
   Prompts and skills carry intent. The UI shows which is which.
4. **Local-first.** Runs offline with a local model. Hosted models are optional.
5. **Learn by poking.** Edit a skill, change a budget, add a policy rule, switch a node off, run
   the same prompt again, and watch the diagram change.
6. **Framework-free core.** The loop is plain code you can read. Agent frameworks appear only as
   optional adapter examples that show where they would plug in.

## How it relates to waku-agent

[waku-agent](https://github.com/ShenSeanChen/waku-agent) is the closest existing project, and a
good one: a readable Python assistant with a loop, three kinds of memory, tools, traces, evals, a
release gate, and a dashboard whose architecture picture lights up live. We learned from it and
adopt several of its ideas: one event stream for UI, trace and evals; a "things to try" table;
deterministic and judged evals kept apart; a per-turn cost receipt; providers as data; topology
derived from code.

harnesswalk aims at a different spot. What it adds:

- Input and output guardrails, policy with action classes, and human approval as real nodes on
  the request path.
- An inspectable context builder with a token budget per slice.
- Knowledge retrieval (RAG) and five starter datasets, so a learner can ask real questions in
  minute one.
- Docker Compose for the learner path, with a local model.
- A data-driven diagram where clicking a node opens its code, prompt, skill or config, and step
  mode that walks a request one node at a time.
- A lesson sequence bound to the diagram.
- A published event schema and a conformance suite, so other runtimes can light the same diagram.
- One permissive licence (Apache-2.0) for the whole learner path, with dataset content under CC0.

If you want a personal assistant you can read in an afternoon, use waku-agent. If you want to
see every stage of a production-style harness and step through it, that is what this project is
for.

## The spec comes first

The contract is in [`spec/`](spec/), written before the code:

| Path | What it fixes |
|---|---|
| [spec/harness.md](spec/harness.md) | Planes, nodes, invariants, the multi-tenant rule, modes |
| [spec/diagram/](spec/diagram/) | Canonical node ids, edges, lessons, walk order |
| [spec/events.schema.json](spec/events.schema.json), [spec/events.md](spec/events.md) | The harness event contract and its OpenTelemetry mapping |
| [spec/contracts/](spec/contracts/) | One interface per component, plus the HTTP API |
| [spec/conformance/](spec/conformance/) | Black-box cases any implementation can run |

The reference implementation is Python (FastAPI, no agent framework) in
[`harnesswalk/`](harnesswalk/), and the TypeScript web UI is in [`web/`](web/). A
TypeScript harness port is planned to prove the spec.

## Try it

You need [uv](https://docs.astral.sh/uv/) (Python 3.12 or newer), and for the web app Node 24
with pnpm 10 (`corepack enable` gives you the pinned pnpm).

### The web app

```sh
make setup && make web-build && make run PROVIDER=fake
```

Then open http://127.0.0.1:8000. `make run` serves the API and the built app from one process on
`127.0.0.1:8000`. With `PROVIDER=fake` no model is needed: the fake provider replays scripted
replies. With [Ollama](https://ollama.com) running (`ollama pull llama3.1:8b`), plain `make run`
answers with a real local model instead.

- **Send a message** and watch it walk the diagram: each node lights while it works and shows a
  one-line stat when it is done. The reply streams into the chat, and the bar above the diagram
  shows the phase, the exit, tokens, cost and the event count.
- **Fixtures.** In dev mode the header has a Fixture menu with the starter and conformance
  fixtures (`tool_call`, `approval_approve`, `approval_deny`, `step_mode`, `cancel_running`,
  `guardrail_block` and more). Fake replies are canned: each fixture scripts the first turn (or
  first few turns) of a session, whatever you type, and a turn past the script fails with
  `fixture_exhausted`. Press New session (the circular arrow) or pick another fixture to go
  again.
- **Approvals.** Pick `approval_approve` or `approval_deny` and ask to cancel an order. Policy
  marks `cancel_order` as destructive, the turn pauses on the Approval node, and a card in the
  chat waits for your Approve or Deny.

![The approval card: the turn waits on the Approval node until a human decides](docs/screenshots/approval-card.png)

- **Step mode.** Switch on Step mode before sending. The harness stops at every node, like a
  debugger; Next moves one hop, Continue runs to the end, and Stop cancels the turn (a model
  or tool call already in flight finishes first).
- **Inspect a node.** Click any node (or focus it and press Enter) for its lesson, its config,
  prompt or records, the events it emitted this turn, and its source code. For the guardrails,
  policy and the memory writer, the inspector can also switch the node off for your next turn,
  to see what breaks without it.

![The inspector on the Loop node, Code tab](docs/screenshots/inspector-code.png)

- **Replay.** Every finished turn has a Replay button: a slider walks the stored events back
  and forth on the diagram without calling the model again. History survives a reload.
- **Simple and Expert.** Expert view opens memory, the model layer, tools and trace into their
  parts, and draws every edge, telemetry included. The simple view folds the LLM Ops edges into
  one telemetry bus; the legend's Show telemetry switch (or hovering a node) brings them back.
- **Reading the colours.** A plane is a coloured box; a chip by each title says what kind of
  thing the node is (code, config, prompt, store, queue, model); a bar on its left says its
  state; an edge's colour and dash say what kind of traffic it carries. The legend in the
  corner of the diagram lists them all, and [web/README.md](web/README.md) has the table.

![The same turn in the expert view: every node opened into its parts](docs/screenshots/turn-done-expert.png)

To work on the UI with hot reload, run `make run PROVIDER=fake` in one terminal and
`make web-dev` in another, then open http://127.0.0.1:5173 (Vite proxies `/v1` to the API). To
look at the UI with no API at all, `make web-mock` plays the recorded streams in
`spec/examples/` from an in-browser mock (a `mock` badge says so).

### The command line

```sh
make turn                                    # one fake turn, printed as a walk
make turn FIXTURE=tool_call PROMPT="Where is order A-1001?"
uv run harnesswalk turn "Cancel A-1002" --provider fake --fixture cancel_order   # asks you to approve
uv run harnesswalk turn "What are your support hours?" --provider fake --fixture plain_answer --step
uv run harnesswalk events <turn_id>          # the stored events as JSON Lines
uv run harnesswalk turn "How long do I have to return an item?" --provider fake --fixture cited_answer
uv run harnesswalk turn "Call me Sam" --provider fake --fixture remember_fact      # the memory writer
uv run harnesswalk ingest _starter           # index the pack's documents and load its seeds
uv run harnesswalk turn "Where is order A-1001?" --provider ollama
```

`make turn` prints every node the request visited, in order, nested, with its status, its time
and a short summary, then the reply and the cost receipt. Starter fixtures live in
`datasets/_starter/fixtures/fake/`; the conformance fixtures in `spec/conformance/fixtures/`
work too.

### Docker

```sh
make up                                      # docker compose: api (with the web app), postgres, ollama
```

The api image builds `web/` in a Node stage and serves it at http://127.0.0.1:8000. See
[deploy/README.md](deploy/README.md) for the profiles and for running it without a model.

### The API

[`spec/contracts/http_api.md`](spec/contracts/http_api.md): `POST /v1/turns`, the event stream
as Server-Sent Events at `GET /v1/turns/{id}/events` (with `?after=` cursors and
`?follow=false` for replay), `GET /v1/turns/{id}`, `POST /v1/turns/{id}/resume` for step mode,
`POST /v1/turns/{id}/cancel`, `POST /v1/approvals/{id}`, `GET /v1/sessions/{id}/turns`,
`GET /v1/diagram`, `GET /v1/nodes/{id}` and its `/source`, `GET /v1/capabilities` and
`GET /healthz`.

### Tests

```sh
make test          # unit tests, spec checks, conformance suite (offline, fake provider)
make conformance   # one PASS or FAIL line per conformance case
make lint          # ruff
make web-test      # eslint, tsc and Vitest for web/
make e2e           # Playwright in Chromium against a real API with the fake provider
```

`make e2e` builds the app, starts `harnesswalk serve --provider fake` on a scratch port
(`E2E_PORT`, default 8765) with its stores in a temporary directory, drives the UI through the
conformance fixtures, and stops the server. `make web-e2e-mock` runs the smoke tests against the
mock build, and `make screenshots` recaptures the pictures above.

## Repository layout

```
spec/       the language-agnostic contract
harnesswalk/ reference implementation (Python); harnesswalk/README.md maps nodes to modules
config/     harness.yaml, policy.yaml, context.yaml, models.yaml
web/        chat and diagram UI (React, TypeScript); web/README.md
datasets/   _starter pack now; five domain packs in P5
lessons/    one chapter per node (P5)
deploy/     docker compose: dev and homelab profiles
docs/       roadmap
tests/      unit tests, spec checks and the conformance runner
```

## Licence

Code and documentation: [Apache-2.0](LICENSE). Starter dataset content: CC0 1.0. Copyright 2026
Vince Elizaga.
