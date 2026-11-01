# Contributing

Thanks for looking. harnesswalk is a teaching harness, so a contribution is judged by two
questions: does it keep the harness correct against its spec, and does it keep it readable for
someone learning how an agent runtime is built. Questions, corrections to the spec and the
lessons, new conformance cases and small fixes are all welcome. For anything bigger, open an
issue first so we can agree on the shape before you write it.

Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md). Security problems go
to `hello@vynci.dev`, never to a public issue ([SECURITY.md](SECURITY.md)).

## Dev setup in five commands

You need [uv](https://docs.astral.sh/uv/) (it fetches Python 3.12 when you have none), and for
the web app Node 24 with pnpm 10 (`corepack enable` gives you the pinned pnpm). No model key,
no Ollama and no Docker are needed: the `fake` provider replays scripted turns.

```sh
git clone https://github.com/vynci/harnesswalk.git && cd harnesswalk
make setup                     # uv sync: .venv with the package and the dev tools
make test                      # unit tests, spec checks and the conformance suite, offline
make web-build                 # the web app into web/dist (pnpm install on first run)
make run PROVIDER=fake         # http://127.0.0.1:8000, the API and the app in one process
```

`make lint` (ruff) and `make web-test` (eslint, tsc, Vitest) are the other two you will run
before every push. `make e2e` drives the app in Chromium against a real fake-provider server,
and `make fresh-clone` repeats the whole setup in a clean container (Docker).

## The spec is the contract

`spec/` was written before the code, and it stays ahead of it. The reference implementation in
`harnesswalk/` and the web app in `web/` follow the spec, never the other way round.

- **A change in behaviour starts in `spec/`.** Edit the prose or contract, the event schema or
  the diagram first, and add or change the conformance case that shows it. Then make the code
  pass. A pull request that changes what the harness emits or answers without a spec change
  will be asked for one.
- **Additive changes only, unless we agree otherwise.** `spec/VERSIONING.md` says what is
  additive (a new optional field, event type, expert node, endpoint or case) and what is
  breaking. Record every spec edit under `Unreleased` in `spec/CHANGELOG.md`.
- **The diagram cannot drift.** Node and edge ids live only in `spec/diagram/diagram.json`.
  A module registers under an id at import time, and an unknown id fails right there; tests
  keep the README diagram, the lessons index and the module map in step with it.
- **Every node emits events.** A component that does work reports it through the event
  contract (`spec/events.md`), inside its node's span.
- **Framework-free core.** No agent framework (LangChain, LangGraph, agent SDKs) as a
  dependency of `control`, `context`, `execution` or `ops`. Plain functions and explicit data
  over clever abstractions; a node should be readable in one sitting.
- **No secrets in the repo.** Keys arrive as environment variables. `.env` is ignored and only
  `.env.example` (names, no values) is tracked. Tests never call a hosted model.

## How to add things

### A node

Nodes are rare and deliberate: each is a box on the diagram, a chapter of the course and a
contract other implementations must meet.

1. Open an issue with the node's job, its inputs and outputs, and why an existing node cannot
   do it. A new expert node is a minor spec change; a new simple-view node is a major one.
2. Spec: add it to `spec/diagram/diagram.json` (id, group, kind, view, parent for an expert
   node, edges, `source_hint`, `lesson`, `lesson_summary`, `lesson_url`), describe it in
   `spec/harness.md`, give it a contract in `spec/contracts/` if it is a component, and list
   its summary keys in `spec/events.md`.
3. Code: one module that calls `register("<id>", __name__)` at import time and does its work
   inside `async with node(ctx, "<id>") as span:` (`harnesswalk/nodes.py`). Add the row to the
   module map in `harnesswalk/README.md`; `tests/unit/test_nodes.py` fails if a registration
   lives anywhere else.
4. A conformance case if the node's behaviour is observable from the stream, unit tests for
   the module, and a lesson (below) or a section in its group's chapter.

### A tool

- **A pack tool** is data: an entry in `datasets/<pack>/tools.yaml` with `name`, `description`,
  `action_class` (`read`, `write`, `destructive`, `external` or `memory.write`), `timeout_ms`,
  a JSON Schema in `parameters`, and a lookup `table` with a `missing` row. It must be in
  `config/policy.yaml` `agent.allow_tools`, or policy denies it. Add a fake fixture that calls
  it and a golden case. `spec/packs.md` has the rules; `uv run harnesswalk packs validate`
  checks them.
- **A code tool** names an `entrypoint` (`module:function`, or `file.py:function` beside the
  yaml) instead of a table. The function takes the validated arguments as one dict and
  returns the observation as a string (see `harnesswalk/execution/tool_impls.py`), runs in the
  subprocess sandbox (`spec/contracts/sandbox.md`), and
  uses the standard library only unless the issue agrees otherwise. Destructive tools need an
  approval rule in `config/policy.yaml`; public mode lists only pure `read` tools.

### A pack

A pack is a fictional business: system prompt, skills, tools, documents, seed facts and
events, a golden set with a fake fixture per case, and a provenance card. Start from
`datasets/_starter/` and the layout in `datasets/README.md` and `spec/packs.md`. Content is
synthetic and CC0 1.0: no real people, brands or places, and no copied text. Run
`uv run harnesswalk packs validate <pack>` and `uv run harnesswalk eval <pack>` until both
pass. The CI `packs` job runs every pack's golden set.

### A lesson

Chapters live in `lessons/`, one per simple-view node in walk order and one per expert group,
indexed in `lessons/README.md`. Each has the front matter (`node`, and a `try` block naming a
pack, fixture and prompt), then the six sections in order: What it does, What breaks without
it, The code walk, Try this, Check yourself, Further reading. `tests/unit/test_http_lessons.py`
checks the sections, a length of 600 to 1200 words, three questions with one right answer each,
code links that resolve to a file and symbol, real fixtures, and two or three primary sources.
Verify every "what breaks" demonstration on the fake provider before you describe it.

### A conformance case

Write the fixture first, then the case, then run
`uv run python -m tests.conformance.runner --case <id>`. The steps and the rules are in
[spec/conformance/README.md](spec/conformance/README.md#adding-a-case). A case that asserts new
behaviour is a spec change and lands with the spec text it tests.

## Tests and lint

- `make test` and `make lint` pass on every commit. CI runs them on Python 3.12 and 3.14, plus
  every pack's golden set, the web lint, unit tests and build, the Playwright suites, and the
  fresh-clone check.
- New behaviour comes with a test at the lowest level that shows it: a unit test for a
  module, a conformance case for anything a client can observe, a web unit test or a mock e2e
  for the UI, a live e2e only for flows that cross the API and the browser.
- Tests use the `fake` provider and local hash embeddings, run offline, and keep their stores
  in a temporary directory. Postgres tests run only with `HARNESSWALK_TEST_PG=1`.
- Docs are plain prose: no em-dashes (a test checks), short sentences, and the exact command
  or fixture when you describe something a reader can try.

## Commits and pull requests

- Small, focused [conventional commits](https://www.conventionalcommits.org/): `feat:`,
  `fix:`, `docs:`, `test:`, `spec:`, `build:`, `chore:`, with a scope when it helps
  (`feat(memory_writer): ...`). One logical change per commit; the message says what changed
  and, when it is not obvious, why.
- Fill in the pull request template: what and why, the spec change if any, how you tested it.
  Add a line under `Unreleased` in `CHANGELOG.md` for anything a user would notice.
- Keep the branch up to date with `main` by rebasing; we merge with a merge commit or a
  squash, whichever keeps the history readable.

## How a pull request is reviewed

1. CI is green (tests, lint, packs, web, e2e, fresh clone).
2. Spec first: behaviour changes have their spec edit, changelog line and conformance case,
   and the change is additive or the issue agreed on a breaking one.
3. Readability: a learner can follow the new code from its node's inspector; no framework in
   the core, no hidden global state, events for the work it does.
4. Safety: nothing widens what public mode allows, no secret can reach an event, a log or the
   repo, and tool code stays inside the sandbox rules.
5. Teaching: the lessons, README and inspector text still describe what the code does.

A maintainer reviews within a week or so. Expect questions rather than a verdict; it is a
conversation about the shape, and most pull requests take a round or two.

## Licence

By contributing you agree that your contribution is licensed under Apache-2.0 (code and docs)
or CC0 1.0 (dataset content), the licences of this repository.
