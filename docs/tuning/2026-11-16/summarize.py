"""Summarize the 2026-11-16 scale-up round: the test-set results, the secondary scorecards,
the loss logs and the GPU traces. The 2026-11-15 script with a `testset` mode added.

    uv run python docs/tuning/2026-11-16/summarize.py testset OUT.json results/*.json
    uv run python docs/tuning/2026-11-16/summarize.py cells OUT.json IN.json [IN.json ...]
    uv run python docs/tuning/2026-11-16/summarize.py loss OUT.csv RUN=STEPS.jsonl [...]
    uv run python docs/tuning/2026-11-16/summarize.py gpu OUT.json GPU_DIR

`testset` reads `export_results.py` files (one per model, case by case, on the frozen test set)
and gives per model: the pass rate with a Wilson 95% interval over all cases, per pack and per
kind, the failing scorers, the `tool_calls` failure split per pack, mean, median and p95 seconds
per case, decode tokens per second and the cost; and, for the models named `<base>-s<seed>`
beside `<base>`, the seed spread (mean, min, max of the pass rate) per pack.

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
import re
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


def wilson(passed: int, n: int, z: float = 1.96) -> list[float]:
    if n <= 0:
        return [0.0, 0.0]
    p = passed / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def rate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(cases)
    p = sum(1 for c in cases if c["status"] == "passed")
    return {"n": n, "passed": p, "rate": round(p / n, 4) if n else 0.0, "ci95": wilson(p, n)}


def model_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    packs = sorted({c["pack"] for c in cases})
    secs = [float((c.get("timing") or {}).get("seconds") or 0) for c in cases]
    gen_ms = sum(float((c.get("timing") or {}).get("gen_ms") or 0) for c in cases)
    out_tok = sum(int((c.get("timing") or {}).get("output_tokens") or 0) for c in cases)
    split = {
        p: dict(Counter(c["tool_split"] for c in cases if c["pack"] == p and c.get("tool_split")))
        for p in packs
    }
    kinds: dict[str, dict[str, Any]] = {}
    for p in packs:
        for k in sorted({c["kind"] for c in cases if c["pack"] == p}):
            kinds[f"{p}/{k}"] = rate([c for c in cases if c["pack"] == p and c["kind"] == k])
    return {
        "all": rate(cases),
        "by_pack": {p: rate([c for c in cases if c["pack"] == p]) for p in packs},
        "by_kind": kinds,
        "failed_scorers": dict(Counter(f for c in cases for f in c.get("failed") or [])),
        "tool_split_by_pack": split,
        "write_tool_not_called": sum(v.get("write tool not called", 0) for v in split.values()),
        "mean_s": round(statistics.mean(secs), 3) if secs else None,
        "median_s": rank(secs, 0.5),
        "p95_s": rank(secs, 0.95),
        "decode_tok_s": round(out_tok / (gen_ms / 1000), 1) if gen_ms else None,
        "usd": round(sum(float(c.get("usd") or 0) for c in cases), 4),
    }


def testset(out: Path, inputs: list[Path]) -> int:
    models = {}
    for path in inputs:
        data = json.loads(path.read_text(encoding="utf-8"))
        models[path.stem] = {"cases": len(data["cases"]), "missing": len(data["missing"]),
                             **model_summary(data["cases"])}  # fmt: skip
    seeds = {}
    for name in models:
        group = [m for m in models if m == name or m.startswith(name + "-s")]
        if len(group) < 2 or re.search(r"-s\d+$", name):
            continue
        keys = ["all", *sorted(models[name]["by_pack"])]
        seeds[name] = {"models": group}
        for key in keys:
            rates = [
                (models[m]["all"] if key == "all" else models[m]["by_pack"][key])["rate"]
                for m in group
            ]
            seeds[name][key] = {
                "rates": rates, "mean": round(statistics.mean(rates), 4),
                "min": min(rates), "max": max(rates),
                "sd": round(statistics.stdev(rates), 4) if len(rates) > 1 else None,
            }  # fmt: skip
    out.write_text(json.dumps({"models": models, "seeds": seeds}, indent=1) + "\n")
    for name, m in sorted(models.items(), key=lambda kv: kv[1]["all"]["rate"]):
        a = m["all"]
        packs = " ".join(f"{p} {v['rate']:.3f}" for p, v in m["by_pack"].items())
        print(f"{name:<26} {a['passed']}/{a['n']} {a['rate']:.3f} {a['ci95']} {packs}")
    for name, s in seeds.items():
        print(f"seeds {name}: {s['models']} all {s['all']['rates']}")
    return 0


def main(argv: list[str]) -> int:
    if argv[0] == "testset":
        return testset(Path(argv[1]), [Path(p) for p in argv[2:]])
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
