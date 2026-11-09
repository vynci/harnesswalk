# Use your own data

How do I use or integrate my own data? In Harnesswalk the answer is a pack: a folder under
`datasets/` that holds your documents, your prompts and skills, the tools the model may call,
the guardrail patterns for your identifiers, and the golden cases that prove it still works.
This guide builds one for a set of support documents and two domain functions, end to end, with
commands you can paste. It assumes a checkout with `make setup` done (see the
[Quickstart](quickstart.md)); every command runs from the repository root.

The pack format is fixed in [`spec/packs.md`](../spec/packs.md), the shipped packs are described
in [`datasets/README.md`](../datasets/README.md), and the code that reads them is
[`harnesswalk/packs.py`](../harnesswalk/packs.py).

## Why packs

Your data never travels alone. A document is only useful with a system prompt that tells the
model to cite it; a tool is only safe with a policy rule that matches its action class; a
guardrail pattern only matters for the identifiers your customers actually type; and none of it
can be changed with confidence without a few cases that show the old behaviour still holds. A
pack keeps these together, so the data, the policy and the tools change in one place and are
checked in one place. The harness loads one pack per session: pick it in the header of the web
app, or name it with `HARNESSWALK_DATASET` or `--set dataset=<id>`.

## The pack layout

```
datasets/my_support/
  pack.yaml            the card: title, domain, summary, tier, feature, the prompts the UI suggests
  provenance.md        where the content came from and under which licence
  system_prompt.md     skill zero: always in context
  skills/*.md          on-demand skills the model reads with read_skill, one procedure each
  tools.yaml           the tools: name, description, JSON Schema, action class, timeout
  support_tools.py     your own functions, when a tool is code rather than a lookup table
  guardrails.yaml      PII patterns for your identifiers (optional)
  facts.jsonl          seed facts for semantic memory (may be empty)
  episodes.jsonl       seed events for episodic memory (may be empty)
  docs/                your documents: markdown (.md, .markdown) and PDF (.pdf)
  golden.jsonl         the golden cases: a prompt, a fixture and what the turn must show
  fixtures/fake/*.jsonl  scripted answers for the fake model, one file per golden case
  prompts.md           "try these": numbered prompts and what each one lights up
```

A directory is a pack when its name is a pack id (lowercase letters, digits, `_` and `-`) and it
holds a `pack.yaml` or a `system_prompt.md`. Anything else under `datasets/` is ignored.

## A worked example: a pack for your own support docs

The example is Larkspur Support, an invented note-taking app with three help-centre documents,
one ticket lookup and one function that opens a ticket. Replace the names and the text with
yours; the shape stays the same.

### 1. Copy the starter's skeleton

There is no separate template: `datasets/_starter` is the reference pack, so take its files and
leave its content behind.

```bash
mkdir -p datasets/my_support/docs datasets/my_support/skills datasets/my_support/fixtures/fake
cp datasets/_starter/{pack.yaml,system_prompt.md,tools.yaml,guardrails.yaml,prompts.md,provenance.md} datasets/my_support/
touch datasets/my_support/{facts,episodes,golden}.jsonl
uv run harnesswalk packs list
```

`packs list` now shows `my_support`, still titled Sample Shop. Edit `datasets/my_support/pack.yaml`
to describe your pack. Remove the `id` line (or set it to the directory name), and keep
`tier: starter`: the `domain` tier enforces the document, skill and golden counts of the shipped
packs.

```yaml
title: Larkspur Support
domain: support
summary: The support assistant for Larkspur, a note-taking app, answering from its help centre and opening tickets.
tier: starter
feature: Answers from your own documents
demonstrates:
  id: own_docs
  nodes: [knowledge, tools, policy]
  summary: >-
    Answers cite the help-centre documents, a ticket lookup is a read, and opening a ticket is
    a write that policy allows and audit records.
licence: CC0-1.0
provenance: provenance.md
synthetic: true
prompts:
  - How do I export my notes?
  - What is the status of ticket T-2041?
  - Please open a ticket, sync stopped on my phone.
```

