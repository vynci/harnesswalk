# Harnesswalk web

The web UI for Harnesswalk: a chat on the left and a live diagram of the harness on the right.
Send a message and watch it walk through the nodes. Click a node to read its lesson, its config or
prompt, its code and its events. Turn on step mode to stop at every node, or replay a past turn
with a slider. The LLM Ops nodes run the harness's lifecycle: edit a skill or a config, run the
golden set from Eval and see what regressed, ship the working set from Release and roll it back,
and query stored turns with their receipts from Trace. Each node has a chapter in the Guide (the
header's Guide button, or Open in Guide in the inspector), which walks them in order, with a short
check at the end of each.

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
                   # the inspector's Trace status per event, tool calls read from the stream,
                   # turn facts (guardrails, budgets, citations, memory, placeholders), payload
                   # shapes, the drift rule (no node id literals in src/), and P4: line diffs,
                   # ops roles from the diagram, chat markers, the eval and receipt text, replay
                   # step-over, the mock's ops routes and ops stream, and the store's ops actions;
                   # P5: the lesson parser (sections, the checks, code links; every chapter in
                   # lessons/ must parse), heading slugs, link kinds, the chapter outline, the
                   # questionnaire state, the progress store, the mock's lesson routes and the
                   # pack prompts
pnpm test:e2e      # Playwright against a mock build (Chromium only): smoke.spec.ts, ops.spec.ts
                   # for P4 (the gate: edit a skill, run golden, see the regression; release,
                   # roll back, see the old version; feedback, step-over, the trace query), and
                   # guide.spec.ts for P5 (the Guide: chapter list, Open in Guide, a code walk
                   # link, the three checks, disable and try)
pnpm lint          # ESLint, then tsc -b
pnpm test:e2e:live # Playwright against the real API with the fake provider, see below
```

`pnpm exec playwright install chromium` once before the first e2e run. The mock suite serves its
build on port 4179 and reuses a server already there; `HARNESSWALK_MOCK_PORT=4189 pnpm test:e2e`
picks another port, so two checkouts never test each other's build. From the repository root
the same runs are `make web-test`, `make web-e2e-mock` and `make e2e`.

The live suite needs no running API. `playwright.live.config.ts` builds the app into
`dist-e2e/` with the schema validator on (`pnpm build:e2e`), starts
`uv run harnesswalk serve --mode dev --provider fake` on a scratch port (`HARNESSWALK_E2E_PORT`,
default 8765) with its stores in a temporary directory, and lets the API serve that build, so
the tests cover the production path: static files, the session cookie and the SSE stream
straight from uvicorn. It drives the conformance fixtures (`tool_call`, `approval_approve`,
`approval_deny`, `step_mode`, `cancel_running`, `guardrail_block`) through the header menu, plus
reconnect, reload, history, replay, New session and the inspector, and fails a test when any
event breaks `spec/events.schema.json`. `e2e-live/p3.spec.ts` walks Phase 3: citations
(`trailhead_return`), memory write and read-back (`remember_fact`), PII (`pii_redacted`), a
tool-call budget (`three_lookups` as tenant `e2e-budget`, sent with `X-Harnesswalk-Tenant`), the
audit chain, step mode through the stores and the writer, and dev turn options. Two tests start
a harness of their own (`e2e-live/helpers.ts`, ports `HARNESSWALK_E2E_PORT` + 1 and + 2): one
SIGKILLs and restarts it to show an approval that survives a restart and a turn closed
`restarted`, the other holds proposals at the review gate (`e2e-live/profile-review.yaml`). The
suite's server runs with `e2e-live/profile.yaml`, which only adds the budget tenant.
`e2e-live/p4.spec.ts` walks the Phase 4 gate against the real ops API (edit the `returns`
skill, run golden, see the regression on the Eval node and in the run detail, open the case in
replay, release, edit again, roll back, and the next turn runs on the old version), then a
release made with `harnesswalk release create` arriving live, feedback across a reload and from
another tab, step-over, the trace query with eval case turns, and `restart_keeps_schema` on a
second server it kills and restarts. P4 tests first cancel any turn earlier tests left open,
because edits are refused while one runs. Playwright stops the server when the run ends.
`HARNESSWALK_SCREENSHOTS=1` (`make screenshots`) also writes `docs/screenshots/*.png` (with
`eval-run.png` and `release-diff.png`); the
side-by-side `edges-before-after.png` puts the kept `edges-before.png` (the old routing, from the
owner's feedback) beside a fresh capture.

## Environment

| Variable | Where | Meaning |
|---|---|---|
| `HARNESSWALK_API_URL` | Vite dev server | Where the dev proxy sends `/v1` and `/healthz`. Default `http://127.0.0.1:8000` |
| `HARNESSWALK_E2E_PORT` | `test:e2e:live` | The scratch port of the API the live suite starts. Default 8765 |
| `HARNESSWALK_MOCK_PORT` | `test:e2e` | The port the mock build is served on. Default 4179 |
| `VITE_HARNESSWALK_MOCK` | build time | `1` runs the UI on the in-browser mock instead of the API |
| `VITE_HARNESSWALK_MOCK_STEP_MS` | build time | Milliseconds between mock events, default 140. The `?mockStepMs=` query overrides it |
| `VITE_HARNESSWALK_API_URL` | build time | API origin when the UI is not served by the API or the proxy. Default same origin |
| `VITE_HARNESSWALK_VALIDATE` | build time | `1` validates every event against the schema in a production build too (dev and mock builds always do) |
| `HARNESSWALK_MOCK_SHOTS` | `test:e2e` | A directory: `e2e/screenshots.spec.ts` writes review screenshots of the P4 panels from the mock build there, light and dark. Skipped when unset |

Per-viewer conveniences (theme, view, collapsed groups, the telemetry switch, the pane sizes, the
session id, the messages this browser sent and the chat's save and release markers for that
session, and the lesson progress in `harnesswalk.lessons`) live in `localStorage` under
`harnesswalk.*`. The app works without storage.

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
- serves the real Python source, config, prompt and skill files from the repo for the inspector
  (`vite.config.ts` lets the dev server read `lessons/`, `harnesswalk/`, `config/` and
  `datasets/_starter/` for `pnpm dev:mock`);
- P4 (`src/mock/ops.ts`, the real API's shapes from `spec/contracts/http_api.md`): a working set seeded from the pack files with
  versions and unified diffs, YAML checked on save, 409 while a turn runs; releases `r1`, `r2`...
  with a hash chain and an append-only rollback; a seeded golden run and twelve golden cases over
  the three recorded streams (a case regresses when the skill it depends on loses the tool it
  teaches; its turn lives in the run's scratch tenant, so `GET /v1/turns/{id}` is 404 and its
  `events_url` replays it, as on the real API), progress on the run's event stream, the judge
  suite answering 422 `judge_unavailable` on the fake provider; feedback, with a Good and judge
  scores on the first seeded turn; the trace query with receipts and `kind=eval`; the tenant's ops
  stream (`GET /v1/events`); `inspector.artifact` on every artifact-backed inspector; step-over
  that runs past expert children.
- P5 (`src/mock/lessons.ts`, the shapes of `harnesswalk/control/http_lessons.py`): the three
  lesson routes from the real chapter files in `lessons/`: the front matter parsed with `yaml`,
  title, hook, words, `try`, prev and next, and the code links with their lines (found by
  indentation for `Class.method`) and the node whose code peek is that file. A chapter that is
  not there answers 404 `lesson_not_found`, a file the chapter does not link to 404
  `source_not_found`.
- P6: a message asking for a `table` or `markdown` gets the markdown reply of
  `src/mock/markdown.ts` (headings, lists, a table, code, a link). `?mode=public` serves the
  public site instead of dev: `capabilities.public` with Claude Sonnet out of today's budget
  (`&budget=spent` for both Claude models, the default moving to the fake model; `&budget=midturn`
  for a first Claude turn that falls through mid-turn, after which the budget reads as spent),
  no seeded history, the model allowlist (400), a `fixture` only on the fake model (400
  otherwise), 409 `budget_exhausted` for an unavailable model sent without the provider key
  header, a 429 with
  `Retry-After` once the requests left this hour run out (`&mockRemaining=0` for the first
  turn), and a sandbox session of `&mockTtl=` seconds (default 7200) after which every turn and
  session is gone.

The mock and the schema validator are dynamic imports behind build-time constants, so a
production build contains neither (nor the chapters the mock bundles). The diagram (React Flow), the inspector and shiki are their
own chunks too, so the main chunk stays near 530 KB.

## Components

The UI is [shadcn/ui](https://ui.shadcn.com) on **Radix** (`radix-ui`), and Radix only: no
`@base-ui/react`. The components are source files in `src/components/ui/`, owned by this repo;
change them freely. The whole app is on one style, **`radix-rhea`**: the soft, filled look of
shadcn's [AI Elements](https://ui.shadcn.com/docs/ai-elements) pages. `components.json` names
it, with the `@/components/ui`, `@/lib/utils`, `@/hooks` aliases. The `shadcn` CLI is a pinned
dev dependency, so `pnpm exec shadcn` is the same version for everyone.

### Adding a component

- **Any component** (a primitive or an AI part: Trace, Message, Bubble, Marker, Message
  Scroller, Code Block, Input Group, Item, Empty, Hover Card, Attachment, Questionnaire...):
  `pnpm exec shadcn add <name>` resolves against `radix-rhea` because `components.json` says so.
  The explicit form is `pnpm exec shadcn add https://ui.shadcn.com/r/styles/radix-rhea/<name>.json`.
- **With the shadcn MCP server**: `view_items_in_registries` shows an item; its search covers the
  `new-york` registry only, so view the `radix-rhea` JSON above for the code you will get.
- **After every add**, check `git diff`. The registry writes `import { cn } from "cn"` and adds
  the `cn` package (and `next-themes` for Sonner): change the import to `@/lib/utils`,
  `pnpm remove cn next-themes`, and pin any new dependency to an exact version. An add with
  `--overwrite` also rewrites the files below; put the local changes back.
- **AI Elements patterns** (Chat, Prompt Input, Message, Tool, Confirmation, Sources, Inline
  Citation, Context, Suggestion, Reasoning, Plan, Queue, Task, Subagent, Terminal, Test
  Results, Checkpoint, File Tree) are not registry items: they are compositions you copy from
  the pattern page into the app, as `src/chat/` does. The map below says which file holds which.

`src/index.css` imports `shadcn/tailwind.css` for the `data-open` / `data-closed` variants, the
`shimmer` utility (streaming text), `scroll-fade` and `no-scrollbar`. Local changes to generated
files, kept on purpose: `sonner.tsx` takes the theme as a prop (no next-themes), `badge.tsx` has
a `kind` variant for node kinds, `scroll-area.tsx` lays its viewport out as a block,
`slider.tsx` puts the `aria-label` on the thumb (the element with role slider),
`lib/highlighter.tsx` loads shiki itself lazily with only the five languages the inspector
shows, `questionnaire.tsx` (P5) imports `cn` from `@/lib/utils`, uses `font-medium` on the title
instead of `font-semibold` and `text-xs` on the choice shortcut instead of 0.625rem, and
`sheet.tsx` (P5) imports `cn` from `@/lib/utils`. Adding the Questionnaire offers to overwrite
`button.tsx`: answer no.

### Tokens

Every colour is a CSS variable in `src/index.css`, light in `:root` and dark in `.dark`: the
shadcn set (`--background`, `--card`, `--primary`, `--muted`, `--border`, ...) and the diagram's
channels (`--plane-*`, `--state-*`, `--kind-*`, `--edge-*`). `@theme inline` maps them to
Tailwind colours, so components write `bg-card`, `text-state-waiting` or `text-kind-store`,
never a hex value. A restyle is an edit to that one file; `colors.test.ts` checks the contrast
of the diagram tokens in both themes.

The brand (P6, the owner's palette) sets `--primary` `#0ea47a` (hover `--primary-hover`
`#0a7557`; in dark mode the primary is `#0a7557`), `--ring` `#12d39d`, `--accent` a mint wash
of it, `--border`, `--input` and `--muted` `#e6e6e6`, `--foreground` and the dark background
`#111111`, the light background `#ffffff`, `--state-active` (the lit node's glow and the active
edge's glow: `#0a7557` in light mode, where `#12d39d` falls under 3:1, `#12d39d` in dark) and
`::selection`. `--brand` colours the header mark (`src/components/logo.tsx`, the same path as
`public/favicon.svg`). The brand green is kept for the active and done states (`--state-done`
is the same green), so a lit node pops on any plane; no plane uses it.

The four planes are one family in oklch: one hue per plane and four roles from it, each role at
one lightness and chroma for all four planes (`-fill` the backdrop tint, `-border` the dashed
outline, `-ink` the label and lit store badges at 4.5:1 on the fill, and the bare `--plane-*`
the request-edge stroke at 3:1 on the canvas and every fill). The light fills sit a step under
the bone node surface so the cards stay lifted. `colors.test.ts` checks the family rule and the
contrast. A second palette (candidate B, `[data-planes='b']` in `index.css`) is there for review
only; the comment above it says how to ship it or drop it.

Radii are one scale in `index.css` (`@theme`): `xs` 4px chips and badges, `sm` 6px inputs and
small buttons, `md` 8px buttons and menu rows, `lg` 12px cards, nodes, planes, popovers and
menus, `xl` 16px panels and dialogs, `full` only for switches, sliders, progress and avatars.
Nested surfaces follow the concentric rule (inner = outer minus the gap) where the gap is
tighter than the outer radius; the comment in `index.css` lists each nesting, and
`src/test/radius.test.ts` fails on any `rounded-*` class off the scale.

### Public mode and private-only controls (P6)

`useFeature(name)` (`src/state/hooks.ts` over `src/state/features.ts`) returns `{enabled,
reason}` for `artifact_edit`, `uploads`, `memory_review`, `evals`, `judge`, `release`,
`disable_nodes`, `diagnose` and `byo_key_ui` (the key button). In public mode every one is disabled with the contract's reason
("Editing needs a private workspace. Coming later."), and the control stays on screen with
`aria-disabled` and a Tooltip on its wrapper, never hidden; read-only views (trace, replay,
audit, the releases list, lessons, packs) stay live. The composer's Model Selector
(`chat/ModelPicker.tsx`, the same one in every mode) starts with Auto, preselected: the turn
names no model and the router decides, and the hint under it says what that is now (the alias
`capabilities.public.models` flags `default`). The concrete models follow by provider,
unavailable ones disabled with the reason as a tooltip ("Back at 00:00 UTC (7:00 PM your time)"
when the shared budget is used up; never a key suggestion in public mode). While the next turn
runs on the fake model a Fixture select sits beside it (`public.fixtures`: the session pack's
own, then the shared ones, the server's default first). When the budget runs out a Claude pick
moves to the fake model with a toast, and a slim banner above the composer says so with the
reset time until the budget is back (closable for the tab's session). The key button is
disabled in public mode ("Bring your own key arrives with accounts. Coming soon."); where it
works (private mode) it holds the key in memory only and sends it as
`X-Harnesswalk-Provider-Key` on each turn. The header's session chip counts down
`session_expires_at`, and its popover shows the session's totals with the requests left this
hour, the step cap and the token cap (the status bar's budget readout shows the caps before the
first turn). Capabilities are read again after every
turn and every 60 s while the tab is visible.
Replies and knowledge documents render as GitHub-flavoured markdown (`src/components/markdown.tsx`:
react-markdown and remark-gfm, fenced code on the Code Block, links in a new tab, raw HTML
dropped), the chat's citation chips and redaction placeholders included.

### The style rules

What makes AI Elements look minimal, applied across the app so the chat, inspector, header,
legend and diagram chrome read as one system:

- **Monochrome.** Status is text colour (`text-destructive`), `opacity-60` for not started, the
  `shimmer` for running (off under reduced motion) and the tense of the title. Primary fill only
  on the send button and the main action of a prompt (Approve, Next). Plane, kind and state
  colours appear on icons, diagram strokes, plane groups and small chips, never as coloured or
  dashed borders around chat or inspector surfaces. A diagram node is a neutral card; only an
  active, waiting or failed one gets a 1 px ring in its state colour.
- **Borders.** Trace lines have none; open content gets one hairline on the left. Bubbles are
  fills. Items are a 1 px outline. Raised surfaces (cards, menus, hover cards) use
  `ring-1 ring-foreground/5` and a shadow.
- **Radii.** One scale for the components and the diagram (see Tokens): badges and chips
  `rounded-xs`, inputs and small buttons `rounded-sm`, buttons and menu rows `rounded-md`,
  cards, nodes, planes, menus, popovers, items, code blocks and bubbles `rounded-lg`, the
  composer and dialogs `rounded-xl`; inner radius = outer minus the gap where they nest.
- **Type.** `text-sm` for body and lines, `text-xs` as the floor: no 10 or 11 px text anywhere.
  `font-medium` for titles only, no `font-semibold`, no uppercase eyebrows; `tabular-nums` for
  numbers; mono only for code, ids, paths and commands.
- **Icons.** lucide at `size-4`, `size-3` in `xs` buttons; one icon per line; chevrons rotate.
- **Muted by default.** Trace lines, descriptions, footers and addons are muted; foreground is
  for what the reader acts on.

### AI Elements map

| AI Elements | Where | Notes |
|---|---|---|
| Chat (Message Scroller, Message, Bubble) | `chat/ChatPanel.tsx` | The person's turn is a muted Bubble on the right; the agent's reply has no bubble. One agent Message holds the turn's lines, prompts, reply, sources and notices |
| Prompt Input (Input Group) | `chat/Composer.tsx` | Plus on the left (`aria-disabled`, tooltip "Attach, coming later"; attachments are P6), dev Options, the Model Selector (Auto first) and the Fixture select on the fake model, the arrow-up send on the right that turns into Stop while a turn runs; Enter and Shift+Enter hints under it |
| Message actions | `chat/ChatPanel.tsx`, `chat/Feedback.tsx` | Replay, then Good and Bad: ghost icon actions with tooltips, `aria-pressed`, the pressed one filled. A press records the rating, then a Popover offers an optional note; the note and the online judge scores (muted chips, "faithfulness 0.92") sit beside them |
| Tool (Trace, Code Block) | `chat/ToolTrace.tsx` | One wrench line per call: running, waiting for approval, ran, denied, failed. The description is a short outcome and the duration; policy, why, budget, approval, sandbox chip and arguments open below |
| Confirmation (Item) | `chat/ApprovalCard.tsx` | "Run X?" with the action class, reason, countdown and arguments; Deny then Approve. Once decided it goes and the tool line above says how |
| Item | `chat/StepControls.tsx` | The step-mode pause: "Paused at X", Continue, Step over (runs past expert children to the next simple-view node), Next |
| Sources, Inline Citation (Hover Card) | `chat/Citations.tsx` | `[n]` chips with a hover card, click opens the document reader (a Dialog); "Cited N passages" starts closed |
| Empty, Suggestion | `chat/ChatPanel.tsx`, `chat/usePackPrompts.ts` | An empty chat: Empty in the thread, a row of suggested prompts above the input. The prompts are the active pack's once the API lists packs (`features.packs`, `GET /v1/packs`), else the `_starter` ones |
| Questionnaire | `lessons/CheckYourself.tsx` | A chapter's Check yourself: one question at a time, Previous question and Next question, letters as shortcuts; a choice says at once whether it was right and shows the explanation |
| Resizable, Sheet | `lessons/GuideDrawer.tsx` | The Guide: the chapters with their hooks and progress and Next chapter, or one chapter; docked under or beside the inspector, a Sheet on a narrow screen |
| Context (Popover, Progress) | `diagram/BudgetDetails.tsx`, `diagram/TurnStatusBar.tsx`, `chat/TurnReport.tsx`, `app/SessionChip.tsx` | A turn's report card: its facts, the release it ran on, a Progress bar per budget (tokens, cost, steps, policy) and the policy's sentence. The status bar's "{n}% budget" readout opens it for the turn shown in a Popover; each turn's row on the chat timeline opens it (a Collapsible); the session chip's popover holds the session's totals and the sandbox limits |
| Reasoning, Plan | `chat/Reasoning.tsx` | Mounted as empty slots. Neither the event schema nor the P4 contract has a reasoning or plan field yet, so they stay empty until one does |
| Marker | `chat/ChatPanel.tsx` | The working shimmer, system notices, and the restart checkpoint notice (`checkpoint.restored`) |
| Turn report rows (Collapsible) | `chat/TurnReport.tsx`, `state/turnReport.ts` | Each turn's divider: "Turn 2 · 04:39 PM", the line "answered · 1.65 s · 2349 tok · $0.0050" and a disclosure to the turn's report card; closed by default, the latest opens when it ends if the reader left the one before open (per session, in memory). A turn read back from the session history reports from its stored summary |
| Checkpoint (Marker, Button) | `chat/OpsMarker.tsx` | "Skill returns v2 saved", "Release r2 active", "Rolled back: release r1 active" after the turn they followed; Restore asks first (Confirmation), then saves the previous version again or rolls back to the release before |
| Test Results (Trace) | `inspector/EvalPanel.tsx` | "Ran 12 cases, 3 failed" with a regressions chip, then the cases (by case: each opens to expected against actual, its scores and a link to its turn in replay; by scorer: one suite per scorer, failing first). Regressions sort first and carry a chip; "was passed" says what changed |
| Trace Commit, Code Block diff | `inspector/ReleasePanel.tsx`, `components/diff-view.tsx` | A release's `diff_from_previous` as one commit line per artifact ("Skill order_status v1 → v2", +1 −1) opening to the diff on the Code Block's `added` and `removed` lines |
| Confirmation (Item) | `components/confirm-item.tsx` | Rollback and Restore: "Roll back to r1?", Cancel then Roll back |
| Code Block in edit mode | `inspector/ArtifactEditor.tsx` | The system prompt, each skill and the YAML configs: Edit opens a mono Textarea with Save and Cancel and a "Review the change" diff; Save shows the API's diff and a toast. Blocked with the reason in public mode, without `features.artifact_edit`, or while a turn runs |
| Trace (query) | `inspector/TracePanel.tsx` | The Trace node's query (session, node, status, since) and turn lines with receipt totals, opening to the per-node receipt Table and to replay |
| Trace | `inspector/EventsTab.tsx`, `chat/ToolTrace.tsx` | The inspector's Events tab is a Trace too; its icons keep the node kind colours |
| Resizable | `app/App.tsx` | The chat and diagram split, the docked inspector and the docked Guide, with the grip handle; sizes kept per viewer |

Later phases: Reasoning and Plan get data once the stream carries it; Task, Chain of Thought,
Terminal and Stack Trace are still unused; File Tree in P5 (the pack browser); Attachment behind
the plus button and the Model Selector for your own key in P6; Subagent in P7.

### Which part uses what

| App part | shadcn components |
|---|---|
| Header | Select (provider, dev only; the fixture select is in the composer), the pack picker (a Dropdown Menu, sized to the pack's name), a ghost Button for the Guide, a ghost icon Button for the theme (Sun in dark, Moon in light; it follows `prefers-color-scheme` until pressed), the session chip at the far right (a Badge for the mode and, in public mode, the countdown) opening a Popover (the session's end in words, the session id with Copy, the pack, New session, the session's totals, the sandbox limits, the release line), Tooltip on every unlabelled control |
| Notices | Sonner toasts (errors carry `role="alert"`), plus a toast when the event stream drops and when it reconnects |
| Chat | Message Scroller, Message, Bubble, Marker, Spinner, Empty, Button (suggestions, message actions), Tooltip |
| Tool calls and memory | Trace lines opening to a Code Block, a Badge for the sandbox; a Memory Trace line per `remember` proposal (written, waiting, rejected) |
| Citations and guardrails | Hover Card and Button (inline citations), Trace (Sources), Dialog (the document reader at the cited chunk, `GET /v1/knowledge/docs/{doc}`: a fixed-height panel whose DialogBody scrolls, a Toggle Group for its Document view, the original PDF in an `<object>` or the markdown with chunk rules, and its Chunks view); secondary Badges for input guardrails (redactions, injection score, warnings) and output guardrails (citations, restored); placeholders as `mark`, a Switch revealing the restored reply (dev only) |
| Composer | Input Group (Textarea, Buttons, Text), Dropdown Menu (Model Selector), Select (fixture), Textarea (`output_schema`, dev only), Kbd, Tooltip, Spinner; Switch (Step mode) on the hint line under the input, right-aligned |
| Approval, step controls | Item (Media, Content, Title, Description, Actions, Footer), Code Block, Badge, Button |
| Inspector | A Resizable pane docked beside the diagram (not a modal Sheet, so step mode keeps it open) with Tabs and Scroll Area; the LLM Ops tabs (Runs, Releases, Turns) with Input, Select, Toggle Group, Badge, Table and the Confirmation Item; artifact edits with Textarea, Input and the Code Block diff; Badge (kind, view); Events as a Trace opening to the event JSON in a Code Block; Code and config in a Code Block with line numbers; prompts in `Prose`; records in a Table; payload views picked by shape (`src/inspector/shapes.ts`): the audit chain with a Verify Button, store records with a scope Badge, the document list, memory writes as Cards with a diff and Accept/Reject, the context budget as Progress bars; Switch (disable next turn); Kbd (Esc) |
| Diagram | Nodes are Cards (CardTitle, CardDescription) on their own warm surface (`--node-surface`) with Badges for the kind chip, store badges, the off and not-yet tags and the regressions chip on the node that runs evals; the canvas controls are one Card with two Switches (Advanced view, Telemetry) and a Button opening the legend Dialog; the turn status bar is a Card with a budget readout that opens the turn shown's report in a Popover. The canvas, edges and layout stay React Flow |
| Guide | A Resizable pane (under the inspector, or beside it from 1680 px) or a Sheet (under 1024 px) with a Scroll Area: the chapter list, or one chapter in `Prose` (react-markdown, remark-gfm) with Buttons (try it, code walk links, Previous and Next chapter) and the Questionnaire |
| Replay bar | Card, Button Group (previous, play, next, step over), Slider, Tooltip with Kbd |
| Feedback | Popover (the optional note), Textarea, Badge (judge scores) |

App-level helpers on top of the primitives live in `src/components/`: `hint.tsx` (a Tooltip on
one control), `code.tsx` (a Code Block highlighted by shiki) and `prose.tsx` (shadcn has no
Prose component; it is the typography styles in one container).

## LLM Ops in the UI (P4)

The UI codes against `spec/contracts/http_api.md` ("P4 additions"); the integration pass checked
every route against a live harness. How it finds things without naming node ids:

- **Which node gets which panel** is read from the diagram's shape (`src/diagram/opsRoles.ts`):
  an LLM Ops node with a `release` edge into another plane ships artifacts (Releases); one with
  a `release` edge inside the ops plane gates them (Runs); any other ops store keeps turns
  (Turns). Expert children take their parent's panel. A node with a panel opens on that tab.
- **Which artifact a node edits** is what its inspector names, `inspector.artifact {kind, name,
  version, url}` (`GET /v1/nodes/{id}`): a config inspector for its file, each prompt item for
  the system prompt or its skill, and a code or model node whose settings are a config artifact
  (the context builder, the model layer), which gets a Config tab of its own. `GET /v1/artifacts`
  only adds who changed it last and the version turns are served. The editor shows the content
  stamp the turns record (`returns@b0e44e5b`), which a rollback brings back.
- **The tenant's ops stream.** `GET /v1/events` (opened at boot when the API offers evals,
  releases or feedback) carries every ops event of the tenant, from whichever process wrote it:
  `artifact_changed` and `artifact_released` place the chat markers (Checkpoint) after the last
  turn and refresh the status bar and the inspector, so a release made with the CLI or in
  another tab appears at once; `feedback_recorded` re-reads the turn it names, so a rating from
  another tab shows; `eval_run_*` drive the progress line and the chip on the node that runs
  evals for a run started anywhere. The event's own `node_id` gives the node a short pulse.
  Without the stream (an older API) the markers come from this browser's own responses, as
  before. Markers are kept per session in `localStorage`, one per change.
- **Status bar and chips.** `release r2`, or `working set (unreleased changes)` when
  `release.working_set_dirty`; the node that ships releases carries an `unreleased` chip then,
  and the node that runs evals a `done/total` chip during a run and a regressions chip after.
- **Eval runs.** "Run judge" (the judge suite and its model menu) shows only with
  `features.judge`. A regression is a case that failed or errored now and passed in the previous
  run, and `totals.failed` counts errors too: the run detail says both. A case opens to expected
  against actual, and to its turn in replay from the case's `events_url` (case turns live in the
  run's scratch tenant, so `GET /v1/turns/{id}` would 404); the chat header names the replay.
- **Trace query.** Kind (chat turns, or the case turns of your eval runs with `kind=eval`),
  session, node, status, since; each turn opens to its receipt per node and per model call.
- **Lists** read the API's envelopes (`{runs}`, `{pack, releases, verify, ...}`, `{pack,
  artifacts, release}`, `{turns, next_after}`).

## Lessons (P5)

The chapters are the markdown files in `lessons/` (`NN-<node_id>.md`, one per simple node in walk
order, then `NN-<parent>-inside.md` per expert group), served by `GET /v1/lessons`,
`GET /v1/lessons/{slug}` and `GET /v1/lessons/{slug}/source?path=`
(`harnesswalk/control/http_lessons.py`). Each diagram node names its chapter in `lesson_url`
(`lessons/02-input_guardrails.md`, or `lessons/16-memory-inside.md#semantic` for an expert node)
and says what it teaches in `lesson_summary`. The code is in `src/lessons/`.

- **A chapter** (`GuideChapter.tsx`, in the Guide). It fetches the chapter `lesson_url` names
  and renders it in `Prose` with react-markdown and remark-gfm; headings get GitHub slugs as ids
  (`slug.ts`, a small rehype plugin), so an expert node scrolls to its own section of the group
  chapter (no smooth scroll under reduced motion). The Guide draws the title and hook itself, so
  `parse.ts` takes them off the top, and it splits the markdown around Check yourself, which
  becomes the Questionnaire. A chapter the API does not serve yet (404 `lesson_not_found`) shows
  "This chapter is not written yet" with the node's summary. The inspector header keeps the
  node's one-paragraph `lesson` and adds "Open in Guide", which opens the Guide on its chapter.
- **Links** (`links.ts`, read relative to `lessons/`). A code walk link
  (`../harnesswalk/<path>#<Symbol>`) opens the node's inspector on the Code tab at the symbol's
  line from the chapter's
  `code_links`, highlighted: in the node's own code peek when the link's `node_id` is the
  inspected node, otherwise in that file from the lesson source route, with "Back to" the node's
  own file (a stacked inspector grows to make room). A link to another chapter opens it in the
  Guide; `#section` scrolls; any other repo path
  opens on GitHub (`REPO_BLOB_URL`) and an https link as is, both in a new tab with a hidden
  "(opens in a new tab)".
- **Try it.** A chapter's front matter `try` (`fixture`, `prompt`, `disable`) becomes a button
  under the hook. With `disable` (every node there `can_disable`, mode dev or private,
  `features.disable_nodes` not false) it is "Disable <Label> and try it": it turns those nodes
  off for the next turn (the same state as the inspector's switch), picks the fixture in the
  composer's Fixture select (dev only), puts the prompt in the composer and focuses it. The reader
  presses send. With a prompt and no `disable` it is a plain "Try it" (dev only when it needs a
  fixture). The composer's text lives in the store (`draft`) for this.
- **Check yourself** (`CheckYourself.tsx`, state in `quiz.ts`). Three questions, each
  `### N. Question`, a task list of three choices with one `- [x]`, then `Answer:` and the
  explanation. One at a time in the Questionnaire; a choice shows at once whether it was right
  and the explanation; a choice can be changed. Answers stay in this browser only.
- **The Guide** (`GuideDrawer.tsx`, its own chunk; `GuideButton.tsx` in the header opens and
  closes it, state in `src/state/panels.ts`). Not modal: from 1024 px it is a Resizable pane
  under the inspector in the right column (the inspector's summary cut to two lines), from
  1680 px a pane beside it, and under 1024 px a Sheet with its own close. Esc or the close
  button closes it and focus goes back to the Guide button. It shows the chapter list in order
  (`outline.ts`: the diagram's `lesson_url`s in walk order, then the groups, marked "inside",
  merged with what `GET /v1/lessons` serves, so a chapter not written yet still has its row),
  each row with the title, the hook and its progress in words, or one chapter with "All
  chapters" back to the list. A chapter from the list selects its node on the canvas (a group
  chapter switches to the advanced view and selects its first covered node) and focus moves to
  the chapter's title; Next chapter (in the list and at the end of a chapter) does the same for
  the next one. While the Guide is open it follows the node picked on the canvas to its chapter.
- **Progress** (`progress.ts`, `useProgress.ts`) is kept per viewer in `localStorage` as
  `harnesswalk.lessons`: `{"<slug>": {"read": true, "answers": {"0": 1}, "checked": true}}`.
  A chapter is read once it was opened in the Guide and checked once every question has an
  answer. Storage failures are ignored, as everywhere.

## Reading the diagram: the colour system

Colour carries four separate meanings, each on its own channel, so they never compete. The
tokens live in `src/index.css` (light and dark), and `src/diagram/colors.test.ts` checks their
contrast in both themes: 4.5:1 for text, 3:1 for state icons and rings, kind icons and edge
strokes.

| Channel | Drawn as | Values |
|---|---|---|
| Plane | group box background, dashed border and header (the node inside is a neutral card on `bg-node-surface`, a warm bone paper in light mode and a slightly warm lift of the card in dark, with `shadow-sm` and `ring-1 ring-foreground/5`) | one oklch family at equal lightness and chroma: control warm (hue 65), context blue (258), execution green (152), LLM Ops violet (325) |
| Node kind (purpose) | icon chip (the Badge `kind` variant) after the title; the kind word stays for screen readers | code slate, config amber, prompt violet, store teal, queue orange, model indigo |
| Node state | the status icon, plus a 1 px ring in the state colour while active, waiting or failed | idle neutral grey, active brand green with a pulsing ring, waiting amber (dashed ring), done brand green (check icon only), error red (ring), skipped grey and faded, not yet built muted with a "not yet" pill |
| Edge kind | stroke colour and dash | request solid in the source plane's colour, reply (a request back against walk order) a lighter tint of it, data solid teal, control dashed amber, telemetry dotted thin grey, release dashed purple |
| Edge state | opacity and motion | idle 40 percent, flowing full opacity with moving dashes and a dot, used this turn 70 percent |

Only an active, waiting or failed node gets a ring, so while a turn runs the one active
node stands out against the quiet done ones. An active edge shows its label beside the line,
never on it, so the moving dot does not pass under it. With reduced motion there is no pulse,
moving dash or dot; states still show by colour and weight.

The canvas controls (one card at the bottom right of the diagram) hold the Advanced view switch,
the Telemetry switch and the Legend button. Legend opens a Dialog with one row per channel:
states, node kinds, edge kinds; Esc or the close button shuts it and focus returns to the
button. It starts closed and nothing about it is remembered. Toasts appear top left, under the
chat's header, clear of the app header, the composer, the canvas controls and the inspector.
The Telemetry switch matters in the simple view only: by default the edges to and from LLM Ops
are hidden and one dotted bus runs from the execution plane to Trace; the switch draws them all,
and hovering a node draws its own. The advanced view always draws every edge, so there the switch
shows on and disabled.

## How the diagram stays in step with the spec

- The UI never names a node id outside the memory badge node: `src/test/drift.test.ts` fails
  on a node id string literal in `src/` beyond a short, explained list of homonyms (`approval` the pause reason, `release` the edge kind, `session`
  the storage key, `artifacts` the list key of `GET /v1/artifacts`). The LLM Ops panels are
  picked by the diagram's shape, not by id (see LLM Ops in the UI). Turn facts, inspector views and stats are found by summary keys and payload
  shapes.
- The UI never hard-codes nodes or edges. It draws whatever `GET /v1/diagram` returns, and the
  API generates that from its node registry merged with `spec/diagram/diagram.json`.
- Layout is computed from the diagram: `layout` hints when a node has them (`layout.expert` in
  the advanced view), otherwise the runtime planes are columns in `groups[].order`, the ops plane
  is a row beneath, and dagre orders each plane along `walk_order`. Expert nodes sit inside their
  `parent`; an expert edge that `refines` a simple edge replaces it in the advanced view and lights
  it in the simple view.
- Edges are routed from the spec too. An edge's `layout` hint names the side each end uses
  (`handles`), its bend points (`waypoints`) and which side of the line its label sits on; a port
  sits where the nearest bend point projects onto its side, so a lane is written once. An edge
  without hints, or whose nodes moved (most of the advanced view), goes through a deterministic
  orthogonal router (`src/diagram/route.ts`: A* over a sparse grid around the node boxes, with
  costs for bends, crossings and, steeply, running along another edge). `routes.test.ts` checks
  that no two edges overlap on one line and no edge runs through a node.
- Events are validated at runtime against `spec/events.schema.json` in dev and mock builds; a
  badge in the header counts failures.
- Mock mode, the tests and the validator read `spec/` directly (`?raw` imports), never a copy.
  A spec change that breaks the UI breaks `pnpm test`.

## Layout of the code

```
src/api        types, HTTP client, SSE stream clients (turns, eval runs), schema validator
src/state      the reducer (events to state), turn facts read from summary keys, the Zustand
               store, persistence, ops helpers (markers, edit blocks, artifact lookup)
src/diagram    layout, React Flow nodes and edges, stats and memory badges, ops roles
src/chat       chat panel, composer, approval confirmation, step controls, tool calls, citations,
               the document reader, guardrail badges, reasoning and plan slots, feedback, markers
src/inspector  node drawer: overview, config or prompt or records, code, events (Trace),
               and the LLM Ops panels (eval runs, releases, trace query) and the artifact editor
src/lessons    the Guide (its button, the chapter list, a chapter), Check yourself, and their pure parts: the chapter
               parser, heading slugs, link kinds, the outline, the checks and the progress store
src/components shadcn/ui in ui/, plus hint, code, prose, the diff view and the confirm item
src/lib, hooks cn() and the shiki highlighter, from the shadcn CLI, and line diffs
src/replay     replay bar and its step-over
src/mock       in-browser mock API over spec/examples, the P4 ops routes and the P5 lesson routes
src/app        shell, header, bootstrap
e2e, e2e-live  Playwright suites (mock build, live API)
```
