"""The harness: every store and component, built once per process from Settings.

This file is the wiring, nothing else: which nodes exist (GENERATED in config.py), which
stores back them and which artifacts each pack serves. Read it first to see the whole shape.
"""

from __future__ import annotations

#% if has("semantic")
import json
#% endif
import threading
from typing import Any

import yaml

from harness.core import config
from harness.core.db import DB
#% if has("knowledge")
from harness.core.knowledge import KnowledgeStore
#% endif
from harness.core.model_layer import ModelLayer
#% if release == "chain"
from harness.core.release_chain import Release
#% else
from harness.core.release_file_door import Release
#% endif
from harness.core.session import SessionStore
from harness.core.tools import Registry
from harness.core.trace import TraceStore


class Harness:
    def __init__(self, settings: config.Settings) -> None:
        self.settings = settings
        self.db = DB(settings.data_dir)
        self.trace = TraceStore(self.db)
        self.sessions = SessionStore(self.db)
        self.models = ModelLayer(settings)
        self.release = Release(self)
        self.live: dict[str, Any] = {}  # turn_id -> Turn, for resume, cancel and approvals
        self.lock = threading.RLock()
        self._registries: dict[str, Registry] = {}
#% if has("knowledge")
        self.knowledge = KnowledgeStore()
#% endif
        for pack in settings.packs.values():
            self._seed(pack)

    def _seed(self, pack: config.Pack) -> None:
        """Load the pack's seed facts and events into its shared scope, once."""
#% if has("knowledge")
        self.knowledge.ingest(pack.id, pack.docs_dir)
#% endif
#% if has("semantic")
        if pack.seeds_dir and not self.db.one("seeded", pack=pack.id):
            from harness.core import episodic, semantic

            facts, events = pack.seeds_dir / "facts.jsonl", pack.seeds_dir / "episodes.jsonl"
            for line in (facts.read_text().splitlines() if facts.exists() else []):
                if line.strip():
                    f = json.loads(line)
                    semantic.write(self.db, "_pack", pack.id, f["key"], str(f["value"]), "seed")
            for line in (events.read_text().splitlines() if events.exists() else []):
                if line.strip():
                    e = json.loads(line)
                    episodic.write(self.db, "_pack", pack.id, e["text"], e.get("kind", "event"),
                                   e.get("outcome"), None, None)  # fmt: skip
            self.db.insert("seeded", {"pack": pack.id})
#% endif

    def registry_for(self, pack: str) -> Registry:
        text = self.release.served(pack)["tools"]
        if text not in self._registries:
            builtins = list(self.settings.get("tools.builtins", ["read_skill", "remember", "read_doc"]))
            self._registries[text] = Registry(text, builtins)
        return self._registries[text]

    def policy_cfg(self, turn: Any) -> dict[str, Any]:
        return self.release.served(turn.data["pack"])["policy"]

    def pack_patterns(self, turn: Any) -> list[dict[str, Any]]:
        path = self.settings.packs[turn.data["pack"]].dir / "guardrails.yaml"
        raw = yaml.safe_load(path.read_text()) if path.exists() else {}
        return (((raw or {}).get("pii") or {}).get("national_id") or {}).get("patterns") or []

    def doc_index(self, turn: Any) -> list[tuple[str, str]]:
#% if has("knowledge")
        if not turn.is_off("knowledge") and "knowledge" in self.settings.get("context.stores", []):
            docs = self.knowledge.docs.get(turn.data["pack"], {})
            return [(d, v["title"]) for d, v in sorted(docs.items())]
#% endif
        return []

    def run_builtin(self, turn: Any, call: dict[str, Any]) -> Any:
        """The harness's own tools (spec/conformance/harness_tools.yaml)."""
        args = call.get("arguments") or {}
        if call["name"] == "remember":  # running it writes nothing: it becomes a proposal
            return {"proposed": True, "kind": args.get("kind"), "note": "the memory writer gates it"}
        if call["name"] == "read_skill":
            for name, _, body in self.release.served(turn.data["pack"])["skills"]:
                if name == args.get("name"):
                    return body
            return {"error": "unknown_skill"}
#% if has("knowledge")
        if call["name"] == "read_doc":
            doc = self.knowledge.docs.get(turn.data["pack"], {}).get(args.get("doc"))
            return doc["text"][:12000] if doc else {"error": "unknown_doc"}
#% endif
        return {"error": "unknown_tool"}
