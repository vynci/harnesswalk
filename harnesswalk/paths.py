"""Where the repository lives: config/, spec/ and datasets/ are read from here."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def repo_root() -> Path:
    """The repo root, or HARNESSWALK_ROOT when the package runs from somewhere else."""
    return Path(os.environ.get("HARNESSWALK_ROOT") or PACKAGE_ROOT)


def spec_dir() -> Path:
    return repo_root() / "spec"
