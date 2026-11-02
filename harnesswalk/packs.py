"""Dataset packs: discovery, the `pack.yaml` schema, the validator and the golden-case checks.

Contract: spec/packs.md. Not a node: packs are what the harness loads (`dataset` in
config/harness.yaml or `HARNESSWALK_DATASET`), and this module is how the CLI
(`harnesswalk packs list|validate|ingest`) and CI reason about them.

A pack is a directory `datasets/<id>/` with a `pack.yaml`. The validator checks, per pack:

    schema      pack.yaml against PACK_SCHEMA; the id is the directory name
    files       the required files exist; counts per tier (documents, skills, facts, ...)
    size        everything under the pack directory is under MAX_BYTES
    tools       tools.yaml loads in the registry (action classes, schemas) and every tool is
                in config/policy.yaml agent.allow_tools, so policy does not deny it
    skills      front matter names match the file stems
    seeds       facts.jsonl and episodes.jsonl lines have their keys
    golden      every case loads, names a fixture that exists and loads, calls only known
                tools (the fixture's calls equal `expect.tool_calls`), cites existing
                documents, and its `checks` name known router rules and stores
    pii         no personal-data pattern of any pack's guardrails (nor the default ones)
                matches the pack's content; prompts may only use reserved test values
    provenance  the provenance card exists, says CC0 and says synthetic

A case's `checks` (router rules, retrieved memory) are scored by the eval runner itself, as
the `checks` scorer (harnesswalk/ops/eval/scorers.py); the validator only checks that they
name known router rules and stores.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from harnesswalk.paths import repo_root

MAX_BYTES = 2 * 1024 * 1024
PACK_ID = re.compile(r"^[a-z_][a-z0-9_-]{0,63}$")  # a directory name a pack may have
DEFAULT_ROOTS = ("datasets",)
MAX_PROMPTS = 10
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")
DOC_EXTS = (".md", ".markdown", ".pdf")
# The media type a knowledge document's original file is served with (`?raw=1`, http_api.md).
DOC_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown; charset=utf-8",
    ".markdown": "text/markdown; charset=utf-8",
}
BUILTIN_TOOLS = ("calculator", "read_skill", "remember", "read_doc")
TIERS = {
    # tier: (docs, pdf docs, skills, pack tools, facts, episodes, golden cases, try-these prompts)
    "domain": {
        "docs": (10, 20),
        "pdfs": (2, 20),
        "skills": (3, 5),
        "tools": (2, 4),
        "facts": (15, 30),
        "episodes": (8, 15),
        "golden": (20, 30),
        "prompts": (6, 10),
        "doc_words": (300, 900),
    },
    "starter": {},  # the hand-written P1 pack: shape only
    "profile": {},  # a pack whose content a profile gives inline (spec/conformance/packs/)
}
# The pack.yaml keys each tier must have. `id` is optional everywhere (the directory name is
# the id).
TIER_REQUIRED = {
    "domain": ("title", "domain", "summary", "tier", "feature", "demonstrates", "licence",
               "provenance", "synthetic", "prompts"),
    "starter": ("title", "domain", "summary", "tier", "feature", "demonstrates", "licence",
                "provenance", "synthetic", "prompts"),
    "profile": ("title", "licence"),
}  # fmt: skip
REQUIRED_FILES = (
    "pack.yaml",
    "system_prompt.md",
    "tools.yaml",
    "golden.jsonl",
    "facts.jsonl",
    "episodes.jsonl",
    "prompts.md",
)
CONTENT_GLOBS = (
    "system_prompt.md",
    "skills/*.md",
    "docs/*.md",
    "sources/*.md",
    "facts.jsonl",
    "episodes.jsonl",
    "tools.yaml",
)
PROMPT_GLOBS = ("golden.jsonl", "fixtures/fake/*.jsonl", "prompts.md", "pack.yaml")
RESERVED_EMAIL = re.compile(r"@(?:[a-z0-9-]+\.)*(?:example\.(?:com|net|org)|invalid|test)$", re.I)
RESERVED_PHONE_DIGITS = "55501"  # the 555-01xx block is reserved for fiction

PACK_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "pattern": r"^_?[a-z][a-z0-9_]{0,31}$"},
        "title": {"type": "string", "minLength": 1, "maxLength": 80},
        "domain": {"type": "string", "minLength": 1, "maxLength": 40},
        "summary": {"type": "string", "minLength": 1, "maxLength": 400},
        "tier": {"enum": sorted(TIERS)},
        "feature": {"type": "string", "minLength": 1, "maxLength": 120},
        "demonstrates": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "nodes", "summary"],
            "properties": {
                "id": {"type": "string", "pattern": r"^[a-z][a-z0-9_]{0,63}$"},
                "nodes": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "summary": {"type": "string", "minLength": 1},
            },
        },
        "licence": {"const": "CC0-1.0"},
        "provenance": {"type": "string", "pattern": r"^[A-Za-z0-9_.-]+\.md$"},
        "synthetic": {"const": True},
        "prompts": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
    },
}


class PackError(ValueError):
    """A pack cannot be found or its pack.yaml cannot be read."""


@dataclass
class Issue:
    pack: str
    check: str
    message: str
    path: str | None = None

    def __str__(self) -> str:
        where = f" ({self.path})" if self.path else ""
        return f"{self.pack}: {self.check}: {self.message}{where}"


@dataclass
class Pack:
    """One pack directory and its card. `meta` is `pack.yaml` ({} for a P1 layout pack that
    only has a `system_prompt.md`, or a card that does not parse: the validator reports it)."""

    id: str
    path: Path
    meta: dict[str, Any] = field(default_factory=dict)
    card_error: str | None = None

    @property
    def dir(self) -> Path:  # the ops plane's name for it
        return self.path

    @property
    def title(self) -> str:
        return self._text("title") or self.id

    def _text(self, key: str) -> str | None:
        value = self.meta.get(key)
        return str(value) if isinstance(value, (str, int, float)) else None

    @property
    def tier(self) -> str:
        """`tier` from the card; a pack without one and without a system prompt is a
        profile pack (its content comes from a profile), otherwise a domain pack."""
        if self.meta.get("tier"):
            return str(self.meta["tier"])
        return "domain" if (self.path / "system_prompt.md").is_file() else "profile"

    @property
    def feature(self) -> str | None:
        return self._text("feature")

    @property
    def demonstrates(self) -> dict[str, Any]:
        value = self.meta.get("demonstrates")
        return dict(value) if isinstance(value, dict) else {}

    @property
    def prompts(self) -> list[str]:
        """The suggestion row: the card's `prompts` (strings, or `{text|prompt}` objects), else
        the numbered or bulleted items of `prompts.md`; at most MAX_PROMPTS."""
        out: list[str] = []
        listed = self.meta.get("prompts")
        if isinstance(listed, list):
            for item in listed:
                text = (item.get("text") or item.get("prompt")) if isinstance(item, dict) else item
                if isinstance(text, str) and text.strip():
                    out.append(text.strip())
        elif (self.path / "prompts.md").is_file():
            for line in (self.path / "prompts.md").read_text(encoding="utf-8").splitlines():
                m = LIST_ITEM.match(line)
                if m:
                    out.append(m.group(1).strip().strip("`\"'"))
        return [x for x in out if x][:MAX_PROMPTS]

    def docs(self) -> list[Path]:
        d = self.path / "docs"
        return sorted(p for p in d.glob("*") if p.is_file() and p.suffix.lower() in DOC_EXTS)

    def doc_file(self, name: str) -> Path | None:
        """The original file of one knowledge document, by its file name under `docs/`, or None:
        one path segment, not hidden, a document type, a regular file, and inside `docs/` once
        symlinks are resolved. The walls of `GET /v1/packs/{id}/files/docs/{name}?raw=1`."""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        docs = self.path / "docs"
        target = docs / name
        if target.suffix.lower() not in DOC_EXTS or not target.is_file():
            return None
        try:
            target.resolve().relative_to(docs.resolve())
        except (OSError, ValueError):
            return None
        return target

    def doc_ids(self) -> set[str]:
        return {re.sub(r"[^a-z0-9_]+", "_", p.stem.lower()).strip("_") for p in self.docs()}

    def tool_names(self) -> list[str]:
        try:
            entries = yaml.safe_load((self.path / "tools.yaml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return []
        if not isinstance(entries, list):
            return []
        return [e["name"] for e in entries if isinstance(e, dict) and "name" in e]

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.path.rglob("*") if p.is_file())

    def counts(self) -> dict[str, int]:
        return {
            "docs": len(self.docs()),
            "facts": _count_lines(self.path / "facts.jsonl"),
            "episodes": _count_lines(self.path / "episodes.jsonl"),
            "skills": len(list((self.path / "skills").glob("*.md"))),
            "tools": len(self.tool_names()),
            "golden": _count_lines(self.path / "golden.jsonl"),
        }

    def files(self) -> list[dict[str, Any]]:
        """The pack's files for the UI's file tree (`GET /v1/packs/{id}`): every file under
        the pack but caches and hidden ones, by path, with its size and the part it plays."""
        out = []
        for path in sorted(self.path.rglob("*")):
            rel = path.relative_to(self.path)
            if not path.is_file() or any(
                part.startswith(".") or part == "__pycache__" for part in rel.parts
            ):
                continue
            out.append({"path": rel.as_posix(), "bytes": path.stat().st_size, "role": _role(rel)})
        return out

    def summary(self, default: str | None = None, loaded: bool = False) -> dict[str, Any]:
        """One item of `GET /v1/packs`."""
        return {
            "id": self.id,
            "title": self.title,
            "domain": self._text("domain"),
            "feature": self.feature,
            "prompts": self.prompts,
            "counts": self.counts(),
            "default": self.id == default,
            "loaded": loaded,
        }

    def card(self) -> dict[str, Any]:
        """`packs list --json`: the `GET /v1/packs` fields, then what only the CLI shows."""
        out = self.summary()
        for key in ("default", "loaded"):
            out.pop(key)
        return {
            **out,
            "summary": self.meta.get("summary"),
            "tier": self.tier,
            "demonstrates": self.demonstrates,
            "licence": self.meta.get("licence"),
            "tools": self.tool_names(),
            "size_bytes": self.size_bytes(),
            "path": str(self.path),
        }


def _role(rel: Path) -> str:
    """What a file is to the harness, for the file tree (the inspectors filter on it)."""
    top = rel.parts[0]
    if len(rel.parts) == 1:
        return {
            "system_prompt.md": "procedural",
            "tools.yaml": "tools",
            "guardrails.yaml": "guardrails",
            "facts.jsonl": "semantic",
            "episodes.jsonl": "episodic",
            "golden.jsonl": "golden",
        }.get(rel.name, "card")
    return {
        "skills": "procedural",
        "docs": "knowledge",
        "sources": "knowledge_source",
        "fixtures": "fixture",
    }.get(top, "other")


# -- discovery ---------------------------------------------------------------------------------


def pack_roots(where: Any = None) -> list[Path]:
    """The pack roots: `packs.roots` (default `[datasets]`), resolved. `where` is a Settings
    (a profile may add roots), or a repository root whose config/harness.yaml is read."""
    if where is not None and hasattr(where, "resolve") and hasattr(where, "get"):
        raw = where.get("packs.roots", None) or list(DEFAULT_ROOTS)
        return [where.resolve(str(r)) for r in ([raw] if isinstance(raw, str) else raw)]
    root = Path(where) if where is not None else repo_root()
    try:
        cfg = yaml.safe_load((root / "config" / "harness.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        cfg = {}
    raw = ((cfg or {}).get("packs") or {}).get("roots") or list(DEFAULT_ROOTS)
    return [
        Path(r) if Path(r).is_absolute() else root / r
        for r in ([raw] if isinstance(raw, str) else raw)
    ]


def load_pack(path: Path) -> Pack:
    """The pack in `path`. A card that is not a YAML mapping still gives a pack (with
    `card_error` set), so discovery never fails on one bad card; `strict` loaders raise."""
    meta_path = path / "pack.yaml"
    if not meta_path.is_file():
        if (path / "system_prompt.md").is_file():
            return Pack(path.name, path, {})
        raise PackError(f"{path} has no pack.yaml")
    try:
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return Pack(path.name, path, {}, f"pack.yaml is not valid YAML: {exc}")
    if not isinstance(meta, dict):
        return Pack(path.name, path, {}, "pack.yaml must hold a mapping")
    return Pack(path.name, path, meta)


def discover(where: Any = None, *, roots: list[Path] | None = None) -> dict[str, Pack]:
    """Every pack under the roots, by id in id order (`_starter` first). A pack is a directory
    whose name is a pack id and that holds a `pack.yaml`, or a `system_prompt.md` (the P1
    layout). The first root that has an id wins. This one function is behind
    `GET /v1/packs`, sessions, eval runs, releases, the CLI and the validator."""
    found: dict[str, Pack] = {}
    bases = roots if roots is not None else pack_roots(where)
    for base in bases:
        if not base.is_dir():
            continue
        for d in sorted(p for p in base.iterdir() if p.is_dir()):
            if d.name in found or not PACK_ID.match(d.name):
                continue
            if (d / "pack.yaml").is_file() or (d / "system_prompt.md").is_file():
                found[d.name] = load_pack(d)
    return dict(sorted(found.items()))


def find(where: Any, pack: str) -> Pack | None:
    if not isinstance(pack, str) or not PACK_ID.match(pack):
        return None
    return discover(where).get(pack)


def get_pack(pack_id: str, where: Any = None, roots: list[Path] | None = None) -> Pack:
    """The pack, or PackError naming the roots searched."""
    found = discover(where, roots=roots).get(pack_id) if PACK_ID.match(pack_id) else None
    if found is None:
        searched = roots if roots is not None else pack_roots(where)
        where_text = ", ".join(str(r) for r in searched)
        raise PackError(f"no dataset pack {pack_id!r} under {where_text}")
    return found


def pack_dir(settings: Any, pack: str) -> Path:
    """The pack's directory: the discovered one, else `datasets/<pack>` (which may not exist,
    for a pack whose content the profile gives inline)."""
    info = find(settings, pack)
    return info.path if info is not None else settings.root / "datasets" / pack


def known(settings: Any, pack: str) -> bool:
    """A pack a session or a run may name: discovered, or the configured one."""
    return pack == settings.get("dataset", "_starter") or find(settings, pack) is not None


# -- validation ---------------------------------------------------------------------------------


def _pii_config(guardrails_file: Path | None) -> dict[str, Any]:
    """The PII detector config the runtime builds for a pack (runtime._input_guardrails_config)."""
    from harnesswalk.config.loader import deep_merge

    cfg: dict[str, Any] = {"kinds": ["email", "phone", "card", "national_id"]}
    if guardrails_file is not None and guardrails_file.is_file():
        extra = yaml.safe_load(guardrails_file.read_text(encoding="utf-8")) or {}
        cfg = deep_merge(cfg, extra.get("pii") or {})
    return cfg


def detectors(packs: list[Pack]) -> list[Any]:
    """The default detector plus one per pack guardrails file (each pack's own patterns)."""
    from harnesswalk.control.pii import PiiDetector

    def scanner(cfg: dict[str, Any]) -> Any:
        # Names are found by conversational cues ("my name is"), which match ordinary prose and
        # front matter; invented people in content are fine, so the scan leaves names out.
        cfg = {**cfg, "kinds": [k for k in cfg.get("kinds", []) if k != "name"]}
        return PiiDetector({**cfg, "names": {"enabled": False}})

    out = [scanner(_pii_config(None))]
    for p in packs:
        g = p.path / "guardrails.yaml"
        if g.is_file():
            try:
                out.append(scanner(_pii_config(g)))
            except (re.error, ValueError, yaml.YAMLError):
                continue  # reported by that pack's own guardrails check
    return out


def _texts(pack: Pack, globs: tuple[str, ...], pdfs: bool = False) -> list[tuple[str, str]]:
    out = []
    for pattern in globs:
        for path in sorted(pack.path.glob(pattern)):
            if path.is_file():
                out.append((str(path.relative_to(pack.path)), path.read_text(encoding="utf-8")))
    if pdfs:
        for path in pack.docs():
            if path.suffix.lower() == ".pdf":
                out.append((str(path.relative_to(pack.path)), pdf_text(path)))
    return out


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _reserved(kind: str, value: str) -> bool:
    if kind == "email":
        return bool(RESERVED_EMAIL.search(value))
    if kind == "phone":
        return RESERVED_PHONE_DIGITS in "".join(ch for ch in value if ch.isdigit())
    return False


def _policy_allowlist(config_root: Path) -> list[str] | None:
    try:
        policy = yaml.safe_load((config_root / "config" / "policy.yaml").read_text()) or {}
    except OSError:
        return None
    return (policy.get("agent") or {}).get("allow_tools")


def _router_rule_ids(config_root: Path) -> set[str]:
    try:
        models = yaml.safe_load((config_root / "config" / "models.yaml").read_text()) or {}
    except OSError:
        return set()
    return {r["id"] for r in (models.get("router") or {}).get("rules") or [] if "id" in r}


def _diagram_ids(config_root: Path) -> set[str]:
    try:
        diagram = json.loads((config_root / "spec" / "diagram" / "diagram.json").read_text())
    except OSError:
        return set()
    return {n["id"] for n in diagram.get("nodes", [])}


def _count_lines(path: Path) -> int:
    """Lines that hold a record: not blank, not a `//` comment (golden sets allow those)."""
    if not path.is_file():
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    return sum(1 for line in lines if line.strip() and not line.lstrip().startswith("//"))


def _within(n: int, bounds: tuple[int, int]) -> bool:
    return bounds[0] <= n <= bounds[1]


def validate(
    pack: Pack, packs: list[Pack] | None = None, *, config_root: Path | None = None
) -> list[Issue]:
    """Every problem with one pack. `packs` (default: all discovered beside it) supplies the
    guardrail patterns the PII scan uses; `config_root` holds config/ and spec/."""
    config_root = config_root or repo_root()
    if packs is None:
        packs = list(discover(config_root).values())
    issues: list[Issue] = []

    def add(check: str, message: str, path: str | None = None) -> None:
        issues.append(Issue(pack.id, check, message, path))

    if pack.card_error or not (pack.path / "pack.yaml").is_file():
        add("schema", pack.card_error or "pack.yaml is missing", "pack.yaml")

    # schema
    tier = pack.tier
    for err in jsonschema.Draft202012Validator(PACK_SCHEMA).iter_errors(pack.meta):
        where = "/".join(str(p) for p in err.absolute_path) or "pack.yaml"
        add("schema", f"{where}: {err.message}", "pack.yaml")
    for key in TIER_REQUIRED.get(tier, ()):
        if key not in pack.meta:
            add("schema", f"{key!r} is required for a {tier} pack", "pack.yaml")
    if "id" in pack.meta and pack.meta["id"] != pack.id:
        add("schema", f"id {pack.meta.get('id')!r} must be the directory name {pack.id!r}")
    known_nodes = _diagram_ids(config_root)
    for node_id in pack.demonstrates.get("nodes") or []:
        if known_nodes and node_id not in known_nodes:
            add("schema", f"node {node_id!r} in demonstrates is not a diagram node", "pack.yaml")
    if tier == "profile":
        return issues + _check_pii(pack, packs)

    # files and size
    for name in REQUIRED_FILES:
        if not (pack.path / name).is_file():
            add("files", f"{name} is missing")
    provenance = str(pack.meta.get("provenance") or "provenance.md")
    if not (pack.path / provenance).is_file():
        add("provenance", f"{provenance} is missing")
    else:
        card = (pack.path / provenance).read_text(encoding="utf-8")
        if "CC0" not in card:
            add("provenance", "the provenance card does not state the CC0 licence", provenance)
        if "synthetic" not in card.lower():
            add("provenance", "the provenance card does not say the content is synthetic")
    size = pack.size_bytes()
    if size > MAX_BYTES:
        add("size", f"{size} bytes, over the {MAX_BYTES} byte limit")

    limits = TIERS.get(pack.tier, {})
    docs = pack.docs()
    skills = sorted((pack.path / "skills").glob("*.md"))
    counts = {
        "docs": len(docs),
        "pdfs": sum(1 for d in docs if d.suffix.lower() == ".pdf"),
        "skills": len(skills),
        "tools": len(pack.tool_names()),
        "facts": _count_lines(pack.path / "facts.jsonl"),
        "episodes": _count_lines(pack.path / "episodes.jsonl"),
        "golden": _count_lines(pack.path / "golden.jsonl"),
        "prompts": _try_these(pack.path / "prompts.md"),
    }
    for key, n in counts.items():
        if key in limits and not _within(n, limits[key]):
            add("files", f"{n} {key}, the {pack.tier} tier needs {limits[key][0]} to "
                f"{limits[key][1]}")  # fmt: skip
    if "doc_words" in limits:
        sources = {p.stem: p for p in (pack.path / "sources").glob("*.md")}
        for doc in docs:
            text_path = doc if doc.suffix.lower() != ".pdf" else sources.get(doc.stem)
            if text_path is None:
                add("files", f"PDF {doc.name} has no markdown source in sources/")
                continue
            words = len(text_path.read_text(encoding="utf-8").split())
            if not _within(words, limits["doc_words"]):
                lo, hi = limits["doc_words"]
                add("files", f"{words} words, documents need {lo} to {hi}",
                    str(text_path.relative_to(pack.path)))  # fmt: skip

    issues += _check_tools(pack, config_root)
    issues += _check_skills(pack, skills)
    issues += _check_seeds(pack)
    issues += _check_guardrails(pack)
    issues += _check_golden(pack, config_root)
    issues += _check_pii(pack, packs)
    return issues


def _try_these(path: Path) -> int:
    if not path.is_file():
        return 0
    return len(re.findall(r"^\d+\. ", path.read_text(encoding="utf-8"), re.M))


def _check_tools(pack: Pack, config_root: Path) -> list[Issue]:
    from harnesswalk.execution.registry import ToolRegistry
    from harnesswalk.types import ConfigError

    out: list[Issue] = []
    path = pack.path / "tools.yaml"
    if not path.is_file():
        return out
    try:
        entries = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as exc:
        return [Issue(pack.id, "tools", f"not valid YAML: {exc}", "tools.yaml")]
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and "action_class" not in entry:
            out.append(Issue(pack.id, "tools", f"{entry.get('name')} declares no action_class"))
    try:
        ToolRegistry().load_yaml(path)
    except (ConfigError, jsonschema.SchemaError, ValueError) as exc:
        out.append(Issue(pack.id, "tools", str(exc), "tools.yaml"))
    allow = _policy_allowlist(config_root)
    for name in pack.tool_names():
        if name in BUILTIN_TOOLS:
            out.append(Issue(pack.id, "tools", f"{name} shadows a built-in tool", "tools.yaml"))
        elif allow is not None and name not in allow:
            out.append(Issue(pack.id, "tools", f"{name} is not in config/policy.yaml "
                             "agent.allow_tools, so policy denies it", "tools.yaml"))  # fmt: skip
    return out


def _check_skills(pack: Pack, skills: list[Path]) -> list[Issue]:
    out = []
    for path in skills:
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(pack.path))
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            out.append(Issue(pack.id, "skills", "no front matter", rel))
            continue
        try:
            meta = yaml.safe_load(text[4:].partition("\n---\n")[0]) or {}
        except yaml.YAMLError as exc:
            out.append(Issue(pack.id, "skills", f"front matter is not valid YAML: {exc}", rel))
            continue
        if meta.get("name") != path.stem:
            out.append(Issue(pack.id, "skills", f"name {meta.get('name')!r} is not the stem", rel))
        if not meta.get("description"):
            out.append(Issue(pack.id, "skills", "no description", rel))
    return out


