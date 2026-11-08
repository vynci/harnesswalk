# Model ladder scorecard

Which model passes which pack's golden set when the model really answers. Each cell is
a `model`-suite eval run: every golden case of the pack, answered by the model alias
(pinned for every call of the case turn) and judged by the deterministic scorers only
(path, tool calls, citations, answer match, checks, schema, PII leak). No LLM judge,
so a cell is reproducible from the trace. `fake-small` answers from the case fixtures,
so its column is the golden suite itself and every pack should be green there.

- Generated: 2026-11-08 20:30 (UTC) by `harnesswalk scorecard`
- Harness: Harnesswalk 0.1.1, spec 0.1.0
- Ollama: http://192.168.8.107:11434 (version 0.34.1)
- Tenant: `scorecard`; embeddings: fake (hash vectors, the same for every model); case timeout: 120.0 s
- Grading: model grade; samples per cell: 2; temperature: 0; n/a (scripted)

Model grade (`spec/contracts/evaluator.md`, "Grading modes"): `tool_calls` passes
when the expected calls appear in order, with extra calls to read tools allowed
(any other extra call, or a call when none is expected, fails); `answer_match`
compares normalized text (case, Unicode, whitespace, dates, times, amounts, numbers).

## Models

| Alias | Provider | Model | Parameters | Quantization |
|---|---|---|---|---|
| `fake-small` | fake | `fake-small` |  |  |
| `local-2b` | ollama | `qwen3.5:2b` | 1.9B | Q8_0 |
| `local-4b` | ollama | `qwen3.5:4b` | 4.2B | Q4_K_M |
| `local-9b` | ollama | `qwen3.5:9b` | 9.0B | Q4_K_M |
| `local-e4b` | ollama | `gemma4:e4b` | 7.5B | Q4_K_M |
| `local-8b` | ollama | `llama3.1:8b` | 8.0B | Q4_K_M |
| `hosted-haiku` | anthropic | `claude-haiku-5-5` |  |  |
| `hosted-sonnet` | anthropic | `claude-sonnet-5-5` |  |  |

## Pass counts

`passed/scored` per pack (a skipped case, outside its modes, is not scored), the mean over the samples with the min and max in brackets; `n/a` means the model could not run here (the reason is under the model below).

| Pack | `fake-small` | `local-2b` | `local-4b` | `local-9b` | `local-e4b` | `local-8b` | `hosted-haiku` | `hosted-sonnet` |
|---|---|---|---|---|---|---|---|---|
| `_starter` | 16/16 (16-16) | 7/16 (7-7) | 8/16 (8-8) | 6.5/16 (6-7) | 5/16 (5-5) | 4.5/16 (4-5) | 9/16¹ | 10/16¹ |
| `ecommerce` | 24/24 (24-24) | 3/24 (2-4) | 4/24 (4-4) | 7/24 (7-7) | 7/24 (6-8) | 3/24 (3-3) | 15/24¹ | 10/24¹ |
| `finance` | 25/25 (25-25) | 6.5/25 (6-7) | 6/25 (6-6) | 13/25 (13-13) | 7/25 (7-7) | 4/25 (4-4) | 17/25¹ | 18/25¹ |
| `healthcare` | 25/25 (25-25) | 3/25 (3-3) | 2/25 (2-2) | 8/25 (8-8) | 7.5/25 (7-8) | 3/25 (3-3) | 10/25¹ | 12/25¹ |
| `logistics` | 24/24 (24-24) | 2/24 (2-2) | 4.5/24 (4-5) | 8.5/24 (8-9) | 8.5/24 (8-9) | 0/24 (0-0) | 2/24¹ | 5/24¹ |
| `sports` | 22/22 (22-22) | 7.5/22 (7-8) | 8/22 (8-8) | 8/22 (8-8) | 14.5/22 (14-15) | 7/22 (7-7) | 14/22¹ | 6/22¹ |
| **All packs** | **136/136** (100%) (136-136) | **29/136** (21%) (27-31) | **32.5/136** (24%) (32-33) | **51/136** (38%) (50-52) | **49.5/136** (36%) (49-50) | **21.5/136** (16%) (21-22) | **67/136** (49%) | **61/136** (45%) |

¹ Carried forward from the 2026-11-08 run (hosted-haiku, hosted-sonnet, one sample, provider default temperature) and re-graded offline under model grade from the stored case results: tool calls and answer match from the stored calls and replies, schema from the answer before the output guardrails (trace) with the JSON extraction; path, citations, checks and PII as stored (their routing predates the pinned price-cap change, so a `lookup` rule check can still fail on a priced model).

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
- Tokens: 299,328 in, 16,088 out; cost $0.0000; wall time 9 s

