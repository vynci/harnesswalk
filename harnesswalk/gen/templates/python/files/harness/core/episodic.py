"""Episodic memory: dated events, per tenant and pack, appended by the memory writer.

Contract: spec/contracts/store.md (`episodic`). Node: `episodic` (on from `remembered`).
Ranking: `0.7 * relevance + 0.3 * exp(-age_days / 30)`, relevance 1.0 on an entity match,
else the cosine of hash vectors; an event under `min_relevance` is left out however recent.

Lesson: "what happened last time" is a different question from "what is true", and it needs a
different ranking rule.
"""

from __future__ import annotations

import math
import time
import uuid
from datetime import datetime
from typing import Any

from harness.core.text import cosine, embed, entities


def _age_days(ts: Any) -> float:
    t = ts if isinstance(ts, (int, float)) else datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    return max(0.0, (time.time() - t) / 86400)


def retrieve(h: Any, turn: Any, query: str, budget: int) -> list[dict[str, Any]]:
    cfg = h.settings.get("stores.episodic", {})
    w = cfg.get("weights") or {"relevance": 0.7, "recency": 0.3}
    half, floor = float(cfg.get("half_life_days", 30)), float(cfg.get("min_relevance", 0.2))
    q, ents, pack = embed(query), entities(query), turn.data["pack"]
    scored = []
    for scope in ("_pack", turn.ids["tenant_id"]):
        for e in h.db.rows("episodes", tenant=scope, pack=pack):
            rel = 1.0 if ents & set(e.get("entities") or []) else cosine(q, embed(e["text"]))
            if rel < floor:
                continue
            score = w["relevance"] * rel + w["recency"] * math.exp(-_age_days(e["ts"]) / half)
            scored.append({"id": e["record_id"], "text": e["text"], "score": score})
    scored.sort(key=lambda x: (-x["score"], x["id"]))
    top_k = int((h.settings.get("context.retrieval.top_k") or {}).get("episodic", 5))
    return scored[:top_k]


def write(db: Any, tenant: str, pack: str, text: str, kind: str, outcome: str | None,
          session: str | None, turn_id: str | None) -> dict[str, Any]:  # fmt: skip
    rid = "ep_" + uuid.uuid4().hex[:16]
    db.insert("episodes", {"record_id": rid, "tenant": tenant, "pack": pack, "text": text,
                           "kind": kind, "outcome": outcome, "session": session, "turn": turn_id,
                           "ts": time.time(), "entities": sorted(entities(text))})  # fmt: skip
    return {"record_id": rid, "version": 1, "op": "create"}
