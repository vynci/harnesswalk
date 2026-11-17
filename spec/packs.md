# Dataset packs (P5, additive)

A pack is the content a harness runs with: a fictional business's system prompt, skills, tools,
documents, seed memory and golden set. The harness loads one pack at a time (`dataset` in
`config/harness.yaml`, or `HARNESSWALK_DATASET`). This file fixes the format; the reference
implementation is `harnesswalk/packs.py` and the CLI is `harnesswalk packs list|validate|ingest`.

## Layout

```
datasets/<id>/
  pack.yaml            required: the pack card (schema below)
  provenance.md        required: how the pack was made, licence, synthetic statement
  system_prompt.md     skill zero (procedural memory, always in context)
  skills/<name>.md     on-demand skills, front matter `name` (= file stem) and `description`
  skills/policy.md     optional (P2): the business rules in words, for the model to read
  tools.yaml           the pack's tools (spec/contracts/tool_registry.md)
  guardrails.yaml      optional: input-guardrail overrides (`pii`), patterns per pack
  facts.jsonl          seed facts {"key", "value", "entities"?}
  episodes.jsonl       seed events {"id", "ts", "kind", "text", "outcome"?, "entities"?}
  docs/                knowledge documents: markdown (.md) and PDF (.pdf)
  sources/<id>.md      the markdown a PDF in docs/ was rendered from (not ingested)
  golden.jsonl         the golden set (spec/contracts/evaluator.md, plus `checks` below)
  fixtures/fake/*.jsonl  fake-provider fixtures, one per golden case
  prompts.md           "try these": numbered prompts, each with what lights up
```

A document's id is its file stem, lowercased, with runs of other characters folded to `_`. A
PDF and a markdown file with the same stem would collide, so a PDF's markdown source lives in
`sources/`, outside `docs/`.

## Where packs live

A pack is a subdirectory of one of the pack roots (`packs.roots` in `config/harness.yaml`,
default `[datasets]`; the conformance profile adds `spec/conformance/packs`). Its id is the
directory name; when two roots hold the same id, the first root wins. One module,
`harnesswalk/packs.py`, discovers packs for `GET /v1/packs`, sessions, eval runs, releases, the
CLI and the validator (P5 integration; it replaced `harnesswalk/ops/pack_index.py`).

## `pack.yaml`

| Key | Type | Meaning |
|---|---|---|
| `id` | string | Optional; when present it must be the directory name |
| `title` | string | The fictional business, shown in the pack picker |
| `domain` | string | healthcare, ecommerce, logistics, sports, finance, ... |
| `summary` | string | One sentence |
| `tier` | `domain`, `starter` or `profile` | Which counts apply (below). Default: `domain` when the directory has a `system_prompt.md`, else `profile` |
| `profile` | string | 0.2.0: the harness profile the pack runs with (`harness.md` section 10), a file under `config/profiles/`: `minimal`, `guarded`, `grounded`, `remembered`, `governed` or `operated`. Required |
| `feature` | string | The harness feature the pack demonstrates, in a few words |
| `demonstrates` | object | `id`, `nodes` (diagram node ids it lights), `summary` (what the learner sees) |
| `licence` | `CC0-1.0` | Content licence; no other value is accepted |
| `provenance` | string or object | The provenance card's file name, normally `provenance.md`; or (P2) `{card, generator?}` with `card` that file name and `generator: {model, date, prompt_hash?}` the model that wrote the pack's synthetic content, the date (`YYYY-MM-DD`) and a hex hash of the generator's prompts. `tools/gen_pack.py` fills it |
| `synthetic` | `true` | No real people, companies or identifiers |
| `prompts` | list of strings | 1 to 10 prompts for the UI's suggestion row |