def _check_seeds(pack: Pack) -> list[Issue]:
    out = []
    for name, keys in (("facts.jsonl", ("key", "value")), ("episodes.jsonl", ("id", "ts", "text"))):
        path = pack.path / name
        if not path.is_file():
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                out.append(Issue(pack.id, "seeds", f"line {n} is not JSON", name))
                continue
            missing = [k for k in keys if not item.get(k)]
            if missing:
                out.append(Issue(pack.id, "seeds", f"line {n} misses {missing}", name))
    return out


def _check_guardrails(pack: Pack) -> list[Issue]:
    path = pack.path / "guardrails.yaml"
    if not path.is_file():
        return []
    from harnesswalk.control.pii import PiiDetector

    try:
        PiiDetector(_pii_config(path))
    except (re.error, ValueError, yaml.YAMLError) as exc:
        return [Issue(pack.id, "guardrails", str(exc), "guardrails.yaml")]
    return []


def _fixture_calls(fixture: dict[str, Any]) -> list[str]:
    """The tool names the fixture's first turn calls, in order."""
    turns = fixture.get("turns") or []
    if not turns:
        return []
    return [c["name"] for r in turns[0]["responses"] for c in r.get("tool_calls", [])]


def _check_golden(pack: Pack, config_root: Path) -> list[Issue]:
    from harnesswalk.execution.providers.fake import find_fixture, load_fixture
    from harnesswalk.ops.eval.golden import GoldenError, load_golden
    from harnesswalk.types import ConfigError

    out: list[Issue] = []
    path = pack.path / "golden.jsonl"
    if not path.is_file():
        return out
    try:
        cases = load_golden(path)
    except GoldenError as exc:
        return [Issue(pack.id, "golden", str(exc), "golden.jsonl")]
    tools = set(pack.tool_names()) | set(BUILTIN_TOOLS)
    docs = pack.doc_ids()
    rules = _router_rule_ids(config_root)
    dirs = [pack.path / "fixtures" / "fake", config_root / "spec" / "conformance" / "fixtures"]
    for case in cases:

        def add(message: str, case_id: str = case.id) -> None:
            out.append(Issue(pack.id, "golden", f"{case_id}: {message}", "golden.jsonl"))

        if not case.fixture:
            add("names no fixture, so the golden suite would skip it")
            continue
        try:
            fixture_path = find_fixture(case.fixture, dirs)
            fixture = load_fixture(fixture_path)
        except ConfigError as exc:
            add(str(exc))
            continue
        text = fixture_path.read_text(encoding="utf-8")
        if "[[cite:" in text:
            add(f"fixture {case.fixture} has unresolved [[cite:]] markers (run finalize)")
        called = _fixture_calls(fixture)
        for name in set(called) - tools:
            add(f"fixture {case.fixture} calls unknown tool {name!r}")
        expected = case.expect.get("tool_calls")
        if expected is not None:
            for name in set(expected) - tools:
                add(f"expects unknown tool {name!r}")
            if list(expected) != called:
                add(f"expects tool calls {expected} but fixture {case.fixture} calls {called}")
        for cite in case.expect.get("citations") or []:
            if cite.get("doc") and cite["doc"] not in docs:
                add(f"cites unknown document {cite['doc']!r}")
        for rule in (case.checks or {}).get("router_rules") or []:
            if rule not in rules:
                add(f"checks names unknown router rule {rule!r}")
    return out


