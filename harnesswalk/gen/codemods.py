"""Codemods for `harnesswalk upgrade`, keyed to the template version in harness.lock.

A codemod is a function over the upgrade's worktree that rewrites what a template change
cannot merge on its own (a renamed config key, a moved file). It runs once: the lock records
it in `codemods_applied`. The one shipped today is trivial and proves the hook.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml


def _version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


OWNED = ["prompts", "skills", "policy", "tools", "packs", "config", "evals"]


def manifest_lists_owned(repo: Path) -> None:
    """Below 0.1.1: harness.yaml names the owned folders an upgrade never touches."""
    path = repo / "harness.yaml"
    text = path.read_text(encoding="utf-8")
    if "owned" not in (yaml.safe_load(text) or {}):
        path.write_text(
            text.rstrip("\n") + "\n" + yaml.safe_dump({"owned": OWNED}), encoding="utf-8"
        )


# (id, applies when upgrading from a version below this one, function)
CODEMODS: list[tuple[str, str, Callable[[Path], None]]] = [
    ("manifest_lists_owned", "0.1.1", manifest_lists_owned),
]


def run(repo: Path, from_version: str, to_version: str) -> list[str]:
    done = []
    for name, below, fn in CODEMODS:
        if _version(from_version) < _version(below) <= _version(to_version):
            fn(repo)
            done.append(name)
    return done
