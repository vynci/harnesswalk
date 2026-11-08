# Model ladder scorecard

Which model passes which pack's golden set when the model really answers. Each cell is
one `model`-suite eval run: every golden case of the pack, answered by the model alias
(pinned for every call of the case turn) and judged by the deterministic scorers only
(path, tool calls, citations, answer match, checks, schema, PII leak). No LLM judge,
so a cell is reproducible from the trace. `fake-small` answers from the case fixtures,
so its column is the golden suite itself and every pack should be green there.

- Generated: 2026-11-08 19:38 (UTC) by `harnesswalk scorecard`
- Harness: Harnesswalk 0.1.1, spec 0.1.0
- Ollama: http://192.168.8.107:11434 (version 0.34.1)
- Tenant: `scorecard`; embeddings: fake (hash vectors, the same for every model); case timeout: 120.0 s

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

`passed/scored` per pack (a skipped case, outside its modes, is not scored); `n/a` means
the model could not run here (the reason is under the model below).

| Pack | `fake-small` | `local-2b` | `local-4b` | `local-9b` | `local-e4b` | `local-8b` | `hosted-haiku` | `hosted-sonnet` |
|---|---|---|---|---|---|---|---|---|
| `_starter` | 16/16 | 5/16 | 4/16 | 4/16 | 4/16 | 6/16 | 6/16 | 6/16 |
| `ecommerce` | 24/24 | 0/24 | 3/24 | 6/24 | 6/24 | 3/24 | 7/24 | 0/24 |
| `finance` | 25/25 | 6/25 | 7/25 | 9/25 | 6/25 | 4/25 | 11/25 | 10/25 |
| `healthcare` | 25/25 | 3/25 | 4/25 | 8/25 | 6/25 | 1/25 | 9/25 | 11/25 |
| `logistics` | 24/24 | 1/24 | 4/24 | 6/24 | 8/24 | 0/24 | 0/24 | 1/24 |
| `sports` | 22/22 | 7/22 | 9/22 | 9/22 | 14/22 | 7/22 | 11/22 | 6/22 |
| **All packs** | **136/136** (100%) | **22/136** (16%) | **31/136** (23%) | **42/136** (31%) | **44/136** (32%) | **21/136** (15%) | **44/136** (32%) | **34/136** (25%) |

## Per model

Failures by scorer count failed cases per scorer (a case can fail several scorers);
`error` counts cases whose turn hit an error (a provider error or a timeout). The most
common failing cases are the ones the most models in this matrix also fail.
`answer_match` checks exact substrings written for the fixture replies, so a real model
that words a right answer differently fails it; "answer_match only" counts the failed
cases where every other scorer passed (read those replies before calling them wrong).
`tool_calls` is equality with the fixture's tool sequence, so a model that also reads a
skill or looks a record up before acting fails it even when the extra call is sensible.

### `fake-small`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

- No failed case
- Tokens: 149,664 in, 8,044 out; cost $0.0000; wall time 4 s

### `local-2b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 25 | 73 | 21 | 86 | 10 | 1 | 0 | 0 |

- 114 failed cases; most common: `_starter/order_status` (7 of 8 models), `_starter/order_status_skill` (7 of 8 models), `_starter/three_orders` (7 of 8 models)
- answer_match only: 27 of the failed cases
- Tokens: 807,574 in, 17,481 out; cost $0.0000; wall time 121 s

### `local-4b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 26 | 56 | 19 | 78 | 8 | 1 | 0 | 0 |

- 105 failed cases; most common: `_starter/order_status` (7 of 8 models), `_starter/order_status_skill` (7 of 8 models), `_starter/three_orders` (7 of 8 models)
- answer_match only: 32 of the failed cases
- Tokens: 661,027 in, 14,346 out; cost $0.0000; wall time 155 s

### `local-9b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 33 | 38 | 11 | 75 | 7 | 1 | 0 | 0 |

- 94 failed cases; most common: `_starter/order_status` (7 of 8 models), `_starter/order_status_skill` (7 of 8 models), `_starter/three_orders` (7 of 8 models)
- answer_match only: 43 of the failed cases
- Tokens: 532,402 in, 13,014 out; cost $0.0000; wall time 176 s

### `local-e4b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 35 | 44 | 10 | 70 | 8 | 1 | 0 | 0 |

- 92 failed cases; most common: `_starter/order_status` (7 of 8 models), `_starter/order_status_skill` (7 of 8 models), `_starter/three_orders` (7 of 8 models)
- answer_match only: 34 of the failed cases
- Tokens: 460,397 in, 9,401 out; cost $0.0000; wall time 105 s

### `local-8b`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 28 | 79 | 32 | 76 | 11 | 1 | 0 | 0 |

- 115 failed cases; most common: `_starter/order_status` (7 of 8 models), `_starter/order_status_skill` (7 of 8 models), `_starter/three_orders` (7 of 8 models)
- answer_match only: 27 of the failed cases
- Tokens: 498,234 in, 8,994 out; cost $0.0000; wall time 168 s

### `hosted-haiku`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 32 | 43 | 12 | 68 | 4 | 1 | 0 | 0 |

- 92 failed cases; most common: `_starter/order_status` (7 of 8 models), `_starter/order_status_skill` (7 of 8 models), `_starter/three_orders` (7 of 8 models)
- answer_match only: 31 of the failed cases
- Tokens: 844,171 in, 25,791 out; cost $0.0973; wall time 246 s

### `hosted-sonnet`

| path | tool_calls | citations | answer_match | checks | schema | pii_leak | error |
|---|---|---|---|---|---|---|---|
| 31 | 60 | 14 | 58 | 17 | 1 | 0 | 0 |

- 102 failed cases; most common: `_starter/order_status` (7 of 8 models), `_starter/order_status_skill` (7 of 8 models), `_starter/three_orders` (7 of 8 models)
- answer_match only: 14 of the failed cases
- Tokens: 882,251 in, 27,671 out; cost $2.0412; wall time 468 s

## Regenerate

```sh
HARNESSWALK_OLLAMA_URL=http://192.168.8.107:11434 ANTHROPIC_API_KEY=<your key> uv run harnesswalk scorecard --models fake-small,local-2b,local-4b,local-9b,local-e4b,local-8b,hosted-haiku,hosted-sonnet --packs all --out docs/scorecard.md --json docs/scorecard/2026-11-08.json
```

The run detail of every cell is in the scorecard JSON (`docs/scorecard/2026-11-08.json`); `harnesswalk eval <pack> --suite model --model <alias>` replays one cell.
