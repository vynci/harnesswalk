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

`check_case` evaluates a case's `checks` (an additive key the eval scorers ignore) against
the events of the turn that ran it: `router_rules` (the router rule of each model call, in
order) and `retrieved` (a substring a store must have put in context).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from harnesswalk.paths import repo_root

MAX_BYTES = 2 * 1024 * 1024
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
}
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
CHECK_KEYS = {"router_rules", "retrieved"}
STORES = ("semantic", "episodic", "knowledge")

PACK_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id", "title", "domain", "summary", "tier", "feature", "licence", "provenance",
        "synthetic", "suggested_prompts",
    ],
    "properties": {
        "id": {"type": "string", "pattern": r"^_?[a-z][a-z0-9_]{0,31}$"},
        "title": {"type": "string", "minLength": 1, "maxLength": 80},
        "domain": {"type": "string", "minLength": 1, "maxLength": 40},
        "summary": {"type": "string", "minLength": 1, "maxLength": 400},
        "tier": {"enum": sorted(TIERS)},
        "feature": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "name", "nodes", "summary"],
            "properties": {
                "id": {"type": "string", "pattern": r"^[a-z][a-z0-9_]{0,63}$"},
                "name": {"type": "string", "minLength": 1},
                "nodes": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "summary": {"type": "string", "minLength": 1},
            },
        },
        "licence": {"const": "CC0-1.0"},
        "provenance": {"type": "string", "pattern": r"^[A-Za-z0-9_.-]+\.md$"},
        "synthetic": {"const": True},
        "suggested_prompts": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
        },
    },
}  # fmt: skip


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
    id: str
    path: Path
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return str(self.meta.get("title") or self.id)

    @property
    def tier(self) -> str:
        return str(self.meta.get("tier") or "domain")

    @property
    def feature(self) -> dict[str, Any]:
        return dict(self.meta.get("feature") or {})

    def docs(self) -> list[Path]:
        d = self.path / "docs"
        exts = (".md", ".markdown", ".pdf")
        return sorted(p for p in d.glob("*") if p.is_file() and p.suffix.lower() in exts)

    def doc_ids(self) -> set[str]:
        return {re.sub(r"[^a-z0-9_]+", "_", p.stem.lower()).strip("_") for p in self.docs()}

    def tool_names(self) -> list[str]:
        try:
            entries = yaml.safe_load((self.path / "tools.yaml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return []
        return [e["name"] for e in entries or [] if isinstance(e, dict) and "name" in e]

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.path.rglob("*") if p.is_file())

    def summary(self) -> dict[str, Any]:
        """What `packs list --json` and a future GET /v1/packs return for a pack."""
        return {
            "id": self.id,
            "title": self.title,
            "domain": self.meta.get("domain"),
            "summary": self.meta.get("summary"),
            "tier": self.tier,
            "feature": self.feature,
            "licence": self.meta.get("licence"),
            "suggested_prompts": list(self.meta.get("suggested_prompts") or []),
            "docs": len(self.docs()),
            "tools": self.tool_names(),
            "size_bytes": self.size_bytes(),
        }


def datasets_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "datasets"


def load_pack(path: Path) -> Pack:
    meta_path = path / "pack.yaml"
    if not meta_path.is_file():
        raise PackError(f"{path} has no pack.yaml")
    try:
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PackError(f"{meta_path}: not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise PackError(f"{meta_path} must hold a mapping")
    return Pack(path.name, path, meta)


def discover(root: Path | None = None) -> list[Pack]:
    """Every directory under datasets/ with a pack.yaml, by id (`_starter` first)."""
    base = datasets_dir(root)
    if not base.is_dir():
        return []
    return [load_pack(p) for p in sorted(base.iterdir()) if (p / "pack.yaml").is_file()]


def get_pack(pack_id: str, root: Path | None = None) -> Pack:
    path = datasets_dir(root) / pack_id
    if not re.fullmatch(r"_?[a-z][a-z0-9_]{0,31}", pack_id) or not path.is_dir():
        raise PackError(f"no dataset pack {pack_id!r} under {datasets_dir(root)}")
    return load_pack(path)


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
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _within(n: int, bounds: tuple[int, int]) -> bool:
    return bounds[0] <= n <= bounds[1]


def validate(
    pack: Pack, packs: list[Pack] | None = None, *, config_root: Path | None = None
) -> list[Issue]:
    """Every problem with one pack. `packs` (default: all discovered beside it) supplies the
    guardrail patterns the PII scan uses; `config_root` holds config/ and spec/."""
    config_root = config_root or repo_root()
    if packs is None:
        packs = discover(pack.path.parent.parent)
    issues: list[Issue] = []

    def add(check: str, message: str, path: str | None = None) -> None:
        issues.append(Issue(pack.id, check, message, path))

    # schema
    for err in jsonschema.Draft202012Validator(PACK_SCHEMA).iter_errors(pack.meta):
        where = "/".join(str(p) for p in err.absolute_path) or "pack.yaml"
        add("schema", f"{where}: {err.message}", "pack.yaml")
    if pack.meta.get("id") != pack.id:
        add("schema", f"id {pack.meta.get('id')!r} must be the directory name {pack.id!r}")
    known_nodes = _diagram_ids(config_root)
    for node_id in pack.feature.get("nodes") or []:
        if known_nodes and node_id not in known_nodes:
            add("schema", f"feature node {node_id!r} is not a diagram node", "pack.yaml")

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
        checks = case.raw.get("checks")
        if checks is None:
            continue
        if not isinstance(checks, dict) or set(checks) - CHECK_KEYS:
            add(f"checks may only hold {sorted(CHECK_KEYS)}")
            continue
        for rule in checks.get("router_rules") or []:
            if rule not in rules:
                add(f"checks names unknown router rule {rule!r}")
        for store in checks.get("retrieved") or {}:
            if store not in STORES:
                add(f"checks.retrieved names unknown store {store!r}")
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


# -- golden-case checks (run after a turn) -------------------------------------------------------


def check_case(
    checks: dict[str, Any] | None,
    events: list[dict[str, Any]],
    get_detail: Callable[[str], Any],
) -> list[str]:
    """Problems with a finished case turn against its `checks`; empty when it passes."""
    if not checks:
        return []
    problems = []
    want_rules = checks.get("router_rules")
    if want_rules is not None:
        got = [
            (e.get("summary") or {}).get("rule")
            for e in events
            if e["type"] == "node_exit" and e.get("node_id") == "router"
        ]
        if got != list(want_rules):
            problems.append(f"router rules {got}, expected {list(want_rules)}")
    retrieved = checks.get("retrieved") or {}
    if retrieved:
        detail: dict[str, Any] = {}
        for e in events:
            if e["type"] == "node_exit" and e.get("node_id") == "context_builder":
                detail = (get_detail(e["detail_ref"]) if e.get("detail_ref") else None) or {}
        items = detail.get("items") or {}
        for store, needle in retrieved.items():
            texts = [h.get("text", "") for h in items.get(store) or []]
            if not any(needle in t for t in texts):
                problems.append(f"{store} context has no {needle!r} (got {texts[:3]})")
    return problems
