# Provenance: device_fleet

**Kestrelway Field Services** is a synthetic dataset pack for harnesswalk. Synthetic, no real people, companies
or identifiers: every business, product, place, person, id and number in it is invented.

| | |
|---|---|
| Licence | CC0 1.0 Universal (public domain dedication) |
| Generator | `tools/gen_pack.py` (hosted stages docs, then finalize; core, golden written by hand) |
| Model | `claude-sonnet-5-5` (Anthropic Messages API), thinking off |
| Generated on | 2026-11-08 |
| Prompts | `tools/packs/_common.prompt.md`, `tools/packs/device_fleet.prompt.md` |
| Document plan | `tools/packs/device_fleet.plan.json` |
| PDF renderings | `docs/command_preconditions.pdf` (source `sources/command_preconditions.md`), `docs/roles_and_permissions.pdf` (source `sources/roles_and_permissions.md`) |

Prompt file hashes (sha256, first 16 hex):

| File | Hash |
|---|---|
| `tools/packs/_common.prompt.md` | `4209890cb96b1c56` |
| `tools/packs/device_fleet.prompt.md` | `9b445b64e3b792d8` |

Token usage and cost (prices from `config/models.yaml`, per million tokens: `claude-sonnet-5-5` $2.00 in and $10.00 out):

| Call | Model | Input tokens | Output tokens | Cost |
|---|---|---|---|---|
| docs[driving_rules_explainer,driver_score_formula,speed_limit_sources,command_preconditions] | `claude-sonnet-5-5` | 23689 | 5222 | $0.0996 |
| docs[inbox_outcome_glossary,roles_and_permissions,session_insights_reference,obd_tiers] | `claude-sonnet-5-5` | 23620 | 4502 | $0.0923 |
| docs[device_status_reference,webhook_signing,data_purge_procedure,sms_inbox_handling] | `claude-sonnet-5-5` | 23632 | 5403 | $0.1013 |
| **Total** | | **70941** | **15127** | **$0.2932** |

Local steps (no model): `guardrails.yaml` and the fixed `pack.yaml` fields come from the
script; em-dashes are replaced; the PDFs are rendered from their markdown sources with
`fpdf2`; `[[cite:doc]]` markers in the fake fixtures become the passage numbers the harness
retrieves for each golden case (fake provider, hash embeddings).

Review after generation: the build agent ran `harnesswalk packs validate device_fleet` and
`harnesswalk eval device_fleet --suite golden` (fake provider) until both were clean, read the
tool tables, skills, system prompt and a sample of documents and fixtures, and searched the
proper nouns for real brands, clubs and places. Edits after generation, which are part of
the pack:

- The card, system prompt, skills (including `skills/policy.md`), tool tables, seeds, prompts
  page, golden set and fixtures were written by hand (the build agent, P2) from the plan's
  telematics notes; only the twelve documents came from the generator. The golden set was
  built with a small local script and its node paths filled from a fake-provider run, then read
  case by case.
- The tool shapes follow a real fleet platform's API (asset status, session insights, driver
  events with their payload fields, driver summary, the webhook inbox, device commands and their
  error codes) and the rule thresholds, score weights and retry schedule are that platform's
  public defaults. Every name, id, plate, place, date and number in the pack is invented; no
  real telemetry, id, road, coordinate or SMS content was used.
- The real API has one command endpoint; the pack splits it into one tool per risk level
  (`obd_scan`, `reboot_device`, `toggle_rule`, `power_off_device`, `clear_sms`,
  `purge_device`) because the harness's policy decides per tool. Agent update is left to the
  console and not a tool.
- `skills/policy.md` front matter: the description was quoted (it held a colon).

Content review (P2, 2026-11-08, done by the build agent, an agent working for the maintainer;
the owner may add a read of their own here): read every document in full and checked each
number against the brief and the tool tables (thresholds, bands, weights, the worked score of
71 over 38.4 km, the 8 attempts over about 44 hours, the 60 s TTL); listed every capitalised
name in the documents and found only the pack's own invented assets, places and makes
(Kestrelway, Brindleford, Carrowmoor, Ostley Cross, Quellby, Kelda, Vesk, Halvard, Torrin,
Brannoch); searched for phone numbers, emails, web and street addresses (none); ran the pack
validator's PII scan (clean) and the golden suite on the fake provider under `governed`
(28 of 28). Reviewed: yes.