`licence` and `synthetic` only accept `CC0-1.0` and `true`, because every pack committed to this
repository is public and invented. If your documents are real and private, write the truth there
instead: the validator then reports exactly those two `schema` lines (and two `provenance` lines
if `provenance.md` no longer says CC0 and synthetic), which is expected for a pack that stays on
your machine. Turns and evals run the same either way. To keep such a pack out of git:

```bash
echo "datasets/my_support/" >> .git/info/exclude
```

Rewrite `provenance.md` in a few lines: where the documents came from and who reviewed them.

### 2. Drop in your markdown and PDFs

Copy your documents into `docs/`. The file stem is the document's id (`exporting_notes.md` is
`exporting_notes`), so a markdown file and a PDF must not share a stem.

```bash
cp ~/help-centre/exporting_notes.md ~/help-centre/plans_and_billing.md datasets/my_support/docs/
cp ~/help-centre/sync_troubleshooting.pdf datasets/my_support/docs/
```

Markdown is split at every `#`, `##` and `###` heading, so give each answer its own heading: a
chunk never spans two sections and its citation names the section. A section longer than
`stores.knowledge.chunk_tokens` (400 tokens, `config/context.yaml`) is cut into overlapping
windows. A PDF's text comes from `pypdf`, one section per page; a scanned PDF without a text
layer gives nothing (see Troubleshooting). For example, `docs/exporting_notes.md`:

```markdown
# Exporting your notes

Larkspur keeps every note in your account, and you can take a copy out at any time.

## Export one notebook

Open the notebook, choose Share, then Export. Pick Markdown for plain files or PDF for a
printable copy. The export is a zip file with one file per note and a folder for attachments.

## Export everything

Go to Settings, then Account, then Export all data. Larkspur prepares a zip of every notebook
and emails a download link to the address on your account within 24 hours. The link works
for 7 days.
```

Seed memory is optional. A fact is one JSON line `{"key", "value", "entities"?}` in
`facts.jsonl`; an event is `{"id", "ts", "kind", "text", "outcome"?, "entities"?}` in
`episodes.jsonl`:

```json
{"key": "support hours", "value": "Larkspur support answers tickets 8am to 8pm UTC, every day."}
```

### 3. Write the system prompt and three skills

`system_prompt.md` is always in context. Keep it short, say who the assistant is, and ask for
citations: the knowledge passages reach the model numbered `[1]`, `[2]`, and only a reply that
uses those markers carries its sources.

```markdown
You are the support assistant for Larkspur, a note-taking app. Answer briefly and plainly,
from the help-centre passages you are given, and cite them with their [n] markers.

Tickets have ids like T-2041. Look a ticket up with ticket_status instead of guessing, and
open a new one with open_ticket only when the customer asks for it. If a skill in the index
matches the question, read it first with the read_skill tool. Never claim an action happened
unless a tool result says so.
```

A skill is a procedure the model reads on demand. Only the index (name and description) is in
context until the model calls `read_skill`. The front matter `name` must be the file stem.
`skills/tickets.md`:

```markdown
---
name: tickets
description: How to look up a ticket or open a new one.
---
# Tickets

1. For "what is happening with my ticket", call `ticket_status` with the id (T- and four digits).
2. If the lookup returns `not_found`, ask the customer to check the id.
3. Open a ticket with `open_ticket` only when the customer asks for one. Pass a one-line
   `summary` and the `area` (sync, billing, export or other).
4. Tell the customer the new ticket id from the tool result. Never invent one.
```

Write two more the same way, for example `skills/answer_from_docs.md` (answer only from the
passages, cite each, say so when nothing covers the question) and `skills/refunds.md` (the
refund rules, and that the assistant cannot refund by itself).

### 4. Declare one read tool and one write tool

