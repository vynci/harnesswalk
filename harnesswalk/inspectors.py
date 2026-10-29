"""What `GET /v1/nodes/{id}` shows for a node: its lesson, its code, its config or contents.

The payload depends on the node's `kind` (http_api.md). Every node has a read-only code peek
from its `source_hint`, and `recent` holds the caller's last exit summaries for it.
"""

from __future__ import annotations

import ast
from types import SimpleNamespace
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


SOURCE_CAP_BYTES = 200 * 1024
LANGUAGES = {".py": "python", ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".json": "json"}


def _symbols(text: str) -> list[dict[str, Any]]:
    """Top-level classes and functions of a Python file, with their 1-based line."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    out = []
    for item in tree.body:
        if isinstance(item, ast.ClassDef):
            out.append({"name": item.name, "line": item.lineno, "kind": "class"})
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append({"name": item.name, "line": item.lineno, "kind": "function"})
    return out


def node_source(h: Harness, node_id: str) -> dict[str, Any] | None:
    """GET /v1/nodes/{id}/source: the node's file, only from inside the `harnesswalk/` package.

    None (a 404) for a node without a file yet, or a hint that resolves (through `..` or a
    link) anywhere outside the package.
    """
    hint = diagram.nodes_by_id()[node_id].get("source_hint")
    if not hint:
        return None
    root = h.settings.root.resolve()
    package = (root / "harnesswalk").resolve()  # the package itself may be a link
    path = (root / hint).resolve()
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
        "node_id": node_id,
        "path": "harnesswalk/" + path.relative_to(package).as_posix(),
        "language": language,
        "line_count": len(text.splitlines()),
        "source": shown,
        "truncated": truncated,
        "symbols": _symbols(text) if language == "python" else [],
    }


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
            "last_routing": h.model_layer.last_route.get(tenant_id),
        }
    if kind == "store":
        if node_id == "audit":
            records = [r.__dict__ for r in h.audit.list(tenant_id, 5)]
            return {"type": "store", "verify": h.audit.verify(tenant_id), "last_records": records}
        if node_id == "session":
            return {"type": "store", "counts": h.session.count(tenant_id)}
        if node_id == "trace":
            return {"type": "store", "turns": h.tracer.list_turns(tenant_id, 5)}
        if node_id in MEMORY_STORES or node_id == "memory":
            return _memory_store(h, tenant_id, node_id)
        return {"type": "store", "counts": {}, "implemented": implemented(node_id)}
    last = h.context_builder.last.get(tenant_id) if node_id == "context_builder" else None
    out = {"type": "source", **_source(h, node), "last_summary": recent[0] if recent else None}
    if last is not None:
        out["last_context"] = {
            "slices": last.slices,
            "used_tokens": last.used_tokens,
            "citations": last.citations,
        }
    if node_id == "memory_writer":
        w = h.memory_writer
        out["last_writes"] = w.recent(tenant_id, 10)
        out["pending"] = w.pending(tenant_id, 20)
        out["settings"] = {
            "enabled": w.enabled,
            "gate": w.gate,
            "gate_in_this_mode": w.gate_for(_view(tenant_id, h.settings.mode)),
            "max_proposals_per_turn": w.max_per_turn,
            "auto_episodes": w.auto_episodes,
        }
    return out


MEMORY_STORES = ("semantic", "episodic", "knowledge")


def _view(tenant_id: str, mode: str = "dev") -> Any:
    """The tenant scope a store read needs, outside a turn."""
    return SimpleNamespace(
        tenant_id=tenant_id, user_id=None, mode=mode, is_disabled=lambda _node: False
    )


def _memory_store(h: Harness, tenant_id: str, node_id: str) -> dict[str, Any]:
    """Counts, the last records (tenant-scoped, pack seeds marked `scope: pack`), settings."""
    view = _view(tenant_id, h.settings.mode)
    enabled = node_id in h.memory.enabled() or node_id == "memory"
    common = {"embedder": h.embedder.id, "vectors": h.vectors.backend, "enabled": enabled}
    if node_id == "memory":
        counts = {
            **h.semantic.count(tenant_id),
            **h.episodic.count(tenant_id),
            **h.knowledge.count(tenant_id),
        }
        return {"type": "store", "counts": counts, "settings": {**common, "stores": [
            "procedural", "session", *h.memory.enabled()]}}  # fmt: skip
    if node_id == "semantic":
        records = [f.to_dict() for f in h.semantic.list(view, 50)]
        settings = {**common, "min_score": h.semantic.min_score}
        return {"type": "store", "counts": h.semantic.count(tenant_id), "last_records": records,
                "settings": settings}  # fmt: skip
    if node_id == "episodic":
        records = [e.to_dict() for e in h.episodic.list(view, 20)]
        e = h.episodic
        settings = {**common, "half_life_days": e.half_life_days, "min_relevance": e.min_relevance,
                    "weights": {"relevance": e.w_rel, "recency": e.w_rec}}  # fmt: skip
        return {"type": "store", "counts": e.count(tenant_id), "last_records": records,
                "settings": settings}  # fmt: skip
    k = h.knowledge
    settings = {
        **common,
        "chunk_tokens": k.chunk_tokens,
        "chunk_overlap": k.chunk_overlap,
        "rrf_k": k.rrf_k,
        "rerank_k": k.rerank_k,
        "reranker": k.reranker,
        "min_vector_score": k.min_vector_score,
        "min_keyword_coverage": k.min_keyword_coverage,
        "pack": h.pack,
    }
    return {"type": "store", "counts": k.count(tenant_id), "last_records": k.docs(view),
            "settings": settings}  # fmt: skip


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
