"""`harnesswalk init`: render the template set, copy the pack in, write the notes and the lock.

The output (docs/generator.md has the tour):

    harness/core/     managed: one file per node, the gateway walk, the stores, the HTTP API
    prompts/ skills/ policy/ tools/     owned: the pack's artifacts, yours to edit
    packs/<id>/       owned: the rest of the pack (documents, seeds, golden set, fixtures)
    config/           owned: runtime config and the six profile files
    evals/            owned: the golden runner and how CI runs it
    tests/            the vendored conformance suite and runner (managed), golden and owned tests
    artifacts/        Modelfile for the served model (with --provider ollama)
    compose.yaml      harness + Ollama + the served model (with --provider ollama)
    harness.yaml      the generator manifest this repo was made from
    harness.lock      template set and spec version, and the hash of every generated file
    .harnesswalk/pristine/   the managed files as generated: the base of a three-way upgrade
    GENERATION_NOTES.md      everything that could not be mapped
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from harnesswalk import __version__, packs, profiles
from harnesswalk.gen.manifest import GenManifest
from harnesswalk.gen.render import render
from harnesswalk.paths import repo_root

TEMPLATES = Path(__file__).resolve().parent / "templates"
SKIP = {"system_prompt.md", "tools.yaml", "skills"}  # these become prompts/, tools/, skills/
SUITE = (
    "cases",
    "fixtures",
    "docs",
    "packs",
    "golden.jsonl",
    "profile.yaml",
    "tools.yaml",
    "harness_tools.yaml",
)


def template_set(lang: str = "python", base: Path | None = None) -> tuple[dict[str, Any], Path]:
    d = (base or TEMPLATES) / lang
    return yaml.safe_load((d / "manifest.yaml").read_text(encoding="utf-8")), d / "files"


def diagram_ids(root: Path) -> set[str]:
    data = json.loads((root / "spec" / "diagram" / "diagram.json").read_text(encoding="utf-8"))
    return {n["id"] for n in data["nodes"]}


TUNING = {"curate", "tune"}  # 0.2.0: generated only with --tune


def generated_nodes(root: Path, profile: profiles.Profile, tune: bool = False) -> set[str]:
    return diagram_ids(root) - set(profile.disable) - (set() if tune else TUNING)


def context(
    m: GenManifest,
    generated: set[str],
    meta: dict[str, Any],
    title: str,
    simple: list[str] | None = None,
) -> dict[str, Any]:
    from harnesswalk.gen.notes import node_table

    storage_doc = (
        "SQLite, one file: data/harness.db (rows are JSON documents)."
        if m.storage == "sqlite"
        else "JSON Lines, one file per table under data/ (small and greppable)."
    )
    return {
        "has": lambda node: node in generated,
        "providers": list(m.providers),
        "release": m.release,
        "storage": m.storage,
        "transport": m.transport,
        "tune": m.tune,
        "profile": m.profile,
        "pack": m.pack,
        "template_version": meta["version"],
        "spec_version": meta["spec_version"],
        "generated": repr(sorted(generated)),
        "storage_doc": storage_doc,
        "title": title,
        "ollama_model": m.ollama_model,
        "anthropic_model": m.anthropic_model,
        "hw_version": __version__,
        "provider": next((p for p in m.providers if p != "fake"), "fake"),
        "node_table": node_table(simple or [], generated),
    }


def render_set(
    m: GenManifest,
    generated: set[str],
    title: str,
    base: Path | None = None,
    root: Path | None = None,
) -> dict[str, tuple[str, bool]]:
    """Every templated output file: path -> (text, managed)."""
    meta, files = template_set(m.template_set, base)
    simple = [
        n["id"]
        for n in json.loads(((root or repo_root()) / "spec/diagram/diagram.json").read_text())[
            "nodes"
        ]
        if n["view"] == "simple"
    ]
    ctx = context(m, generated, meta, title, simple)
    out: dict[str, tuple[str, bool]] = {}
    for entry in meta["files"]:
        if not eval(entry.get("when", "True"), {"__builtins__": {"set": set}}, ctx):  # noqa: S307
            continue
        src = files / entry.get("from", entry["path"])
        out[entry["path"]] = (
            render(src.read_text(encoding="utf-8"), ctx, entry["path"]),
            bool(entry.get("managed")),
        )
    return out


def policy_for(root: Path, tool_names: list[str]) -> str:
    ref = yaml.safe_load((root / "config" / "policy.yaml").read_text(encoding="utf-8"))
    builtins = ["read_skill", "read_doc", "remember"]
    doc = {
        "version": 1,
        "enabled": True,
        "agent": {"allow_tools": builtins + [t for t in tool_names if t not in builtins]},
        "roles": ref.get("roles") or {},
        "rules": ref.get("rules") or [],
        "budgets": {
            "default": {"tool_calls_per_turn": ref["budgets"]["default"]["tool_calls_per_turn"]}
        },
    }
    head = (
        "# Policy (yours): which tool calls may run. First match wins, no match is a deny\n"
        "# (default.deny). spec/contracts/policy.md. Prompt text can never change a decision.\n"
    )
    return head + yaml.safe_dump(doc, sort_keys=False, width=100)


def data_files(m: GenManifest, pack: packs.Pack, root: Path) -> dict[str, bytes]:
    """Files copied, not rendered: the pack, the profiles, the diagram, the conformance suite."""
    out: dict[str, bytes] = {}
    for path in sorted(pack.path.rglob("*")):
        rel = path.relative_to(pack.path)
        if (
            path.is_file()
            and rel.parts[0] not in SKIP
            and path.suffix != ".py"
            and not any(p.startswith(".") or p == "__pycache__" for p in rel.parts)
        ):
            out[f"packs/{pack.id}/{rel}"] = path.read_bytes()
    for pdf in sorted((pack.path / "docs").glob("*.pdf")) if (pack.path / "docs").is_dir() else []:
        out[f"packs/{pack.id}/sources/{pdf.stem}.pages.json"] = pdf_pages(pdf)
    for code in sorted(
        pack.path.glob("*.py")
    ):  # code tools (`entrypoint: file.py:fn`) move to tools/
        out[f"tools/{code.name}"] = code.read_bytes()
    out["prompts/system_prompt.md"] = (pack.path / "system_prompt.md").read_bytes()
    for skill in sorted((pack.path / "skills").glob("*.md")):
        out[f"skills/{skill.name}"] = skill.read_bytes()
    tools_text = (
        (pack.path / "tools.yaml").read_text(encoding="utf-8")
        if (pack.path / "tools.yaml").exists()
        else "[]\n"
    )
    out["tools/tools.yaml"] = tools_text.encode()
    names = [t["name"] for t in yaml.safe_load(tools_text) or []]
    out["policy/policy.yaml"] = policy_for(root, names).encode()
    for prof in sorted((root / "config" / "profiles").glob("*.yaml")):
        out[f"config/profiles/{prof.name}"] = prof.read_bytes()
    out["harness/core/diagram.json"] = (root / "spec" / "diagram" / "diagram.json").read_bytes()
    arts = [
        {"kind": "prompt", "name": "system_prompt", "sha256": sha(out["prompts/system_prompt.md"])},
        *[
            {
                "kind": "skill",
                "name": p.removeprefix("skills/").removesuffix(".md"),
                "sha256": sha(out[p]),
            }
            for p in sorted(out)
            if p.startswith("skills/")
        ],
        {"kind": "policy", "name": "policy", "sha256": sha(out["policy/policy.yaml"])},
        {"kind": "tools", "name": "tools", "sha256": sha(out["tools/tools.yaml"])},
    ]
    out["artifacts/release.json"] = (
        json.dumps({"release": "generated", "pack": pack.id, "artifacts": arts}, indent=2) + "\n"
    ).encode()
    if m.transport == "http":
        conf = root / "spec" / "conformance"
        for name in SUITE:
            src = conf / name
            for f in [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file()):
                out[f"tests/conformance/suite/spec/conformance/{f.relative_to(conf)}"] = (
                    f.read_bytes()
                )
        out["tests/conformance/suite/spec/events.schema.json"] = (
            root / "spec" / "events.schema.json"
        ).read_bytes()
        out["tests/conformance/suite/spec/diagram/diagram.json"] = (
            root / "spec" / "diagram" / "diagram.json"
        ).read_bytes()
        out["tests/conformance/suite/datasets/_starter/pack.yaml"] = (
            b"# The conformance suite's default pack: its content comes inline from the profile.\n"
            b"title: Conformance starter\nlicence: CC0-1.0\nprofile: operated\n"
        )
    return out


def pdf_pages(path: Path) -> bytes:
    """A PDF's text, page by page, extracted now (pypdf runs in the generator, never in the
    output), so the generated knowledge store chunks it exactly as the reference does."""
    import re

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [re.sub(r"[ \t]+", " ", p.extract_text() or "").strip() for p in reader.pages]
    title = ((reader.metadata.title if reader.metadata else None) or "").strip()
    doc = {"title": title or path.stem.replace("_", " "), "pages": pages}
    return (json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_pack(root: Path, ref: str) -> packs.Pack:
    path = Path(ref)
    if path.is_dir() and (path / "pack.yaml").exists():
        return packs.load_pack(path.resolve())
    return packs.get_pack(ref, root)


def plan(m: GenManifest, root: Path | None = None, base: Path | None = None) -> dict[str, Any]:
    """Everything init would write, without writing: files, managed set, notes, lock."""
    from harnesswalk.gen import notes

    root = root or repo_root()
    pack = resolve_pack(root, m.pack)
    m.pack = pack.id
    profile = profiles.load(root, m.profile or pack.meta.get("profile") or "minimal")
    m.profile = profile.id
    meta, _ = template_set(m.template_set, base)
    m.check(profile.rung, diagram_ids(root))
    generated = generated_nodes(root, profile, m.tune)
    missing = sorted(set(meta.get("not_generated") or []) & generated)
    if missing:
        what = "--tune" if set(missing) <= TUNING else f"profile {profile.id}"
        raise ValueError(f"{what} needs {', '.join(missing)}, which no template implements yet")
    rendered = render_set(m, generated, pack.meta.get("title") or pack.id, base, root)
    files: dict[str, bytes] = {p: t.encode() for p, (t, _) in rendered.items()}
    files.update(data_files(m, pack, root))
    managed = sorted(p for p, (_, mg) in rendered.items() if mg)
    files["GENERATION_NOTES.md"] = notes.notes(m, profile, pack, generated, root).encode()
    files["harness.yaml"] = (
        "# The generator manifest (harnesswalk init); `harnesswalk upgrade` reads it.\n"
        + yaml.safe_dump({**m.to_dict(), "generated_nodes": sorted(generated)}, sort_keys=False)
    ).encode()
    lock = {
        "template_set": m.template_set,
        "template_version": meta["version"],
        "spec_version": meta["spec_version"],
        "harnesswalk": __version__,
        "codemods_applied": [],
        "files": {p: {"sha256": sha(files[p]), "managed": p in managed} for p in sorted(files)},
    }
    files["harness.lock"] = (
        "# Written by harnesswalk; do not edit by hand.\n" + yaml.safe_dump(lock, sort_keys=False)
    ).encode()
    for p in managed:
        files[f".harnesswalk/pristine/{p}"] = files[p]
    return {
        "files": files,
        "managed": managed,
        "profile": profile,
        "pack": pack,
        "lock": lock,
        "generated": generated,
    }


def write(files: dict[str, bytes], out: Path) -> None:
    for rel, data in files.items():
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


def git(out: Path, *args: str) -> str:
    """Git in the output repo, as the user's identity (a neutral one when none is set)."""
    who = subprocess.run(["git", "config", "user.email"], cwd=out, capture_output=True, text=True)
    ident = (
        []
        if who.stdout.strip()
        else ["-c", "user.name=harnesswalk", "-c", "user.email=init@harnesswalk.invalid"]
    )
    done = subprocess.run(
        ["git", *ident, *args], cwd=out, check=True, capture_output=True, text=True
    )
    return done.stdout


def init(
    m: GenManifest,
    out: Path,
    *,
    root: Path | None = None,
    force: bool = False,
    use_git: bool = True,
    base: Path | None = None,
) -> dict[str, Any]:
    out = out.resolve()
    if out.exists() and any(out.iterdir()) and not force:
        raise FileExistsError(f"{out} is not empty (use --force to write into it)")
    p = plan(m, root, base)
    if force and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    write(p["files"], out)
    if use_git and shutil.which("git"):
        git(out, "init", "-q", "-b", "main")
        git(out, "add", "-A")
        git(out, "commit", "-q", "-m", f"harnesswalk init: {m.profile} harness for pack {m.pack}")
    return {**p, "out": out, "lines": line_counts(out)}


def line_counts(out: Path) -> dict[str, int]:
    def count(paths: list[Path]) -> int:
        return sum(len(f.read_text(encoding="utf-8").splitlines()) for f in paths)

    harness = sorted((out / "harness").rglob("*.py"))
    every = sorted(p for p in out.rglob("*.py") if ".harnesswalk" not in p.parts)
    return {"harness": count(harness), "python": count(every), "files": len(harness)}