Every tool in `tools.yaml` has a `name`, a `description` the model reads, a JSON Schema in
`parameters`, an `action_class` and a `timeout_ms`. The implementation is either a lookup
`table` keyed by the one required argument, or an `entrypoint`: `module:function`, or
`file.py:function` relative to `tools.yaml`. Replace the starter's two tools with yours:

```yaml
- name: ticket_status
  description: Look up a support ticket by id and return its status and last update.
  action_class: read
  timeout_ms: 2000
  idempotent: true
  pure: true
  parameters:
    type: object
    additionalProperties: false
    required: [ticket_id]
    properties:
      ticket_id: { type: string, pattern: "^T-[0-9]{4}$" }
  table:
    T-2041: { ticket_id: T-2041, status: waiting_on_engineering, area: sync, updated: "2026-10-30" }
    T-2042: { ticket_id: T-2042, status: solved, area: billing, updated: "2026-10-28" }
  missing: { error: not_found }

- name: open_ticket
  description: Open a new support ticket with a one-line summary. Returns the new ticket id.
  action_class: write
  timeout_ms: 3000
  idempotent: false
  entrypoint: support_tools.py:open_ticket
  parameters:
    type: object
    additionalProperties: false
    required: [summary, area]
    properties:
      summary: { type: string, minLength: 5, maxLength: 200 }
      area: { enum: [sync, billing, export, other] }
```

The function takes the validated arguments as one dict and returns the observation the model
reads, as a string (a tool with a `data:` key gets it as a second argument).
`datasets/my_support/support_tools.py`:

```python
import hashlib
import json


def open_ticket(arguments: dict) -> str:
    # Your real function would call your helpdesk here. This one derives a stable id.
    digest = hashlib.sha256(arguments["summary"].encode()).hexdigest()
    ticket_id = f"T-{3000 + int(digest[:6], 16) % 1000}"
    return json.dumps({"ticket_id": ticket_id, "status": "open", "area": arguments["area"]})
```

Then let the agent use them: add both names to `agent.allow_tools` in `config/policy.yaml`. A
tool outside that list is denied before any rule runs, and the model never sees it.

```yaml
agent:
  allow_tools:
    # ... the built-ins and the shipped packs' tools ...
    - ticket_status       # my_support
    - open_ticket         # my_support
```

The action classes are `read` (looks, changes nothing), `write` (changes your own system),
`destructive` (hard to undo: a cancel, a refund), `external` (leaves your systems: an email, a
third-party API) and `memory.write` (reserved for the built-in `remember`). Pick the class by
what the call does, not by how the code is written: it decides the policy rule and whether the
sandbox opens the network (only for `external`, see below). Tool names must not shadow the
built-ins `calculator`, `read_skill`, `remember` and `read_doc`.

### 5. Add guardrail patterns

`guardrails.yaml` adds the identifiers of your domain to the input guardrails. A match is
replaced with a placeholder such as `[ID_1]` before the model sees the message, and dev mode
puts the user's own value back in the reply. The pack's `national_id` patterns replace the
defaults, so keep the US SSN and Canadian SIN lines. A Larkspur licence key must never reach the
model:

```yaml
pii:
  national_id:
    patterns:
      - { name: us_ssn, regex: "(?<![\\d-])\\d{3}-\\d{2}-\\d{4}(?![\\d-])", luhn: false }
      - { name: ca_sin, regex: "(?<![\\d-])\\d{3}[ -]\\d{3}[ -]\\d{3}(?![\\d-])", luhn: true }
      - { name: larkspur_licence_key, regex: "(?<![A-Z0-9-])LK-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}(?![A-Z0-9-])", luhn: false }
```

Emails, phone numbers and card numbers are caught by default. The validator also scans your
documents, skills, seeds and tool tables with every pack's patterns, so a real email or phone
number in your content shows up as a `pii` issue: decide for each one whether it belongs there.

### 6. Write five golden cases

