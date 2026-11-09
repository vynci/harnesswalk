"""Shared helpers of the homelab worker: the run directory, its status file and step log.

The harness (harnesswalk/ops/tune/trainers.py, SshTrainer) polls two files in the run
directory: `status.json` ({state, phase, error, ...}) and `steps.jsonl` (one JSON object per
optimizer step or phase). `result.json` is written last, when everything is done.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


class Run:
    def __init__(self, run_dir: str | Path) -> None:
        self.dir = Path(run_dir).expanduser().resolve()
        self.spec: dict[str, Any] = json.loads((self.dir / "run.json").read_text())
        self.started = time.time()

    @property
    def lab(self) -> Path:
        return Path(os.environ.get("HARNESSWALK_LAB", str(Path.home() / "harnesswalk-lab")))

    def status(self, state: str, phase: str, **extra: Any) -> None:
        data = {"state": state, "phase": phase, "at": time.time(), **extra}
        tmp = self.dir / "status.json.tmp"
        tmp.write_text(json.dumps(data))
        tmp.replace(self.dir / "status.json")

    def step(self, **event: Any) -> None:
        with (self.dir / "steps.jsonl").open("a") as f:
            f.write(json.dumps(event) + "\n")

    def result(self, data: dict[str, Any]) -> None:
        (self.dir / "result.json").write_text(json.dumps(data, indent=2))

    def read_result(self) -> dict[str, Any]:
        path = self.dir / "result.json"
        return json.loads(path.read_text()) if path.is_file() else {}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tree_sha256(folder: Path, pattern: str = "*.safetensors") -> tuple[str, int]:
    """One sha256 over the sorted files matching `pattern` (name and content), and their bytes."""
    h = hashlib.sha256()
    size = 0
    for path in sorted(folder.glob(pattern)):
        h.update(path.name.encode())
        h.update(sha256_file(path).encode())
        size += path.stat().st_size
    return h.hexdigest(), size
