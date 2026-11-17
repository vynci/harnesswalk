# Provenance: long_session

**Fernhollow Journeys** is a synthetic dataset pack for harnesswalk. Synthetic, no real people, companies
or identifiers: every business, product, place, person, id and number in it is invented.

| | |
|---|---|
| Licence | CC0 1.0 Universal (public domain dedication) |
| Generator | `tools/gen_pack.py` (hosted stages docs, notes, then finalize; core, golden written by hand) |
| Model | `claude-haiku-5-5` (Anthropic Messages API), thinking off |
| Generated on | 2026-11-08 |
| Prompts | `tools/packs/_common.prompt.md`, `tools/packs/long_session.prompt.md` |
| Document plan | `tools/packs/long_session.plan.json` |
| PDF renderings | `docs/booking_terms.pdf` (source `sources/booking_terms.md`), `docs/seasonal_calendar.pdf` (source `sources/seasonal_calendar.md`) |

Prompt file hashes (sha256, first 16 hex):

| File | Hash |
|---|---|
| `tools/packs/_common.prompt.md` | `4209890cb96b1c56` |
| `tools/packs/long_session.prompt.md` | `6dde7560ffeccc65` |

Token usage and cost (prices from `config/models.yaml`, per million tokens: `claude-haiku-5-5` $0.10 in and $0.50 out):

| Call | Model | Input tokens | Output tokens | Cost |
|---|---|---|---|---|
| docs[carrow_island_guide,mirelake_town_guide,brannock_highlands_guide,port_ellery_guide] | `claude-haiku-5-5` | 10737 | 2650 | $0.0024 |
| docs[ferry_fare_rules,luggage_policy,booking_terms,seasonal_calendar] | `claude-haiku-5-5` | 10713 | 2426 | $0.0023 |
| docs[accessibility_guide,travel_documents_checklist] | `claude-haiku-5-5` | 10487 | 1906 | $0.0020 |
| docs[mirelake_town_guide,brannock_highlands_guide,port_ellery_guide,ferry_fare_rules] | `claude-haiku-5-5` | 10828 | 3356 | $0.0028 |
| docs[luggage_policy,booking_terms,seasonal_calendar] | `claude-haiku-5-5` | 10684 | 2698 | $0.0024 |
| notes[lc1] | `claude-haiku-5-5` | 808 | 3477 | $0.0018 |
| notes[lc2] | `claude-haiku-5-5` | 703 | 3729 | $0.0019 |
| notes[lc3] | `claude-haiku-5-5` | 707 | 3510 | $0.0018 |
| notes[lc4] | `claude-haiku-5-5` | 852 | 4546 | $0.0024 |
| notes[lc1+] | `claude-haiku-5-5` | 527 | 1724 | $0.0009 |
| notes[lc2+] | `claude-haiku-5-5` | 504 | 2033 | $0.0011 |
| notes[lc3+] | `claude-haiku-5-5` | 503 | 1983 | $0.0010 |
| notes[lc4+] | `claude-haiku-5-5` | 501 | 2181 | $0.0011 |
| **Total** | | **58554** | **36219** | **$0.0239** |

Local steps (no model): `guardrails.yaml` and the fixed `pack.yaml` fields come from the
script; em-dashes are replaced; the PDFs are rendered from their markdown sources with
`fpdf2`; `[[cite:doc]]` markers in the fake fixtures become the passage numbers the harness
retrieves for each golden case (fake provider, hash embeddings).

Review after generation: the build agent ran `harnesswalk packs validate long_session` and
`harnesswalk eval long_session --suite golden` (fake provider) until both were clean, read the
tool tables, skills, system prompt and a sample of documents and fixtures, and searched the
proper nouns for real brands, clubs and places. Edits after generation, which are part of
the pack:

- The card, system prompt, skills, tool tables, seeds, prompts page, golden set and fixtures
  were written by hand (the build agent, P2); the ten documents and the 34 pasted planning
  notes in the long golden cases came from the generator (`claude-haiku-5-5`). Seven documents
  came back under 300 words and were generated again with a longer brief. The notes were
  written by `tools/packs/long_session_notes.py` (calls `notes[...]` in the usage table); each
  note's first sentence was given word for word, so a fact the summary must keep sits in the
  first 160 characters of its turn.
- Long turns paste one or two notes (about 500 to 1,100 tokens each) so the session slice
  crosses its compaction threshold (0.8 of 5,000 tokens) at a known turn on the fake provider.
- Trip legs read "2026-11-14 at 12:30": a date followed directly by a time read as a phone
  number to the PII scan.

Content review (P2, 2026-11-08, done by the build agent, an agent working for the maintainer;
the owner may add a read of their own here): read every document and checked the numbers
against the brief and the tool tables (the four MIR-CAR departures and the 23:30 overnight
sailing, 1 h 40 min, the fares and children's rules, 23 kg bags, bicycles 6.00, the 20 percent
deposit and the three cancellation bands, no ferry on 25 December 2026); skimmed every pasted
note and listed capitalised names (only the invented destinations, hotels, coves and trails);
searched for phone numbers, emails, web and street addresses (none); ran the validator's PII
scan (clean) and the golden suite on the fake provider under `remembered` (20 of 20).
Reviewed: yes.
