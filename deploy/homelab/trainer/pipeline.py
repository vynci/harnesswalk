# ruff: noqa: E501 (the renderer text below is Ollama's, verbatim)
"""The homelab worker's pipeline: train a LoRA adapter, merge it, convert and quantize the merge
to GGUF, create the Ollama model, check the template round trip, and clean up.

    python pipeline.py all <run_dir>        what worker.sh runs (the harness starts it over ssh)
    python pipeline.py train <run_dir>      one phase at a time, for the runbook
    python pipeline.py merge|convert|create|check|adapter|clean <run_dir>

The run directory holds `run.json` (written by the harness: student, base repo and revision,
the training config, the Ollama tag), `train.jsonl` and `val.jsonl` (the curated dataset in
the common chat format, with tools). Progress goes to `steps.jsonl` and `status.json`;
`result.json` is the hand-back (spec/contracts/tuning.md, deploy/homelab/README.md).

Each assistant message of an example is one sample: the prompt exactly as Ollama renders the
conversation up to it (its `qwen3.5` renderer, thinking off, ported below), and the message as
the target; loss is on the target only. A conversation with a sample longer than
`max_seq_len` tokens is dropped and counted, never truncated.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from hwlab import Run, sha256_file, tree_sha256

IMPORT_DIR = "/tmp/hw-import"  # inside the Ollama container; files are deleted after create


def base_dir(run: Run) -> str:
    """The base weights: a local snapshot under the lab cache when there is one."""
    repo = run.spec["base"]["repo"]
    local = run.lab / "cache" / repo.split("/")[-1]
    if (local / "config.json").is_file():
        return str(local)
    from huggingface_hub import snapshot_download

    return snapshot_download(repo, revision=run.spec["base"].get("revision") or "main")


def base_revision(path: str) -> str:
    """The snapshot's commit when known (the HF cache keeps it), else the path."""
    ref = Path(path) / ".cache" / "huggingface" / "download"
    for meta in sorted(ref.glob("*.metadata")) if ref.is_dir() else []:
        line = meta.read_text().splitlines()[:1]
        if line:
            return line[0]
    return run_rev_from_path(path)


def run_rev_from_path(path: str) -> str:
    parts = Path(path).parts
    return parts[parts.index("snapshots") + 1] if "snapshots" in parts else path


# -- rendering -------------------------------------------------------------------------------
#
# Serve what you train on: the examples are rendered exactly as Ollama renders a chat for this
# model at serving time (its built-in `RENDERER qwen3.5`, ollama/ollama model/renderers/qwen35.go,
# with thinking off), not with the Hugging Face chat template, which differs in the tools block
# (Ollama serializes tools through its own structs: fewer fields, another key order) and in the
# think blocks of earlier assistant turns. `check` verifies the match against a live Ollama.

TOOL_POSTAMBLE = """
</tools>

If you choose to call a function ONLY reply in the following format with NO suffix:

<tool_call>
<function=example_function_name>
<parameter=example_parameter_1>
value_1
</parameter>
<parameter=example_parameter_2>
This is the value for the second parameter
that can span
multiple lines
</parameter>
</function>
</tool_call>

<IMPORTANT>
Reminder:
- Function calls MUST follow the specified format: an inner <function=...></function> block must be nested within <tool_call></tool_call> XML tags
- Required parameters MUST be specified
- You may provide optional reasoning for your function call in natural language BEFORE the function call, but NOT after
- If there is no function call available, answer the question like normal with your current knowledge and do not tell the user about function calls
</IMPORTANT>"""
SEPARATORS = (", ", ": ")


def _prop(p: dict[str, Any]) -> dict[str, Any]:
    """A property as Ollama's api.ToolProperty keeps it (field order, omitempty)."""
    out: dict[str, Any] = {}
    if p.get("anyOf"):
        out["anyOf"] = [_prop(x) for x in p["anyOf"]]
    if p.get("type"):
        t = p["type"]
        out["type"] = t[0] if isinstance(t, list) and len(t) == 1 else t
    if p.get("items") is not None:
        out["items"] = p["items"]
    if p.get("description"):
        out["description"] = p["description"]
    if p.get("enum"):
        out["enum"] = p["enum"]
    if p.get("properties"):
        out["properties"] = {k: _prop(v) for k, v in p["properties"].items()}
    if p.get("required"):
        out["required"] = p["required"]
    return out


