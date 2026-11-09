"""Semantic memory: durable facts, per tenant and pack, written only by the memory writer.

Contract: spec/contracts/store.md (`semantic`). Node: `semantic` (on from `remembered`).
Ranking: an exact key or entity match scores 1.0, else the cosine of hash vectors; under
`stores.semantic.min_score` a fact is left out. A tenant's fact shadows a pack seed's.

Lesson: precision over recall. One wrong fact in every later context does more harm than a
fact that was not found.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from harness.core.text import cosine, embed, entities

PACK_SCOPE = "_pack"  # seeds of the pack, read by every tenant that runs it


def retrieve(h: Any, turn: Any, query: str, budget: int) -> list[dict[str, Any]]:
    tenant, pack = turn.ids["tenant_id"], turn.data["pack"]
    facts: dict[str, dict[str, Any]] = {}
    for scope in (PACK_SCOPE, tenant):  # the tenant's own fact with the same key wins
        for f in h.db.rows("facts", tenant=scope, pack=pack):
            facts[f["key"].lower()] = f
    q, ents, low = embed(query), entities(query), query.lower()
    floor = float(h.settings.get("stores.semantic.min_score", 0.35))
    scored = []
    for f in facts.values():
        exact = f["key"].lower() in low or bool(ents & set(f.get("entities") or []))
        score = 1.0 if exact else cosine(q, embed(f"{f['key']} {f['value']}"))
        if score >= floor:
            scored.append({"id": f["record_id"], "text": f"{f['key']}: {f['value']}",
                           "score": score, "entity": exact})  # fmt: skip
    scored.sort(key=lambda x: (-x["score"], x["id"]))
    top_k = int((h.settings.get("context.retrieval.top_k") or {}).get("semantic", 5))
    return scored[:top_k]


def write(db: Any, tenant: str, pack: str, key: str, value: str, source: str) -> dict[str, Any]:
    """Only the memory writer calls this. An update keeps the old version (versioned)."""
    old = [f for f in db.rows("facts", tenant=tenant, pack=pack) if f["key"].lower() == key.lower()]
    version = (old[-1]["version"] + 1) if old else 1
    rid = old[-1]["record_id"] if old else "fact_" + uuid.uuid4().hex[:16]
    row = {"record_id": rid, "tenant": tenant, "pack": pack, "key": key, "value": value,
           "entities": sorted(entities(f"{key} {value}")), "version": version, "source": source,
           "updated": time.time()}  # fmt: skip
    if old:
        db.insert("fact_versions", old[-1])
        db.update("facts", {"record_id": rid}, row)
    else:
        db.insert("facts", row)
    return {"record_id": rid, "version": version, "op": "update" if old else "create"}
