# Model ladder scorecard

Which model passes which pack's golden set when the model really answers. Each cell is
a `model`-suite eval run: every golden case of the pack, answered by the model alias
(pinned for every call of the case turn) and judged by the deterministic scorers only
(path, tool calls, citations, answer match, checks, schema, PII leak). No LLM judge,
so a cell is reproducible from the trace. `fake-small` answers from the case fixtures,
so its column is the golden suite itself and every pack should be green there.

- Generated: 2026-11-08 20:58 (UTC) by `harnesswalk scorecard`
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
| `hosted-haiku` | anthropic | `claude-haiku-5-5` |  |  | provider default |
| `hosted-sonnet` | anthropic | `claude-sonnet-5-5` |  |  | provider default |

## Pass counts

`passed/scored` per pack (a skipped case, outside its modes, is not scored), the mean over the samples with the min and max in brackets; `n/a` means the model could not run here (the reason is under the model below).

| Pack | `fake-small` | `local-2b` | `local-4b` | `local-9b` | `local-e4b` | `local-8b` | `hosted-haiku` | `hosted-sonnet` |
|---|---|---|---|---|---|---|---|---|
| `_starter` (operated) | 16/16 | 7/16 | 8/16 | 6.5/16 (6-7) | 5/16 | 4.5/16 (4-5) | 9/16¹ | 10/16¹ |
| `ecommerce` (governed) | 24/24 | 3/24 (2-4) | 4/24 | 7/24 | 7/24 (6-8) | 3/24 | 15/24¹ | 10/24¹ |
| `finance` (operated) | 25/25 | 6.5/25 (6-7) | 6/25 | 13/25 | 7/25 | 4/25 | 17/25¹ | 18/25¹ |
| `healthcare` (grounded) | 25/25 | 2/25 | 7/25 | 7.5/25 (7-8) | 8/25 | 2/25 | 10/25¹ | 12/25¹ |
| `logistics` (governed) | 24/24 | 2/24 | 4.5/24 (4-5) | 8.5/24 (8-9) | 8.5/24 (8-9) | 0/24 | 2/24¹ | 5/24¹ |
| `sports` (grounded) | 22/22 | 6.5/22 (6-7) | 12/22 | 11/22 | 15/22 (14-16) | 6/22 | 14/22¹ | 6/22¹ |
| **All packs** | **136/136** (100%) | **27/136** (20%) (25-29) | **41.5/136** (31%) (41-42) | **53.5/136** (39%) (53-54) | **50.5/136** (37%) (50-51) | **19.5/136** (14%) (19-20) | **67/136** (49%) | **61/136** (45%) |

¹ Carried forward from the 2026-11-08 run (hosted-haiku, hosted-sonnet, one sample, provider default temperature, every pack on the 0.1 harness with every node on, before packs named a profile) and re-graded offline under model grade from the stored case results: tool calls and answer match from the stored calls and replies, schema from the answer before the output guardrails (trace) with the JSON extraction; path, citations, checks and PII as stored (their routing predates the pinned price-cap change, so a `lookup` rule check can still fail on a priced model).

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
- Tokens: 299,328 in, 16,088 out; cost $0.0000; wall time 7 s

### `local-2b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 20.5 | 63 | 22.5 | 70 | 9 | 0 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 41 | 3 | 9 | 9 | 1 |

- 109 failed cases; answer_match only: 36.5
- Fails where most pass: `ecommerce/track_qt58120394` (6 of 6), `ecommerce/order_lookup_qf20402` (5 of 6), `finance/mem_notice_delivery_set` (5 of 6), `healthcare/kb_prescription_renewal` (5 of 6), `healthcare/kb_records_timing` (5 of 6) and 30 more
- Tokens: 1,658,125 in, 32,454 out; cost $0.0000; wall time 228 s

### `local-4b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 24 | 47.5 | 20.5 | 63.5 | 6 | 0 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 22 | 2.5 | 13.5 | 9.5 | 0 |

- 94.5 failed cases; answer_match only: 31
- Fails where most pass: `finance/cite_mgmt_fee_hcp01` (4 of 6), `finance/cite_return_of_capital_share` (4 of 6), `finance/cite_transfer_consent` (4 of 6), `sports/rules_postponement_deadline` (4 of 6), `_starter/trailhead_cited` (3 of 6) and 12 more
- Tokens: 1,247,259 in, 23,845 out; cost $0.0000; wall time 255 s

### `local-9b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 31 | 33 | 10.5 | 65 | 5 | 0 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 1 | 13 | 11.5 | 7.5 | 0 |

