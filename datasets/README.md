# Dataset packs

A pack is a small, fictional business the harness can run as: its system prompt and skills,
its tools, its documents, its seed memory and a golden set that proves it works. Five domain
packs each show off one harness feature on purpose, and the hand-written `_starter` pack
touches every node once. Everything here is synthetic and CC0.

The format is fixed in [`spec/packs.md`](../spec/packs.md); the code that reads it is
`harnesswalk/packs.py`.

## The packs

| Pack | Business | Feature it demonstrates | Watch these nodes |
|---|---|---|---|
| `_starter` | Example Store, a shop | A tour of every node | all of them |
| `healthcare` | Brindlemoor Family Clinic, three clinic sites | PII redaction and refusal: health numbers, emails and phone numbers become placeholders before the model sees them; a leaked detail in a reply is repaired; medical advice and other people's records are refused | `input_guardrails`, `output_guardrails` |
| `ecommerce` | Quillfern Outfitters, outdoor and home goods | A destructive action behind approval: `cancel_order` and `issue_refund` pause for a human, with an audit record before and after | `policy`, `approval`, `audit` |
| `logistics` | Tallowgate Freight, six hubs | Multi-step tool use: `track`, then `hub_capacity`, then `reroute` (a write a policy rule sends to a human), then `eta`, one tool per loop step | `loop`, `tools`, `policy` |
| `sports` | The Copperline Community League, two divisions | Cheap model routing for lookups: the call after a read-only lookup routes on the `lookup` rule to the cheapest configured model | `model_layer`, `router` |
| `finance` | Hollowmere Capital Partners, four private funds | Citations and the memory writer: cited answers from fund documents, and preferences proposed with `remember`, written after the reply and recalled in a later session | `knowledge`, `output_guardrails`, `memory_writer` |

Run one:

```bash
uv run harnesswalk packs list                         # every pack, its feature and size
HARNESSWALK_DATASET=logistics make run                # serve the harness on a pack
uv run harnesswalk eval sports --suite golden         # a pack's golden set on the fake provider
uv run harnesswalk turn "Track TG-604118, check whether its alternate hub can take it, reroute it, and then give me the ETA confidence." \
  --provider fake --fixture reroute_four_tools --set dataset=logistics --approval approve
```

Each pack's `prompts.md` lists prompts to try and what each one lights up; `pack.yaml`
`prompts` is the short list the UI shows as suggestions (`GET /v1/packs`).

## What is in a pack

```
datasets/<pack>/
  pack.yaml            the card: title, domain, feature, demonstrates, licence, provenance, prompts
  provenance.md        how it was made: generator, model, prompts and hashes, tokens and cost
  system_prompt.md     skill zero
  skills/*.md          3 to 5 on-demand skills
  tools.yaml           2 to 4 tools: pure lookups over the bundled tables in this file
  guardrails.yaml      PII patterns for the pack (healthcare: health numbers; finance: accounts)
  facts.jsonl          about 20 seed facts for semantic memory
  episodes.jsonl       about 10 seed events for episodic memory
  docs/                12 to 15 documents for knowledge, two of them PDF
  sources/             the markdown each PDF was rendered from (not ingested)
  golden.jsonl         20 to 30 eval cases with expected node paths
  fixtures/fake/       a scripted fake-model answer for every golden case
  prompts.md           try these
```

Documents and seeds are indexed when the harness starts on a pack (or with
`harnesswalk packs ingest <pack>`) into the shared pack scope, which every tenant on the pack
reads; nothing a tenant writes ever lands there.

Seed lines: a fact is `{"key", "value", "entities"?}`; an event is `{"id", "ts", "kind",
"text", "outcome"?, "entities"?}`.

Tool names are in `config/policy.yaml` `agent.allow_tools`; a pack tool that is not would be
denied by policy and hidden from the model, and the validator says so.

## Golden cases and fixtures

A golden case is a prompt, the fake fixture that answers it, and what the turn must show:
the node path, the tool calls, the citations, a piece of the answer (spec/contracts/
evaluator.md). Packs add one optional key, `checks`, evaluated on the stored events by the
pack suite: `router_rules` (the router rule of each model call; the sports pack) and
`retrieved` (a remembered fact that must reach the context; the finance pack). Cases run in
file order in one scratch tenant, so a recall case can read what the case before it wrote.

Fake fixtures are JSON Lines: one line per chat turn of a session, each line
`{"responses": [...]}` with responses shaped as in `spec/conformance/fixture.schema.json`.
The file stem is the fixture name, so `harnesswalk turn "..." --provider fake --fixture
tool_call` reads `fixtures/fake/tool_call.jsonl` of the active pack, and falls back to
`spec/conformance/fixtures/` when the pack has no fixture of that name. A response's `expect`
checks what the model was sent, so a case also proves, for example, that the model saw
`[ID_1]` and never the health number.

