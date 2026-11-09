# Model ladder scorecard

Which model passes which pack's golden set when the model really answers. Each cell is
a `model`-suite eval run: every golden case of the pack, answered by the model alias
(pinned for every call of the case turn) and judged by the deterministic scorers only
(path, tool calls, citations, answer match, checks, schema, PII leak). No LLM judge,
so a cell is reproducible from the trace. `fake-small` answers from the case fixtures,
so its column is the golden suite itself and every pack should be green there.

- Generated: 2026-11-09 07:33 (UTC) by `harnesswalk scorecard`
- Harness: Harnesswalk 0.1.1, spec 0.2.0
- Ollama: http://192.168.8.107:11434 (version 0.34.1)
- Tenant: `scorecard`; embeddings: fake (hash vectors, the same for every model); case timeout: 120.0 s
- Grading: model grade; samples per cell: 2
- Profiles: each pack runs on the harness profile its `pack.yaml` names (in brackets after the pack)

Model grade (`spec/contracts/evaluator.md`, "Grading modes"): `tool_calls` passes
when the expected calls appear in order, with extra calls to read tools allowed
(any other extra call, or a call when none is expected, fails); `answer_match`
compares normalized text (case, Unicode, whitespace, dates, times, amounts, numbers).

## Models

| Alias | Provider | Model | Parameters | Quantization | Temperature |
|---|---|---|---|---|---|
| `fake-small` | fake | `fake-small` |  |  | n/a (scripted) |
| `local-2b` | ollama | `qwen3.5:2b` | 1.9B | Q8_0 | 0 |
| `local-4b` | ollama | `qwen3.5:4b` | 4.2B | Q4_K_M | 0 |
| `local-9b` | ollama | `qwen3.5:9b` | 9.0B | Q4_K_M | 0 |
| `local-e4b` | ollama | `gemma4:e4b` | 7.5B | Q4_K_M | 0 |
| `local-8b` | ollama | `llama3.1:8b` | 8.0B | Q4_K_M | 0 |
| `hosted-haiku` | anthropic | `claude-haiku-5-5` |  |  | provider default (the API rejects temperature) |
| `hosted-sonnet` | anthropic | `claude-sonnet-5-5` |  |  | provider default (the API rejects temperature) |

## Pass counts

`passed/scored` per pack (a skipped case, outside its modes, is not scored), the mean over the samples with the min and max in brackets; `n/a` means the model could not run here (the reason is under the model below).

| Pack | `fake-small` | `local-2b` | `local-4b` | `local-9b` | `local-e4b` | `local-8b` | `hosted-haiku` | `hosted-sonnet` |
|---|---|---|---|---|---|---|---|---|
| `_starter` (operated) | 16/16 | 7/16 | 7/16 | 6.5/16 (6-7) | 5/16 | 5/16 | 10/16¹ | 10/16¹ |
| `device_fleet` (governed) | 28/28 | 6/28 | 6/28 | 7/28 | 4/28 | 4/28 | 12/28¹ | 18/28¹ |
| `ecommerce` (governed) | 24/24 | 1.5/24 (1-2) | 4/24 | 7/24 | 6/24 | 2/24 (1-3) | 14/24¹ | 14/24¹ |
| `finance` (operated) | 25/25 | 7/25 | 8/25 | 12/25 | 7/25 | 4/25 | 18/25¹ | 17/25¹ |
| `gov_services` (grounded) | 27/27 | 0/27 | 10.5/27 (10-11) | 14.5/27 (14-15) | 10.5/27 (10-11) | 1/27 | 15/27¹ | 17/27¹ |
| `healthcare` (grounded) | 25/25 | 2/25 | 7/25 | 7.5/25 (7-8) | 8/25 | 2/25 | 10/25¹ | 12/25¹ |
| `helpdesk_lite` (minimal) | 21/21 | 17/21 | 16/21 | 17/21 | 19/21 | 14/21 | 19/21¹ | 19/21¹ |
| `logistics` (governed) | 24/24 | 2/24 | 1.5/24 (1-2) | 10.5/24 (10-11) | 8/24 | 0/24 | 7/24¹ | 9/24¹ |
| `long_session` (remembered) | 20/20 | 6.5/20 (6-7) | 8/20 | 10.5/20 (9-12) | 8/20 | 3/20 | 14/20¹ | 11/20¹ |
| `sports` (grounded) | 22/22 | 8.5/22 (8-9) | 12/22 | 12/22 | 15/22 (14-16) | 6/22 | 14/22¹ | 15/22¹ |
| **All packs** | **232/232** (100%) | **57.5/232** (25%) (56-59) | **80/232** (34%) | **104.5/232** (45%) (104-105) | **90.5/232** (39%) (89-92) | **41/232** (18%) (40-42) | **133/232** (57%) | **142/232** (61%) |

¹ Carried forward from the 2026-11-09 run (hosted-haiku, hosted-sonnet, one sample, provider default temperature) and re-graded offline under model grade from the stored case results: tool calls and answer match from the stored calls and replies, schema from the answer before the output guardrails (trace) with the JSON extraction; path, citations, checks and PII as stored (their routing predates the pinned price-cap change, so a `lookup` rule check can still fail on a priced model).

## Per model