- 82.5 failed cases; answer_match only: 35
- Fails where most pass: `_starter/pii_leak_repaired` (5 of 6), `sports/rules_red_card` (4 of 6), `_starter/trailhead_cited` (3 of 6), `ecommerce/cancel_approved_qf20430` (3 of 6), `ecommerce/refund_approved_qf20402` (3 of 6) and 5 more
- Tokens: 1,064,306 in, 24,114 out; cost $0.0000; wall time 329 s

### `local-e4b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 36 | 38 | 7.5 | 66 | 7 | 0 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 0 | 23.5 | 8 | 6.5 | 0 |

- 85.5 failed cases; answer_match only: 36
- Fails where most pass: `finance/mem_reporting_currency_set` (6 of 6), `finance/mem_statement_format_set` (6 of 6), `finance/tool_calls_hcp02` (6 of 6), `_starter/cancel_needs_approval` (5 of 6), `_starter/pii_leak_repaired` (5 of 6) and 12 more
- Tokens: 902,179 in, 17,786 out; cost $0.0000; wall time 170 s

### `local-8b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 28.5 | 75 | 27.5 | 78 | 9 | 1 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 44.5 | 3 | 9 | 18.5 | 0 |

- 116.5 failed cases; answer_match only: 32
- Fails where most pass: `_starter/structured_status` (6 of 6), `_starter/support_hours` (6 of 6), `_starter/cancel_needs_approval` (5 of 6), `_starter/remember_name` (5 of 6), `ecommerce/order_lookup_qf20402` (5 of 6) and 34 more
- Tokens: 960,848 in, 16,255 out; cost $0.0000; wall time 255 s

### `hosted-haiku`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 32 | 22 | 12 | 57 | 4 | 0 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 2 | 10 | 4 | 6 | 0 |

- 69 failed cases; answer_match only: 29
- Fails where most pass: `finance/tool_nav_hcp02` (5 of 6), `logistics/track_then_eta_in_transit` (5 of 6), `logistics/eta_single` (3 of 6), `logistics/kb_escalation_time` (3 of 6), `sports/stats_pl203` (3 of 6)
- Tokens: 844,171 in, 25,791 out; cost $0.0973; wall time 246 s

### `hosted-sonnet`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 31 | 16 | 14 | 54 | 17 | 0 | 0 | 0 |

| tool_calls: called when none expected | tool_calls: none called | tool_calls: write tool not called | tool_calls: read tool not called | tool_calls: extra or wrong tool |
|---|---|---|---|---|
| 5 | 9 | 1 | 1 | 0 |

- 75 failed cases; answer_match only: 24
- Fails where most pass: `sports/fixtures_nor` (6 of 6), `sports/stats_pl114` (6 of 6), `sports/stats_pl121` (6 of 6), `sports/stats_pl137` (6 of 6), `sports/fixtures_ash` (5 of 6) and 11 more
- Tokens: 882,251 in, 27,671 out; cost $2.0412; wall time 468 s

## Regenerate

```sh
HARNESSWALK_OLLAMA_URL=http://192.168.8.107:11434 uv run harnesswalk scorecard --models fake-small,local-2b,local-4b,local-9b,local-e4b,local-8b --packs all --out docs/scorecard.md --json docs/scorecard/2026-11-08-local.json --samples 2 --carry docs/scorecard/2026-11-08.json --carry-models hosted-haiku,hosted-sonnet
```

The run detail of every cell is in the scorecard JSON (`docs/scorecard/2026-11-08-local.json`); `harnesswalk eval <pack> --suite model --model <alias>` replays one cell.
The command raises the hosted providers' call timeout (`models.providers.<name>.timeout_s`) to at least `--case-timeout` unless a `--set` names it, and sends Anthropic no `temperature` (the API rejects it for these models).

## The P2 packs (attached run)

The four packs P2 added were scored in a run of their own, with the same model grading, two
samples and temperature 0, on `fake-small` and three local models; the full page and JSON are
[docs/scorecard/2026-11-08-p2.md](scorecard/2026-11-08-p2.md) and
[2026-11-08-p2.json](scorecard/2026-11-08-p2.json). The next full scorecard run covers every
pack and replaces this section.

| Pack | `fake-small` | `local-4b` | `local-e4b` | `local-9b` |
|---|---|---|---|---|
| `device_fleet` (governed) | 28/28 | 5/28 | 4/28 | 6/28 (5-7) |
| `gov_services` (grounded) | 27/27 | 10/27 | 10.5/27 (10-11) | 14/27 |
| `long_session` (remembered) | 20/20 | 9/20 | 8/20 | 5/20 (0-10)* |
| `helpdesk_lite` (minimal) | 21/21 | 16/21 | 19/21 | 14.5/21 (13-16) |

\* One sample lost every call after a 60 s timeout to `provider_error`; the other sample is
10/20. The long cases need the per-tenant token budgets lifted for eval runs (the attached
page says how).
