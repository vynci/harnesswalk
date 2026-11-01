# Provenance: finance

**Hollowmere Capital Partners** is a synthetic dataset pack for Harnesswalk. Synthetic, no real people, companies
or identifiers: every business, product, place, person, id and number in it is invented.

| | |
|---|---|
| Licence | CC0 1.0 Universal (public domain dedication) |
| Generator | `tools/gen_pack.py` (stages core, docs, golden, then finalize) |
| Model | `claude-sonnet-5-5` (Anthropic Messages API), thinking off |
| Generated on | 2026-11-01 |
| Prompts | `tools/packs/_common.prompt.md`, `tools/packs/finance.prompt.md` |
| Document plan | `tools/packs/finance.plan.json` |
| PDF renderings | `docs/capital_call_notice_hcp01.pdf` (source `sources/capital_call_notice_hcp01.md`), `docs/glossary_of_fund_terms.pdf` (source `sources/glossary_of_fund_terms.md`) |

Prompt file hashes (sha256, first 16 hex):

| File | Hash |
|---|---|
| `tools/packs/_common.prompt.md` | `0089694bc323b7db` |
| `tools/packs/finance.prompt.md` | `ac79312780428856` |

Token usage and cost (prices from `config/models.yaml`, $2.00 in and
$10.00 out per million tokens):

| Call | Input tokens | Output tokens | Cost |
|---|---|---|---|
| core | 4950 | 16000 | $0.1699 |
| core | 4950 | 13161 | $0.1415 |
| docs[0] | 12532 | 4104 | $0.0661 |
| docs[1] | 12463 | 3891 | $0.0638 |
| docs[2] | 12404 | 4139 | $0.0662 |
| docs[3] | 12272 | 2918 | $0.0537 |
| golden | 26514 | 14865 | $0.2017 |
| **Total** | **86085** | **59078** | **$0.7629** |

Local steps (no model): `guardrails.yaml` and the fixed `pack.yaml` fields come from the
script; em-dashes are replaced; the PDFs are rendered from their markdown sources with
`fpdf2`; `[[cite:doc]]` markers in the fake fixtures become the passage numbers the harness
retrieves for each golden case (fake provider, hash embeddings).

Review after generation: the build agent ran `harnesswalk packs validate finance` and
`harnesswalk eval finance --suite golden` (fake provider) until both were clean, read the
tool tables, skills, system prompt and a sample of documents and fixtures, and searched the
proper nouns for real brands, clubs and places. Edits after generation, which are part of
the pack:

- The first core call stopped at its 16,000 token limit before the document plan; the stage
  was run again with a higher limit. Both calls are in the usage table.
- Skill and tool descriptions containing ": " were quoted, so their YAML front matter parses.
- The system prompt and the `account_security` skill showed the account number format as a
  literal example, which the pack's own guardrail pattern matches; both now describe it in
  words.

Content review (P5 integration pass, 2026-11-01, done by the integration builder, an agent
working for the maintainer; the owner may add a read of their own here): read this card and
`tools/packs/finance.review.md`; listed every capitalised name in the system prompt, skills,
documents, PDF sources, seeds, tool tables and prompts and checked them for real brands, clubs,
places and people; searched for well-known brand names, street addresses, postcodes and URLs
(none); read `fund_sheet_hcp01.md`, `distribution_waterfall_guide.md`,
`default_and_late_payment_policy.md` and both PDF sources in full and sampled the rest; ran the
pack validator's PII scan (clean) and the golden suite (fake provider) in a data directory
holding every other pack's runs (clean). The manager, its four funds and every investor are
invented; there are no bank details, IBANs or SWIFT codes. Every document that touches money
says the desk gives no investment, tax or legal advice and sends the investor to their own
advisers; nothing recommends buying, selling, paying or delaying. The two cases that expect a
typed account number restored in the reply are `modes: [dev]`. Reviewed: yes.
