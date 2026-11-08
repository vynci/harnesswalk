# Provenance: gov_services

**Aldervale Services Desk** is a synthetic dataset pack for harnesswalk. Synthetic, no real people, companies
or identifiers: every business, product, place, person, id and number in it is invented.

| | |
|---|---|
| Licence | CC0 1.0 Universal (public domain dedication) |
| Generator | `tools/gen_pack.py` (hosted stages docs, then finalize; core, golden written by hand) |
| Model | `claude-haiku-5-5` (Anthropic Messages API), thinking off |
| Generated on | 2026-11-08 |
| Prompts | `tools/packs/_common.prompt.md`, `tools/packs/gov_services.prompt.md` |
| Document plan | `tools/packs/gov_services.plan.json` |
| PDF renderings | `docs/heating_rebate_guide.pdf` (source `sources/heating_rebate_guide.md`), `docs/guide_remise_chauffage.pdf` (source `sources/guide_remise_chauffage.md`) |

Prompt file hashes (sha256, first 16 hex):

| File | Hash |
|---|---|
| `tools/packs/_common.prompt.md` | `4209890cb96b1c56` |
| `tools/packs/gov_services.prompt.md` | `f1bd65360899c72f` |

Token usage and cost (prices from `config/models.yaml`, per million tokens: `claude-haiku-5-5` $0.10 in and $0.50 out):

| Call | Model | Input tokens | Output tokens | Cost |
|---|---|---|---|---|
| docs[heating_rebate_en,remise_chauffage_fr,child_activity_credit_en,credit_activites_enfants_fr] | `claude-haiku-5-5` | 11045 | 2656 | $0.0024 |
| docs[senior_transit_pass_en,laissez_passer_aines_fr,proof_of_residence_en,preuve_residence_fr] | `claude-haiku-5-5` | 10937 | 2934 | $0.0026 |
| docs[privacy_notice_en,avis_confidentialite_fr,heating_rebate_guide,guide_remise_chauffage] | `claude-haiku-5-5` | 10958 | 5340 | $0.0038 |
| docs[child_activity_credit_en,credit_activites_enfants_fr,proof_of_residence_en,preuve_residence_fr] | `claude-haiku-5-5` | 11053 | 4762 | $0.0035 |
| **Total** | | **43993** | **15692** | **$0.0123** |

Local steps (no model): `guardrails.yaml` and the fixed `pack.yaml` fields come from the
script; em-dashes are replaced; the PDFs are rendered from their markdown sources with
`fpdf2`; `[[cite:doc]]` markers in the fake fixtures become the passage numbers the harness
retrieves for each golden case (fake provider, hash embeddings).

Review after generation: the build agent ran `harnesswalk packs validate gov_services` and
`harnesswalk eval gov_services --suite golden` (fake provider) until both were clean, read the
tool tables, skills, system prompt and a sample of documents and fixtures, and searched the
proper nouns for real brands, clubs and places. Edits after generation, which are part of
the pack:

- The card, system prompt, skills, tool tables, guardrails, seeds, prompts page, golden set
  and fixtures were written by hand (the build agent, P2); only the twelve documents came from
  the generator (`claude-haiku-5-5`). Four documents came back under 300 words and were
  generated again with a longer brief (both calls are in the usage table).
- `guardrails.yaml` is hand-written: two civic address patterns (a French and an English
  street address) are national ids for this pack; the validator checked that they match no
  other pack's content.
- Application references are a letter and four digits (`AV-K4417`): a longer all-digit form
  read as a phone number to the PII detector and would have been redacted from the prompt.
- The synthetic social insurance number in the French PII case passes the Luhn check on
  purpose and was generated at random; it belongs to no one.

Content review (P2, 2026-11-08, done by the build agent, an agent working for the maintainer;
the owner may add a read of their own here): read every English and French document and
checked that each pair states the same rules and numbers (12 months, 58,000 dollars, 450 and
600 dollars, 15 September to 15 December 2026, 25 and 10 business days, 30 days to appeal;
under 16, 300 and 500 dollars; 65, 2 years, 60 days, 15 business days, 10 dollars; the proof of
residence list; 90 days of transcripts); read the French for natural Canadian usage; listed
capitalised names (only Aldervale and the invented offices); searched for phone numbers,
emails, web and street addresses (none); ran the validator's PII scan with every pack's
patterns (clean) and the golden suite on the fake provider under `grounded` (27 of 27).
Reviewed: yes.
