# Harnesswalk docs

The [README](../README.md) is the front door. These pages hold the detail.

| Page | What it covers |
|---|---|
| [quickstart.md](quickstart.md) | Every way to run Harnesswalk: from a clone, from a release, with a real model, or in the hosted sandbox |
| [walkthrough.md](walkthrough.md) | A guided tour of the web app and the command line, step by step, with screenshots |
| [architecture.md](architecture.md) | The planes and nodes, the event stream, the spec and its conformance suite, and the principles |
| [your-own-data.md](your-own-data.md) | Build a pack from your own documents and functions, from the starter to a release |
| [repository.md](repository.md) | What lives where in the repository |
| [scorecard.md](scorecard.md) | The model ladder: which model passes which pack's golden set when it really answers |
| [RELEASING.md](RELEASING.md) | How a release is made, rolled back and set up |

## Model ladder

[scorecard.md](scorecard.md) is a pack x model pass matrix: every pack's golden set answered by
`fake-small`, five local models on Ollama and two hosted models, judged by the deterministic
scorers only (no LLM judge) under model grading (tool calls in order with extra read calls
allowed, answers compared as normalized text; `spec/contracts/evaluator.md`, "Grading
modes"), with a failure breakdown by scorer and a `tool_calls` split per model. Each cell is
`model`-suite eval runs at temperature 0, each pack on the harness profile its `pack.yaml`
names; the cell data is in [scorecard/](scorecard/) as JSON.
To regenerate it against your own Ollama (each model pulled first, for example
`ollama pull qwen3.5:4b`):

```sh
HARNESSWALK_OLLAMA_URL=http://localhost:11434 uv run harnesswalk scorecard \
  --models fake-small,local-2b,local-4b,local-9b,local-e4b,local-8b --samples 2 \
  --out docs/scorecard.md --json docs/scorecard/$(date -u +%F)-local.json
```

`--samples N` runs every cell N times and shows the mean with the range. Add
`hosted-haiku,hosted-sonnet` to `--models` with `ANTHROPIC_API_KEY` set to include the hosted
cells (they cost money; `--max-usd` stops priced cells once the runs spent that much, 5 by
default), or carry them from an earlier run without a model call:
`--carry docs/scorecard/2026-11-08.json --carry-models hosted-haiku,hosted-sonnet` grades the
stored results of those columns again under the current rules (it needs the data directory
that ran them). A model that is not pulled, a provider that is not configured or a model that
takes no tools is `n/a` with the reason, and the rest of the matrix still runs. One cell is
`uv run harnesswalk eval <pack> --suite model --model <alias>`.