A golden case is a prompt, the fake-model fixture that answers it, and what the turn must show:
the node path (in order, gaps allowed), the tool calls (exactly), the citations and pieces of
the answer. The fixture is a JSON Lines file under `fixtures/fake/`, one line per chat turn,
whose responses are what the fake model says; a response's `expect` checks what the model was
sent, so a case can prove the model saw `[ID_1]` and never the key. `golden.jsonl`:

```json
{"id": "export_cited", "title": "A how-to answer cites the export document", "prompt": "How do I export all my notes?", "fixture": "export_cited", "expect": {"tool_calls": [], "citations": [{"doc": "exporting_notes", "chunk": 2}], "answer_contains": ["Export all data", "7 days"]}, "rubric": "Explains Settings, Account, Export all data, the emailed link within 24 hours, valid 7 days, citing the export document."}
{"id": "ticket_lookup", "title": "A ticket lookup is one read tool", "prompt": "What is the status of ticket T-2041?", "fixture": "ticket_lookup", "expect": {"path": ["gateway", "input_guardrails", "context_builder", "memory", "loop", "model_layer", "tools", "policy", "model_layer", "output_guardrails", "gateway", "trace"], "tool_calls": ["ticket_status"], "answer_contains": ["with engineering"]}, "rubric": "Says T-2041 is waiting on engineering, last updated 30 October."}
{"id": "open_ticket", "title": "Opening a ticket is a write: policy allows it and audit records it", "prompt": "Please open a ticket, sync stopped on my phone.", "fixture": "open_ticket", "expect": {"path": ["loop", "model_layer", "tools", "policy", "audit", "model_layer", "output_guardrails"], "tool_calls": ["open_ticket"], "answer_contains": ["opened a ticket"]}, "rubric": "Says a ticket was opened and gives the id from the tool result."}
{"id": "licence_key_redacted", "title": "A licence key reaches the model as a placeholder", "prompt": "My licence key is LK-7Q2M-X9RT-44KD, why does it not work?", "fixture": "licence_key", "expect": {"tool_calls": [], "answer_contains": ["Contact support"]}, "rubric": "Does not repeat the key and sends the customer to Contact support."}
{"id": "refund_skill", "title": "The refunds skill is read before a refund answer", "prompt": "Can I get a refund on my monthly plan?", "fixture": "refund_skill", "expect": {"tool_calls": ["read_skill"], "answer_contains": ["Monthly plans are not refunded"]}, "rubric": "Says monthly plans are not refunded and Plus lasts until the end of the paid month."}
```

One fixture per case. `fixtures/fake/ticket_lookup.jsonl` calls the read tool, then answers
once it has the result:

```json
{"responses": [{"tool_calls": [{"name": "ticket_status", "arguments": {"ticket_id": "T-2041"}}]}, {"text": "Ticket T-2041 is with engineering; it was last updated on 30 October.", "expect": {"last_message_role": "tool", "last_message_contains": "waiting_on_engineering"}}]}
```

`fixtures/fake/licence_key.jsonl` fails the turn unless the model was sent the placeholder:

```json
{"responses": [{"text": "Thanks. I cannot check licence keys here, so please open Help, then Contact support, and paste it into the ticket form.", "expect": {"last_message_role": "user", "last_message_contains": "[ID_1]"}}]}
```

`fixtures/fake/refund_skill.jsonl` checks that the skill's rule reached the model, so an edit to
the skill that changes the rule fails the case:

```json
{"responses": [{"tool_calls": [{"name": "read_skill", "arguments": {"name": "refunds"}}]}, {"text": "A yearly Plus plan is refunded in full within 14 days of payment. Monthly plans are not refunded, but you keep Plus until the end of the month you paid for.", "expect": {"last_message_role": "tool", "last_message_contains": "within 14 days of payment"}}]}
```

`open_ticket.jsonl` has the same shape as `ticket_lookup.jsonl`, calling `open_ticket` with a
`summary` and an `area`. For a cited answer you need the number of the passage, which depends on
what retrieval returns for the prompt. Find it with a throwaway fixture that cites everything,
`fixtures/fake/probe.jsonl`:

```json
{"responses": [{"text": "Probe [1] [2] [3] [4]."}]}
```

```bash
uv run harnesswalk turn "How do I export all my notes?" --provider fake --fixture probe --set dataset=my_support
```

The `sources` block of the output maps each number to a document and chunk, for example
`[2] Exporting your notes, Export everything (exporting_notes#2)`. Cite `[2]` in
`fixtures/fake/export_cited.jsonl` and put `{"doc": "exporting_notes", "chunk": 2}` in the case
(the `chunk` is the position in the document, the `#2`). The fixture reads:

```json
{"responses": [{"text": "Go to Settings, then Account, then Export all data. Larkspur emails you a download link within 24 hours, and the link works for 7 days [2].", "expect": {"last_message_role": "user"}}]}
```

Then delete `fixtures/fake/probe.jsonl`, or it stays in the app's Fixture select. The numbers
come from the fake provider's hash embeddings, which is what the golden suite runs on, so they
are the same on every machine.

Finally list a few prompts in `prompts.md` (numbered lines, each with what it lights up), the
"try these" of the pack.

## How policy decides

Every tool call goes through the `policy` node before it runs. The order is fixed: the agent
allowlist (`agent.allow_tools`), what the user's roles allow, the tenant's tool-call budget, then
the rules in `config/policy.yaml` in file order, first match wins, and no match is a deny
(`default.deny`). A rule matches on `modes`, `action_classes` and `tools`; its `decision` is
`allow`, `deny` or `needs_approval`. The shipped rules, in short:

| Rule | Modes | Matches | Decision |
|---|---|---|---|
| `dev.read.allow` | dev, private, public | class `read` | allow |
| `dev.write.allow` | dev, private | class `write` | allow, audited |
| `dev.destructive.approve` | dev, private | class `destructive` | needs approval |
| `external.approve` | dev, private | class `external` | needs approval |
| `public.deny_rest` | public | anything left | deny |

So `ticket_status` is allowed everywhere, and `open_ticket` is allowed in dev and private mode
with an audit record before and after. Run it and read the walk:

```bash
uv run harnesswalk turn "Please open a ticket, sync stopped on my phone." --provider fake --fixture open_ticket --set dataset=my_support
```

```
    tools        done   tool=open_ticket outcome=ok executed=True
      registry   done   tool=open_ticket
      policy     done   tool=open_ticket decision=allow rule=dev.write.allow
      audit      done   action=policy_decision tool=open_ticket decision=allow
      sandbox    done   tool=open_ticket outcome=ok executed=True level=subprocess network=blocked
      audit      done   action=tool_executed tool=open_ticket decision=executed executed=True
```

To have a person approve every new ticket, add a rule that names the tool above the class rule
it narrows (the logistics pack does the same for `reroute`):

```yaml
rules:
  - { id: dev.read.allow,          modes: [dev, private, public], action_classes: [read], decision: allow }
  - { id: open_ticket.approve,     modes: [dev, private], tools: [open_ticket], decision: needs_approval }
  - { id: dev.write.allow,         modes: [dev, private], action_classes: [write], decision: allow }
```

The same turn now pauses: `decision=needs_approval rule=open_ticket.approve`, then an
`approval` span, then the call runs or not. On the command line `--approval approve` (or `deny`)
answers for you; in the web app an approval card appears in the chat. Your golden case changes
with the rule: add `"approval": "approve"` to the `open_ticket` case and `"approval", "audit"`
after the first `"audit"` in its path. Leave the case alone and the eval in the next sections
fails it, which is the point.

In the web app, click the Policy node after a turn: it shows the decision, the rule and a `why`
sentence naming the conditions that matched, and its Config tab edits `config/policy.yaml` as a
versioned artifact (dev mode). Public mode refuses to start if any rule would allow or ask
approval for a `destructive` or `external` call. The full contract is
[`spec/contracts/policy.md`](../spec/contracts/policy.md).