Failures by scorer count failed cases per scorer (a case can fail several scorers);
`error` counts cases whose turn hit an error (a provider error or a timeout). Counts are means per sample.
"answer_match only" counts failed cases where every other scorer passed. The
`tool_calls` split says why that scorer failed: a call when none was expected, no call
at all, an expected write (non-read) tool not called, an expected read tool not called,
or the expected calls made with an extra or wrong one. "Fails where most pass" lists
the cases this model fails that at least half the other real models pass.

### `fake-small`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

- No failed case
- Tokens: 483,948 in, 32,790 out; cost $0.0000; wall time 13 s

### `local-2b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 18.5 | 99.5 | 21.5 | 103 | 8 | 2 | 0 | 1 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 71 | 3 | 9.5 | 12 | 4 |

- 174.5 failed cases; answer_match only: 60.5
- Fails where most pass: `device_fleet/score_formula_explained` (5 of 6), `ecommerce/order_lookup_qf20402` (5 of 6), `ecommerce/track_qt58120394` (5 of 6), `finance/mem_notice_delivery_set` (5 of 6), `gov_services/address_en_redacted` (5 of 6) and 55 more
- Tokens: 4,961,472 in, 98,588 out; cost $0.0000; wall time 681 s

### `local-4b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 34 | 68 | 24 | 102.5 | 6 | 2 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 28 | 11.5 | 16 | 12.5 | 0 |

- 152 failed cases; answer_match only: 57.5
- Fails where most pass: `_starter/return_window_cited` (5 of 6), `_starter/pii_leak_repaired` (4 of 6), `device_fleet/command_preconditions_kb` (4 of 6), `device_fleet/operator_reboot_denied` (4 of 6), `ecommerce/kb_quillridge_tent_weight` (4 of 6) and 31 more
- Tokens: 2,575,332 in, 64,290 out; cost $0.0000; wall time 675 s

### `local-9b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 52.5 | 60.5 | 8.5 | 99.5 | 5 | 2 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 3.5 | 33 | 13.5 | 10.5 | 0 |

- 127.5 failed cases; answer_match only: 48
- Fails where most pass: `finance/tool_calls_hcp02` (5 of 6), `gov_services/address_en_redacted` (5 of 6), `long_session/ferry_times_mir_car` (5 of 6), `_starter/pii_leak_repaired` (4 of 6), `_starter/trailhead_cited` (4 of 6) and 13 more
- Tokens: 2,313,638 in, 67,395 out; cost $0.0000; wall time 998 s

### `local-e4b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 61 | 73.5 | 13.5 | 105 | 8 | 2 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 2 | 48 | 10 | 12 | 1.5 |

- 141.5 failed cases; answer_match only: 47
- Fails where most pass: `_starter/cancel_needs_approval` (6 of 6), `device_fleet/score_kw0012` (6 of 6), `device_fleet/score_too_short` (6 of 6), `finance/mem_reporting_currency_set` (6 of 6), `finance/mem_statement_format_set` (6 of 6) and 28 more
- Tokens: 2,024,612 in, 51,376 out; cost $0.0000; wall time 751 s

### `local-8b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 34.5 | 111 | 47.5 | 124 | 8 | 3 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 69.5 | 6 | 10.5 | 25 | 0 |

- 191 failed cases; answer_match only: 62.5
- Fails where most pass: `_starter/structured_status` (6 of 6), `_starter/support_hours` (6 of 6), `device_fleet/failed_sync_lookup` (6 of 6), `finance/tool_nav_hcp02` (6 of 6), `healthcare/tool_find_appointment` (6 of 6) and 72 more
- Tokens: 2,075,153 in, 40,268 out; cost $0.0000; wall time 931 s

### `hosted-haiku`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 34 | 35 | 4 | 73 | 5 | 2 | 0 | 1 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 2 | 15 | 8 | 10 | 0 |

- 99 failed cases; answer_match only: 47
- Fails where most pass: `long_session/ferry_times_mir_car` (5 of 6), `logistics/track_then_eta_in_transit` (4 of 6), `sports/stats_pl203` (3 of 6)
- Tokens: 1,917,343 in, 68,588 out; cost $0.2260; wall time 2672 s

### `hosted-sonnet`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 19 | 28 | 1 | 61 | 4 | 2 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 12 | 11 | 1 | 3 | 1 |

- 90 failed cases; answer_match only: 47
- Fails where most pass: `device_fleet/command_preconditions_kb` (4 of 6), `finance/cite_distribution_payment_date` (3 of 6), `finance/cite_return_of_capital_share` (3 of 6), `logistics/kb_hub_cutoff` (3 of 6), `sports/stats_pl203` (3 of 6)
- Tokens: 2,046,811 in, 75,906 out; cost $4.8527; wall time 1089 s

## Regenerate

```sh
HARNESSWALK_OLLAMA_URL=http://192.168.8.107:11434 uv run harnesswalk scorecard --models fake-small,local-2b,local-4b,local-9b,local-e4b,local-8b --packs all --out docs/scorecard.md --json docs/scorecard/2026-11-09-local.json --samples 2 --carry docs/scorecard/2026-11-09-hosted.json --carry-models hosted-haiku,hosted-sonnet
```

The run detail of every cell is in the scorecard JSON (`docs/scorecard/2026-11-09-local.json`); `harnesswalk eval <pack> --suite model --model <alias>` replays one cell.
The command raises the hosted providers' call timeout (`models.providers.<name>.timeout_s`) to at least `--case-timeout` unless a `--set` names it, and sends Anthropic no `temperature` (the API rejects it for these models).