No other keys: `provenance` holds only `card` and `generator`, and `generator` only `model`,
`date` and `prompt_hash` (P2, additive; the spec stays 0.2.0). `domain` and `starter` packs
need every key but `id`; a `profile` pack (its
content comes inline from a config overlay, like `spec/conformance/packs/conformance_alt`;
the tier's name predates harness profiles) needs only `title`, `licence` and `profile`, and
the file, tool and golden checks below do not apply to it. `GET /v1/packs` returns `id`,
`title`, `domain`, `feature`, `profile` (the one the server runs it with), `prompts` and
`counts` from this card; `packs list --json` returns the same fields plus `summary`, `tier`, `demonstrates`,
`licence`, `tools`, `size_bytes` and `path`.

## Tiers and counts

| | domain | starter, profile |
|---|---|---|
| documents in `docs/` | 10 to 20, at least 2 PDFs | any |
| words per document (markdown, or the PDF's source) | 300 to 900 | any |
| skills | 3 to 5 | any |
| pack tools | 2 to 12 (P2: was 2 to 4; a governed pack splits commands by risk) | any |
| facts / episodes | 15 to 30 / 8 to 15 | any |
| golden cases | 20 to 30 | any |
| numbered prompts in `prompts.md` | 6 to 10 | any |

Every pack is under 2 MB (all files under its directory).

## Tools

Tools are pure functions over the pack's bundled data: each `tools.yaml` entry is a lookup
`table` keyed by its one required argument, with a `missing` row (the registry's table tools).
Every tool declares `action_class` and `timeout_ms`. A pack tool must not shadow a built-in
(`calculator`, `read_skill`, `remember`, `read_doc`), and must be in `config/policy.yaml`
`agent.allow_tools`, or policy denies it (rule `agent.allowlist`) and the model never sees it.

## Skills: `skills/policy.md` (P2)

A pack may ship its business rules as a skill named `policy` (`skills/policy.md`, front matter
`name: policy`). The procedural store loads it like any skill, so the model sees it in the
skills index and reads it on demand with `read_skill`. It is the human-readable version, the
rules the model may promise and must refuse; the enforced rules stay in code, in
`config/policy.yaml` (the `policy` node decides every tool call from them, never from the
skill). A pack that ships one must read it in at least one golden case: a fixture turn the
case plays calls `read_skill` with `{"name": "policy"}`.

## Golden cases: `checks`, `modes` and `after`

A golden case may carry `checks`, scored by the eval runner as the `checks` scorer
(`spec/contracts/evaluator.md`) on the stored events of the turn that ran the case:

- `router_rules`: the `router` node's `summary.rule` of each model call, in order. A one-tool
  lookup on the sports pack is `["default", "lookup"]`.
- `retrieved`: `{store: substring}`. The context builder's retrieved items for that store
  (`semantic`, `episodic` or `knowledge`) include the substring. A recall case on the finance
  pack checks that a fact the previous case asked to `remember` reached the context.

P2 adds two keys to `checks`, for long sessions:

- `compacted`: `true` or `false`. Whether the context builder folded old turns into the
  running summary (its exit summary's `compacted`) in the turn, or in any turn of a multi-turn
  case when the check sits on the case.
- `summary`: a substring (compared without case) the running session summary holds in the (last) turn's context. On
  the fake provider the summary is the truncation summary (each folded turn's first 160
  characters of user and reply text), so a fact the case must keep goes early in its turn; a
  model summarises under `remembered` and up when a non-fake `summarize` model is routed.

Cases run in file order in one scratch tenant, so a case may rely on what an earlier case's
memory writer wrote; such a case names that case in `after` (the loader refuses an `after`
that is not an earlier case). `modes` limits a case to some harness modes: the starter's
`pii_redacted` expects the user's own email back, which only dev restores, so it is
`"modes": ["dev"]` and the release gate can pass in private mode.

## Golden cases: `turns` and `roles` (P2)

A case may carry `turns` instead of `prompt`: a list of `{user, expect?, checks?}`, at least
one. The turns run in order in one session (one session id, so turn `n` sees the earlier
turns as history, and the context builder compacts when the session slice passes its
threshold). `expect` and `checks` may sit on any turn and on the case: a turn's are scored on
that turn's events as `turn<n>.<scorer>` (`turn2.tool_calls`); the case's are scored over the
whole session (`path` and `tool_calls` are every turn's in order, `citations` every turn's,
`answer_*` and `schema` the last turn's reply, `pii_leak` every reply against every user
message, `checks` every turn's events, with `retrieved` and `summary` read from the last
context). A turn has no other keys; `approval`, `modes`, `after` and `roles` stay on the case.
A case with both `prompt` and `turns` does not load.

`fixture` is then one name for every turn, whose turn `n` answers the case's turn `n` (the
fake provider counts turns per session and fixture), or a list of names aligned with `turns`.
The validator checks each turn's `tool_calls` against the fixture turn it reads, and the
case's `tool_calls` against every turn's calls in order.

`roles` is a list of role names the case identity runs as (default `["user"]`), so a case can
prove what `config/policy.yaml` `roles` and role-scoped rules deny: a `viewer` asking for a
reboot is denied by policy (rule `roles`), whatever the model does.

## Validation (`harnesswalk packs validate [<id>] [--profile <file>]`)

The packs checked are those under `packs.roots` (a profile may add roots). A pack is valid
when:

1. `pack.yaml` matches the schema and has the keys its tier needs, its `id` (when given) is
   the directory name, and `demonstrates.nodes` are diagram nodes;
2. the required files exist, the counts fit the tier and the pack is under 2 MB;
3. `tools.yaml` loads in the registry and every tool is in the agent allowlist;
4. skills have front matter whose `name` is the stem; seed lines have their keys;
5. `guardrails.yaml`, when present, builds a PII detector;
6. every golden case loads, names a fixture that exists (the pack's, or a conformance one),
   whose first turn calls exactly `expect.tool_calls`, only known tools, cites only existing
   documents, has no unresolved `[[cite:doc]]` marker, whose `checks` name known router
   rules and stores, and whose `after` names an earlier case; a multi-turn case (P2) is
   checked turn by turn against the fixture turn each reads, and its own `tool_calls` against
   all of them in order;
7. no personal-data pattern matches the pack's content (system prompt, skills, documents
   including PDF text, PDF sources, seeds, tool tables). The patterns are the default detector's
   (email, Luhn-valid card, phone, US SSN, Canadian SIN) plus every pack's `guardrails.yaml`
   national ids, so one pack's health-number format is caught in another pack too. Emails on
   reserved domains (`example.com`, `example.net`, `example.org`, `.invalid`, `.test`) and
   phone numbers in the fictional 555-01xx block are allowed. Cue-based names are not scanned.
   Prompts (golden prompts, fixtures, `prompts.md`, the card's `prompts`) may carry synthetic
   ids on purpose, since they exercise the guardrails, but their emails and phones must be
   reserved values;
8. the provenance card exists, states CC0 and says the content is synthetic;
9. (0.2.0) the card's `profile` exists and loads, every node of `demonstrates.nodes` is on
   under it, and every golden case is reachable under it: a case whose `expect.path` names
   `approval` needs `policy` and `approval` on, a case that expects `citations` needs
   `knowledge`, and a `checks.retrieved` store must be on. A profile with `approval` off may
   only carry tools that need no human: no `destructive` or `external` class, and none that a
   rule of `config/policy.yaml` sends to `needs_approval` in dev mode. The golden set itself
   must pass on the fake provider under the pack's profile (CI runs every pack's golden set,
   which now runs on its profile). The node and store needs of every turn of a multi-turn
   case count too;
10. (P2) a `skills/policy.md`, when present, is read by at least one golden case (a fixture
    turn the case plays calls `read_skill` with name `policy`).

A pack goes on the lowest rung whose nodes its golden set needs, or higher; when a case needs a
node the profile switches off, the pack moves up a rung rather than the case changing. The
shipped packs, with the golden set measured on every rung of the fake provider:

| Pack | Profile | Why that rung |
|---|---|---|
| `_starter` | `operated` | The tour of every node and the default pack of the public site: today's node set. Its golden set needs `governed` (an approval) |
| `healthcare` | `grounded` | Its knowledge cases cite documents; it passes on `grounded` and up |
| `sports` | `grounded` | Rules questions cite documents; the router's `lookup` rule needs no more |
| `ecommerce` | `governed` | Cancels and refunds pause for approval |
| `logistics` | `governed` | A reroute is a `write` that a policy rule sends to approval |
| `finance` | `operated` | Its recall cases need `remembered`; it stays on the full harness, as proposed |
| `device_fleet` | `governed` | (P2) Power-off, SMS clearing and purges pause for approval; a viewer's reboot is a policy deny |
| `gov_services` | `grounded` | (P2) Bilingual programme rules cited from documents; read-only tools need no human |
| `long_session` | `remembered` | (P2) Long multi-turn cases cross the compaction threshold; a remembered fact is read in a later session |
| `helpdesk_lite` | `minimal` | (P2) One read-only lookup per question, no approvals, citations or memory: it passes on every rung, so it sits on the lowest |

The exit code is 0 when every pack checked is valid, 1 otherwise, 2 for an unknown pack.

## Licence and provenance

Pack content is CC0 1.0. The provenance card records the generator and its prompts (with
hashes), the model, the date, the token usage and cost of generation, the local steps and the
human review. A domain pack is generated by `tools/gen_pack.py` from `tools/packs/*.prompt.md`
and then reviewed and edited by hand; the edits are part of the pack.