## How the tool registry works

Tools register when a pack is loaded: the built-ins from code
([`harnesswalk/execution/builtin_tools.py`](../harnesswalk/execution/builtin_tools.py)), then the
active pack's `tools.yaml` ([`harnesswalk/execution/registry.py`](../harnesswalk/execution/registry.py)).
Registration fails on a name that is not `^[a-z][a-z0-9_]{0,63}$`, a missing or unknown
`action_class`, a missing `timeout_ms`, a `parameters` that is not a valid JSON Schema, a
duplicate name, or an entrypoint that does not import. Only the active pack's tools are
registered, so two packs can reuse a name.

Every call then takes the same path ([`spec/contracts/tool_registry.md`](../spec/contracts/tool_registry.md)):

1. The registry resolves the name. An unknown tool is an error observation.
2. The arguments are validated against `parameters`. Invalid arguments are an error
   observation (`Invalid arguments: ...`), and policy never sees the call.
3. Policy decides, audit records, approval pauses when asked.
4. The sandbox runs it under `timeout_ms` (capped by `sandbox.max_timeout_ms`), and the result
   goes back to the model, truncated to `tools.max_observation_chars`.

The sandbox level is `sandbox.level` in `config/harness.yaml` (`subprocess` by default) or the
tool's own `sandbox: inprocess|subprocess`. At `subprocess` your function runs in a child Python
process with a scrubbed environment, a memory cap and an output cap, no new processes, no file
writes outside a per-turn work directory, and no network unless the tool's class is `external`
and policy allowed the call ([`spec/contracts/sandbox.md`](../spec/contracts/sandbox.md)). So a
write that must reach your helpdesk over HTTP is an `external` tool, which also means a person
approves it under the shipped rules. Prefer the standard library in entrypoint files: the child
refuses `ctypes` and `subprocess`, so some third-party packages will not import there.

Public mode, the hosted demo site, is stricter: it offers the model only tools that are both
`pure: true` and `read`, and runs them in-process. `ticket_status` (a table, pure by default)
works there. `open_ticket` (an entrypoint, not pure unless you say so) is never shown to the
model, and a call to it anyway comes back as `Tool 'open_ticket' is not offered here.` without
running. Mark a function `pure: true` only when it really has no I/O.

Entrypoint tools load from disk at start-up. The release store refuses a `tools.yaml` edit that
adds a new entrypoint, so add one in the file and restart.

## Ingest and verify

Check the pack, index it, and run one turn per behaviour on the fake model before you spend a
real model call:

```bash
uv run harnesswalk packs validate my_support
uv run harnesswalk packs ingest my_support
uv run harnesswalk turn "How do I export all my notes?" --provider fake --fixture export_cited --set dataset=my_support
uv run harnesswalk turn "My licence key is LK-7Q2M-X9RT-44KD, why does it not work?" --provider fake --fixture licence_key --set dataset=my_support
```

`packs validate` prints `ok   my_support (0 issue(s))` when the card, the files, the tools (and
their allowlist entries), the skills' front matter, the seeds, the guardrails, the golden cases
and their fixtures, the PII scan and the provenance card all check out. `packs ingest` reports
the documents, chunks and seeds it indexed:

```
pack my_support: 3 documents, 8 chunks (indexed); seeded 2 facts and 1 events; embedder fake:256, vectors sqlite
```

You rarely need it by hand: the harness indexes a pack's changed documents and seeds whenever it
starts on the pack (the probe turn above already did), so a second run says `(unchanged)` until
a document changes, and `--force` indexes again anyway. The embedder is Ollama's when Ollama is
running with the embedding model, otherwise hash embeddings. Of the two turns, the first shows
`knowledge done hits=4` and a `sources` block; the second shows `input_guardrails done
action=redact`, and the fixture's `expect` proves the model was sent `[ID_1]`.

