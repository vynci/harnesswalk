#!/usr/bin/env python3
"""GPU logger for the homelab: one `nvidia-smi` CSV per stage, and a JSON sidecar of stages.

    python3 gpulog.py start curate_v2 --note "qwen3.5:27b teacher on GPU 0"
    python3 gpulog.py stop curate_v2
    python3 gpulog.py status
    python3 gpulog.py downsample curate_v2.csv curate_v2-5s.csv --seconds 5

`start` runs, detached (it survives the ssh session that started it),

    nvidia-smi --query-gpu=timestamp,index,utilization.gpu,memory.used,power.draw,temperature.gpu
               --format=csv -l 1

into `<dir>/<stage>.csv` and records the stage in `<dir>/stages.json`: name, start and end (UTC,
RFC 3339, and epoch seconds), the CSV, the logger's pid, the host, the host's UTC offset (the
`timestamp` column of nvidia-smi is local time) and a free-text note. `stop` ends the logger and
records the end. Stages may overlap: every CSV has a row per GPU per second, so a chart cuts by
the `index` column (GPU 0 serves Ollama, GPU 1 trains; deploy/homelab/README.md).

`downsample` averages each GPU's rows over windows of N seconds, to keep a long stage's CSV
small enough for git. Standard library only, so it runs on the host Python.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

QUERY = "timestamp,index,utilization.gpu,memory.used,power.draw,temperature.gpu"
DEFAULT_DIR = Path.home() / "harnesswalk-lab" / "gpulog"


def now() -> tuple[str, float]:
    t = time.time()
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    ), round(t, 3)


@contextlib.contextmanager
def sidecar(folder: Path):
    """The stages list, locked for the read-modify-write (two stages may start at once)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "stages.json"
    with (folder / ".stages.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text()) if path.is_file() else {"stages": []}
        yield data
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(path)


def open_stage(data: dict[str, Any], stage: str) -> dict[str, Any] | None:
    return next((s for s in data["stages"] if s["stage"] == stage and not s.get("end")), None)


def start(args: argparse.Namespace) -> int:
    folder = Path(args.dir).expanduser()
    with sidecar(folder) as data:
        if open_stage(data, args.stage):
            print(f"gpulog: stage {args.stage} is already logging", file=sys.stderr)
            return 1
        out = folder / f"{args.stage}.csv"
        if out.exists():
            print(f"gpulog: {out} exists; pick a new stage name", file=sys.stderr)
            return 1
        cmd = ["nvidia-smi", f"--query-gpu={QUERY}", "--format=csv", "-l", str(args.interval)]
        with out.open("w") as f:
            proc = subprocess.Popen(  # noqa: S603 (a fixed command)
                cmd, stdout=f, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                start_new_session=True,
            )  # fmt: skip
        stamp, epoch = now()
        data["stages"].append({
            "stage": args.stage, "start": stamp, "start_epoch": epoch, "end": None,
            "end_epoch": None, "csv": out.name, "pid": proc.pid, "host": socket.gethostname(),
            "utc_offset": time.strftime("%z"), "interval_s": args.interval,
            "command": " ".join(cmd), "note": args.note or "",
        })  # fmt: skip
    print(f"gpulog: {args.stage} logging to {out} (pid {proc.pid})")
    return 0


def stop(args: argparse.Namespace) -> int:
    folder = Path(args.dir).expanduser()
    with sidecar(folder) as data:
        stage = open_stage(data, args.stage)
        if stage is None:
            print(f"gpulog: no open stage {args.stage}", file=sys.stderr)
            return 1
        with contextlib.suppress(ProcessLookupError):
            os.killpg(stage["pid"], signal.SIGTERM)
        stage["end"], stage["end_epoch"] = now()
        if args.note:
            stage["note"] = (stage["note"] + " " + args.note).strip()
        rows = sum(1 for _ in (folder / stage["csv"]).open()) - 1
        stage["rows"] = rows
    print(f"gpulog: {args.stage} stopped ({rows} rows)")
    return 0


def status(args: argparse.Namespace) -> int:
    folder = Path(args.dir).expanduser()
    path = folder / "stages.json"
    data = json.loads(path.read_text()) if path.is_file() else {"stages": []}
    for s in data["stages"]:
        state = f"until {s['end']}" if s.get("end") else "logging"
        print(f"{s['stage']:<28} from {s['start']} {state}  {s['csv']}")
    return 0


def number(cell: str) -> float | None:
    """`45 %`, `1234 MiB`, `85.21 W`, `61` as a number; `[N/A]` as None."""
    head = cell.strip().split(" ")[0]
    try:
        return float(head)
    except ValueError:
        return None


def downsample(args: argparse.Namespace) -> int:
    """Mean of each GPU's rows per window of `seconds`, the window's first timestamp kept."""
    window = max(1, int(args.seconds))
    with open(args.src, newline="") as f:
        reader = csv.reader(f)
        header = [h.strip() for h in next(reader)]
        rows = [[c.strip() for c in r] for r in reader if r and not r[0].startswith("timestamp")]
    groups: dict[str, list[list[str]]] = {}
    for row in rows:
        gpu = row[1]
        groups.setdefault(gpu, []).append(row)
    out_rows: list[list[str]] = []
    for gpu, items in groups.items():
        for at in range(0, len(items), window):
            chunk = items[at : at + window]
            means = []
            for col in range(2, len(header)):
                vals = [v for v in (number(r[col]) for r in chunk) if v is not None]
                means.append(f"{sum(vals) / len(vals):.2f}" if vals else "")
            out_rows.append([chunk[0][0], gpu, *means])
    out_rows.sort(key=lambda r: (r[0], int(r[1])))
    plain = ["timestamp", "index", "utilization_gpu_pct", "memory_used_mib", "power_draw_w",
             "temperature_c"]  # fmt: skip
    with open(args.dst, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(plain[: len(header)])
        w.writerows(out_rows)
    print(f"gpulog: {len(rows)} rows -> {len(out_rows)} ({window} s windows) in {args.dst}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="action", required=True)
    for name in ("start", "stop"):
        s = sub.add_parser(name)
        s.add_argument("stage", help="a stage name: letters, digits, dash, underscore")
        s.add_argument("--dir", default=str(DEFAULT_DIR))
        s.add_argument("--note", default="")
        if name == "start":
            s.add_argument("--interval", type=int, default=1, help="seconds between samples")
    s = sub.add_parser("status")
    s.add_argument("--dir", default=str(DEFAULT_DIR))
    s = sub.add_parser("downsample")
    s.add_argument("src")
    s.add_argument("dst")
    s.add_argument("--seconds", type=int, default=5)
    args = p.parse_args(argv)
    if (
        args.action in ("start", "stop")
        and not args.stage.replace("-", "").replace("_", "").isalnum()
    ):
        p.error(f"bad stage name {args.stage!r}")
    return {"start": start, "stop": stop, "status": status, "downsample": downsample}[args.action](
        args
    )


if __name__ == "__main__":
    sys.exit(main())
