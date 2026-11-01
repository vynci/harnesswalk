# Provenance: logistics

**Tallowgate Freight** is a synthetic dataset pack for harnesswalk. Synthetic, no real people, companies
or identifiers: every business, product, place, person, id and number in it is invented.

| | |
|---|---|
| Licence | CC0 1.0 Universal (public domain dedication) |
| Generator | `tools/gen_pack.py` (stages core, docs, golden, then finalize) |
| Model | `claude-sonnet-5-5` (Anthropic Messages API), thinking off |
| Generated on | 2026-11-01 |
| Prompts | `tools/packs/_common.prompt.md`, `tools/packs/logistics.prompt.md` |
| Document plan | `tools/packs/logistics.plan.json` |
| PDF renderings | `docs/reroute_sop.pdf` (source `sources/reroute_sop.md`), `docs/hub_directory.pdf` (source `sources/hub_directory.md`) |

Prompt file hashes (sha256, first 16 hex):

| File | Hash |
|---|---|
| `tools/packs/_common.prompt.md` | `0089694bc323b7db` |
| `tools/packs/logistics.prompt.md` | `c35c08fd03ddbd8f` |

Token usage and cost (prices from `config/models.yaml`, $2.00 in and
$10.00 out per million tokens):

| Call | Input tokens | Output tokens | Cost |
|---|---|---|---|
| core | 4954 | 13043 | $0.1403 |
| docs[0] | 13249 | 3936 | $0.0659 |
| docs[1] | 13139 | 4577 | $0.0720 |
| docs[2] | 13223 | 4642 | $0.0729 |
| docs[3] | 12897 | 2226 | $0.0481 |
| golden | 27715 | 24640 | $0.3018 |
| **Total** | **85177** | **53064** | **$0.7010** |

Local steps (no model): `guardrails.yaml` and the fixed `pack.yaml` fields come from the
script; em-dashes are replaced; the PDFs are rendered from their markdown sources with
`fpdf2`; `[[cite:doc]]` markers in the fake fixtures become the passage numbers the harness
retrieves for each golden case (fake provider, hash embeddings).

Review after generation: the build agent ran `harnesswalk packs validate logistics` and
`harnesswalk eval logistics --suite golden` (fake provider) until both were clean, read the
tool tables, skills, system prompt and a sample of documents and fixtures, and searched the
proper nouns for real brands, clubs and places. Edits after generation, which are part of
the pack:

- "Tarbet" (a real village name) became "Tarnwick" in hub names.

A maintainer's human review of the content (safety, licence card, no PII) is part of the P5
integration pass.