def tool_json(tool: dict[str, Any]) -> str:
    """A tool as Ollama marshals api.Tool: type, function {name, description, parameters}."""
    fn = tool.get("function") or {}
    par = fn.get("parameters") or {}
    params: dict[str, Any] = {"type": par.get("type", "object")}
    for key, name in (("$defs", "$defs"), ("items", "items")):
        if par.get(key) is not None:
            params[name] = par[key]
    if par.get("required"):
        params["required"] = par["required"]
    params["properties"] = {k: _prop(v) for k, v in (par.get("properties") or {}).items()}
    function: dict[str, Any] = {"name": fn.get("name", "")}
    if fn.get("description"):
        function["description"] = fn["description"]
    function["parameters"] = params
    return json.dumps({"type": tool.get("type", "function"), "function": function},
                      separators=SEPARATORS, ensure_ascii=False)  # fmt: skip


def _arg(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def assistant_body(m: dict[str, Any]) -> str:
    """What the model writes for an assistant message (after the generation prompt)."""
    content = (m.get("content") or "").strip()
    out = content
    for j, call in enumerate(m.get("tool_calls") or []):
        fn = call.get("function") or call
        out += ("\n\n" if content else "") if j == 0 else "\n"
        out += "<tool_call>\n<function=" + fn["name"] + ">\n"
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            args = json.loads(args or "{}")
        for name, value in args.items():
            out += "<parameter=" + name + ">\n" + _arg(value) + "\n</parameter>\n"
        out += "</function>\n</tool_call>"
    return out


def render(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> str:
    """The prompt Ollama builds for `messages` (thinking off), ending with the generation
    prompt for the next assistant message."""
    sb = ""
    system = (
        (messages[0].get("content") or "").strip()
        if messages and messages[0]["role"] == "system"
        else None
    )
    if tools:
        sb += (
            "<|im_start|>system\n# Tools\n\nYou have access to the following functions:\n\n<tools>"
        )
        for tool in tools:
            sb += "\n" + tool_json(tool)
        sb += TOOL_POSTAMBLE
        if system:
            sb += "\n\n" + system
        sb += "<|im_end|>\n"
    elif system is not None:
        sb += "<|im_start|>system\n" + system + "<|im_end|>\n"
    for i, m in enumerate(messages):
        role, content = m["role"], (m.get("content") or "").strip()
        if role == "user" or (role == "system" and i != 0):
            sb += "<|im_start|>" + role + "\n" + content + "<|im_end|>\n"
        elif role == "assistant":  # thinking off: no think block in the history
            sb += "<|im_start|>assistant\n" + assistant_body(m) + "<|im_end|>\n"
        elif role == "tool":
            if i == 0 or messages[i - 1]["role"] != "tool":
                sb += "<|im_start|>user"
            sb += "\n<tool_response>\n" + content + "\n</tool_response>"
            if i == len(messages) - 1 or messages[i + 1]["role"] != "tool":
                sb += "<|im_end|>\n"
    return sb + "<|im_start|>assistant\n<think>\n\n</think>\n\n"


def encode(tok: Any, example: dict[str, Any], max_len: int) -> list[dict[str, list[int]]] | None:
    """One sample per assistant message (one per model call the harness made): the prompt as
    Ollama rendered it then, and the assistant's reply plus `<|im_end|>` as the target. None
    when any of them is longer than `max_len` tokens."""
    messages, tools = example["messages"], example.get("tools") or []
    out = []
    for i, m in enumerate(messages):
        if m["role"] != "assistant":
            continue
        prompt = tok(render(messages[:i], tools), add_special_tokens=False)["input_ids"]
        target = tok(assistant_body(m) + "<|im_end|>", add_special_tokens=False)["input_ids"]
        if len(prompt) + len(target) > max_len:
            return None
        out.append({"input_ids": prompt + target, "labels": [-100] * len(prompt) + target})
    return out


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# -- phases -------------------------------------------------------------------------------------


def train(run: Run) -> dict[str, Any]:
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        Trainer,
        TrainerCallback,
        TrainingArguments,
    )

    cfg = run.spec["config"]
    path = base_dir(run)
    tok = AutoTokenizer.from_pretrained(path)
    if tok.pad_token_id is None:
        tok.pad_token = tok.eos_token
    max_len = int(cfg.get("max_seq_len", 4096))
    data: dict[str, list[dict[str, list[int]]]] = {}
    dropped: dict[str, int] = {}
    lengths: list[int] = []
    for split in ("train", "val"):
        rows = [encode(tok, ex, max_len) for ex in load_jsonl(run.dir / f"{split}.jsonl")]
        data[split] = [sample for r in rows if r is not None for sample in r]
        dropped[split] = sum(1 for r in rows if r is None)
        lengths += [len(r["input_ids"]) for r in data[split]]
    trained_tokens = sum(sum(1 for x in r["labels"] if x != -100) for r in data["train"])
    run.status("running", "train", examples={k: len(v) for k, v in data.items()},
               dropped_too_long=dropped)  # fmt: skip
    model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.bfloat16, device_map={"": 0})
    model.config.use_cache = False
    lora = LoraConfig(
        r=int(cfg.get("rank", 16)), lora_alpha=int(cfg.get("alpha", 32)),
        lora_dropout=float(cfg.get("dropout", 0.05)),
        target_modules=cfg.get("target_modules", "all-linear"), task_type="CAUSAL_LM",
    )  # fmt: skip
    model = get_peft_model(model, lora)
    model.enable_input_require_grads()
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)

    class Rows(torch.utils.data.Dataset):
        def __init__(self, rows: list[dict[str, list[int]]]) -> None:
            self.rows = rows

        def __len__(self) -> int:
            return len(self.rows)

        def __getitem__(self, i: int) -> dict[str, list[int]]:
            return self.rows[i]

    def collate(batch: list[dict[str, list[int]]]) -> dict[str, Any]:
        width = max(len(b["input_ids"]) for b in batch)
        pad = tok.pad_token_id
        ids = [b["input_ids"] + [pad] * (width - len(b["input_ids"])) for b in batch]
        labels = [b["labels"] + [-100] * (width - len(b["labels"])) for b in batch]
        mask = [[1] * len(b["input_ids"]) + [0] * (width - len(b["input_ids"])) for b in batch]
        return {"input_ids": torch.tensor(ids), "labels": torch.tensor(labels),
                "attention_mask": torch.tensor(mask)}  # fmt: skip

    batch, accum = int(cfg.get("batch_size", 1)), int(cfg.get("grad_accum", 8))
    epochs = float(cfg.get("epochs", 2))
    total = max(1, math.ceil(len(data["train"]) / (batch * accum)) * math.ceil(epochs))
    started = time.time()
    last_eval: dict[str, float] = {}

    class Progress(TrainerCallback):
        def on_log(self, args: Any, state: Any, control: Any, logs: Any = None, **kw: Any):
            logs = logs or {}
            elapsed = time.time() - started
            step = int(state.global_step)
            per = elapsed / max(1, step)
            event = {"step": step, "total_steps": int(state.max_steps or total),
                     "epoch": round(float(state.epoch or 0), 3), "elapsed_s": round(elapsed, 1),
                     "eta_s": round(per * max(0, (state.max_steps or total) - step), 1),
                     "phase": "train"}  # fmt: skip
            if "eval_loss" in logs:
                last_eval["eval_loss"] = round(float(logs["eval_loss"]), 4)
                run.step(**event, eval_loss=last_eval["eval_loss"])
            elif "loss" in logs and step >= 1:
                run.step(**event, loss=round(float(logs["loss"]), 4),
                         learning_rate=float(logs.get("learning_rate", 0)))  # fmt: skip

    args = TrainingArguments(
        output_dir=str(run.dir / "trainer"), per_device_train_batch_size=batch,
        per_device_eval_batch_size=1, gradient_accumulation_steps=accum,
        num_train_epochs=epochs, learning_rate=float(cfg.get("learning_rate", 2e-4)),
        lr_scheduler_type="cosine",
        warmup_steps=max(1, round(total * float(cfg.get("warmup_ratio", 0.05)))),
        bf16=True, gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False}, logging_steps=1,
        eval_strategy="steps", eval_steps=max(1, total // 4), save_strategy="no",
        report_to=[], seed=int(cfg.get("seed", 7)), remove_unused_columns=False,
        dataloader_num_workers=0,
    )  # fmt: skip
    trainer = Trainer(
        model=model, args=args, train_dataset=Rows(data["train"]),
        eval_dataset=Rows(data["val"]) if data["val"] else None, data_collator=collate,
        callbacks=[Progress()],
    )  # fmt: skip
    out = trainer.train()
    final_eval = trainer.evaluate() if data["val"] else {}
    adapter = run.dir / "adapter"
    model.save_pretrained(str(adapter))
    tok.save_pretrained(str(adapter))
    digest, size = tree_sha256(adapter)
    peak = torch.cuda.max_memory_allocated() / 2**30
    return {
        "metrics": {
            "steps": int(out.global_step), "train_loss": round(float(out.training_loss), 4),
            "eval_loss": round(float(final_eval.get("eval_loss", 0)), 4) or None,
            "wall_s": round(time.time() - started, 1), "trained_tokens": trained_tokens,
            "tokens": sum(lengths), "max_tokens": max(lengths) if lengths else 0,
            "examples": {k: len(v) for k, v in data.items()}, "dropped_too_long": dropped,
            "trainable_params": trainable, "peak_gpu_gib": round(peak, 2),
            "gpu": torch.cuda.get_device_name(0),
        },
        "weights": {"location": f"homelab:{adapter}", "sha256": digest, "bytes": size},
        "base_revision": base_revision(path),
    }  # fmt: skip