### `local-2b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 20.5 | 59.5 | 21.5 | 70.5 | 7.5 | 0 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 36.5 | 3.5 | 9 | 9.5 | 1 |

- 107 failed cases; answer_match only: 35
- Fails where most pass: `ecommerce/track_qt58120394` (6 of 6), `ecommerce/order_lookup_qf20402` (5 of 6), `finance/mem_notice_delivery_set` (5 of 6), `healthcare/kb_records_timing` (5 of 6), `ecommerce/kb_tent_warranty` (4 of 6) and 27 more
- Tokens: 1,656,600 in, 32,051 out; cost $0.0000; wall time 221 s

### `local-4b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 24.5 | 53.5 | 24.5 | 64 | 8 | 0 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 27 | 3.5 | 13.5 | 9.5 | 0 |

- 103.5 failed cases; answer_match only: 32.5
- Fails where most pass: `healthcare/tool_find_appointment` (6 of 6), `healthcare/kb_prescription_renewal` (5 of 6), `sports/rules_postponement_deadline` (5 of 6), `finance/cite_mgmt_fee_hcp01` (4 of 6), `finance/cite_return_of_capital_share` (4 of 6) and 19 more
- Tokens: 1,311,439 in, 24,238 out; cost $0.0000; wall time 269 s

### `local-9b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 33 | 36 | 12.5 | 65.5 | 7 | 0 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 1 | 15 | 11.5 | 8.5 | 0 |

- 85 failed cases; answer_match only: 33.5
- Fails where most pass: `_starter/pii_leak_repaired` (5 of 6), `sports/stats_pl114` (5 of 6), `sports/stats_pl142` (5 of 6), `sports/rules_match_length` (4 of 6), `_starter/trailhead_cited` (3 of 6) and 6 more
- Tokens: 1,077,242 in, 24,220 out; cost $0.0000; wall time 342 s

### `local-e4b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 36 | 40 | 7.5 | 67.5 | 7 | 0 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 0 | 25 | 8 | 7 | 0 |

- 86.5 failed cases; answer_match only: 37
- Fails where most pass: `finance/mem_reporting_currency_set` (6 of 6), `finance/mem_statement_format_set` (6 of 6), `finance/tool_calls_hcp02` (6 of 6), `_starter/cancel_needs_approval` (5 of 6), `_starter/pii_leak_repaired` (5 of 6) and 11 more
- Tokens: 915,508 in, 17,594 out; cost $0.0000; wall time 183 s

### `local-8b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 28.5 | 73.5 | 30.5 | 73.5 | 9.5 | 1 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 43 | 3 | 9 | 18.5 | 0 |

- 114.5 failed cases; answer_match only: 28
- Fails where most pass: `_starter/structured_status` (6 of 6), `_starter/support_hours` (6 of 6), `_starter/cancel_needs_approval` (5 of 6), `_starter/remember_name` (5 of 6), `ecommerce/order_lookup_qf20402` (5 of 6) and 32 more
- Tokens: 981,128 in, 15,448 out; cost $0.0000; wall time 254 s

### `hosted-haiku`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 32 | 22 | 12 | 57 | 4 | 0 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 2 | 10 | 4 | 6 | 0 |

- 69 failed cases; answer_match only: 29
- Fails where most pass: `finance/tool_nav_hcp02` (5 of 6), `logistics/track_then_eta_in_transit` (5 of 6), `logistics/eta_single` (3 of 6), `logistics/kb_escalation_time` (3 of 6)
- Tokens: 844,171 in, 25,791 out; cost $0.0973; wall time 246 s

### `hosted-sonnet`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 31 | 16 | 14 | 54 | 17 | 0 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 5 | 9 | 1 | 1 | 0 |

- 75 failed cases; answer_match only: 24
- Fails where most pass: `sports/fixtures_nor` (6 of 6), `sports/stats_pl121` (6 of 6), `sports/stats_pl137` (6 of 6), `sports/stats_pl114` (5 of 6), `sports/stats_pl142` (5 of 6) and 10 more
- Tokens: 882,251 in, 27,671 out; cost $2.0412; wall time 468 s

## Regenerate

```sh
HARNESSWALK_OLLAMA_URL=http://192.168.8.107:11434 uv run harnesswalk scorecard --models fake-small,local-2b,local-4b,local-9b,local-e4b,local-8b --packs all --out docs/scorecard.md --json docs/scorecard/2026-11-08-local.json --samples 2 --carry docs/scorecard/2026-11-08.json --carry-models hosted-haiku,hosted-sonnet
```

The run detail of every cell is in the scorecard JSON (`docs/scorecard/2026-11-08-local.json`); `harnesswalk eval <pack> --suite model --model <alias>` replays one cell.
