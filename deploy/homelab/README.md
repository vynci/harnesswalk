# The homelab training worker

The `homelab-trl` trainer of `config/trainers.yaml` (spec/contracts/tuning.md): a GPU box the
harness reaches over ssh. The harness copies the dataset and the scripts in this folder to the
box, starts one run, polls its status file and reads the result back. The box owns the GPUs,
the Python environment and the weights; the harness keeps only manifests with hashes.

Tested on: 2x RTX 3090 (24 GiB each, no NVLink), Ubuntu 26.04, driver 595, Docker 29 with
Ollama 0.34.1 in a container named `ollama`; student qwen3.5:4b (`Qwen/Qwen3.5-4B` on Hugging
Face, Apache-2.0).

## Files

| File | What it does |
|---|---|
| `trainer/setup.sh` | One-time setup in `~/harnesswalk-lab`: uv, a Python 3.12 venv with torch (cu128 wheels), transformers 5, peft, trl, datasets, accelerate, bitsandbytes, flash-linear-attention, and llama.cpp (the converter and a CPU build of `llama-quantize`) |
| `trainer/worker.sh` | What the harness starts for a run (`nohup`, detached): sets `CUDA_VISIBLE_DEVICES` from `run.json` and runs every phase |
| `trainer/pipeline.py` | The phases: `train`, `merge`, `convert`, `create`, `check`, `adapter`, `clean`, or `all` |
| `trainer/hwlab.py` | The run directory, `status.json`, `steps.jsonl`, `result.json` |

## Setup (once)

```sh
scp deploy/homelab/trainer/setup.sh ai@192.168.8.107:harnesswalk-lab/setup.sh
ssh ai@192.168.8.107 'bash ~/harnesswalk-lab/setup.sh'
```

The first run downloads the base weights (about 9 GB for Qwen3.5-4B, vision tower included;
training loads the text model only) into `~/harnesswalk-lab/cache/`.

### GPU placement

Ollama spreads every model over both cards by default (`OLLAMA_SCHED_SPREAD=1`, `count: all`
in `~/ai-stack/docker-compose.yml`). Training needs a card of its own, so before the first run
pin Ollama to card 0 and train on card 1:

1. Check nothing is using Ollama: `docker exec ollama ollama ps` shows no model busy, and no
   eval or scorecard run is going (the harness's plan coordinates this in PROGRESS.md).
2. In `~/ai-stack/docker-compose.yml`, under the `ollama` service, replace `count: all` with
   `device_ids: ["0"]` and remove the `OLLAMA_SCHED_SPREAD` line (keep a copy of the file).
3. `cd ~/ai-stack && docker compose up -d ollama` (recreates only that container; the models
   volume is kept). `nvidia-smi` then shows Ollama's models on card 0 only.
4. Training uses `CUDA_VISIBLE_DEVICES=1` (`cuda_visible_devices` in `trainers.yaml`).

Hermes (`docker stop hermes hermes-dashboard`) may be stopped while training if card 0 runs
short; start it again after. Never stop the xorgate-sim or claude-workbench containers.

## Train (from the harness)

```sh
# 1. curate on the teacher (a model-suite run through the harness, kept turns only)
HARNESSWALK_OLLAMA_URL=http://192.168.8.107:11434 uv run harnesswalk tune curate \
  --pack ecommerce --pack logistics --teacher local-27b --out dataset/approvals
# 2. train, merge, convert, create (the worker; steps stream back as tune_step events)
HARNESSWALK_OLLAMA_URL=http://192.168.8.107:11434 uv run harnesswalk tune run \
  --dataset approvals --trainer homelab-trl --student local-4b --name approvals_4b
# 3. the gate: base, the served Q4_K_M model, and the prompt arm, on target and canary packs
HARNESSWALK_OLLAMA_URL=http://192.168.8.107:11434 uv run harnesswalk tune compare \
  --candidate model/approvals_4b --against local-4b --packs ecommerce,logistics \
  --canary-packs sports,_starter --samples 2 --out compare.md
```

What `tune run` does on the box, phase by phase (`~/harnesswalk-lab/runs/<run_id>/`):

| Phase | What | Output |
|---|---|---|
| `train` | LoRA in bf16 on all linear layers of the text model (rank 16, alpha 32, dropout 0.05, 2 epochs, lr 2e-4 cosine, batch 1 x 8, gradient checkpointing, max 4096 tokens; a longer conversation is dropped and counted). One sample per model call: the prompt exactly as Ollama renders the conversation so far (a port of its `qwen3.5` renderer, thinking off) and that call's reply as the target, the only tokens with loss. `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`, or a long sample's logits fragment the cache into an out-of-memory error | `adapter/`, `steps.jsonl` |
| `merge` | Base plus adapter, merged in bf16 on the training card | `merged/` (deleted at the end) |
| `convert` | `llama.cpp/convert_hf_to_gguf.py --no-mtp` to bf16 GGUF (Ollama's qwen3.5 has no multi-token prediction layer, and a GGUF that declares one without its weights does not load), `llama-quantize` to Q4_K_M | `model-q4_k_m.gguf` |
| `create` | The GGUF copied into the container, a Modelfile with the base's `TEMPLATE`, `RENDERER`, `PARSER` and `PARAMETER` lines (`ollama show --modelfile qwen3.5:4b`), `ollama create hw-<name>:<run_id>` | the Ollama tag and digest |
| `check` | Serve what you evaluate: the first validation example's prefix rendered by the HF template and sent raw (`/api/generate`) against the same messages through `/api/chat` (Ollama's renderer); the same prompt token count means the templates agree | `result.json` `template_check` |
| `adapter` | The other route, tested and recorded: the LoRA as GGUF (`convert_lora_to_gguf.py`) with `ADAPTER` over Ollama's own `qwen3.5:4b`. Ollama 0.34.1 refuses it ("LoRA adapters are no longer supported"), so the merged GGUF is the route | `result.json` `adapter_route` |
| `clean` | Keeps the adapter, the Q4_K_M GGUF and the logs; removes the merge and the bf16 GGUF | |

By hand, one phase at a time (for debugging):

```sh
ssh ai@192.168.8.107
cd ~/harnesswalk-lab && . .venv/bin/activate && export PYTHONPATH=bin HF_HOME=cache/hf
CUDA_VISIBLE_DEVICES=1 python bin/pipeline.py train runs/<run_id>
python bin/pipeline.py check runs/<run_id>
```

## Clean up

- A tuned model in Ollama: `docker exec ollama ollama rm hw-<name>:<run_id>` (and the
  `-adapter` tag when the adapter route was created).
- A run directory: `rm -r ~/harnesswalk-lab/runs/<run_id>` (the adapter, the GGUF and logs).
- Back to the two-card spread: restore the saved `docker-compose.yml` and `docker compose up -d
  ollama`, when nothing is using Ollama.

Weights, merges and GGUF files never leave the box or enter git: the `adapter/<name>` and
`model/<name>` manifests name them by sha256 and location.
