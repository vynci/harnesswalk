# Provenance: helpdesk_lite

**Pebblebrook IT Helpdesk** is a synthetic dataset pack for harnesswalk. Synthetic, no real people, companies
or identifiers: every business, product, place, person, id and number in it is invented.

| | |
|---|---|
| Licence | CC0 1.0 Universal (public domain dedication) |
| Generator | `tools/gen_pack.py` (hosted stages docs, then finalize; core, golden written by hand) |
| Model | `claude-haiku-5-5` (Anthropic Messages API), thinking off |
| Generated on | 2026-11-08 |
| Prompts | `tools/packs/_common.prompt.md`, `tools/packs/helpdesk_lite.prompt.md` |
| Document plan | `tools/packs/helpdesk_lite.plan.json` |
| PDF renderings | `docs/it_policy.pdf` (source `sources/it_policy.md`), `docs/security_basics.pdf` (source `sources/security_basics.md`) |

Prompt file hashes (sha256, first 16 hex):

| File | Hash |
|---|---|
| `tools/packs/_common.prompt.md` | `4209890cb96b1c56` |
| `tools/packs/helpdesk_lite.prompt.md` | `d8c1c8f8df55910c` |

Token usage and cost (prices from `config/models.yaml`, per million tokens: `claude-haiku-5-5` $0.10 in and $0.50 out):

| Call | Model | Input tokens | Output tokens | Cost |
|---|---|---|---|---|
| docs[it_policy,password_policy,vpn_guide,printer_guide] | `claude-haiku-5-5` | 8865 | 3415 | $0.0026 |
| docs[wifi_guide,file_share_guide,video_calls_guide,equipment_loans] | `claude-haiku-5-5` | 8849 | 4075 | $0.0029 |
| docs[new_starter_checklist,security_basics] | `claude-haiku-5-5` | 8691 | 2473 | $0.0021 |
| **Total** | | **26405** | **9963** | **$0.0076** |

Local steps (no model): `guardrails.yaml` and the fixed `pack.yaml` fields come from the
script; em-dashes are replaced; the PDFs are rendered from their markdown sources with
`fpdf2`; `[[cite:doc]]` markers in the fake fixtures become the passage numbers the harness
retrieves for each golden case (fake provider, hash embeddings).

Review after generation: the build agent ran `harnesswalk packs validate helpdesk_lite` and
`harnesswalk eval helpdesk_lite --suite golden` (fake provider) until both were clean, read the
tool tables, skills, system prompt and a sample of documents and fixtures, and searched the
proper nouns for real brands, clubs and places. Edits after generation, which are part of
the pack:

- The card, system prompt, skills, tool tables, seeds, prompts page, golden set and fixtures
  were written by hand (the build agent, P2); only the ten documents came from the generator
  (`claude-haiku-5-5`). Under its `minimal` profile no document is retrieved; they are there
  for a learner who moves the pack up the ladder.
- The golden set is short single lookups with answers that echo a tool value, so a small
  local model can pass it: no approvals, no citations, no memory.

Content review (P2, 2026-11-08, done by the build agent, an agent working for the maintainer;
the owner may add a read of their own here): read every document and checked the numbers and
names against the brief and the tool tables (desk hours per office, printer names, guest
networks, the 5 minute reset and 15 minute lockout, the 01:00 to 02:00 backup); listed
capitalised names (only Pebblebrook and its invented printers, rooms and networks); searched
for phone numbers, emails, web and street addresses (none); ran the validator's PII scan
(clean) and the golden suite on the fake provider under `minimal`. Reviewed: yes.
