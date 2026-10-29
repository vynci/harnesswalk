# harnesswalk web

The web UI for harnesswalk: a chat on the left and a live diagram of the harness on the right.
Send a message and watch it walk through the nodes. Click a node to read its lesson, its config or
prompt, its code and its events. Turn on step mode to stop at every node, or replay a past turn
with a slider.

Stack: React 19, Vite, TypeScript (strict), Tailwind 4, shadcn/ui on Radix (see
[Components](#components)), React Flow (`@xyflow/react`, attribution kept), shiki (code
highlighting), Zustand, dagre (fallback layout), Vitest and Playwright. Node 24 and pnpm 10.

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
pnpm test          # Vitest: reducer, layout, edge routes, colour contrast, memory badges, stream client,
                   # the inspector's Trace status per event, tool calls read from the stream
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
`HARNESSWALK_SCREENSHOTS=1` (`make screenshots`) also writes `docs/screenshots/*.png`; the
side-by-side `edges-before-after.png` puts the kept `edges-before.png` (the old routing, from the
owner's feedback) beside a fresh capture.

## Environment

| Variable | Where | Meaning |
|---|---|---|
| `HARNESSWALK_API_URL` | Vite dev server | Where the dev proxy sends `/v1` and `/healthz`. Default `http://127.0.0.1:8000` |
| `HARNESSWALK_E2E_PORT` | `test:e2e:live` | The scratch port of the API the live suite starts. Default 8765 |
| `VITE_HARNESSWALK_MOCK` | build time | `1` runs the UI on the in-browser mock instead of the API |
| `VITE_HARNESSWALK_MOCK_STEP_MS` | build time | Milliseconds between mock events, default 140. The `?mockStepMs=` query overrides it |
| `VITE_HARNESSWALK_API_URL` | build time | API origin when the UI is not served by the API or the proxy. Default same origin |
| `VITE_HARNESSWALK_VALIDATE` | build time | `1` validates every event against the schema in a production build too (dev and mock builds always do) |

Per-viewer conveniences (theme, view, collapsed groups, the telemetry switch, the session id and
the messages this browser sent) live in `localStorage` under `harnesswalk.*`. The app works without storage.

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
production build contains neither. The diagram (React Flow), the inspector and shiki are their
own chunks too, so the main chunk stays near 570 KB.

## Components

The UI is [shadcn/ui](https://ui.shadcn.com) on **Radix** (`radix-ui`), and Radix only: no
`@base-ui/react`. The components are source files in `src/components/ui/`, owned by this repo;
change them freely. `components.json` keeps the `new-york` style and the `@/components/ui`,
`@/lib/utils`, `@/hooks` aliases. The `shadcn` CLI is a pinned dev dependency, so
`pnpm exec shadcn` is the same version for everyone.

### Adding a component

- **A primitive** (button, card, dialog, ...): `pnpm exec shadcn add <name>`. It resolves against
  the `new-york` registry, like the eight primitives the app started with.
- **An AI component** (Trace, Message, Message Scroller, Marker, Bubble, Code Block, Spinner,
  Attachment, Questionnaire): these ship only in the newer `radix-*` styles, so add them by URL
  from `radix-vega`, the style closest to new-york:
  `pnpm exec shadcn add https://ui.shadcn.com/r/styles/radix-vega/<name>.json`.
  An item whose own dependencies exist only there (Code Block needs `use-highlighted-code`)
  fails to resolve that way: set `"style": "radix-vega"` in `components.json` for that one
  `add`, answer no to overwriting `button.tsx`, then put `components.json` back.
- **With the shadcn MCP server**: `search_items_in_registries` / `view_items_in_registries` with
  registry `@shadcn` (new-york items), `get_add_command_for_items` for the CLI line; for the AI
  components, view the radix-vega JSON above and add by URL.
- **After every add**, check the diff. Current registries write `import { cn } from "cn"` and
  add the `cn` package (and `next-themes` for Sonner): change the import to `@/lib/utils`,
  `pnpm remove cn next-themes`, and pin any new dependency to an exact version.
- Patterns from shadcn's AI Elements pages (Tool, Sources, Reasoning, Task, Plan, Queue, Chain of
  Thought, Terminal, Stack Trace, Test Results) are not components: they are compositions of
  Trace and Code Block that you copy into the app, as `src/chat/ToolTrace.tsx` does.

`src/index.css` imports `shadcn/tailwind.css` for the `data-open` / `data-closed` variants, the
`shimmer` utility (streaming text) and `scroll-fade`. Local changes to generated files, kept on
purpose: `sonner.tsx` takes the theme as a prop (no next-themes), `badge.tsx` has a `kind`
variant for node kinds, `lib/highlighter.tsx` loads shiki itself lazily and only the five
languages the inspector shows.

### Tokens

Every colour is a CSS variable in `src/index.css`, light in `:root` and dark in `.dark`: the
shadcn set (`--background`, `--card`, `--primary`, `--muted`, `--border`, ...) and the diagram's
channels (`--plane-*`, `--state-*`, `--kind-*`, `--edge-*`). `@theme inline` maps them to
Tailwind colours, so components write `bg-card`, `border-state-waiting` or `text-kind-store`,
never a hex value. A restyle is an edit to that one file; `colors.test.ts` checks the contrast
of the diagram tokens in both themes.

### Which part uses what

| App part | shadcn components |
|---|---|
| Header | Select (provider, fixture), Switch (step mode), Toggle Group (Simple/Expert, theme), Dropdown Menu (session: new, copy id), Badge (mode), Tooltip on every unlabelled control |
| Notices | Sonner toasts (errors carry `role="alert"`), plus a toast when the event stream drops and when it reconnects |
| Chat transcript | Message Scroller (auto-scroll, scroll-to-latest button), Message and Bubble (user, assistant), Marker (turn dividers, the shimmering "walking the harness" state, system notices for blocked, limit, error, cancelled), Spinner |
| Tool calls in the chat | Trace (one line per call: running, waiting for approval, ran, denied, failed) opening to a Code Block; Sources (Trace) waits for the P3 citations |
| Approval | Card with an Alert (why it needs a person), the arguments in a Code Block, Buttons and the countdown |
| Step controls, composer | Button Group (Next, Continue); Textarea, Button and Kbd (Enter, Shift+Enter) |
| Inspector | A docked panel (not a modal Sheet, so step mode keeps it open) with Tabs and Scroll Area; Badge (kind, view); Events as a Trace (running, pending, error per event, opening to the event JSON in a Code Block); Code and config in a Code Block with line numbers; prompts in `Prose`; records in a Table; Switch (disable next turn); Kbd (Esc) |
| Diagram | Nodes are Cards (CardTitle, CardDescription) with Badges for the kind chip, store badges and the off and not-yet tags; the legend is a Card with Badge rows and a Switch; the turn status bar is a Card. The canvas, edges and layout stay React Flow |
| Replay bar | Card, Button Group (previous, play, next), Slider, Tooltip with Kbd |

App-level helpers on top of the primitives live in `src/components/`: `hint.tsx` (a Tooltip on
one control), `code.tsx` (a Code Block highlighted by shiki) and `prose.tsx` (shadcn has no
Prose component; it is the typography styles in one container).

## Reading the diagram: the colour system

Colour carries four separate meanings, each on its own channel, so they never compete. The
tokens live in `src/index.css` (light and dark), and `src/diagram/colors.test.ts` checks their
contrast in both themes: 4.5:1 for text, 3:1 for accents, icons and edge strokes.

| Channel | Drawn as | Values |
|---|---|---|
| Plane | group box background, dashed border and header | control amber, context blue, execution green, LLM Ops purple |
| Node kind (purpose) | icon chip (the Badge `kind` variant) after the title; the kind word stays for screen readers | code slate, config amber, prompt violet, store teal, queue orange, model indigo |
| Node state | left accent bar plus the status icon | idle neutral grey, active blue with a pulse, waiting amber (dashed border), done green (bar and icon only, border stays the plane's), error red, skipped grey dashed bar, not yet built muted and dashed |
| Edge kind | stroke colour and dash | request solid in the source plane's colour, reply (a request back against walk order) a lighter tint of it, data solid teal, control dashed amber, telemetry dotted thin grey, release dashed purple |
| Edge state | opacity and motion | idle 40 percent, flowing full opacity with moving dashes and a dot, used this turn 70 percent |

Only an active, waiting or failed node changes its border, so while a turn runs the one active
node stands out against the quiet done ones. An active edge shows its label beside the line,
never on it, so the moving dot does not pass under it. With reduced motion there is no pulse,
moving dash or dot; states still show by colour and weight.

The legend (bottom right of the diagram) has one row per channel: states, node kinds, edge kinds.
It collapses, and opens by default in the simple view. Its Show telemetry switch matters in the
simple view only: by default the edges to and from LLM Ops are hidden and one dotted bus runs
from the execution plane to Trace; the switch draws them all, and hovering a node draws its own.
The expert view always draws every edge.

## How the diagram stays in step with the spec

- The UI never hard-codes nodes or edges. It draws whatever `GET /v1/diagram` returns, and the
  API generates that from its node registry merged with `spec/diagram/diagram.json`.
- Layout is computed from the diagram: `layout` hints when a node has them (`layout.expert` in
  the expert view), otherwise the runtime planes are columns in `groups[].order`, the ops plane
  is a row beneath, and dagre orders each plane along `walk_order`. Expert nodes sit inside their
  `parent`; an expert edge that `refines` a simple edge replaces it in the expert view and lights
  it in the simple view.
- Edges are routed from the spec too. An edge's `layout` hint names the side each end uses
  (`handles`), its bend points (`waypoints`) and which side of the line its label sits on; a port
  sits where the nearest bend point projects onto its side, so a lane is written once. An edge
  without hints, or whose nodes moved (most of the expert view), goes through a deterministic
  orthogonal router (`src/diagram/route.ts`: A* over a sparse grid around the node boxes, with
  costs for bends, crossings and, steeply, running along another edge). `routes.test.ts` checks
  that no two edges overlap on one line and no edge runs through a node.
- Events are validated at runtime against `spec/events.schema.json` in dev and mock builds; a
  badge in the header counts failures.
- Mock mode, the tests and the validator read `spec/` directly (`?raw` imports), never a copy.
  A spec change that breaks the UI breaks `pnpm test`.

## Layout of the code

```
src/api        types, HTTP client, SSE stream client, schema validator
src/state      the reducer (events to state), the Zustand store, persistence
src/diagram    layout, React Flow nodes and edges, stats and memory badges
src/chat       chat panel, composer, approval card, step controls, tool calls (Tool, Sources)
src/inspector  node drawer: overview, config or prompt or records, code, events (Trace)
src/components shadcn/ui in ui/, plus hint, code and prose
src/lib, hooks cn() and the shiki highlighter, from the shadcn CLI
src/replay     replay bar
src/mock       in-browser mock API over spec/examples
src/app        shell, header, bootstrap
e2e, e2e-live  Playwright suites (mock build, live API)
```
