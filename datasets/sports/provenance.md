# Provenance: sports

**The Copperline Community League** is a synthetic dataset pack for Harnesswalk. Synthetic, no real people, companies
or identifiers: every business, product, place, person, id and number in it is invented.

| | |
|---|---|
| Licence | CC0 1.0 Universal (public domain dedication) |
| Generator | `tools/gen_pack.py` (stages core, docs, golden, then finalize) |
| Model | `claude-sonnet-5-5` (Anthropic Messages API), thinking off |
| Generated on | 2026-11-01 |
| Prompts | `tools/packs/_common.prompt.md`, `tools/packs/sports.prompt.md` |
| Document plan | `tools/packs/sports.plan.json` |
| PDF renderings | `docs/league_rulebook.pdf` (source `sources/league_rulebook.md`), `docs/discipline_code.pdf` (source `sources/discipline_code.md`) |

Prompt file hashes (sha256, first 16 hex):

| File | Hash |
|---|---|
| `tools/packs/_common.prompt.md` | `0089694bc323b7db` |
| `tools/packs/sports.prompt.md` | `9cebc79b58c18550` |

Token usage and cost (prices from `config/models.yaml`, $2.00 in and
$10.00 out per million tokens):

| Call | Input tokens | Output tokens | Cost |
|---|---|---|---|
| core | 4694 | 8566 | $0.0950 |
| docs[0] | 11676 | 4383 | $0.0672 |
| docs[1] | 11751 | 3321 | $0.0567 |
| docs[2] | 11680 | 3841 | $0.0618 |
| docs[3] | 11482 | 2823 | $0.0512 |
| golden | 25400 | 14268 | $0.1935 |
| **Total** | **76683** | **37202** | **$0.5254** |

Local steps (no model): `guardrails.yaml` and the fixed `pack.yaml` fields come from the
script; em-dashes are replaced; the PDFs are rendered from their markdown sources with
`fpdf2`; `[[cite:doc]]` markers in the fake fixtures become the passage numbers the harness
retrieves for each golden case (fake provider, hash embeddings).

Review after generation: the build agent ran `harnesswalk packs validate sports` and
`harnesswalk eval sports --suite golden` (fake provider) until both were clean, read the
tool tables, skills, system prompt and a sample of documents and fixtures, and searched the
proper nouns for real brands, clubs and places. Edits after generation, which are part of
the pack:

- Club names renamed to invented stems so none matches a real club: Ashgrove to Ashquill,
  Millbrook to Millcress, Northfield to Norrowby, Quarry Town to Quarrimoor Town, Riverside
  Wanderers to Rivelmere Wanderers, Tannery Park to Tanswold Park, Brackenridge to
  Brackenwhist, Lowmoor to Lowfennel.
- The `rules_squad_size` prompt was reworded ("Under the league rulebook, ...") so keyword
  retrieval finds the rulebook passage it cites.

Content review (P5 integration pass, 2026-11-01, done by the integration builder, an agent
working for the maintainer; the owner may add a read of their own here): read this card and
`tools/packs/sports.review.md`; listed every capitalised name in the system prompt, skills,
documents, PDF sources, seeds, tool tables and prompts and checked them for real brands, clubs,
places and people; searched for well-known brand names, street addresses, postcodes and URLs
(none); read `team_sheet_ash.md`, `code_of_conduct.md`, `registration_eligibility.md` and the
rulebook source in full and sampled the rest; ran the pack validator's PII scan (clean) and the
golden suite (fake provider) in a data directory holding every other pack's runs (clean). The
league, clubs and players are invented; team sheets hold a name, a player id and a position only
(no dates of birth or contact details), and venue names (Harbour Park, Quarry Lane, Old Mill
Ground, Ridge Meadow) are generic descriptors. Reviewed: yes.
