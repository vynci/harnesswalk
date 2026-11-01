# Provenance: healthcare

**Brindlemoor Family Clinic** is a synthetic dataset pack for Harnesswalk. Synthetic, no real people, companies
or identifiers: every business, product, place, person, id and number in it is invented.

| | |
|---|---|
| Licence | CC0 1.0 Universal (public domain dedication) |
| Generator | `tools/gen_pack.py` (stages core, docs, golden, then finalize) |
| Model | `claude-sonnet-5-5` (Anthropic Messages API), thinking off |
| Generated on | 2026-11-01 |
| Prompts | `tools/packs/_common.prompt.md`, `tools/packs/healthcare.prompt.md` |
| Document plan | `tools/packs/healthcare.plan.json` |
| PDF renderings | `docs/appointment_cancellation_policy.pdf` (source `sources/appointment_cancellation_policy.md`), `docs/billing_faq.pdf` (source `sources/billing_faq.md`) |

Prompt file hashes (sha256, first 16 hex):

| File | Hash |
|---|---|
| `tools/packs/_common.prompt.md` | `0089694bc323b7db` |
| `tools/packs/healthcare.prompt.md` | `11dd2adebc6495d8` |

Token usage and cost (prices from `config/models.yaml`, $2.00 in and
$10.00 out per million tokens):

| Call | Input tokens | Output tokens | Cost |
|---|---|---|---|
| core | 5428 | 11514 | $0.1260 |
| docs[0] | 12938 | 3775 | $0.0636 |
| docs[1] | 12877 | 3796 | $0.0637 |
| docs[2] | 12944 | 4188 | $0.0678 |
| docs[3] | 12659 | 2636 | $0.0517 |
| golden | 26559 | 16776 | $0.2209 |
| **Total** | **83405** | **42685** | **$0.5937** |

Local steps (no model): `guardrails.yaml` and the fixed `pack.yaml` fields come from the
script; em-dashes are replaced; the PDFs are rendered from their markdown sources with
`fpdf2`; `[[cite:doc]]` markers in the fake fixtures become the passage numbers the harness
retrieves for each golden case (fake provider, hash embeddings).

Review after generation: the build agent ran `harnesswalk packs validate healthcare` and
`harnesswalk eval healthcare --suite golden` (fake provider) until both were clean, read the
tool tables, skills, system prompt and a sample of documents and fixtures, and searched the
proper nouns for real brands, clubs and places. Edits after generation, which are part of
the pack:

- The system prompt and `docs/new_patient_registration.md` showed the health number format as
  a literal example, which the pack's own guardrail pattern matches (and would redact in a
  reply); both now describe the format in words.
- "Mill Lane" (a common real street name) became "Willowbank Row".
- Cue-based name redaction is off for this pack (the brief asked for it): the detector in
  `harnesswalk/control/pii.py` compiles its cue pattern case-insensitively, so "I am looking"
  or "I'm not sure" are taken as names. The `pii_name_hours` case became `pii_id_phone_hours`
  (a health number and a phone together) until the detector is fixed. (Fixed in the P5
  integration pass: names are redacted again and `pii_name_hours` is back.)

Content review (P5 integration pass, 2026-11-01, done by the integration builder, an agent
working for the maintainer; the owner may add a read of their own here): read this card and
`tools/packs/healthcare.review.md`; listed every capitalised name in the system prompt, skills,
documents, PDF sources, seeds, tool tables and prompts and checked them for real brands, clubs,
places and people; searched for well-known brand names, street addresses, postcodes and URLs
(none); read `patient_faq.md`, `after_hours_urgent_care.md`, `lab_tests_guide.md` and
`vaccination_clinic_guide.md` in full and sampled the rest; ran the pack validator's PII scan
(clean) and the golden suite (fake provider) in a data directory holding every other pack's runs
(clean). The clinic, sites (north, riverside, eastgate), the Willowbank, Eastgate Quarter and
Harbour District areas and every person are invented or generic descriptors. Clinical content
stays within front-desk policy: the documents and the `clinical_boundaries` skill decline
advice, diagnosis, dosing and symptom interpretation and send urgent symptoms to emergency
services; the preparation notes (fasting for a blood test, the wait after a vaccination) are
clinic procedure, not treatment advice. Name redaction is on again now that the detector scopes
its case rule to the cue words (`pii_name_hours`); the six cases that expect a typed value
restored in the reply are `modes: [dev]`. Reviewed: yes.
