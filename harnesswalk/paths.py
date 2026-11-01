"""Where the repository lives: config/, spec/ and datasets/ are read from here.

In a checkout that is the directory above the package. An installed wheel has no checkout
around it, so the build bundles `config/`, `spec/`, `datasets/`, `lessons/`, the package's own
sources (for the code peek) and, when it was built, `web/dist` under `harnesswalk/_bundle/`
(`tools/hatch_build.py`), and that directory stands in for the repo root.
"""

from __future__ import annotations

import os
from pathlib import Path

_CHECKOUT = Path(__file__).resolve().parents[1]
BUNDLE = Path(__file__).resolve().parent / "_bundle"


def _package_root() -> Path:
    if (_CHECKOUT / "config" / "harness.yaml").is_file() or not BUNDLE.is_dir():
        return _CHECKOUT
    return BUNDLE


PACKAGE_ROOT = _package_root()


def repo_root() -> Path:
    """The repo root, or HARNESSWALK_ROOT when the package runs from somewhere else."""
    return Path(os.environ.get("HARNESSWALK_ROOT") or PACKAGE_ROOT)


def spec_dir() -> Path:
    return repo_root() / "spec"


def default_data_dir(root: Path) -> Path:
    """`data/` in a checkout. An installed wheel never writes into site-packages: its stores
    go to `harnesswalk-data/` in the working directory unless HARNESSWALK_DATA_DIR says."""
    if Path(root).resolve() == BUNDLE:
        return Path.cwd() / "harnesswalk-data"
    return Path(root) / "data"
