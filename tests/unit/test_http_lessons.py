"""The lesson chapters (lessons/) and their routes (http_lessons.py): every node has a chapter,
every chapter has the six sections, three checks and code links that still resolve."""

import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from harnesswalk.control.http_api import create_app
from harnesswalk.control.http_lessons import chapters, symbol_line
from harnesswalk.runtime import Harness
from tests.spec.support import ROOT, diagram
from tests.unit.support import settings

SECTIONS = [
    "What it does",
    "What breaks without it",
    "The code walk",
    "Try this",
    "Check yourself",
    "Further reading",
]
GROUP_CHAPTERS = {
    "memory": "16-memory-inside",
    "model_layer": "17-model_layer-inside",
    "tools": "18-tools-inside",
    "trace": "19-trace-inside",
    "eval": "20-eval-inside",
    "release": "21-release-inside",
}


def github_slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", heading.strip().lower()).replace(" ", "-")


def all_chapters() -> list[dict[str, Any]]:
    return chapters(ROOT)


def by_slug() -> dict[str, dict[str, Any]]:
    return {c["slug"]: c for c in all_chapters()}


def checks(markdown: str) -> list[dict[str, Any]]:
    """The Check yourself questions: title, options, which one is right, the answer text."""
    part = markdown.split("## Check yourself", 1)[1].split("\n## ", 1)[0]
    out = []
    for block in re.split(r"^### ", part, flags=re.M)[1:]:
        title, _, rest = block.partition("\n")
        options = re.findall(r"^- \[( |x)\] (.+)$", rest, re.M)
        answer = re.search(r"^Answer: (.+)", rest, re.M)
        out.append(
            {
                "title": title,
                "options": [text for _, text in options],
                "right": [i for i, (mark, _) in enumerate(options) if mark == "x"],
                "answer": answer.group(1) if answer else None,
            }
        )
    return out


# -- the chapters -----------------------------------------------------------------------------


def test_one_chapter_per_walk_node_then_one_per_expert_group() -> None:
    d = diagram()
    expected = [f"{i:02d}-{node}" for i, node in enumerate(d["walk_order"], 1)]
    expected += [GROUP_CHAPTERS[p] for p in d["walk_order"] if p in GROUP_CHAPTERS]
    assert [c["slug"] for c in all_chapters()] == expected
    assert [c["n"] for c in all_chapters()] == list(range(1, len(expected) + 1))


def test_every_node_links_its_chapter_and_section() -> None:
    lessons = by_slug()
    for node in diagram()["nodes"]:
        url = node["lesson_url"]
        path, _, anchor = url.partition("#")
        slug = Path(path).stem
        assert slug in lessons, (node["id"], url)
        chapter = lessons[slug]
        assert node["id"] in chapter["covers"], (node["id"], slug)
        if node["view"] == "simple":
            assert anchor == "" and chapter["node_id"] == node["id"]
            assert node["lesson_summary"] == chapter["hook"], node["id"]
        else:
            assert chapter["group"] and chapter["node_id"] == node["parent"]
            headings = {
                github_slug(h) for h in re.findall(r"^### (.+)$", chapter["markdown"], re.M)
            }
            assert anchor in headings, (node["id"], anchor)


def test_every_chapter_has_the_six_sections_in_order() -> None:
    for c in all_chapters():
        headings = re.findall(r"^## (.+)$", c["markdown"], re.M)
        assert headings == SECTIONS, c["slug"]
        assert c["title"] and c["hook"], c["slug"]


def test_chapter_length() -> None:
    for c in all_chapters():
        assert 600 <= c["words"] <= 1200, (c["slug"], c["words"])


def test_three_checks_with_one_right_answer_each() -> None:
    for c in all_chapters():
        found = checks(c["markdown"])
        assert len(found) == 3, c["slug"]
        for q in found:
            assert len(q["options"]) == 3 and len(q["right"]) == 1, (c["slug"], q["title"])
            assert q["answer"], (c["slug"], q["title"])


def test_code_links_resolve_to_a_file_and_symbol() -> None:
    for c in all_chapters():
        assert c["code_links"], c["slug"]
        for link in c["code_links"]:
            assert (ROOT / link["path"]).is_file(), (c["slug"], link["path"])
            if link["symbol"]:
                assert link["line"], (c["slug"], link["path"], link["symbol"])


