"""The registry cannot drift from spec/diagram/diagram.json (spec/harness.md section 6)."""

import importlib

import pytest

from harnesswalk import diagram
from harnesswalk.nodes import (
    UnknownNodeError,
    generated_diagram,
    implemented,
    load_components,
    register,
    registered,
)
from tests.spec.support import ROOT, load_json

STUBS: set[str] = set()  # P4 made eval and release real
NOT_REGISTERED: set[str] = set()  # diagnose arrived in P5


def test_unknown_node_fails_at_registration() -> None:
    with pytest.raises(UnknownNodeError):
        register("not_in_the_diagram", "tests.unit.test_nodes")


def test_generated_diagram_matches_the_file() -> None:
    load_components()
    want = load_json(ROOT / "spec" / "diagram" / "diagram.json")
    got = generated_diagram()

    def shape(doc):
        nodes = {(n["id"], n["group"], n.get("parent"), n["view"]) for n in doc["nodes"]}
        edges = {(e["id"], e["from"], e["to"], e["view"]) for e in doc["edges"]}
        return nodes, edges, [g["id"] for g in doc["groups"]]

    assert shape(got) == shape(want)
    assert all({"implemented", "stub", "disabled"} <= set(n) for n in got["nodes"])


def test_registered_set_is_what_p3_promises() -> None:
    regs = load_components()
    every = set(diagram.nodes_by_id())
    assert set(regs) == every - NOT_REGISTERED
    assert {n for n, r in regs.items() if r.stub} == STUBS
    assert diagram.simple_ids() <= set(regs)
    assert implemented("online_eval") and implemented("loop") and implemented("eval")
    assert implemented("release") and implemented("artifacts") and implemented("diagnose")


def test_every_registration_lives_in_its_source_hint() -> None:
    for node_id, reg in registered().items():
        module = importlib.import_module(reg.module)
        hint = diagram.nodes_by_id()[node_id]["source_hint"]
        assert module.__file__.endswith(hint), (node_id, module.__file__, hint)


def test_simple_view_filter() -> None:
    doc = generated_diagram("simple")
    assert {n["id"] for n in doc["nodes"]} == diagram.simple_ids()
    assert all(e["view"] == "simple" for e in doc["edges"])
