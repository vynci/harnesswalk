"""Summarize the 2026-11-15 evidence round's scorecard JSON files and loss logs.

    uv run python docs/tuning/2026-11-15/summarize.py cells OUT.json IN.json [IN.json ...]
    uv run python docs/tuning/2026-11-15/summarize.py loss OUT.csv RUN=STEPS.jsonl [...]
    uv run python docs/tuning/2026-11-15/summarize.py gpu OUT.json GPU_DIR

`gpu` reads `GPU_DIR/stages.json` (deploy/homelab/gpulog.py) and each stage's CSV (raw or
downsampled) and gives per stage and GPU: seconds, mean and p95 utilization, peak memory, mean
and peak power, peak temperature, and the energy in watt-hours (mean power times duration).

`loss` joins the homelab worker's `steps.jsonl` files (one JSON object per optimizer step, and
one per evaluation with `eval_loss`) into one CSV: run, step, epoch, elapsed_s, loss, eval_loss,
learning_rate, one row per logged line of the train phase.

For every (model, pack) cell of the inputs: the pass count of each sample, the mean, min, max
and standard deviation (sample, n - 1), the scored case count, the median and p95 seconds per
case (nearest rank, over every case run of every sample), decode and prefill tokens per second
(Ollama's own `eval_duration` and `prompt_eval_duration`, summed over the cell), the cost per
sample, the `tool_calls` failure split (mean per sample) and the error count. A model that
appears in several inputs keeps its cells apart by input file name. Standard library only.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def rank(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def cell_summary(cell: dict[str, Any]) -> dict[str, Any]:
    samples = cell.get("samples") or [cell]
    passed = [int((s.get("totals") or {}).get("passed", 0)) for s in samples]
    totals = samples[0].get("totals") or {}
    scored = int(totals.get("cases", 0)) - int(totals.get("skipped", 0))
    secs: list[float] = []
    gen_ms = prompt_ms = 0.0
    gen_out = prompt_in = errors = 0
    split: Counter[str] = Counter()
    for s in samples:
        for c in s.get("cases") or []:
            if c.get("status") == "error" or c.get("errors"):
                errors += 1
            if c.get("tool_split"):
                split[c["tool_split"]] += 1
            t = c.get("timing")
            if not t or c.get("status") == "skipped":
                continue
            secs.append(float(t.get("seconds") or 0))
            if t.get("gen_ms"):
                gen_ms += float(t["gen_ms"])
                gen_out += int(t.get("output_tokens") or 0)
            if t.get("prompt_ms"):
                prompt_ms += float(t["prompt_ms"])
                prompt_in += int(t.get("input_tokens") or 0)
    usd = [float((s.get("cost") or {}).get("usd", 0.0)) for s in samples]
    n = len(samples)
    return {
        "status": cell.get("status"),
        "samples": n,
        "scored": scored,
        "passed": passed,
        "mean": round(statistics.mean(passed), 2) if passed else None,
        "min": min(passed) if passed else None,
        "max": max(passed) if passed else None,
        "sd": round(statistics.stdev(passed), 2) if len(passed) > 1 else None,
        "case_runs_timed": len(secs),
        "median_s": round(rank(secs, 0.5), 2) if secs else None,
        "p95_s": round(rank(secs, 0.95), 2) if secs else None,
        "mean_s": round(statistics.mean(secs), 2) if secs else None,
        "decode_tok_s": round(gen_out / (gen_ms / 1000), 1) if gen_ms else None,
        "prefill_tok_s": round(prompt_in / (prompt_ms / 1000), 1) if prompt_ms else None,
        "usd_per_sample": round(statistics.mean(usd), 4) if usd else 0.0,
        "usd_total": round(sum(usd), 4),
        "sample_seconds": [s.get("seconds") for s in samples],
        "tool_split_per_sample": {k: round(v / n, 2) for k, v in sorted(split.items())},
        "error_case_runs": errors,
    }


def loss_csv(out: Path, runs: list[str]) -> int:
    import csv

    cols = ["run", "step", "epoch", "elapsed_s", "loss", "eval_loss", "learning_rate"]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for item in runs:
            run, _, path = item.partition("=")
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row.get("phase") != "train" or "step" not in row:
                    continue
                w.writerow([run, *(row.get(c, "") for c in cols[1:])])
    return 0


def gpu_summary(out: Path, folder: Path) -> int:
    import csv

    stages = json.loads((folder / "stages.json").read_text(encoding="utf-8"))["stages"]
    result = []
    for st in stages:
        path = folder / st["csv"]
        if not path.is_file():
            continue
        per: dict[str, list[list[float]]] = {}
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)
            for row in reader:
                if len(row) < 6 or row[0].strip().startswith("timestamp"):
                    continue
                vals = [_num(c) for c in row[2:6]]
                if None in vals:
                    continue
                per.setdefault(row[1].strip(), []).append(vals)  # type: ignore[arg-type]
        secs = (st.get("end_epoch") or 0) - (st.get("start_epoch") or 0)
        gpus = {}
        for gpu, rows in sorted(per.items()):
            util, mem, power, temp = (list(c) for c in zip(*rows, strict=True))
            gpus[gpu] = {
                "rows": len(rows), "util_mean_pct": round(statistics.mean(util), 1),
                "util_p95_pct": rank(util, 0.95), "mem_peak_mib": max(mem),
                "power_mean_w": round(statistics.mean(power), 1), "power_peak_w": max(power),
                "temp_peak_c": max(temp),
                "energy_wh": round(statistics.mean(power) * secs / 3600, 1) if secs > 0 else None,
            }  # fmt: skip
        result.append({
            "stage": st["stage"], "start": st["start"], "end": st.get("end"),
            "seconds": round(secs, 1), "note": st.get("note"), "gpus": gpus,
        })  # fmt: skip
    out.write_text(json.dumps({"stages": result}, indent=1) + "\n", encoding="utf-8")
    for r in result:
        for gpu, g in r["gpus"].items():
            print(
                f"{r['stage']:<34} gpu{gpu} {r['seconds']:>7.0f}s util {g['util_mean_pct']}%"
                f" mem {g['mem_peak_mib']:.0f} MiB"
                f" power {g['power_mean_w']}/{g['power_peak_w']} W"
                f" temp {g['temp_peak_c']} C {g['energy_wh']} Wh"
            )
    return 0


def _num(cell: str) -> float | None:
    head = cell.strip().split(" ")[0]
    try:
        return float(head)
    except ValueError:
        return None


def _merge(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A model's cells over several packs as samples of one big cell: sample i holds every
    pack's sample i (totals and cost added, cases joined)."""
    n = max((len(c.get("samples") or [c]) for c in cells), default=0)
    out = []
    for i in range(n):
        totals: Counter[str] = Counter()
        usd, cases = 0.0, []
        for c in cells:
            samples = c.get("samples") or [c]
            x = samples[min(i, len(samples) - 1)]
            totals.update({k: int(v) for k, v in (x.get("totals") or {}).items()})
            usd += float((x.get("cost") or {}).get("usd", 0.0))
            cases += x.get("cases") or []
        out.append({"totals": dict(totals), "cost": {"usd": usd}, "cases": cases})
    return out


