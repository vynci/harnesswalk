# Provenance: ecommerce

**Quillfern Outfitters** is a synthetic dataset pack for harnesswalk. Synthetic, no real people, companies
or identifiers: every business, product, place, person, id and number in it is invented.

| | |
|---|---|
| Licence | CC0 1.0 Universal (public domain dedication) |
| Generator | `tools/gen_pack.py` (stages core, docs, golden, then finalize) |
| Model | `claude-sonnet-5-5` (Anthropic Messages API), thinking off |
| Generated on | 2026-11-01 |
| Prompts | `tools/packs/_common.prompt.md`, `tools/packs/ecommerce.prompt.md` |
| Document plan | `tools/packs/ecommerce.plan.json` |
| PDF renderings | `docs/returns_and_refunds_policy.pdf` (source `sources/returns_and_refunds_policy.md`), `docs/warranty_policy.pdf` (source `sources/warranty_policy.md`) |

Prompt file hashes (sha256, first 16 hex):

| File | Hash |
|---|---|
| `tools/packs/_common.prompt.md` | `0089694bc323b7db` |
| `tools/packs/ecommerce.prompt.md` | `4bd84ef9724d5fb2` |

Token usage and cost (prices from `config/models.yaml`, $2.00 in and
$10.00 out per million tokens):

| Call | Input tokens | Output tokens | Cost |
|---|---|---|---|
| core | 4950 | 8276 | $0.0927 |
| docs[0] | 11735 | 3610 | $0.0596 |
| docs[1] | 11818 | 3827 | $0.0619 |
| docs[2] | 11721 | 3471 | $0.0582 |
| docs[3] | 11515 | 2794 | $0.0510 |
| golden | 24704 | 17384 | $0.2232 |
| **Total** | **76443** | **39362** | **$0.5466** |

Local steps (no model): `guardrails.yaml` and the fixed `pack.yaml` fields come from the
script; em-dashes are replaced; the PDFs are rendered from their markdown sources with
`fpdf2`; `[[cite:doc]]` markers in the fake fixtures become the passage numbers the harness
retrieves for each golden case (fake provider, hash embeddings).

Review after generation: the build agent ran `harnesswalk packs validate ecommerce` and
`harnesswalk eval ecommerce --suite golden` (fake provider) until both were clean, read the
tool tables, skills, system prompt and a sample of documents and fixtures, and searched the
proper nouns for real brands, clubs and places. Edits after generation, which are part of
the pack:

- Product lines renamed away from words that are also real brand or product names:
  Glowworm to Fernglow, Ridgeway to Quillridge, Trailmate to Fernpath, Nightjar to Mothdown,
  Hearthstone to Emberhollow (file names, doc ids and case ids included). Ridgeway and
  Hearthstone came from the pack prompt, which is kept as it was run.
- Scan times in `track_shipment` rows read "2026-10-30 at 06:40": a date and a time joined by a
  space look like a phone number to the PII detector, which would redact them in a reply.

A maintainer's human review of the content (safety, licence card, no PII) is part of the P5
integration pass.
