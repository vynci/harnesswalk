"""Bundle the repo content an installed harnesswalk reads into the wheel.

The harness reads `config/`, `spec/`, `datasets/` and `lessons/` relative to the repo root, and
serves its own sources as the code peek and `web/dist` as the app. A wheel has no repo around
it, so this hook copies them under `harnesswalk/_bundle/`, which `harnesswalk/paths.py` uses as
the root when no checkout is found.

The hook is off by default, so `uv sync` (an editable install, in a checkout or the Docker
image) never loads it. `make dist`, `tools/release.py` and the release workflow switch it on:

    HATCH_BUILD_HOOK_ENABLE_CUSTOM=1 uv build --wheel

`web/dist` goes in when it holds a built app (`make web-build` first); set
`HARNESSWALK_BUNDLE_WEB=1` to make a missing build an error, as the release workflow does.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

BUNDLE = "harnesswalk/_bundle"
TREES = ("config", "spec", "datasets", "lessons", "harnesswalk")
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", "_bundle"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".sqlite", ".sqlite3", ".db"}
SKIP_NAMES = {".DS_Store", ".env"}


def _files(base: Path) -> list[Path]:
    out = []
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base)
        if not path.is_file() or SKIP_DIRS & set(rel.parts[:-1]):
            continue
        if path.suffix in SKIP_SUFFIXES or path.name in SKIP_NAMES:
            continue
        out.append(path)
    return out


class BundleHook(BuildHookInterface):
    PLUGIN_NAME = "custom"
    _copies: str | None = None

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel" or version == "editable":
            return
        root = Path(self.root)
        forced = build_data.setdefault("force_include", {})
        # A force-included file leaves its normal place in the wheel, so the package's own
        # sources go into the bundle from a copy; the package itself stays where it is.
        self._copies = tempfile.mkdtemp(prefix="harnesswalk-bundle-")
        for tree in TREES:
            base = root / tree
            if not base.is_dir():
                raise RuntimeError(f"cannot bundle {tree}/: not found under {root}")
            for path in _files(base):
                rel = path.relative_to(base).as_posix()
                source = path
                if tree == "harnesswalk":
                    source = Path(self._copies) / rel
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, source)
                forced[str(source)] = f"{BUNDLE}/{tree}/{rel}"
        web = root / "web" / "dist"
        if (web / "index.html").is_file():
            for path in _files(web):
                if path.suffix == ".map":  # source maps stay out of the wheel
                    continue
                forced[str(path)] = f"{BUNDLE}/web/dist/{path.relative_to(web).as_posix()}"
        elif os.environ.get("HARNESSWALK_BUNDLE_WEB") == "1":
            raise RuntimeError("HARNESSWALK_BUNDLE_WEB=1 but web/dist has no index.html")

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        if self._copies:
            shutil.rmtree(self._copies, ignore_errors=True)