def merge(run: Run) -> None:
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    path = base_dir(run)
    model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.bfloat16, device_map={"": 0})
    model = PeftModel.from_pretrained(model, str(run.dir / "adapter")).merge_and_unload()
    merged = run.dir / "merged"
    model.save_pretrained(str(merged), safe_serialization=True)
    AutoTokenizer.from_pretrained(path).save_pretrained(str(merged))
    for name in ("chat_template.jinja",):
        if (Path(path) / name).is_file():
            shutil.copy(Path(path) / name, merged / name)


def sh(*args: str) -> str:
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {(proc.stderr or proc.stdout)[-800:]}")
    return proc.stdout


def convert(run: Run) -> dict[str, Any]:
    lab = run.lab
    bf16, q4 = run.dir / "model-bf16.gguf", run.dir / "model-q4_k_m.gguf"
    sh(sys.executable, str(lab / "llama.cpp" / "convert_hf_to_gguf.py"), str(run.dir / "merged"),
       "--outtype", "bf16", "--no-mtp", "--outfile", str(bf16))  # fmt: skip
    sh(str(lab / "llama.cpp-build" / "bin" / "llama-quantize"), str(bf16), str(q4), "Q4_K_M")
    return {"gguf_sha256": sha256_file(q4), "gguf_bytes": q4.stat().st_size,
            "quantization": "Q4_K_M"}  # fmt: skip


