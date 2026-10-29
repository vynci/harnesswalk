"""Documentation stays in step with the spec, and follows the house style."""

import re
from pathlib import Path

from tests.spec.support import ROOT, SPEC, diagram, simple_ids

CONTRACTS = [
    "gateway", "guardrail", "policy", "approval_queue", "audit", "context_builder", "store",
    "memory_writer", "loop", "model_provider", "router", "tool_registry", "sandbox", "tracer",
    "evaluator", "release", "http_api",
]  # fmt: skip


def _markdown_files() -> list[Path]:
    skip = {".venv", ".git", "node_modules"}
    return [p for p in ROOT.rglob("*.md") if not skip & set(p.relative_to(ROOT).parts)]


def test_every_contract_exists() -> None:
    for name in CONTRACTS:
        assert (SPEC / "contracts" / f"{name}.md").exists(), name


def test_readme_mermaid_matches_simple_view() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    block = re.search(r"```mermaid\n(.*?)```", text, re.S).group(1)
    declared = set(re.findall(r"^\s+([a-z_]+)[\[\(]", block, re.M))
    assert declared == simple_ids()
    arrows = set(re.findall(r"^\s+([a-z_]+) [-.=]+>\|[^|]+\| ([a-z_]+)$", block, re.M))
    simple_edges = {(e["from"], e["to"]) for e in diagram()["edges"] if e["view"] == "simple"}
    assert arrows == simple_edges


def test_lessons_follow_walk_order() -> None:
    text = (ROOT / "lessons" / "README.md").read_text(encoding="utf-8")
    listed = re.findall(r"^\| \d+ \| `([a-z_]+)`", text, re.M)
    assert listed == diagram()["walk_order"]


def test_harness_readme_maps_every_node() -> None:
    text = (ROOT / "harness" / "README.md").read_text(encoding="utf-8")
    mapped = set(re.findall(r"\| `([a-z_]+)` \| `[a-z_]+\.md`", text))
    assert mapped == {n["id"] for n in diagram()["nodes"]}


def test_no_em_dashes_in_docs() -> None:
    offenders = [str(p) for p in _markdown_files() if "—" in p.read_text(encoding="utf-8")]
    assert offenders == []
