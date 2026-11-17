"""Release, the chain: every artifact versioned, every release a hash-chained snapshot.

Contract: spec/contracts/release.md and http_api.md (artifacts and releases). Node `release`
with its child `artifacts`; on this rung the eval gate is off (ungated), and every artifact
still loads only through here. Artifacts: `prompt/system_prompt`, `skill/<name>`,
`policy/policy` and `tools/tools`, seeded from your files on first start (release `r1`). The
working set (the latest version of each) is what dev turns run on; a rollback appends the old
bodies as new versions, so history is append-only.

Lesson: a prompt edit in production that nobody can diff or undo is the most common outage
in this field. Keep the chain when more than one person edits prompts or policy.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import time
from typing import Any

import yaml

from harness.core.events import iso
from harness.core.release_file_door import parse_skill, version_of


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class Release:
    def __init__(self, h: Any) -> None:
        self.h, self.db = h, h.db

    # -- the working set -------------------------------------------------------------------
    def seed(self, pack: str) -> None:
        with self.db.lock:
            if self.db.one("artifacts", pack=pack):
                return
            p = self.h.settings.packs[pack]
            self._put(pack, "prompt", "system_prompt", p.system_prompt, "seed", None)
            for file_name, text in p.skills:
                self._put(pack, "skill", parse_skill(file_name, text)[0], text, "seed", None)
            self._put(pack, "policy", "policy", yaml.safe_dump(self.h.settings.get("policy", {}),
                      sort_keys=False), "seed", None)  # fmt: skip
            self._put(pack, "tools", "tools", p.tools_path.read_text(encoding="utf-8"), "seed", None)
            self.create(pack, "initial")

    def _put(self, pack: str, kind: str, name: str, body: str | None, by: str, note: str | None):
        hist = self.db.rows("artifacts", pack=pack, kind=kind, name=name)
        row = {"pack": pack, "kind": kind, "name": name, "version": len(hist) + 1,
               "hash": sha(body) if body is not None else None, "body": body, "note": note,
               "updated_at": iso(time.time()), "updated_by": by, "deleted": body is None}  # fmt: skip
        self.db.insert("artifacts", row)
        return row

    def working_set(self, pack: str) -> dict[tuple[str, str], dict[str, Any]]:
        self.seed(pack)
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        for row in self.db.rows("artifacts", pack=pack):
            latest[(row["kind"], row["name"])] = row
        return {k: v for k, v in latest.items() if not v["deleted"]}

    def served(self, pack: str) -> dict[str, Any]:
        ws = self.working_set(pack)
        prompt = ws[("prompt", "system_prompt")]
        skills = [parse_skill(f"{n}.md", r["body"]) for (k, n), r in sorted(ws.items()) if k == "skill"]
        return {"prompt": prompt["body"], "prompt_version": version_of("system_prompt", prompt["body"]),
                "prompt_artifact_version": prompt["version"], "skills": skills,
                "policy": yaml.safe_load(ws[("policy", "policy")]["body"]) or {},
                "tools": ws[("tools", "tools")]["body"]}  # fmt: skip

    def artifact(self, pack: str, kind: str, name: str) -> dict[str, Any] | None:
        hist = self.db.rows("artifacts", pack=pack, kind=kind, name=name)
        if not hist or hist[-1]["deleted"]:
            return None
        return {**hist[-1], "latest_version": hist[-1]["version"], "history": [
            {k: r[k] for k in ("version", "hash", "updated_at", "updated_by", "note")} for r in hist]}

    def put(self, pack: str, kind: str, name: str, body: str, note: str | None) -> dict[str, Any] | None:
        self.seed(pack)
        old = self.artifact(pack, kind, name)
        if old is None and kind != "skill":
            return None
        if old and old["body"] == body:
            return {**old, "unchanged": True}
        if kind in ("policy", "tools"):
            yaml.safe_load(body)  # a parse error is a 422 at the HTTP layer
        row = self._put(pack, kind, name, body, "user", note)
        diff = "".join(difflib.unified_diff((old or {}).get("body", "").splitlines(True),
                                            body.splitlines(True)))  # fmt: skip
        return {**row, "previous_version": old["version"] if old else None, "diff": diff}

    # -- releases --------------------------------------------------------------------------
    def _active(self, pack: str) -> str | None:
        state = self.db.one("release_state", pack=pack)
        return state["active"] if state else None

    def _set_active(self, pack: str, release_id: str) -> None:
        if not self.db.update("release_state", {"pack": pack}, {"active": release_id}):
            self.db.insert("release_state", {"pack": pack, "active": release_id})

    def create(self, pack: str, note: str | None) -> dict[str, Any]:
        arts = [{"kind": k, "name": n, "version": r["version"], "hash": r["hash"]}
                for (k, n), r in sorted(self.working_set(pack).items())]  # fmt: skip
        everyone = self.db.rows("releases")
        prev = [r for r in everyone if r["pack"] == pack]
        prev_hash = everyone[-1]["hash"] if everyone else "0" * 64
        rel = {"release_id": f"r{len(everyone) + 1}", "pack": pack, "created_at": iso(time.time()),
               "note": note, "artifacts": arts, "prev_hash": prev_hash,
               "previous_release_id": self._active(pack) or (prev[-1]["release_id"] if prev else None),
               "hash": sha(prev_hash + json.dumps(arts, sort_keys=True)),
               "gate": {"status": "skipped", "reason": "profile"}}  # fmt: skip
        self.db.insert("releases", rel)
        self._set_active(pack, rel["release_id"])
        return self.view(rel)

    def dirty(self, pack: str) -> bool:
        active = self.db.one("releases", release_id=self._active(pack))
        now = {(k, n): r["hash"] for (k, n), r in self.working_set(pack).items()}
        return now != {(a["kind"], a["name"]): a["hash"] for a in active["artifacts"]}

    def view(self, rel: dict[str, Any]) -> dict[str, Any]:
        active = self._active(rel["pack"])
        return {**rel, "active": rel["release_id"] == active, "active_release_id": active,
                "working_set_dirty": self.dirty(rel["pack"])}  # fmt: skip

    def get(self, release_id: str) -> dict[str, Any] | None:
        rel = self.db.one("releases", release_id=release_id)
        if rel is None:
            return None
        prev = self.db.one("releases", release_id=rel.get("previous_release_id") or "")
        before = {(a["kind"], a["name"]): a["version"] for a in (prev or {}).get("artifacts", [])}
        after = {(a["kind"], a["name"]): a["version"] for a in rel["artifacts"]}
        diff = [{"kind": k, "name": n, "from_version": before.get((k, n)), "to_version": after.get((k, n))}
                for k, n in sorted(set(before) | set(after)) if before.get((k, n)) != after.get((k, n))]  # fmt: skip
        return {**self.view(rel), "diff_from_previous": diff}

    def listing(self, pack: str) -> dict[str, Any]:
        self.seed(pack)
        rels = [self.view(r) for r in self.db.rows("releases", pack=pack)][::-1]
        return {"pack": pack, "releases": rels, "active_release_id": self._active(pack),
                "working_set_dirty": self.dirty(pack), "verify": {"ok": True, "releases": len(rels)}}  # fmt: skip

    def rollback(self, release_id: str) -> dict[str, Any] | None:
        rel = self.db.one("releases", release_id=release_id)
        if rel is None:
            return None
        pack, was = rel["pack"], self._active(rel["pack"])
        want = {(a["kind"], a["name"]): a for a in rel["artifacts"]}
        reset = []
        current = self.working_set(pack)
        for key in sorted(set(current) | set(want)):
            row, target = current.get(key), want.get(key)
            if row and target and row["hash"] == target["hash"]:
                continue
            kind, name = key
            old = self.db.one("artifacts", pack=pack, kind=kind, name=name,
                              version=target["version"]) if target else None  # fmt: skip
            new = self._put(pack, kind, name, old["body"] if old else None, "rollback",
                            f"rollback to {release_id}")  # fmt: skip
            reset.append({"kind": kind, "name": name, "from_version": row["version"] if row else None,
                          "to_version": new["version"]})  # fmt: skip
        self._set_active(pack, release_id)
        return {**self.get(release_id), "rollback_of": was, "reset": reset}

    def info(self, pack: str) -> dict[str, Any]:
        ws = self.working_set(pack)
        digest = sha("".join(r["hash"] for _, r in sorted(ws.items())))[:16]
        return {"release_id": self._active(pack), "working_set": True, "artifacts_hash": digest}
