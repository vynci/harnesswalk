"""Release, the file door: the harness loads its artifacts through here and nowhere else.

Contract: spec/contracts/release.md (the loading rule). `release: file_door` in harness.yaml:
the artifacts are your files (`prompts/`, `skills/`, `policy/`, `tools/`), versioned by git,
stamped by content (`system_prompt@<sha256 prefix>`) so a trace says which prompt it ran on.

Lesson: even without a release store, one door means one place to add versioning later
(`harnesswalk init --release chain` generates the hash-chained store in miniature).
"""

from __future__ import annotations

import hashlib
from typing import Any

import yaml


def version_of(name: str, text: str) -> str:
    return f"{name}@{hashlib.sha256(text.encode()).hexdigest()[:8]}"


def parse_skill(file_name: str, text: str) -> tuple[str, str, str]:
    meta: dict[str, Any] = {}
    body = text
    if text.startswith("---\n"):
        head, _, body = text[4:].partition("\n---\n")
        meta = yaml.safe_load(head) or {}
    lines = [ln for ln in body.strip().splitlines() if ln.strip()]
    desc = meta.get("description") or (lines[0].lstrip("# ") if lines else "")
    return meta.get("name") or file_name.rsplit(".", 1)[0], desc, body.strip()


class Release:
    def __init__(self, h: Any) -> None:
        self.h = h

    def served(self, pack: str) -> dict[str, Any]:
        p = self.h.settings.packs[pack]
        return {"prompt": p.system_prompt, "prompt_version": version_of("system_prompt", p.system_prompt),
                "skills": [parse_skill(n, t) for n, t in p.skills],
                "policy": self.h.settings.get("policy", {}),
                "tools": p.tools_path.read_text(encoding="utf-8")}  # fmt: skip

    def info(self, pack: str) -> dict[str, Any] | None:
        return None
