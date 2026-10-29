"""The canonical diagram, spec/diagram/diagram.json, loaded once.

Node ids, edge ids and parents come from here and nowhere else. The registry, the emitter and
`GET /v1/diagram` all read this module.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any

from harnesswalk.paths import spec_dir


@cache
def diagram() -> dict[str, Any]:
    return json.loads((spec_dir() / "diagram" / "diagram.json").read_text(encoding="utf-8"))


@cache
def nodes_by_id() -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in diagram()["nodes"]}


@cache
def edge_ids() -> frozenset[str]:
    return frozenset(e["id"] for e in diagram()["edges"])


@cache
def simple_ids() -> frozenset[str]:
    return frozenset(n["id"] for n in diagram()["nodes"] if n["view"] == "simple")


def parent_of(node_id: str) -> str | None:
    return nodes_by_id()[node_id].get("parent")
