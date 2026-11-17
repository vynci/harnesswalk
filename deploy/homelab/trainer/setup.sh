#!/usr/bin/env bash
# One-time setup of the training worker on the homelab (ssh ai@<box>).
# Idempotent: re-running skips what is already there. Never touches Ollama or the GPUs.
set -euo pipefail
LAB="${HARNESSWALK_LAB:-$HOME/harnesswalk-lab}"
mkdir -p "$LAB"/{runs,cache,llama.cpp-build}
cd "$LAB"

# 1. uv (user install, no sudo)
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# 2. Python 3.12 venv with the training stack (torch cu128 wheels)
if [ ! -x "$LAB/.venv/bin/python" ]; then
  uv venv --python 3.12 "$LAB/.venv"
fi
. "$LAB/.venv/bin/activate"
uv pip install --index-url https://download.pytorch.org/whl/cu128 torch torchvision
uv pip install "transformers>=4.57" "peft>=0.17" "trl>=0.24" "datasets>=4" "accelerate>=1.10" \
  "bitsandbytes>=0.48" sentencepiece protobuf safetensors "huggingface_hub[hf_xet]" numpy

# 3. llama.cpp: the GGUF converter (python) and llama-quantize (CPU build is enough)
if [ ! -d "$LAB/llama.cpp" ]; then
  git clone --depth 1 https://github.com/ggml-org/llama.cpp "$LAB/llama.cpp"
fi
uv pip install -r "$LAB/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt" --index-strategy unsafe-best-match \
  --extra-index-url https://download.pytorch.org/whl/cu128 || true
if [ ! -x "$LAB/llama.cpp-build/bin/llama-quantize" ]; then
  cmake -S "$LAB/llama.cpp" -B "$LAB/llama.cpp-build" -DGGML_CUDA=OFF -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release
  cmake --build "$LAB/llama.cpp-build" --target llama-quantize -j 16
fi
python -c "import torch, transformers, peft, trl; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.device_count()); print('transformers', transformers.__version__, 'peft', peft.__version__, 'trl', trl.__version__)"
echo "setup ok: $LAB"
