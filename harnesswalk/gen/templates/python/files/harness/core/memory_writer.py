"""Memory writer: the single gated write path into semantic and episodic memory.

Contract: spec/contracts/memory_writer.md. Node: `memory_writer` (on from `remembered`). It
runs after the reply and before trace, only when the turn has proposals: the model's
`remember` calls, and one short episode per turn when `episodic` is a configured store.
Every proposal is redacted, scored for injection, checked by policy (class `memory.write`) and
for duplicates, then gated: `auto` in dev, `review` when it looks like an instruction.

Lesson: one hallucinated "fact" becomes permanent context for every later turn. Nothing else
in the harness may write these two stores.
"""

from __future__ import annotations

import uuid
from typing import Any

from harness.core import episodic, semantic
from harness.core.input_guardrails import injection_score, redact
from harness.core.policy import decide_class


def run(h: Any, turn: Any, proposals: list[dict[str, Any]]) -> None:
    cfg = h.settings.get("memory_writer", {})
    tenant, pack = turn.ids["tenant_id"], turn.data["pack"]
    written = rejected = pending = model_seen = 0
    rejections: list[dict[str, str]] = []
    rule = None
    with turn.span("memory_writer") as span:
        for p in proposals:
            pid = "prop_" + uuid.uuid4().hex[:16]
            text = p.get("text") or f"{p.get('key')}: {p.get('value')}"
            text, _ = redact(text, {}, h.pack_patterns(turn))
            reason = None
            model_seen += p["source"] == "loop"
            if turn.is_off(p["store"]):
                reason = "store_off"
            elif tenant.startswith("_"):
                reason = "reserved_tenant"
            elif p["source"] == "loop" and model_seen > int(cfg.get("max_proposals_per_turn", 3)):
                reason = "too_many"
            score, signals = injection_score(text)
            gate = "auto"
            if score >= float((cfg.get("injection") or {}).get("review_threshold", 0.45)):
                gate = "review"
            if score >= float((cfg.get("injection") or {}).get("block_threshold", 0.8)):
                reason = "injection: " + ", ".join(signals)
            d = decide_class(h.policy_cfg(turn), h.settings.mode, turn.data.get("roles") or ["user"],
                             "remember", "memory.write")  # fmt: skip
            rule = d["rule"]
            if d["decision"] == "deny" and not turn.is_off("policy"):
                reason = reason or f"policy: {d['rule']}"
            if p["store"] == "semantic" and any(
                    f["key"].lower() == str(p.get("key")).lower() and f["value"] == p.get("value")
                    for f in h.db.rows("facts", tenant=tenant, pack=pack)):  # fmt: skip
                reason = reason or "duplicate"
            if reason == "store_off":
                rejected += 1
                rejections.append({"proposal_id": pid, "reason": reason})
                continue
            turn.emit("memory_proposed", "memory_writer", "waiting", summary={
                "proposal_id": pid, "store": p["store"], "gate": gate, "preview": text[:120],
                "source": p["source"], **({"injection": score} if signals else {})})  # fmt: skip
            row = {"proposal_id": pid, "tenant": tenant, "pack": pack, "store": p["store"],
                   "content": p, "gate": gate, "status": "pending"}  # fmt: skip
            if reason:
                rejected += 1
                rejections.append({"proposal_id": pid, "reason": reason})
                h.db.insert("proposals", {**row, "status": "rejected", "reason": reason})
                continue
            if gate == "review":
                pending += 1
                h.db.insert("proposals", row)
                continue
            if p["store"] == "semantic":
                key, _ = redact(str(p["key"]), {}, h.pack_patterns(turn))
                value, _ = redact(str(p["value"]), {}, h.pack_patterns(turn))
                out = semantic.write(h.db, tenant, pack, key, value, p["source"])
            else:
                out = episodic.write(h.db, tenant, pack, text, p.get("kind", "event"),
                                     p.get("outcome"), turn.ids["session_id"], turn.ids["turn_id"])  # fmt: skip
            written += 1
            h.db.insert("proposals", {**row, "status": "accepted", **out})
            turn.emit("memory_written", "memory_writer", "done", summary={
                "proposal_id": pid, "store": p["store"], **out})  # fmt: skip
        span.set(proposed=len(proposals), written=written, rejected=rejected, pending=pending,
                 gate="auto", policy_rule=rule, rejections=rejections[:5] or None)  # fmt: skip