def modelfile_head(run: Run) -> list[str]:
    """The base's TEMPLATE, RENDERER, PARSER and PARAMETER lines, from Ollama itself."""
    container = run.spec.get("ollama_container", "ollama")
    text = sh("docker", "exec", container, "ollama", "show", "--modelfile",
              run.spec["base"]["ollama_base"])  # fmt: skip
    keep = ("TEMPLATE ", "RENDERER ", "PARSER ", "PARAMETER ")
    return [line for line in text.splitlines() if line.startswith(keep)]


def create(run: Run, gguf: Path, tag: str, *, adapter: bool = False) -> dict[str, Any]:
    container = run.spec.get("ollama_container", "ollama")
    name = gguf.name
    sh("docker", "exec", container, "mkdir", "-p", IMPORT_DIR)
    sh("docker", "cp", str(gguf), f"{container}:{IMPORT_DIR}/{name}")
    if adapter:
        lines = [f"FROM {run.spec['base']['ollama_base']}", f"ADAPTER {IMPORT_DIR}/{name}"]
    else:
        lines = [f"FROM {IMPORT_DIR}/{name}", *modelfile_head(run)]
    head = [
        f"# harnesswalk tune run {run.spec['run_id']}: {run.spec['name']}",
        f"# base {run.spec['base']['repo']} ({run.spec['base'].get('revision')})",
    ]
    text = "\n".join(head + lines) + "\n"
    mf = run.dir / ("Modelfile.adapter" if adapter else "Modelfile")
    mf.write_text(text)
    sh("docker", "cp", str(mf), f"{container}:{IMPORT_DIR}/{mf.name}")
    try:
        sh("docker", "exec", container, "ollama", "create", tag, "-f", f"{IMPORT_DIR}/{mf.name}")
    finally:
        subprocess.run(["docker", "exec", container, "rm", "-f", f"{IMPORT_DIR}/{name}",
                        f"{IMPORT_DIR}/{mf.name}"], capture_output=True)  # fmt: skip
    tags = json.loads(sh("curl", "-s", "http://localhost:11434/api/tags"))
    digest = next((m.get("digest") for m in tags.get("models", []) if m.get("name") == tag), None)
    return {"tag": tag, "digest": digest, "modelfile": text}


