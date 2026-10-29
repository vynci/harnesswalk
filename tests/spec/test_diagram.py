"""spec/diagram: schema validity and the referential rules of spec/harness.md section 6."""

import jsonschema

from tests.spec.support import ROOT, SPEC, diagram, load_json, nodes_by_id, simple_ids

SIMPLE = {
    "gateway", "input_guardrails", "policy", "approval", "audit",
    "context_builder", "memory", "memory_writer",
    "loop", "model_layer", "tools", "output_guardrails",
    "trace", "eval", "release",
}  # fmt: skip
EXPERT = {
    "memory": {"procedural", "session", "semantic", "episodic", "knowledge"},
    "model_layer": {"router", "providers"},
    "tools": {"registry", "sandbox"},
    "eval": {"online_eval", "offline_eval", "diagnose"},
    "trace": {"cost"},
    "release": {"artifacts"},
}


def test_diagram_matches_schema() -> None:
    schema = load_json(SPEC / "diagram" / "diagram.schema.json")
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(diagram())


def test_node_ids_unique() -> None:
    ids = [n["id"] for n in diagram()["nodes"]]
    assert len(ids) == len(set(ids))


def test_simple_view_is_exactly_the_fifteen_nodes() -> None:
    assert simple_ids() == SIMPLE


def test_expert_children_and_parents() -> None:
    nodes = nodes_by_id()
    children: dict[str, set[str]] = {}
    for n in diagram()["nodes"]:
        if n["view"] == "expert":
            parent = nodes[n["parent"]]
            assert parent["view"] == "simple", n["id"]
            assert parent["group"] == n["group"], n["id"]
            children.setdefault(n["parent"], set()).add(n["id"])
    assert children == EXPERT


def test_edges_reference_known_nodes() -> None:
    nodes = nodes_by_id()
    edges = diagram()["edges"]
    ids = [e["id"] for e in edges]
    assert len(ids) == len(set(ids))
    simple_edges = {e["id"] for e in edges if e["view"] == "simple"}
    seen_expert = False
    for e in edges:
        assert e["from"] in nodes and e["to"] in nodes, e["id"]
        if e["view"] == "simple":
            assert not seen_expert, f"simple edge {e['id']} after an expert edge"
            assert nodes[e["from"]]["view"] == "simple" and nodes[e["to"]]["view"] == "simple"
        else:
            seen_expert = True
        if "refines" in e:
            assert e["refines"] in simple_edges, e["id"]


def test_walk_order_covers_every_simple_node_once() -> None:
    walk = diagram()["walk_order"]
    assert len(walk) == len(set(walk))
    assert set(walk) == SIMPLE


def test_paths_use_simple_nodes() -> None:
    for name, path in diagram()["paths"].items():
        assert set(path) <= SIMPLE, name
        assert path[0] == "gateway" and path[-1] == "trace", name


def test_source_hints_point_into_planned_packages() -> None:
    for n in diagram()["nodes"]:
        package = n["source_hint"].split("/")[1]
        assert (ROOT / "harnesswalk" / package / "__init__.py").exists(), n["id"]


def test_event_schema_node_ids_match_diagram() -> None:
    schema = load_json(SPEC / "events.schema.json")
    assert set(schema["$defs"]["node_id"]["enum"]) == set(nodes_by_id())
