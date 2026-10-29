"""What `GET /v1/nodes/{id}` shows for a node: its lesson, its code, its config or contents.

The payload depends on the node's `kind` (http_api.md). Every node has a read-only code peek
from its `source_hint`, and `recent` holds the caller's last exit summaries for it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harnesswalk import diagram
from harnesswalk.nodes import implemented, registered
from harnesswalk.ops import release

if TYPE_CHECKING:
    from harnesswalk.runtime import Harness

CONFIG_FOR = {
    "policy": "config/policy.yaml",
    "router": "config/models.yaml",
    "registry": None,  # the pack's tools.yaml, resolved below
    "context_builder": "config/context.yaml",
    "model_layer": "config/models.yaml",
}


def _source(h: Harness, node: dict[str, Any]) -> dict[str, Any]:
    path = h.settings.root / node["source_hint"]
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"path": node["source_hint"], "language": "python", "content": content}


def _config(h: Harness, node_id: str) -> dict[str, Any]:
    rel = CONFIG_FOR.get(node_id)
    if node_id == "registry":
        rel = f"datasets/{h.settings.get('dataset', '_starter')}/tools.yaml"
    if not rel or not (h.settings.root / rel).exists():
        return {"type": "config", "path": None, "content": None, "editable": False}
    content, version = release.current(h.settings.root, rel)
    return {
        "type": "config",
        "path": rel,
        "version": version,
        "format": "yaml",
        "content": content,
        "editable": h.settings.mode != "public",
        "effective": _effective(h, node_id),
    }


def _effective(h: Harness, node_id: str) -> Any:
    """The configuration actually in force (after profile and overrides)."""
    if node_id == "policy":
        return {"agent": h.policy.cfg.get("agent"), "rules": h.policy.rules()}
    if node_id in ("router", "model_layer"):
        return {"rules": h.router.rules(), "providers": sorted(h.providers)}
    if node_id == "context_builder":
        return h.settings.get("context", {})
    if node_id == "registry":
        return [
            {"name": t.name, "action_class": t.action_class, "timeout_ms": t.timeout_ms}
            for t in h.registry.all()
        ]
    return None


def _inspector(h: Harness, tenant_id: str, node: dict[str, Any], recent: list[dict]) -> dict:
    kind, node_id = node["kind"], node["id"]
    if kind == "config":
        return _config(h, node_id)
    if kind == "prompt":
        p = h.procedural
        items = [{"name": "system_prompt", "version": p.prompt_version, "content": p.system_prompt}]
        items += [
            {"name": s.name, "version": s.version, "description": s.description, "content": s.body}
            for s in p.skills.values()
        ]
        return {"type": "prompt", "items": items, "editable": h.settings.mode != "public"}
    if kind == "queue":
        return {"type": "queue", "pending": h.approvals.pending(tenant_id)}
    if kind == "model":
        return {
            "type": "model",
            "models": h.settings.get("models.models", {}),
            "router_rules": h.router.rules(),
            "providers": sorted(h.providers),
            "last_routing": h.model_layer.last_route,
        }
    if kind == "store":
        if node_id == "audit":
            records = [r.__dict__ for r in h.audit.list(tenant_id, 5)]
            return {"type": "store", "verify": h.audit.verify(tenant_id), "last_records": records}
        if node_id == "session":
            return {"type": "store", "counts": h.session.count(tenant_id)}
        if node_id == "trace":
            return {"type": "store", "turns": h.tracer.list_turns(tenant_id, 5)}
        return {"type": "store", "counts": {}, "implemented": implemented(node_id)}
    last = h.context_builder.last.get(tenant_id) if node_id == "context_builder" else None
    out = {"type": "source", **_source(h, node), "last_summary": recent[0] if recent else None}
    if last is not None:
        out["last_context"] = {"slices": last.slices, "used_tokens": last.used_tokens}
    return out


def inspect_node(h: Harness, tenant_id: str, node_id: str, recent_n: int = 5) -> dict[str, Any]:
    node = diagram.nodes_by_id()[node_id]
    recent = [
        {"turn_id": e["turn_id"], "status": e["status"], "summary": e.get("summary", {})}
        for e in h.tracer.recent_exits(tenant_id, node_id, recent_n)
    ]
    reg = registered().get(node_id)
    return {
        "id": node_id,
        "label": node["label"],
        "kind": node["kind"],
        "group": node["group"],
        "lesson": node["lesson"],
        "implemented": implemented(node_id),
        "stub": bool(reg and reg.stub),
        "can_disable": bool(node.get("can_disable")),
        "inspector": _inspector(h, tenant_id, node, recent),
        "source": _source(h, node),
        "recent": recent,
    }
