"""The lesson routes: the chapters in `lessons/`, served for the web app's Lessons tab and drawer.

Served by http_api.py. No identity is needed (the lessons, like the code, are the same for
every tenant), and every mode serves them.

    GET /v1/lessons                         the chapters in order, with their metadata
    GET /v1/lessons/{slug}                  one chapter: metadata, markdown, code links, try
    GET /v1/lessons/{slug}/source?path=...  a file the chapter's code walk links to

A chapter is `lessons/NN-<name>.md`: YAML front matter, then markdown that starts with
`# Title` and a one-line `> hook`. The front matter names the node the chapter is about
(`node`), the nodes it covers when that is not just `node` (`covers`, for the expert groups),
and an optional `try` (`fixture`, `prompt`, `disable`) the UI offers as a button. Code walk
links are relative markdown links into the package, `[text](../harnesswalk/<path>#<Symbol>)`,
where `Symbol` is a top-level name or `Class.method`; each comes back with its line.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from harnesswalk import diagram
from harnesswalk.control.gateway import GatewayError
from harnesswalk.inspectors import LANGUAGES, SOURCE_CAP_BYTES, _symbols

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

CHAPTER = re.compile(r"^(\d{2})-([a-z0-9_-]+)\.md$")
FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
CODE_LINK = re.compile(r"\]\(\.\./(harnesswalk/[A-Za-z0-9_/.-]+)(?:#([A-Za-z_][A-Za-z0-9_.]*))?\)")
LINK_TARGET = re.compile(r"\]\([^)]*\)")


def lessons_dir(root: Path) -> Path:
    return root / "lessons"


def _split(text: str) -> tuple[dict[str, Any], str]:
    match = FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta = yaml.safe_load(match.group(1)) or {}
    return (meta if isinstance(meta, dict) else {}), text[match.end() :]


def _title_and_hook(body: str) -> tuple[str, str]:
    title = hook = ""
    for line in body.splitlines():
        if not title and line.startswith("# "):
            title = line[2:].strip()
        elif title and line.startswith("> "):
            hook = line[2:].strip()
            break
        elif title and line.strip() and not line.startswith(">"):
            break
    return title, hook


def symbol_line(path: Path, symbol: str | None) -> int | None:
    """The 1-based line of `Name` or `Class.method` in a Python file, else None."""
    if not symbol or path.suffix != ".py":
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError, OSError):
        return None
    scope: list[ast.stmt] = tree.body
    line = None
    for part in symbol.split("."):
        found = next(
            (
                item
                for item in scope
                if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == part
            ),
            None,
        )
        if found is None:
            found = next((item for item in scope if _assigns(item, part)), None)
            if found is None:
                return None
        line = found.lineno
        scope = getattr(found, "body", [])
    return line


def _assigns(item: ast.stmt, name: str) -> bool:
    """A module or class constant: `NAME = ...` or `NAME: T = ...`."""
    if isinstance(item, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id == name for t in item.targets)
    if isinstance(item, ast.AnnAssign):
        return isinstance(item.target, ast.Name) and item.target.id == name
    return False


def _words(body: str) -> int:
    """Whitespace-separated words, with link targets left out (they are not prose)."""
    return len(LINK_TARGET.sub("]", body).split())


def _source_nodes() -> dict[str, str]:
    """Package path to the node whose code peek is that file."""
    return {
        n["source_hint"]: n["id"] for n in diagram.nodes_by_id().values() if n.get("source_hint")
    }


@lru_cache(maxsize=64)
def _read(path: Path, mtime: float) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    meta, body = _split(text)
    match = CHAPTER.match(path.name)
    assert match is not None
    title, hook = _title_and_hook(body)
    node_id = meta.get("node")
    covers = meta.get("covers") or ([node_id] if node_id else [])
    root = path.parent.parent
    by_path = _source_nodes()
    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for rel, symbol in CODE_LINK.findall(body):
        key = (rel, symbol or None)
        if key in seen:
            continue
        seen.add(key)
        links.append(
            {
                "path": rel,
                "symbol": symbol or None,
                "line": symbol_line(root / rel, symbol or None),
                "node_id": by_path.get(rel),
            }
        )
    return {
        "slug": path.stem,
        "n": int(match.group(1)),
        "title": title,
        "hook": hook,
        "node_id": node_id,
        "covers": list(covers),
        "group": node_id not in covers if node_id else False,
        "words": _words(body),
        "url": f"/v1/lessons/{path.stem}",
        "markdown": body.lstrip("\n"),
        "try": meta.get("try") or None,
        "code_links": links,
    }


def chapters(root: Path) -> list[dict[str, Any]]:
    """Every chapter in `lessons/`, in chapter order."""
    folder = lessons_dir(root)
    if not folder.is_dir():
        return []
    files = sorted(p for p in folder.iterdir() if p.is_file() and CHAPTER.match(p.name))
    return [_read(p, p.stat().st_mtime) for p in files]


LIST_KEYS = ("slug", "n", "title", "hook", "node_id", "covers", "group", "words", "url")


def read_source(root: Path, rel: str) -> dict[str, Any] | None:
    """A file inside the `harnesswalk/` package, as the code peek shapes it, else None."""
    root = root.resolve()
    package = (root / "harnesswalk").resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(package) or not path.is_file():
        return None
    raw = path.read_bytes()
    text = raw.decode("utf-8", "replace")
    shown, truncated = text, len(raw) > SOURCE_CAP_BYTES
    if truncated:
        head = raw[:SOURCE_CAP_BYTES].decode("utf-8", "ignore")
        shown = head[: head.rfind("\n") + 1] or head
    language = LANGUAGES.get(path.suffix, "text")
    return {
        "path": "harnesswalk/" + path.relative_to(package).as_posix(),
        "language": language,
        "line_count": len(text.splitlines()),
        "source": shown,
        "truncated": truncated,
        "symbols": _symbols(text) if language == "python" else [],
    }


def add_lesson_routes(app: FastAPI, harness: Harness) -> None:
    root = harness.settings.root

    def find(slug: str) -> tuple[list[dict[str, Any]], int]:
        items = chapters(root)
        for i, item in enumerate(items):
            if item["slug"] == slug:
                return items, i
        raise GatewayError(404, "lesson_not_found", f"No lesson {slug}.")

    @app.get("/v1/lessons")
    async def list_lessons() -> JSONResponse:
        items = chapters(root)
        return JSONResponse(
            {
                "lessons": [{k: item[k] for k in LIST_KEYS} for item in items],
                "walk_order": diagram.diagram()["walk_order"],
            }
        )

    @app.get("/v1/lessons/{slug}")
    async def get_lesson(slug: str) -> JSONResponse:
        items, i = find(slug)
        item = dict(items[i])
        item["prev"] = items[i - 1]["slug"] if i > 0 else None
        item["next"] = items[i + 1]["slug"] if i + 1 < len(items) else None
        return JSONResponse(item)

    @app.get("/v1/lessons/{slug}/source")
    async def get_lesson_source(slug: str, path: str) -> JSONResponse:
        items, i = find(slug)
        if path not in {link["path"] for link in items[i]["code_links"]}:
            raise GatewayError(404, "source_not_found", f"Lesson {slug} does not link to {path}.")
        source = read_source(root, path)
        if source is None:
            raise GatewayError(404, "source_not_found", f"No file {path} in the package.")
        return JSONResponse(source)
