"""Pack discovery: which dataset packs this harness can load (P5).

Contract: spec/contracts/http_api.md (P5 additions, `GET /v1/packs`). A pack is a directory
under one of the roots (`packs.roots`, default `[datasets]`) that holds a `pack.yaml`, or else
a `system_prompt.md` (the P1 to P4 layout, which `_starter` still uses). Its id is the
directory name. The first root that has an id wins.

This is the minimal index the ops plane needs (sessions, evals and releases name packs). The
datasets builder's `harnesswalk/packs.py` (validation, ingestion) is unified with it at
integration; both read the same files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PACK_ID = re.compile(r"^[a-z_][a-z0-9_-]{0,63}$")
DEFAULT_ROOTS = ("datasets",)
MAX_PROMPTS = 10
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")


@dataclass
class PackInfo:
    id: str
    dir: Path
    meta: dict[str, Any] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        docs = self.dir / "docs"
        return {
            "docs": sum(1 for p in docs.glob("*") if p.suffix.lower() in (".md", ".pdf", ".txt"))
            if docs.is_dir()
            else 0,
            "facts": _lines(self.dir / "facts.jsonl"),
            "episodes": _lines(self.dir / "episodes.jsonl"),
            "skills": len(list((self.dir / "skills").glob("*.md"))),
            "tools": _tool_count(self.dir / "tools.yaml"),
            "golden": _lines(self.dir / "golden.jsonl"),
        }

    def prompts(self) -> list[str]:
        """Suggested prompts: `pack.yaml` `prompts`, else the list items of `prompts.md`."""
        out: list[str] = []
        listed = self.meta.get("prompts")
        if isinstance(listed, list):
            for item in listed:
                text = item.get("text") or item.get("prompt") if isinstance(item, dict) else item
                if isinstance(text, str) and text.strip():
                    out.append(text.strip())
        elif (self.dir / "prompts.md").is_file():
            for line in (self.dir / "prompts.md").read_text(encoding="utf-8").splitlines():
                m = LIST_ITEM.match(line)
                if m:
                    out.append(m.group(1).strip().strip("`\"'"))
        return [p for p in out if p][:MAX_PROMPTS]

    def summary(self, default: str, loaded: bool = False) -> dict[str, Any]:
        meta = self.meta

        def text(key: str) -> str | None:
            value = meta.get(key)
            return str(value) if isinstance(value, (str, int, float)) else None

        return {
            "id": self.id,
            "title": text("title") or self.id,
            "domain": text("domain"),
            "feature": text("feature"),
            "prompts": self.prompts(),
            "counts": self.counts(),
            "default": self.id == default,
            "loaded": loaded,
        }


def _lines(path: Path) -> int:
    if not path.is_file():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if line.strip() and not line.lstrip().startswith("//"))


def _tool_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return 0
    return len(data) if isinstance(data, list) else 0


def _meta(directory: Path) -> dict[str, Any]:
    path = directory / "pack.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def roots(settings: Any) -> list[Path]:
    raw = settings.get("packs.roots", None) or list(DEFAULT_ROOTS)
    if isinstance(raw, str):
        raw = [raw]
    return [settings.resolve(str(r)) for r in raw]


def discover(settings: Any) -> dict[str, PackInfo]:
    """Every pack under the roots, by id, in id order."""
    found: dict[str, PackInfo] = {}
    for root in roots(settings):
        if not root.is_dir():
            continue
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            pack_id = directory.name
            if pack_id in found or not PACK_ID.match(pack_id):
                continue
            if (directory / "pack.yaml").is_file() or (directory / "system_prompt.md").is_file():
                found[pack_id] = PackInfo(pack_id, directory, _meta(directory))
    return dict(sorted(found.items()))


def find(settings: Any, pack: str) -> PackInfo | None:
    if not isinstance(pack, str) or not PACK_ID.match(pack):
        return None
    return discover(settings).get(pack)


def pack_dir(settings: Any, pack: str) -> Path:
    """The pack's directory: the discovered one, else `datasets/<pack>` (which may not exist,
    for a pack whose content the profile gives inline)."""
    info = find(settings, pack)
    return info.dir if info is not None else settings.root / "datasets" / pack


def known(settings: Any, pack: str) -> bool:
    """A pack a session or a run may name: discovered, or the configured one."""
    return pack == settings.get("dataset", "_starter") or find(settings, pack) is not None
