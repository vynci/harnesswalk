# Shared instructions for every harnesswalk pack

You are writing one dataset pack for harnesswalk, an open-source teaching harness that shows
every step of an AI assistant's turn (guardrails, context, memory, loop, model, tools, policy,
approval, audit, trace) as a live diagram. A pack is a small, fictional business the learner
can talk to in minute one. Each pack shows one harness feature on purpose; the pack brief
below says which.

## Content rules (all stages)

- Everything is synthetic and invented. Invent the company, its products, places, people and
  ids. Never name a real company, brand, product, hospital, carrier, league, team, club,
  stadium, fund manager, bank, city landmark or person. Generic place words ("the north
  depot", "Harbour District") are fine; real city names are not.
- No real identifiers: no real phone numbers, emails, web addresses, street addresses, tax
  ids, card numbers, health numbers, account numbers or tickers. Do not write any email
  address or phone number at all in documents, facts, episodes, skills, the system prompt or
  tool tables: say "the patient portal", "the front desk", "the support form", "the investor
  relations desk" instead.
- Numbers are plausible but fictional. Dates fall between 2026-09-01 and 2026-12-31.
- No medical advice beyond generic clinic policy. No investment advice, no real financial
  instruments, no real market data.
- Write plain, friendly prose. Never use the em-dash character; use periods, commas,
  parentheses or colons.
- Keep every fact consistent across files: the tool tables, the documents, the facts and the
  golden cases describe one world.

## Output format (all stages)

Write each file as a block, and nothing outside the blocks:

```
=== FILE: relative/path.ext ===
...file content...
=== END FILE ===
```

Paths are relative to the pack directory (for example `skills/booking.md`, `docs/billing_faq.md`,
`fixtures/fake/booking_lookup.jsonl`). Do not wrap file content in code fences.

## Stage: core

Write these files:

1. `pack.yaml` with keys `title` (the fictional business name), `domain`, `summary` (one
   sentence), `feature` (`name`, `nodes` (diagram node ids it lights), `summary` (two
   sentences on what the learner sees)) and `suggested_prompts` (4 to 6 short questions a
   learner can paste). Plain YAML.
2. `system_prompt.md`: 150 to 300 words. Who the assistant is, what it helps with, the
   business hours, the id formats, and the rules: use the tools to look things up instead of
   guessing; read a matching skill with `read_skill` first; never claim an action happened
   unless a tool result says so; cite knowledge passages as [1], [2]. Add the pack's own
   rules from the brief.
3. `skills/<name>.md`: 3 to 5 skills, each 80 to 200 words, starting with front matter:

   ```
   ---
   name: <name, lowercase with underscores, same as the file stem>
   description: <one line: when to use this skill>
   ---
   # <Title>

   1. numbered steps that name the tools to call
   ```
4. `tools.yaml`: exactly the tools the brief lists, in that order, as a YAML list. Every
   entry has `name`, `description`, `action_class` (as the brief says), `timeout_ms: 2000`,
   `idempotent: true`, `pure: true`, `parameters` (JSON Schema: `type: object`,
   `additionalProperties: false`, one `required` key that is the lookup key, with a `pattern`
   for ids), `table` (a mapping from key value to the result row; 4 to 8 rows; each row a flow
   mapping of short scalar values, and the row repeats its own key) and `missing` (the row
   returned for an unknown key, for example `{ error: not_found }`). A destructive tool that
   changes a row the read tool returns also has `audit: { before: <read tool name> }`. Start
   the file with a two-line comment saying the tables are the pack's bundled data.
5. `facts.jsonl`: about 20 lines, each `{"key": "...", "value": "...", "entities": ["ID-123"]}`
   (`entities` only when the fact is about an id from the tool tables). Facts are durable
   notes: preferences on file, standing arrangements, policy numbers.
6. `episodes.jsonl`: about 10 lines, each `{"id": "ep-...", "ts": "2026-10-..T..:..:00Z",
   "kind": "support", "text": "...", "outcome": "resolved", "entities": [...]}`: things that
   happened recently, consistent with the tables.
7. `prompts.md`: a "Try these" page: a title, one paragraph on the pack's feature, then 6 to
   10 numbered prompts, each followed by one line in italics on what lights up in the diagram
   (which node, which tool, which store).
8. `docs_plan.json`: a JSON list of 12 to 15 documents to write in the next stage, each
   `{"id": "snake_case_id", "title": "...", "brief": "two sentences of what it covers, with
   the concrete numbers it must state", "pdf": false}`. Exactly two documents have
   `"pdf": true` (they will also be rendered to PDF).
9. `guardrails.yaml` only when the brief asks for one, in exactly the format the brief gives.

## Stage: docs

Write the documents you are asked for, as `docs/<id>.md`. Each is 300 to 900 words, starts
with `# <title>`, and is organised under `## ` headings (four to eight sections). Use the
numbers and names from the core files exactly. Documents are what a real business would
publish or keep internally: policies, FAQs, sheets, notices, guides, directories.

## Stage: golden

Write `golden.jsonl` (the number of cases the brief asks for, one JSON object per line) and
one fake-model fixture per case at `fixtures/fake/<fixture>.jsonl`.

A golden case:

```
{"id": "snake_case", "title": "what the case proves", "prompt": "what the user types",
 "fixture": "<fixture name, usually the case id>", "expect": {...}, "rubric": "what a good
 answer says", "tags": ["..."]}
```

Optional keys: `"approval": "approve"` or `"deny"` (how the eval runner decides an approval
request; default deny), `"checks": {...}` (only when the brief asks for it).

`expect` may hold:

- `path`: the diagram nodes the turn passes, in order. Use these templates exactly:
  - no tool: `["gateway", "input_guardrails", "context_builder", "memory", "loop", "model_layer", "output_guardrails", "gateway", "memory_writer", "trace"]`
  - one read tool: `["gateway", "input_guardrails", "context_builder", "memory", "loop", "model_layer", "tools", "policy", "model_layer", "output_guardrails", "gateway", "memory_writer", "trace"]`
  - more tools: repeat `"model_layer", "tools", "policy"` per step (one tool call per step).
  - a call that needs approval, denied: `... "model_layer", "tools", "policy", "audit", "approval", "audit", "model_layer" ...`
  - a call that needs approval, approved: `... "model_layer", "tools", "policy", "audit", "approval", "audit", "audit", "model_layer" ...`
  - a `remember` call: `... "model_layer", "tools", "policy", "audit", "model_layer", "output_guardrails", "gateway", "memory_writer", "trace"]`
- `tool_calls`: every tool name the turn calls, in order (built-ins count: `read_skill`,
  `read_doc`, `remember`, `calculator`). `[]` for no tool.
- `answer_contains`: one to three short exact substrings of the fixture's final reply text.
- `citations`: `[{"doc": "<doc id>"}]` for each document the reply cites.

A fixture file has one line per chat turn (one line unless the brief says otherwise):

```
{"responses": [<model call 1>, <model call 2>, ...]}
```

Each model call is either `{"tool_calls": [{"name": "...", "arguments": {...}}], "usage":
{"input_tokens": N, "output_tokens": M}, "expect": {...}}` or `{"text": "...", "usage": {...},
"expect": {...}}`. Every call has `usage` with plausible numbers (input 200 to 900, output 15
to 80). `expect` checks what the model receives:

- the first call: `{"last_message_role": "user"}`, plus `"last_message_contains": "[ID_1]"`
  (or `[EMAIL_1]`, `[PHONE_1]`) when the prompt holds personal data, because the input
  guardrails replace it with that placeholder before the model sees it.
- a call after a tool result: `{"last_message_role": "tool", "last_message_contains":
  "<one short literal value from that tool's table row, or from the skill body>"}`. The tool
  result is the row as compact JSON, so pick a plain value such as `confirmed` or `09:00`,
  never a key with quotes.

Final reply text rules:

- It uses only facts from the tool rows, the skills and the documents.
- When the prompt held personal data, the reply repeats the placeholder (`[ID_1]`), never the
  value: the harness puts the user's own value back afterwards. Then `answer_contains` may
  hold the original value.
- When the reply uses a document, it cites it with a marker `[[cite:<doc id>]]` right after
  the claim; the build replaces markers with the passage numbers. Then `expect.citations`
  names that doc. Cite at most two documents.
- 1 to 4 sentences.
