# harnesswalk web

The web UI for harnesswalk: a chat on the left and a live diagram of the harness on the right.
Send a message and watch it walk through the nodes. Click a node to read its lesson, its config or
prompt, its code and its events. Turn on step mode to stop at every node, or replay a past turn
with a slider.

Stack: React 19, Vite, TypeScript (strict), Tailwind 4, shadcn/ui, React Flow (`@xyflow/react`,
attribution kept), Zustand, dagre, Vitest and Playwright. Node 24 and pnpm 10.

## Run it

```sh
pnpm install

# Against a running API (make run, or uv run harnesswalk serve) on 127.0.0.1:8000.
# Vite proxies /v1 and /healthz to it, so the UI is same-origin with the API in dev.
pnpm dev                          # http://127.0.0.1:5173

# With no API at all: the in-browser mock plays the recorded spec/examples streams.
pnpm dev:mock

# Production build into web/dist, which the API serves at / (HARNESSWALK_WEB_DIST).
pnpm build
```

## Test it

```sh
pnpm test          # Vitest: reducer, layout, memory badges, stream client against the mock
pnpm test:e2e      # Playwright smoke tests against a mock build (Chromium only)
pnpm lint          # ESLint, then tsc -b
pnpm test:e2e:live # Playwright against the real API with the fake provider, see below
```

`pnpm exec playwright install chromium` once before the first e2e run. From the repository root
the same runs are `make web-test`, `make web-e2e-mock` and `make e2e`.

The live suite needs no running API. `playwright.live.config.ts` builds the app into
`dist-e2e/` with the schema validator on (`pnpm build:e2e`), starts
`uv run harnesswalk serve --mode dev --provider fake` on a scratch port (`HARNESSWALK_E2E_PORT`,
default 8765) with its stores in a temporary directory, and lets the API serve that build, so
the tests cover the production path: static files, the session cookie and the SSE stream
straight from uvicorn. It drives the conformance fixtures (`tool_call`, `approval_approve`,
`approval_deny`, `step_mode`, `cancel_running`, `guardrail_block`) through the header menu, plus
reconnect, reload, history, replay, New session and the inspector, and fails a test when any
event breaks `spec/events.schema.json`. Playwright stops the server when the run ends.
`HARNESSWALK_SCREENSHOTS=1` (`make screenshots`) also writes `docs/screenshots/*.png`.

## Environment

| Variable | Where | Meaning |
|---|---|---|
| `HARNESSWALK_API_URL` | Vite dev server | Where the dev proxy sends `/v1` and `/healthz`. Default `http://127.0.0.1:8000` |
| `HARNESSWALK_E2E_PORT` | `test:e2e:live` | The scratch port of the API the live suite starts. Default 8765 |
| `VITE_HARNESSWALK_MOCK` | build time | `1` runs the UI on the in-browser mock instead of the API |
| `VITE_HARNESSWALK_MOCK_STEP_MS` | build time | Milliseconds between mock events, default 140. The `?mockStepMs=` query overrides it |
| `VITE_HARNESSWALK_API_URL` | build time | API origin when the UI is not served by the API or the proxy. Default same origin |
| `VITE_HARNESSWALK_VALIDATE` | build time | `1` validates every event against the schema in a production build too (dev and mock builds always do) |

Per-viewer conveniences (theme, view, collapsed groups, the session id and the messages this
browser sent) live in `localStorage` under `harnesswalk.*`. The app works without storage.

## Mock mode

`VITE_HARNESSWALK_MOCK=1` swaps the transport, not the client. `src/mock/server.ts` answers
`fetch` and provides an `EventSource` class, so the same HTTP client, stream client and reducer
run as against the real API. It:

- serves `GET /v1/diagram` from `spec/diagram/diagram.json`, marking a node implemented when it
  appears in a recorded stream;
- seeds session `sess_demo` with the three recorded turns, so history and replay work at once;
- plays `plain_answer`, `tool_call` or `approval_pause` for a new message (picked by the fixture
  menu, else by the words in the message), with fresh ids and timestamps, the reply split into
  `message_delta` events, breakpoints in step mode, approve and deny, cancel, and disabled nodes;
- serves the real Python source, config, prompt and skill files from the repo for the inspector.

The mock and the schema validator are dynamic imports behind build-time constants, so a
production build contains neither.

## How the diagram stays in step with the spec

- The UI never hard-codes nodes or edges. It draws whatever `GET /v1/diagram` returns, and the
  API generates that from its node registry merged with `spec/diagram/diagram.json`.
- Layout is computed from the diagram: `layout` hints when a node has them, otherwise the runtime
  planes are columns in `groups[].order`, the ops plane is a row beneath, and dagre orders each
  plane along `walk_order`. Expert nodes sit inside their `parent`; an expert edge that `refines`
  a simple edge replaces it in the expert view and lights it in the simple view.
- Events are validated at runtime against `spec/events.schema.json` in dev and mock builds; a
  badge in the header counts failures.
- Mock mode, the tests and the validator read `spec/` directly (`?raw` imports), never a copy.
  A spec change that breaks the UI breaks `pnpm test`.

## Layout of the code

```
src/api        types, HTTP client, SSE stream client, schema validator
src/state      the reducer (events to state), the Zustand store, persistence
src/diagram    layout, React Flow nodes and edges, stats and memory badges
src/chat       chat panel, composer, approval card, step controls
src/inspector  node drawer: overview, config or prompt or records, code, events
src/replay     replay bar
src/mock       in-browser mock API over spec/examples
src/app        shell, header, bootstrap
e2e, e2e-live  Playwright suites (mock build, live API)
```
