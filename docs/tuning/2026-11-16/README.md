# Scale-up round, 2026-11-16

Raw data of the third tuning round ([../../tuning.md](../../tuning.md), "2026-11-16: the
scale-up round"): a generated 1,000-case test set (979 after the sanity pass), a 10,582-example
dataset curated from Claude Haiku 5.5, a 1,124-example dataset from qwen3.5 27B, the size curve
of qwen3.5 4B (200, 1,000 and 5,000 examples; three seeds at 1,000, two at 5,000), a tuned
qwen3.5 9B, and the untuned 4B, 9B, 27B and Haiku 5.5 on the same cases. Measured on 2026-11-15
to 2026-11-17 on the homelab (2x RTX 3090, Ollama 0.34.1 pinned to GPU 0) unless a file says
otherwise.

## Files

| Path | What it is |
|---|---|
| `summary.json` | `summarize.py`: `testset` (per model on the frozen test set: pass rate with a Wilson 95% interval over all cases, per pack and per kind, failing scorers, the `tool_calls` failure split per pack, "write tool not called", mean, median and p95 seconds per case, decode tokens per second, cost), `seeds` (the spread of the models named `<base>-s<n>`), `cells` and `models` (the secondary scorecards, phase 5's format), `spend` |
| `results/<model>.json` | `export_results.py`: every frozen test case of one model (pack, kind, status, failing scorers, tool split, tools called, timing, cost). `-s<n>` is another seed of a tuned model, or another sample of an untuned one (`local-4b-s2` and `local-9b-s2` are the first runs, which shared GPU 0 with the 27B curation and so have inflated timings; their pass rates count, their timings do not) |
| `compare/<model>.json` | Secondary check: `harnesswalk scorecard` on the golden sets of `ecommerce`, `logistics`, `device_fleet` and `_starter`, three samples (phase 5's comparison) |
| `collateral/<model>.json` | Secondary check: the golden sets of all ten packs, two samples |
| `testset/manifest-generated.json` | The generated test set (1,000 cases, seed 61, hash `c18bd20799f8e749`): counts per pack and kind, the id ranges |
| `testset/manifest-frozen.json` | The frozen test set (979 cases, hash `d48263ccfc3698d6`): what was dropped and fixed |
| `testset/sanity-hosted-sonnet.json` | Claude Sonnet 5.5 on the 153 generated cases Haiku 5.5 and the 27B both failed |
| `curation/<state>.json` | Each curation's report (cases, keep rate, cost, failing scorers): `curate-haiku`, `curate-27b`, and the three pilots |
| `curation/finalize-*.json` | The dataset artifacts each finalize wrote (counts per size, pack, kind, source and teacher, what the filters dropped) |
| `curation/verify-run-<run>.json` | `verify_dataset.py` on the exact files each run trained on: examples, samples, token lengths |
| `runs/<run>.result.json` | The homelab worker's result per run (config, metrics, weights, serve tag); `runs/index.txt` maps a run to its run directory |
| `loss/<run>.steps.jsonl`, `loss/loss_curves.csv` | The worker's step logs and the joined loss curves |
| `gpu/stages.json`, `gpu/<stage>.csv`, `gpu/summary.json` | `deploy/homelab/gpulog.py` per stage (stages over an hour downsampled to 5 s; the 1 Hz files stay on the homelab), and `summarize.py gpu` |
| `spend.json` | Hosted spend of the phase, per item |
| `export_results.py`, `summarize.py`, `verify_dataset.py`, `curve_table.py` | The scripts that made the files above |
| `merge_cpu.py` | The 9B run's merge, redone on the CPU after the worker's GPU merge ran out of memory |

## How it was produced

```sh
# test set (CPU only) and the freeze after the strong-model sanity pass
uv run harnesswalk tune testset --out data/p6/testset
uv run harnesswalk tune testset --testset data/p6/testset --out data/p6/testset-frozen \
  --drop-kinds power_off_offline \
  --drop-cases ecommerce/kb_00305,ecommerce/kb_00314,ecommerce/kb_00296,ecommerce/refund_deny_00161,ecommerce/refund_deny_00131 \
  --fix-kinds clear_sms_approve

# curation (resumable; the Haiku arm with the API key loaded from a file, prompt caching on)
uv run harnesswalk tune scale --teacher hosted-haiku --scenarios 3200 --variants 1 --seed 7 \
  --workers 6 --prior-usd 0.0012 --prompt-cache --target-kept 11000 --max-usd 25 \
  --testset data/p6/testset --state data/p6/state/curate-haiku
uv run harnesswalk tune scale --teacher local-27b --scenarios 3200 --variants 1 --seed 7 \
  --target-kept 1100 --max-usd 0 --testset data/p6/testset --state data/p6/state/curate-27b
uv run harnesswalk tune scale --no-run --state data/p6/state/curate-haiku --testset data/p6/testset \
  --finalize p6_haiku --sizes 200,1000,5000,10000 --val-max 100

# training (one per card; the 9B with --student local-9b)
uv run harnesswalk tune run --dataset p6_haiku_1000 --trainer homelab-trl --student local-4b \
  --name p6_haiku_1000 --config max_seq_len=6144 --config tail_logits=true --config epochs=2 --config seed=7

# evaluation on the frozen test set (and the secondary scorecards)
uv run harnesswalk tune testrun --testset data/p6/testset-frozen --model tuned-p6-haiku-1000 \
  --state data/p6/eval/tuned-p6-haiku-1000
uv run harnesswalk scorecard --models tuned-p6-haiku-1000 --packs ecommerce,logistics,device_fleet,_starter --samples 3 --json compare/tuned-p6-haiku-1000.json
uv run harnesswalk scorecard --models tuned-p6-haiku-1000 --packs all --samples 2 --json collateral/tuned-p6-haiku-1000.json

# this folder
python3 export_results.py results/<model>.json <frozen>/scenarios.jsonl <state> [<state> ...]
uv run python docs/tuning/2026-11-16/summarize.py testset summary-testset.json results/*.json
uv run python docs/tuning/2026-11-16/summarize.py cells summary-secondary.json compare/*.json collateral/*.json
```

Every local command ran with `HARNESSWALK_OLLAMA_URL` at the homelab's Ollama and every model
at temperature 0 (Claude 5 models reject `temperature`; they ran at the provider default).
Every test case runs as an eval run of its own, so it has a scratch tenant and a memory of its
own.

## Measured or carried

Measured in this round: every file here. The phase 5 numbers the docs quote beside them (Haiku
5.5 on the golden sets, 40.3 of 92 and 133 of 232) are carried from `../2026-11-15/`.
