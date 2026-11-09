#!/usr/bin/env bash
# Started by the harness over ssh (SshTrainer): runs every phase of one tune run in its
# directory and leaves status.json, steps.jsonl and result.json there. See README.md.
set -uo pipefail
RUN_DIR="$1"
LAB="${HARNESSWALK_LAB:-$HOME/harnesswalk-lab}"
export HF_HOME="$LAB/cache/hf"
export PYTHONPATH="$LAB/bin"
export TOKENIZERS_PARALLELISM=false
GPU="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['cuda_visible_devices'])" "$RUN_DIR/run.json")"
export CUDA_VISIBLE_DEVICES="$GPU"
# shellcheck disable=SC1091
. "$LAB/.venv/bin/activate"
cd "$RUN_DIR" || exit 1
echo "{\"state\": \"running\", \"phase\": \"start\"}" > status.json
nvidia-smi --query-gpu=index,name,memory.used,memory.total --format=csv > gpu.txt 2>&1 || true
python "$LAB/bin/pipeline.py" all "$RUN_DIR"
