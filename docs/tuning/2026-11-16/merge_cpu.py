"""The worker's merge (deploy/homelab/trainer/pipeline.py), with the base on the CPU."""

import shutil
import sys
import time
from pathlib import Path

import pipeline
import torch
from hwlab import Run
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

run = Run(sys.argv[1])
t0 = time.time()
path = pipeline.base_dir(run)
model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.bfloat16, device_map={"": "cpu"})
model = PeftModel.from_pretrained(model, str(run.dir / "adapter")).merge_and_unload()
merged = run.dir / "merged"
model.save_pretrained(str(merged), safe_serialization=True)
AutoTokenizer.from_pretrained(path).save_pretrained(str(merged))
for name in ("chat_template.jinja",):
    if (Path(path) / name).is_file():
        shutil.copy(Path(path) / name, merged / name)
print(f"merged on the CPU in {time.time() - t0:.0f} s")