def test_repo_links_exist() -> None:
    for c in all_chapters():
        for target in re.findall(r"\]\(\.\./([^)#\s]+)", c["markdown"]):
            assert (ROOT / target).exists(), (c["slug"], target)


def test_further_reading_is_primary_sources() -> None:
    for c in all_chapters():
        part = c["markdown"].split("## Further reading", 1)[1]
        links = re.findall(r"^- \[.+?\]\((.+?)\)", part, re.M)
        assert 2 <= len(links) <= 3, c["slug"]


def test_try_names_real_fixtures_and_disableable_nodes() -> None:
    nodes = {n["id"]: n for n in diagram()["nodes"]}
    fixtures = {p.stem for p in (ROOT / "datasets" / "_starter" / "fixtures" / "fake").iterdir()}
    fixtures |= {p.stem for p in (ROOT / "spec" / "conformance" / "fixtures").iterdir()}
    for c in all_chapters():
        t = c["try"]
        if not t:
            continue
        assert t.get("prompt"), c["slug"]
        if t.get("fixture"):
            assert t["fixture"] in fixtures, (c["slug"], t["fixture"])
        for node_id in t.get("disable") or []:
            assert nodes[node_id].get("can_disable"), (c["slug"], node_id)


def test_index_lists_every_chapter() -> None:
    text = (ROOT / "lessons" / "README.md").read_text(encoding="utf-8")
    for c in all_chapters():
        assert f"({c['slug']}.md)" in text, c["slug"]


def test_symbol_line_reads_methods_and_constants() -> None:
    path = ROOT / "harnesswalk" / "control" / "input_guardrails.py"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[symbol_line(path, "InputGuardrails") - 1].startswith("class InputGuardrails")
    assert "def run" in lines[symbol_line(path, "InputGuardrails.run") - 1]
    assert lines[symbol_line(path, "STRENGTH") - 1].startswith("STRENGTH")
    assert symbol_line(path, "InputGuardrails.nope") is None


# -- the routes -------------------------------------------------------------------------------


def client(h: Harness) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(h)), base_url="http://t")


@pytest.mark.anyio
async def test_list_and_read_a_lesson(tmp_path: Path) -> None:
    async with client(Harness(settings(tmp_path))) as c:
        body = (await c.get("/v1/lessons")).json()
        assert body["walk_order"] == diagram()["walk_order"]
        first = body["lessons"][0]
        assert set(first) == {
            "slug", "n", "title", "hook", "node_id", "covers", "group", "words", "url"
        }  # fmt: skip
        assert "markdown" not in first

        lesson = (await c.get("/v1/lessons/02-input_guardrails")).json()
        assert lesson["node_id"] == "input_guardrails" and lesson["covers"] == ["input_guardrails"]
        assert lesson["markdown"].startswith("# Input guardrails")
        assert "---" not in lesson["markdown"].splitlines()[0]
        assert lesson["try"]["disable"] == ["input_guardrails"]
        assert lesson["prev"] == "01-gateway" and lesson["next"] == "03-context_builder"
        run = next(x for x in lesson["code_links"] if x["symbol"] == "InputGuardrails.run")
        assert run["node_id"] == "input_guardrails" and run["line"] > 1

        r = await c.get("/v1/lessons/99-nothing")
        assert r.status_code == 404 and r.json()["error"]["code"] == "lesson_not_found"


@pytest.mark.anyio
async def test_lesson_source_serves_only_linked_package_files(tmp_path: Path) -> None:
    async with client(Harness(settings(tmp_path))) as c:
        r = await c.get(
            "/v1/lessons/02-input_guardrails/source", params={"path": "harnesswalk/control/pii.py"}
        )
        body = r.json()
        assert r.status_code == 200 and body["path"] == "harnesswalk/control/pii.py"
        assert body["language"] == "python" and any(s["name"] == "Vault" for s in body["symbols"])
        for path in ("harnesswalk/runtime.py", "../pyproject.toml", "harnesswalk/../README.md"):
            r = await c.get("/v1/lessons/02-input_guardrails/source", params={"path": path})
            assert r.status_code == 404, path
            assert r.json()["error"]["code"] == "source_not_found"


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["public", "private"])
async def test_lessons_in_every_mode(tmp_path: Path, mode: str) -> None:
    async with client(Harness(settings(tmp_path, mode=mode))) as c:
        r = await c.get("/v1/lessons")
        assert r.status_code == 200 and r.json()["lessons"]