## Checking a pack

```bash
uv run harnesswalk packs validate              # every pack; exit 1 on any issue
uv run harnesswalk packs validate finance      # one pack
uv run pytest tests/unit/test_packs.py         # the validator, plus every golden suite and its checks
```

The validator checks the card's schema, the counts and the 2 MB size limit, that every golden
case's fixture exists and calls exactly the tools the case expects, that tools declare action
classes and are allowed by policy, that cited documents exist, and that no personal-data
pattern from any pack's guardrails (or the defaults: email, card, phone, SSN, SIN) matches pack
content. Emails on `example.com`, `example.net`, `example.org`, `.invalid` or `.test` and phones
in the 555-01xx block are reserved test values and allowed. CI runs `packs validate` and the
golden suite of every pack on the fake provider.

## Making or regenerating a pack

Domain packs are generated with `tools/gen_pack.py`, Synthea style: the generator and its
prompts are committed, so a pack can be regenerated and diffed.

```bash
export ANTHROPIC_API_KEY=...                               # in your shell only, never in a file
uv run python tools/gen_pack.py sports --stage core        # card, prompt, skills, tools, seeds, doc plan
uv run python tools/gen_pack.py sports --stage docs        # the documents, four per call
uv run python tools/gen_pack.py sports --stage golden      # golden cases and fake fixtures
uv run python tools/gen_pack.py sports --stage finalize    # local: guardrails, PDFs, citations, provenance
```

The prompts are `tools/packs/_common.prompt.md` (rules and file formats for every pack) and
`tools/packs/<pack>.prompt.md` (the business, the feature, the tools and the case recipe).
Each hosted call appends its tokens and cost to `tools/packs/<pack>.usage.jsonl`, and
`finalize` sums them into `provenance.md`. `finalize` also turns `[[cite:<doc>]]` markers in
fixtures into the passage numbers the harness retrieves for each case (fake provider, hash
embeddings, so the numbers are the same on every machine). After generation a maintainer
reviews and edits the pack until `packs validate` is clean and the golden suite passes.

## Licence and provenance rule

- **Content is CC0 1.0.** Everything under `datasets/<pack>/` is dedicated to the public
  domain. Code that generates or loads packs is Apache-2.0 like the rest of the repo.
- **Synthetic only.** No real people, patients, customers, accounts, companies, brands, teams
  or places that identify anyone. Names, ids and numbers are invented, and nothing is copied
  from a source with a different licence.
- **Provenance card required.** `provenance.md` records the generator, the model, the prompts
  and their hashes, the date, the token usage and cost, the local steps, the human review,
  and that the content is synthetic and CC0. The validator refuses a pack without one.
- **Upgrade datasets are separate.** Real, permissively licensed sets (nflverse, Worldcup26
  match reports, UCI Online Retail II, FinQA, EDGAR) may be offered as optional downloads with
  their own licence and attribution. They are never committed into the CC0 packs.

## The `_starter` pack

`datasets/_starter/` is the small hand-written pack the harness runs with by default: a
fictional shop's system prompt, two skills (`returns`, `order_status`), two lookup tools
(`lookup_order` read, `cancel_order` destructive, so approval can be tried), three documents
(a returns policy, a shipping FAQ and a product sheet), six seed facts and four seed events.
Ask "Can I return order A-1003, the Trailhead backpack?" and all five memory badges light:
procedural always, session from the second turn, a seed fact and a seed event about A-1003,
and the product sheet from knowledge.

| Fixture | What it shows |
|---|---|
| `plain_answer` | No tool: the canonical plain path |
| `tool_call` | A read tool, allowed by policy, not audited |
| `calculator` | The built-in calculator |
| `read_skill` | The on-demand tier: the model reads a skill body, then looks an order up |
| `cancel_order` | A destructive tool: policy, audit, approval pause, audit, then execute or not |
| `two_turns` | Two turns in one session, so the second sees the first as history |
| `cited_answer` | An answer from knowledge that cites the retrieved chunks as `[1]` and `[2]` |
| `read_doc` | The on-demand tier of knowledge: the model reads a whole document with `read_doc` |
| `remember_fact` | The model asks to remember a fact with `remember`; the memory writer writes it after the reply, and the next turn retrieves it |
| `trailhead_return` | The Trailhead question: all five stores answer and the reply cites the returns policy as `[6]` and `[5]` |
| `three_lookups` | Three `lookup_order` calls in one step, for trying a tool-call budget (`policy.budgets`): under a budget of two the third is denied and the turn ends on a limit |
