"""This repo is yours: no file imports Harnesswalk, and the managed core matches the lock."""

import hashlib
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
IMPORT = re.compile(r"^\s*(from|import)\s+harnesswalk\b", re.M)


def test_no_harnesswalk_import() -> None:
    found = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py")
             if ".venv" not in p.parts and IMPORT.search(p.read_text(encoding="utf-8"))]  # fmt: skip
    assert found == []


def test_lock_names_every_managed_file() -> None:
    lock = yaml.safe_load((ROOT / "harness.lock").read_text())
    for path, entry in lock["files"].items():
        if entry["managed"]:
            pristine = (ROOT / ".harnesswalk" / "pristine" / path).read_bytes()
            assert hashlib.sha256(pristine).hexdigest() == entry["sha256"], path