Then the app, on the fake model first. A new folder appears in the pack menu without a restart
(`GET /v1/packs` discovers packs on every call); start on it directly with:

```bash
HARNESSWALK_DATASET=my_support make run PROVIDER=fake
```

Open http://127.0.0.1:8000, pick a fixture of your pack in the Fixture select and send its
prompt. Then the real model: with Ollama running, `make run` (or `make run PROVIDER=anthropic`
with a key, see [Use a real model](quickstart.md#use-a-real-model)), pick your pack in the menu
and ask in your own words, or on the command line:

```bash
uv run harnesswalk turn "How do I export all my notes?" --provider ollama --set dataset=my_support
```

A real model may phrase things differently, skip a skill or cite another passage. That is what
the next section is for.

## Evals as the safety net

The golden suite plays every case on the fake model, in file order, in a scratch tenant, and
scores each with the deterministic scorers: `path`, `tool_calls`, `citations`, `answer_match`,
`schema`, `pii_leak` and `checks` ([`spec/contracts/evaluator.md`](../spec/contracts/evaluator.md)):

```bash
make eval PACK=my_support          # the same as: uv run harnesswalk eval my_support --suite golden
```

```
PASSED  export_cited             A how-to answer cites the export document
PASSED  ticket_lookup            A ticket lookup is one read tool
PASSED  open_ticket              Opening a ticket is a write: policy allows it and audit records it
PASSED  licence_key_redacted     A licence key reaches the model as a placeholder
PASSED  refund_skill             The refunds skill is read before a refund answer

run run_0001 (golden, pack my_support): 5 passed, 0 failed, 0 skipped of 5; 0 regression(s) against no earlier run; ...
```

Each run is compared with the last one, so a case that passed before and fails now is marked
`REGRESSION`, with the scorers that failed under it, and `make eval` exits non-zero. Add the
`open_ticket.approve` rule without updating the case and the run says
`FAILED open_ticket ... REGRESSION`; change the refund window in `skills/refunds.md` and
`refund_skill` fails the same way, because its fixture checks what the skill told the model. In
the app, the Eval node runs the same set and offers a diagnosis of a failed case.

The same set gates releases. A release snapshots the pack's prompts, skills, tools and policy as
versioned artifacts; `release.gate` in `config/harness.yaml` runs the golden set first and
refuses the release below `min_pass_rate` (1.0 by default):

```bash
uv run harnesswalk release create --pack my_support --provider fake --note "first cut"
```

```
created r3 with 8 artifacts
  gate passed (ok; run_0002; pass rate 1.0)
```

With a failing case it prints `gate failed: The golden gate failed for pack my_support: 1
case(s) failed (pass rate 0.8, needs 1.0).`, exits 1, and nothing is released. `--no-gate`
skips the gate in dev mode only, and the audit log records it.

The golden suite proves the harness around the model. To score a real model's own answers, the
`judge` suite answers each case on a model and has a judge model (`evals.judge.model`) score
faithfulness and relevance against the case's `rubric`:
`uv run harnesswalk eval my_support --suite judge --model hosted-haiku`. It calls a hosted
model, so it costs money and never runs in CI.

Between the two, the `model` suite answers each case on a real model and keeps the
deterministic scorers, with no judge: `uv run harnesswalk eval my_support --suite model --model
local-4b` tells you whether that model calls the tools your cases expect, cites what they
expect and keeps to policy. It grades behaviour, not the fixture's wording: the expected tool
calls must come in order, but extra calls to read tools (a skill, a lookup) are allowed, and
`answer_match` compares normalized text, so `$6` matches "6 dollars" and "October 14, 2026"
matches "2026-10-14". Set `"tool_calls_mode": "exact"` in a case's `expect` when a model run
must call exactly the listed tools. A different wording of the facts still fails, so read a
reply that fails only `answer_match` before you call it wrong. `harnesswalk scorecard
--packs my_support --models fake-small,local-4b,local-9b --out my_scorecard.md` runs that for a
row of models at once ([scorecard.md](scorecard.md) is the one for the shipped packs).

## Tune a local model on your pack

When the `model` suite shows a local model failing your pack the same way every time (it asks
for approval in prose instead of calling your write tool, or calls a tool when none is needed),
the tuning loop can teach it from a stronger model's passing turns, on your pack only
([spec/contracts/tuning.md](../spec/contracts/tuning.md), [tuning.md](tuning.md)):

```bash
uv run harnesswalk tune curate --pack my_support --teacher local-27b --out dataset/my_support
uv run harnesswalk tune run --dataset my_support --trainer homelab-trl --student local-4b
uv run harnesswalk tune compare --candidate model/my_support_local4b --against local-4b \
  --packs my_support --canary-packs _starter
```

Curation reads nothing but your pack: the teacher answers your golden prompts and rewrites of
them in scratch tenants, and only the turns every deterministic scorer passed are kept. Your
golden cases are therefore also in the training data, so keep a few cases out of the golden set
(or rewrite them) if you want an honest held-out number. The comparison always runs a
prompt-only control arm beside the tuned model: when your skill text alone fixes the failure,
it is the cheaper lever. `trainers.yaml` says where training runs; nothing is ever trained on a
public server.

## What is coming in the app

The pack menu in the header (and in the empty chat) ends with **Add your own dataset**. It is
disabled for now, with the tooltip "Feature coming soon.", and links back to this guide. Adding
documents from the browser arrives with accounts and private workspaces: uploads into a pack of
your own, kept out of the shared packs every visitor reads. Until then a pack is a folder under
`datasets/`, and everything in this guide works the same in the app once the folder exists.

## Troubleshooting

- **The pack is not listed.** The directory must sit directly under `datasets/`, be named with
  lowercase letters, digits, `_` or `-` (no spaces, capitals or dots), and hold a `pack.yaml` or
  a `system_prompt.md`. A `pack.yaml` that is not valid YAML still lists the pack under its
  directory name; `uv run harnesswalk packs validate <id>` says what is wrong. The web app reads
  the list on every page load, so reload the page.
- **A PDF has no text.** `packs ingest` counts the document but adds no chunks for it. Check
  what `pypdf` extracts:
  `uv run python -c "from pathlib import Path; from harnesswalk.packs import pdf_text; print(repr(pdf_text(Path('datasets/my_support/docs/manual.pdf'))[:300]))"`.
  An empty string means a scanned PDF with no text layer: run it through OCR (for example
  `ocrmypdf`) or convert it to markdown, which also gives you headings to chunk on.
- **Citations are missing.** First look at the `knowledge` line of the walk. `hits=0` means
  nothing was retrieved: the question shares too few words with the passages
  (`stores.knowledge.min_keyword_coverage`) or the documents were never indexed (run
  `packs ingest` and check the chunk count). With hits but no `[n]` in the reply, the output
  guardrails append "(No sources were cited for this answer.)": tell the model to cite in
  `system_prompt.md`. A golden case that expects a citation fails when the passage number in
  its fixture is stale, which happens after you edit or add a document: run the probe again.
- **A tool is refused by policy.** `decision=deny rule=agent.allowlist` means the name is not in
  `agent.allow_tools` (the validator reports this too). `rule=default.deny` means no rule
  matches the tool's class in this mode. `is not offered here` means public mode, which only
  runs pure read tools. `decision=needs_approval` waits for a person: answer the approval card
  in the chat, or pass `--approval approve` (or `deny`) on the command line. An approval nobody
  answers expires after `approval.timeout_s` and the call does not run.
- **A tool fails in the sandbox.** `outcome=error` on the `sandbox` line, with
  `network=blocked`, usually means the function tried a network call or a file write outside its
  work directory, and the sandbox refused it: a network call needs `action_class: external`.
  The error text goes back to the model as the tool result, so the reply usually says what
  failed. `outcome=timeout` means `timeout_ms` passed.
