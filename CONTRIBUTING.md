# Contributing

harnesswalk is pre-alpha. The spec is still moving, so the most useful contributions right now
are questions and corrections to `spec/`. Code contributions open with P1.

## Ground rules

- **Spec first.** A change in behaviour starts as a change to `spec/` (and to the conformance
  cases when it is observable). Code follows the spec, never the other way round.
- **Framework-free core.** No agent framework (LangChain, LangGraph, agent SDKs) as a dependency
  of the harness. Adapters that show how a framework plugs in live under `harnesswalk/adapters/` as
  examples, outside the core path.
- **Small and readable.** Every node should be readable in one sitting. Prefer plain functions
  and explicit data over clever abstractions.
- **Every node emits events.** If a component does work, it reports it through the event
  contract (`spec/events.md`).
- **No secrets in the repo.** Keys arrive as environment variables. `.env` is ignored; only
  `.env.example` (keys, no values) is tracked.

## Setup

```sh
make setup      # uv sync (Python 3.12 or newer)
make test
make lint
```

## Commits

Conventional commits, small and focused: `spec:`, `feat:`, `fix:`, `docs:`, `test:`, `chore:`.

## Datasets

Dataset content is contributed under CC0 1.0 and must carry a provenance card. See
`datasets/README.md`.

## Licence

By contributing you agree that your contribution is licensed under Apache-2.0 (code and docs)
or CC0 1.0 (dataset content).