def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    out = sh("curl", "-s", "-X", "POST", f"http://localhost:11434{path}", "-d", json.dumps(body))
    return json.loads(out)


def to_ollama(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        item = {"role": m["role"], "content": m.get("content") or ""}
        if m.get("tool_calls"):
            item["tool_calls"] = [{"function": c["function"]} for c in m["tool_calls"]]
        if m["role"] == "tool" and m.get("name"):
            item["tool_name"] = m["name"]
        out.append(item)
    return out


def check(run: Run, tag: str) -> dict[str, Any]:
    """Serve what you train on: an example's prefix rendered by this file's port of Ollama's
    renderer and sent raw, against the same messages through Ollama's chat (its own renderer),
    for a plain example and one whose history has a tool call and its result. The same prompt
    token count and the same reply mean they agree."""
    from transformers import AutoTokenizer

    rows = load_jsonl(run.dir / "val.jsonl") + load_jsonl(run.dir / "train.jsonl")
    with_tools = next((r for r in rows if any(m["role"] == "tool" for m in r["messages"])), None)
    tok = AutoTokenizer.from_pretrained(base_dir(run))
    opts = {"temperature": 0, "num_ctx": 16384, "num_predict": 256, "seed": 7}
    out = []
    for ex in [rows[0], *([with_tools] if with_tools else [])]:
        last = max(i for i, m in enumerate(ex["messages"]) if m["role"] == "assistant")
        prefix, tools = ex["messages"][:last], ex.get("tools") or []
        prompt = render(prefix, tools)
        raw = post(
            "/api/generate",
            {
                "model": tag,
                "prompt": prompt,
                "raw": True,
                "stream": False,
                "options": opts,
                "keep_alive": "2m",
            },
        )
        chat = post("/api/chat", {"model": tag, "messages": to_ollama(prefix), "stream": False,
                                   "tools": tools or None, "think": False, "options": opts,
                                   "keep_alive": "2m"})  # fmt: skip
        out.append({
            "rendered_prompt_tokens": len(tok(prompt, add_special_tokens=False)["input_ids"]),
            "raw_prompt_tokens": raw.get("prompt_eval_count"),
            "chat_prompt_tokens": chat.get("prompt_eval_count"),
            "raw_reply": (raw.get("response") or "")[:300],
            "chat_reply": (chat.get("message") or {}).get("content", "")[:300],
            "chat_tool_calls": (chat.get("message") or {}).get("tool_calls"),
            "expected": assistant_body(ex["messages"][last])[:300],
        })  # fmt: skip
    same = all(c["rendered_prompt_tokens"] == c["chat_prompt_tokens"] for c in out)
    return {"same_prompt_tokens": same, "examples": out}


def adapter_path(run: Run) -> dict[str, Any]:
    """The other serving route: the LoRA itself as GGUF on top of Ollama's own base."""
    lab = run.lab
    lora = run.dir / "adapter-f16.gguf"
    try:
        sh(sys.executable, str(lab / "llama.cpp" / "convert_lora_to_gguf.py"),
           "--base", base_dir(run), "--outfile", str(lora), "--outtype", "f16",
           str(run.dir / "adapter"))  # fmt: skip
        made = create(run, lora, run.spec["tag"].replace(":", "-adapter:"), adapter=True)
        reply = post(
            "/api/chat",
            {
                "model": made["tag"],
                "stream": False,
                "think": False,
                "keep_alive": 0,
                "options": {"temperature": 0, "num_ctx": 4096},
                "messages": [{"role": "user", "content": "Say hello."}],
            },
        )
        if reply.get("error"):
            return {"ok": False, "error": reply["error"][:300], "tag": made["tag"]}
        return {"ok": True, "tag": made["tag"], "digest": made["digest"],
                "reply": (reply.get("message") or {}).get("content", "")[:200]}  # fmt: skip
    except Exception as exc:  # the route is tested, not relied on
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}