def main(argv: list[str]) -> int:
    if argv[0] == "gpu":
        return gpu_summary(Path(argv[1]), Path(argv[2]))
    if argv[0] == "loss":
        return loss_csv(Path(argv[1]), argv[2:])
    if argv[0] == "cells":
        argv = argv[1:]
    out_path, inputs = Path(argv[0]), [Path(p) for p in argv[1:]]
    rows: list[dict[str, Any]] = []
    for path in inputs:
        data = json.loads(path.read_text(encoding="utf-8"))
        names = {m["alias"]: m.get("name") for m in data.get("models") or []}
        for cell in data.get("cells") or []:
            rows.append({
                "source": f"{path.parent.name}/{path.name}", "model": cell["model"],
                "pack": cell["pack"],
                "ollama_name": names.get(cell["model"]), **cell_summary(cell),
            })  # fmt: skip
    models = []
    for path in inputs:
        data = json.loads(path.read_text(encoding="utf-8"))
        for alias in dict.fromkeys(c["model"] for c in data.get("cells") or []):
            cells = [c for c in data["cells"] if c["model"] == alias and c.get("status") == "ok"]
            merged = {"samples": _merge(cells)}
            one = cell_summary(merged)
            models.append({
                "source": f"{path.parent.name}/{path.name}", "model": alias,
                "packs": [c["pack"] for c in cells],
                **{k: one[k] for k in ("samples", "scored", "passed", "mean", "min", "max", "sd",
                                       "case_runs_timed", "median_s", "p95_s", "mean_s",
                                       "decode_tok_s", "prefill_tok_s", "usd_per_sample",
                                       "usd_total", "error_case_runs")},
            })  # fmt: skip
    out_path.write_text(
        json.dumps({"cells": rows, "models": models}, indent=1) + "\n", encoding="utf-8"
    )
    for r in rows:
        print(
            f"{r['model']:<22} {r['pack']:<14} {r['mean']}/{r['scored']} "
            f"({r['min']}-{r['max']}, sd {r['sd']}) median {r['median_s']} s p95 {r['p95_s']} s "
            f"decode {r['decode_tok_s']} tok/s ${r['usd_per_sample']}/sample"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
