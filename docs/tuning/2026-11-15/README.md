# Evidence round, 2026-11-15

Raw data of the second tuning round ([../../tuning.md](../../tuning.md), "2026-11-15: the
evidence round"): two reruns of the first adapter's recipe, a second adapter curated from three
governed packs without their golden cases, a comparison against the base 4B, the untuned 9B and
Claude Haiku 5.5, a ten-pack collateral check, and GPU traces of every stage. Everything here
was measured on 2026-11-15 on the homelab (2x RTX 3090, Ollama 0.34.1 pinned to GPU 0,
training on GPU 1) unless a file says it is carried over.

## Files

| Path | What it is |
|---|---|
| `compare/<alias>.json` | Comparison (questions 1 to 3): one `harnesswalk scorecard` JSON per arm (`local-4b`, `tuned-approvals-4b` = v1, `tuned-approvals-4b-r1`, `tuned-approvals-4b-r2`, `tuned-approvals-v2-4b`, `tuned-approvals-v2l-4b`, `local-9b`, `hosted-haiku`) on `ecommerce`, `logistics`, `device_fleet` and `_starter`, three samples per cell, temperature 0 for the local arms. Every case keeps `timing` (seconds per case, model calls, tokens, Ollama's own generation and prompt time) |
| `collateral/<alias>.json` | Collateral check (question 5): base 4B, v1, v2, v2l, the untuned 9B and Haiku on all ten packs, two samples per cell |
| `summary.json` | `summarize.py cells` over every file above: per (model, pack) and per model over a file's packs, the pass count per sample, mean, min, max, standard deviation, median and p95 seconds per case, decode and prefill tokens per second, cost per sample, the `tool_calls` failure split |
| `loss/loss_curves.csv` | Per-step training loss and the validation losses (every quarter of the run and at the end) of v1 (`tr_2`, carried over from 2026-11-09), rerun 1 (`tr_3`), rerun 2 (`tr_4`), v2 (`tr_5`), the run that ran out of memory (`tr_6`, 13 steps) and v2l (`tr_7`) |
| `loss/<run>.steps.jsonl` | The worker's own step logs the CSV is built from |
| `runs/tr_<n>.json`, `runs/cu_<n>.json` | The harness's tune run and curation records (request, config, metrics, error), without the step list (that is in `loss/`). `tr_2` and `cu_3` are 2026-11-09's |
| `runs/dataset-<name>.json` | The dataset manifests: `approvals` (2026-11-09, its file paths made relative here) and `approvals_v2` (teacher, packs, scenario counts, kept and dropped counts, file hashes; the files themselves are 2.6 MB and stay out of git) |
| `runs/model-<name>.json` | The served models' manifests (Ollama tag and digest, GGUF sha256 and size) |
| `gpu/stages.json` | The GPU logger's stage sidecar: stage name, start and end (UTC and epoch), the CSV, the host's UTC offset (the CSV timestamps are local time, UTC-5), a note |
| `gpu/<stage>.csv` | `nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used,power.draw,temperature.gpu --format=csv -l 1`, one row per GPU per second, as recorded (the longest, curation, is 0.56 MB, so nothing was downsampled). Stages: `idle_baseline`, `train_repro_seed7`, `curate_v2_27b`, `train_repro_seed11`, `train_v2`, `train_v2_long` (out of memory), `train_v2_long2`, `d_<alias>` (comparison), `e_<alias>` (collateral; the Haiku runs used no GPU and have no stage), `idle_end` |
| `gpu/summary.json` | `summarize.py gpu`: per stage and GPU, mean and p95 utilization, peak memory, mean and peak power, peak temperature, energy |
| `gpu/nvtop-train_repro_seed7.txt` | Plain-text captures of `nvtop` every 5 s for three minutes of rerun 1's training (GPU 1 alone, before curation started on GPU 0) |
| `summarize.py` | The script that builds `summary.json`, `loss/loss_curves.csv` and `gpu/summary.json` |

## How it was produced

```sh
# GPU logger on the homelab (deploy/homelab/gpulog.py), around every stage:
python3 ~/harnesswalk-lab/gpulog.py start <stage> --dir ~/harnesswalk-lab/gpulog/2026-11-15
python3 ~/harnesswalk-lab/gpulog.py stop <stage> --dir ~/harnesswalk-lab/gpulog/2026-11-15

# B: reruns of the first recipe on the same dataset (dataset/approvals@v1)
uv run harnesswalk tune run --dataset approvals --trainer homelab-trl --student local-4b --name approvals_4b_r1
uv run harnesswalk tune run --dataset approvals --trainer homelab-trl --student local-4b --name approvals_4b_r2 --config seed=11

# C: v2 dataset and adapter (no golden case in the data; _starter held out)
uv run harnesswalk tune curate --pack ecommerce --pack logistics --pack device_fleet \
  --teacher local-27b --variants 10 --seed 7 --no-golden --out dataset/approvals_v2
uv run harnesswalk tune run --dataset approvals_v2 --trainer homelab-trl --student local-4b --name approvals_v2_4b
uv run harnesswalk tune run --dataset approvals_v2 --trainer homelab-trl --student local-4b --name approvals_v2l_4b \
  --config max_seq_len=6144 --config tail_logits=true

# D: one scorecard per arm (the Haiku arms with ANTHROPIC_API_KEY set, --max-usd 3 and 2)
uv run harnesswalk scorecard --models <alias> --packs ecommerce,logistics,device_fleet,_starter \
  --samples 3 --json compare/<alias>.json
# E
uv run harnesswalk scorecard --models <alias> --packs all --samples 2 --json collateral/<alias>.json

uv run python docs/tuning/2026-11-15/summarize.py cells summary.json compare/*.json collateral/*.json
uv run python docs/tuning/2026-11-15/summarize.py loss loss/loss_curves.csv v1=loss/tr_2.steps.jsonl ...
uv run python docs/tuning/2026-11-15/summarize.py gpu gpu/summary.json gpu
```

Every command ran with `HARNESSWALK_OLLAMA_URL=http://192.168.8.107:11434` from a Mac kept
awake with `caffeinate -i`; the tune runs train detached on the homelab, so a sleeping laptop
would only have delayed polling.

## Measured or carried

Measured on 2026-11-15: every scorecard file, the runs `tr_3` to `tr_7` and `cu_4`, every GPU
file. Carried from 2026-11-09: `tr_2` and `cu_3` (v1's training and its dataset) and their
loss log; v1's pass counts here are new runs of that model.

Stages overlap in time (for example `train_repro_seed11` on GPU 1 while `curate_v2_27b` ran on
GPU 0), so read a stage's CSV by the GPU it names. A stage's peak memory can include a model a
previous stage left loaded in Ollama.