def clean(run: Run) -> None:
    """Keep the adapter, the Q4_K_M GGUF and the logs; the merge and the bf16 GGUF go."""
    shutil.rmtree(run.dir / "merged", ignore_errors=True)
    shutil.rmtree(run.dir / "trainer", ignore_errors=True)
    for name in ("model-bf16.gguf", "adapter-f16.gguf"):
        (run.dir / name).unlink(missing_ok=True)


def all_phases(run: Run) -> None:
    result: dict[str, Any] = {"run_id": run.spec["run_id"]}
    trained = train(run)
    total = trained["metrics"]["steps"]

    def phase(name: str) -> None:
        run.status("running", name)
        run.step(step=max(1, total), total_steps=max(1, total), phase=name,
                 elapsed_s=round(time.time() - run.started, 1))  # fmt: skip

    phase("merge")
    merge(run)
    phase("convert")
    converted = convert(run)
    phase("create")
    made = create(run, run.dir / "model-q4_k_m.gguf", run.spec["tag"])
    phase("check")
    checked = check(run, made["tag"])
    tried = adapter_path(run) if run.spec.get("try_adapter", True) else {"ok": None}
    clean(run)
    result.update(
        metrics={**trained["metrics"], "pipeline_wall_s": round(time.time() - run.started, 1)},
        weights=trained["weights"],
        serve={
            "provider": "ollama",
            "tag": made["tag"],
            "digest": made["digest"],
            "method": "merged_gguf",
            **converted,
            "gguf_location": f"homelab:{run.dir / 'model-q4_k_m.gguf'}",
        },  # fmt: skip
        modelfile=made["modelfile"],
        template_check=checked,
        adapter_route=tried,
        base_revision=trained["base_revision"],
    )
    run.result(result)
    run.status("done", "done")


def main() -> int:
    action, run_dir = sys.argv[1], sys.argv[2]
    run = Run(run_dir)
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", str(run.spec.get("cuda_visible_devices", "1")))
    try:
        if action == "all":
            all_phases(run)
        elif action == "train":
            print(json.dumps(train(run), indent=2))
        elif action == "merge":
            merge(run)
        elif action == "convert":
            print(json.dumps(convert(run), indent=2))
        elif action == "create":
            print(json.dumps(create(run, run.dir / "model-q4_k_m.gguf", run.spec["tag"]), indent=2))
        elif action == "check":
            print(json.dumps(check(run, run.spec["tag"]), indent=2))
        elif action == "adapter":
            print(json.dumps(adapter_path(run), indent=2))
        elif action == "clean":
            clean(run)
        else:
            raise SystemExit(f"unknown action {action}")
    except Exception as exc:
        run.status("error", "error", error=f"{type(exc).__name__}: {exc}"[:600])
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
