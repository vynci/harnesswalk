"""Tuning, in miniature: curate a dataset from teacher turns that pass, then hand it to a trainer.

Contract: spec/contracts/tuning.md and events.md section 14. Nodes: `curate` (under `eval`)
and `tune` (under `release`); both run outside turns and report as ops events on streams of
their own (`curate:<id>`, `tune:<run_id>`), kept in the `ops_events` table.

- `curate`: every golden case of the pack runs as a turn pinned to the teacher (a fake teacher
  replays the case's fixture), through the whole harness, approvals decided by the case. Only
  turns every deterministic scorer passed are kept; each becomes one chat example (the last
  model call's messages and the tools it offered, plus the reply), deduplicated by hash, with a
  validation split. Files under `data/tune/<name>/`, a manifest with their sha256.
- `run`: the `fake` trainer is deterministic and instant (a loss curve from the dataset hash),
  for CI and for seeing the events; the `external` trainer writes what a real LoRA run needs
  (the dataset, the student, a Modelfile with an ADAPTER line) for you to train elsewhere.
  Neither edits a router rule: serving a tuned model is your edit to config/harness.yaml.

Not generated from tuning.md: teacher rewrites of the prompts (`variants`), the prompt arm,
compare (the model gate), promote, the canary rule and rollback. GENERATION_NOTES.md says so.

Lesson: fine-tuning is the fourth lever, after memory, prompts and config. Curating only what
the harness itself scored as passing is what keeps a tuned model from learning its mistakes.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from harness.core import evals, gateway
from harness.core.events import SCHEMA_VERSION, iso


class OpsStream:
    """One ops stream (events.md section 10): seq from 0, one trace id, no turn."""

    def __init__(self, h: Any, stream: str, tenant: str) -> None:
        self.h, self.stream, self.tenant = h, stream, tenant
        self.trace, self.seq = secrets.token_hex(16), 0

    def emit(self, type_: str, node_id: str, status: str, summary: dict[str, Any]) -> dict[str, Any]:
        event = {"schema_version": SCHEMA_VERSION, "event_id": uuid.uuid4().hex, "seq": self.seq,
                 "ts": iso(time.time()), "trace_id": self.trace, "turn_id": None, "session_id": None,
                 "tenant_id": self.tenant, "type": type_, "node_id": node_id, "status": status,
                 "summary": {k: v for k, v in summary.items() if v is not None}}  # fmt: skip
        self.seq += 1
        self.h.db.insert("ops_events", {"stream": self.stream, "tenant": self.tenant, "event": event})
        return event


def chat(m: dict[str, Any]) -> dict[str, Any]:
    out = {"role": m["role"], "content": m.get("content") or ""}
    if m.get("tool_calls"):
        out["tool_calls"] = [{"id": c.get("call_id"), "type": "function",
                              "function": {"name": c["name"], "arguments": c.get("arguments") or {}}}
                             for c in m["tool_calls"]]  # fmt: skip
    if m["role"] == "tool":
        out["tool_call_id"], out["name"] = m.get("call_id") or "", m.get("name") or ""
    return out


def curate(h: Any, name: str, teacher: str, tenant: str = "dev", val_fraction: float = 0.1) -> dict[str, Any]:
    pack = h.settings.default_pack
    cid = f"cur_{uuid.uuid4().hex[:8]}"
    stream = OpsStream(h, f"curate:{cid}", tenant)
    cases = [c for c in evals.load_cases(h, pack) if c.get("prompt") and c.get("fixture")]
    stream.emit("eval_run_started", "curate", "active", {"run_id": cid, "purpose": "curate",
                "model": teacher, "pack": pack, "packs": [pack], "total": len(cases)})  # fmt: skip
    fake = h.settings.get(f"models.models.{teacher}.provider") == "fake"
    examples: dict[str, dict[str, Any]] = {}
    dropped: dict[str, int] = {}
    for i, case in enumerate(cases):
        extra = {"roles": case.get("roles"), "approval": case.get("approval", "deny"),
                 "fixture": case["fixture"] if fake else None}  # fmt: skip
        body = {"message": case["prompt"], "options": {"model": None if fake else teacher}}
        turn = gateway.start_turn(h, f"curate-{cid}", body, kind="eval", extra=extra)
        with turn.cond:
            while not turn.done:
                turn.cond.wait(0.5)
        act = evals.actual_of(turn.events)
        scores = evals.score(case.get("expect") or {}, case.get("checks") or {}, act, [case["prompt"]], None)
        failed = [k for k, v in scores.items() if not v["pass"]]
        last = turn.data.get("last_call") or {}
        kept = not failed and bool((last.get("response") or {}).get("text"))
        if kept:
            specs = turn.registry.specs
            ex = {"messages": [chat(m) for m in last["messages"]]
                  + [{"role": "assistant", "content": last["response"]["text"]}],
                  "tools": [{"type": "function", "function": {"name": n, "description": specs[n].get("description", ""),
                             "parameters": specs[n]["parameters"]}} for n in last["tools"] if n in specs]}  # fmt: skip
            examples[hashlib.sha256(json.dumps(ex, sort_keys=True).encode()).hexdigest()] = ex
        else:
            dropped[failed[0] if failed else "no_answer"] = dropped.get(failed[0] if failed else "no_answer", 0) + 1
        stream.emit("eval_case_finished", "curate", "done", {"run_id": cid, "case_id": f"{pack}/{case['id']}",
                    "status": "passed" if not failed else "failed", "kept": kept, "index": i, "total": len(cases)})  # fmt: skip
    out = Path(h.settings.data_dir) / "tune" / name
    out.mkdir(parents=True, exist_ok=True)
    split = {"train": [], "val": []}
    for digest, ex in sorted(examples.items()):
        split["val" if int(digest[:8], 16) / 2**32 < val_fraction else "train"].append(ex)
    files = {}
    for part, rows in split.items():
        text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        (out / f"{part}.jsonl").write_text(text, encoding="utf-8")
        files[part] = {"path": f"{part}.jsonl", "sha256": hashlib.sha256(text.encode()).hexdigest(), "rows": len(rows)}
    manifest = {"dataset": f"dataset/{name}@v1", "teacher": teacher, "packs": [pack], "curation_id": cid,
                "counts": {"turns": len(cases), "kept": len(examples), "dropped": dropped,
                           "train": len(split["train"]), "val": len(split["val"])},
                "files": files, "synthetic": True,
                "statement": "Only the pack's own golden prompts were used; no real tenant's turns."}  # fmt: skip
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    stream.emit("eval_run_finished", "curate", "done", {"run_id": cid, "status": "done",
                "totals": {"cases": len(cases), "kept": len(examples)}, "kept": len(examples),
                "dataset": manifest["dataset"]})  # fmt: skip
    return manifest


def run(h: Any, dataset: str, trainer: str, student: str, tenant: str = "dev") -> dict[str, Any]:
    base = Path(h.settings.data_dir) / "tune" / dataset
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    run_id, name = f"tun_{uuid.uuid4().hex[:8]}", f"{dataset}_{student}".replace(":", "_").replace(".", "_")
    stream = OpsStream(h, f"tune:{run_id}", tenant)
    counts = manifest["counts"]
    total = max(1, math.ceil(counts["train"] / 4)) * 3
    stream.emit("tune_run_started", "tune", "active", {"run_id": run_id, "trainer": trainer,
                "trainer_kind": trainer, "student": student, "teacher": manifest["teacher"],
                "dataset": manifest["dataset"], "examples": {"train": counts["train"], "val": counts["val"]},
                "total_steps": total, "config": {"method": "lora", "rank": 16, "epochs": 3}})  # fmt: skip
    model_name = h.settings.get(f"models.models.{student}.name") or student
    modelfile = f"FROM {model_name}\nADAPTER ./adapter.gguf\nPARAMETER temperature 0\n"
    (base / f"{name}.Modelfile").write_text(modelfile, encoding="utf-8")
    if trainer == "fake":  # a fixed loss curve from the dataset hash: deterministic, instant
        seed = int(manifest["files"]["train"]["sha256"][:8], 16)
        for step in range(1, total + 1):
            loss = round(2.0 * math.exp(-step / (total / 2)) + (seed % 7) / 100, 4)
            stream.emit("tune_step", "tune", "active", {"run_id": run_id, "step": step, "total_steps": total,
                        "epoch": 1 + (step - 1) * 3 // total, "loss": loss, "phase": "train"})  # fmt: skip
        weights = hashlib.sha256(f"{seed}:{student}".encode()).hexdigest()
        record = {"adapter": f"adapter/{name}@v1", "model": f"model/{name}@v1", "weights_sha256": weights,
                  "metrics": {"steps": total, "train_loss": loss, "eval_loss": None, "wall_s": 0}}  # fmt: skip
    else:  # external: everything a real LoRA run needs, for you to train elsewhere
        record = {"adapter": None, "model": None, "instructions": (
            f"Train a LoRA adapter for {model_name} on {base / 'train.jsonl'} (chat format with tool "
            f"calls), export it as GGUF next to {name}.Modelfile, then `ollama create {name} -f "
            f"{name}.Modelfile` and point a model alias in config/harness.yaml at it.")}  # fmt: skip
    (base / f"{name}.run.json").write_text(json.dumps({"run_id": run_id, **record}, indent=2) + "\n")
    stream.emit("tune_run_finished", "tune", "done", {"run_id": run_id, "status": "done",
                "adapter": record.get("adapter"), "model": record.get("model"),
                "metrics": record.get("metrics")})  # fmt: skip
    return {"run_id": run_id, "name": name, **record}