def _findings(scanners: list[Any], text: str) -> list[Any]:
    """Findings of every detector, without a looser kind inside a span another detector already
    took as an id or a card (the digits of an LP- account number are not also a phone)."""
    found = [f for d in scanners for f in d.find(text)]
    strong = [(f.start, f.end) for f in found if f.kind in ("national_id", "card", "email")]
    return [
        f
        for f in found
        if f.kind != "phone" or not any(s <= f.start and f.end <= e for s, e in strong)
    ]


def _check_pii(pack: Pack, packs: list[Pack]) -> list[Issue]:
    out: list[Issue] = []
    scanners = detectors(packs)
    for rel, text in _texts(pack, CONTENT_GLOBS, pdfs=True):
        hits = {
            (f.kind, f.value) for f in _findings(scanners, text) if not _reserved(f.kind, f.value)
        }
        for kind, value in sorted(hits):
            out.append(Issue(pack.id, "pii", f"{kind} pattern matches {value!r} in content", rel))
    for rel, text in _texts(pack, PROMPT_GLOBS):
        for f in _findings(scanners, text):
            if f.kind in ("email", "phone") and not _reserved(f.kind, f.value):
                out.append(Issue(pack.id, "pii", f"{f.kind} {f.value!r} is not a reserved "
                                 "test value (example.com, 555-01xx)", rel))  # fmt: skip
    seen: set[tuple[str, str, str | None]] = set()
    unique = []
    for issue in out:
        key = (issue.check, issue.message, issue.path)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique
