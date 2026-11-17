"""Drift tests for the template set, like the diagram-vs-registry tests: the templates cannot
fall behind the spec without a test going red."""

from __future__ import annotations

import re

from harnesswalk.gen import build
from tests.spec.support import ROOT, load_json

META, FILES = build.template_set("python")
DIAGRAM = load_json(ROOT / "spec" / "diagram" / "diagram.json")
IDS = {n["id"] for n in DIAGRAM["nodes"]}
SCHEMA = load_json(ROOT / "spec" / "events.schema.json")
TEMPLATES = {p: p.read_text() for p in FILES.rglob("*.py")}


def test_template_set_matches_the_spec_version() -> None:
    assert META["spec_version"] == DIAGRAM["spec_version"]
    events = (FILES / "harness/core/events.py").read_text()
    assert f'SCHEMA_VERSION = "{DIAGRAM["spec_version"]}"' in events
    assert (
        f'SPEC_VERSION = "{DIAGRAM["spec_version"]}"'
        in (FILES / "harness/core/config.py").read_text()
    )


def test_every_diagram_node_is_implemented_or_declared_not_generated() -> None:
    covered = {n for f in META["files"] for n in f.get("nodes") or []}
    assert covered <= IDS, covered - IDS
    assert covered | set(META["not_generated"]) == IDS, IDS - covered - set(META["not_generated"])
    for entry in META["files"]:
        assert (FILES / entry.get("from", entry["path"])).is_file(), entry["path"]


def test_node_ids_in_templates_are_diagram_nodes() -> None:
    used = set()
    for text in TEMPLATES.values():
        used |= set(
            re.findall(r'(?:span|off_node\(turn,|retrieve_store\(h, turn,)\(?\s*"([a-z_]+)"', text)
        )
        used |= set(re.findall(r'is_off\("([a-z_]+)"\)', text))
    assert used and used <= IDS, used - IDS


def test_event_types_in_templates_are_in_the_schema_enum() -> None:
    enum = set(SCHEMA["properties"]["type"]["enum"])
    used = {t for text in TEMPLATES.values() for t in re.findall(r'emit\(\s*"([a-z_]+)"', text)}
    assert {"turn_start", "turn_end", "node_enter", "node_exit", "approval_requested"} <= used
    assert used <= enum, used - enum
