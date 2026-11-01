"""Artifacts: the versioned store behind release.

Contract: spec/contracts/release.md (P4 additions). Node: `artifacts` (expert child of
`release`, kind store).

Everything the harness loads that a learner may change is an artifact, named by `(kind, name)`:

    prompt/system_prompt  datasets/<pack>/system_prompt.md (or the profile's inline prompt)
    skill/<name>          datasets/<pack>/skills/<name>.md (or the profile's inline skills)
    tools/tools           datasets/<pack>/tools.yaml
    policy/policy         config/policy.yaml
    context/context       config/context.yaml
    models/models         config/models.yaml

Every change appends a version (an integer from 1 per artifact) with the whole body and its
sha256. Nothing is ever updated in place: a rollback appends versions too, and an artifact that
a rollback removes gets a `deleted` version. The working set is the latest version of each
artifact. A release is a frozen list of `(kind, name, version, hash)` whose own hash covers the
previous release's hash, so the chain shows if a stored release was altered.

One SQLite file (`data/releases.db`). A `generation` counter goes up on every write, so a
server can see that another process (the CLI) changed the store and reload before its next
turn.

P5: every served set a turn ran on is kept by its hash (`served_sets`), so diagnose can say
which artifact versions a failing turn and its last passing run saw. A release carries its eval
gate result (`release_gates`), and the gate is part of the release hash when present. The
activation log is hash-chained per pack (`prev_hash`, `hash`).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from harnesswalk.adapters.storage.sqlite import Database
from harnesswalk.events import iso
from harnesswalk.nodes import register

register("artifacts", __name__)

# kind -> scope. Pack artifacts belong to one dataset pack; global ones to every pack.
KINDS = {
    "prompt": "pack",
    "skill": "pack",
    "tools": "pack",
    "policy": "global",
    "context": "global",
    "models": "global",
}
YAML_KINDS = ("policy", "context", "models", "tools")
SKILL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact_versions (
    pack TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    hash TEXT NOT NULL,
    body TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    note TEXT,
    PRIMARY KEY (pack, kind, name, version)
);
CREATE TABLE IF NOT EXISTS artifact_sources (
    pack TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    path TEXT,
    imported_at TEXT NOT NULL,
    PRIMARY KEY (pack, kind, name)
);
CREATE TABLE IF NOT EXISTS releases (
    seq INTEGER PRIMARY KEY,
    release_id TEXT NOT NULL UNIQUE,
    pack TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    note TEXT,
    artifacts TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    pack TEXT NOT NULL,
    release_id TEXT NOT NULL,
    at TEXT NOT NULL,
    by TEXT NOT NULL,
    rollback_of TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS served_sets (
    hash TEXT PRIMARY KEY,
    pack TEXT NOT NULL,
    refs TEXT NOT NULL,
    first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS release_gates (
    release_id TEXT PRIMARY KEY,
    gate TEXT NOT NULL
);
"""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def unified_diff(before: str | None, after: str | None, label: str) -> str:
    """A unified diff of two bodies; a missing side is an empty file."""
    lines = difflib.unified_diff(
        (before or "").splitlines(keepends=True),
        (after or "").splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    )
    out = "".join(line if line.endswith("\n") else line + "\n" for line in lines)
    return out


def scope_of(kind: str, pack: str) -> str:
    """The pack column of an artifact: the pack for pack kinds, '' for global ones."""
    return pack if KINDS[kind] == "pack" else ""


@dataclass
class Version:
    kind: str
    name: str
    version: int
    hash: str
    body: str
    updated_at: str
    updated_by: str
    note: str | None = None
    deleted: bool = False
    pack: str = ""

    def meta(self) -> dict[str, Any]:
        """Everything but the body (the listing shape)."""
        out = {
            "kind": self.kind,
            "name": self.name,
            "version": self.version,
            "hash": self.hash,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "note": self.note,
        }
        if self.deleted:
            out["deleted"] = True
        return out

    def ref(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "version": self.version, "hash": self.hash}

    def to_dict(self) -> dict[str, Any]:
        return {**self.meta(), "body": self.body}


@dataclass
class Source:
    """Where an artifact came from on disk (or inline in a profile), for seeding."""

    kind: str
    name: str
    body: str
    path: str | None = None


@dataclass
class Release:
    release_id: str
    pack: str
    created_at: str
    created_by: str
    note: str | None
    artifacts: list[dict[str, Any]]
    prev_hash: str
    hash: str
    seq: int = 0
    extra: dict[str, Any] = field(default_factory=dict)
    gate: dict[str, Any] | None = None  # P5: the eval gate's result (None before P5)

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "pack": self.pack,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "note": self.note,
            "artifacts": self.artifacts,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
            "gate": self.gate,
            **self.extra,
        }


def release_hash(fields: dict[str, Any]) -> str:
    """The release's hash. P5: a release's `gate`, when it has one, is hashed with it."""
    keys = ("release_id", "pack", "created_at", "created_by", "note", "artifacts", "prev_hash")
    body = {k: fields[k] for k in keys}
    if fields.get("gate") is not None:
        body["gate"] = fields["gate"]
    return sha256(canonical(body))


def activation_hash(row: dict[str, Any], prev_hash: str) -> str:
    """One link of the activation log (P5): which release became active, when, by whom."""
    keys = ("pack", "release_id", "at", "by", "rollback_of")
    return sha256(canonical({**{k: row.get(k) for k in keys}, "prev_hash": prev_hash}))


class ArtifactStore:
    """Versions, releases and the hash chain, in one SQLite file."""

    def __init__(self, path: str) -> None:
        self.db = Database(path, SCHEMA)
        self._chain_activations()

    # -- the activation log's chain (P5) --------------------------------------------------------

    def _chain_activations(self) -> None:
        """Add the chain columns to a pre-P5 store and chain any row that has no hash yet."""
        columns = {r["name"] for r in self.db.query("PRAGMA table_info(activations)")}
        if "hash" not in columns:
            with self.db.transaction() as conn:
                columns = {r[1] for r in conn.execute("PRAGMA table_info(activations)")}
                if "hash" not in columns:
                    conn.execute("ALTER TABLE activations ADD COLUMN prev_hash TEXT")
                    conn.execute("ALTER TABLE activations ADD COLUMN hash TEXT")
        if self.db.one("SELECT 1 FROM activations WHERE hash IS NULL LIMIT 1") is None:
            return
        with self.db.transaction() as conn:
            last: dict[str, str] = {}
            for row in conn.execute("SELECT * FROM activations ORDER BY seq").fetchall():
                prev = last.get(row["pack"], GENESIS)
                digest = row["hash"]
                if digest is None:
                    digest = activation_hash(dict(row), prev)
                    conn.execute(
                        "UPDATE activations SET prev_hash = ?, hash = ? WHERE seq = ?",
                        (prev, digest, row["seq"]),
                    )
                last[row["pack"]] = digest

    def verify_activations(self, pack: str) -> dict[str, Any]:
        rows = self.db.query("SELECT * FROM activations WHERE pack = ? ORDER BY seq", (pack,))
        prev = GENESIS
        for n, row in enumerate(rows, start=1):
            if row["prev_hash"] != prev or activation_hash(dict(row), prev) != row["hash"]:
                return {"ok": False, "activations": len(rows), "activation_broken_at": n}
            prev = row["hash"]
        return {"ok": True, "activations": len(rows)}

    # -- served sets (P5) -----------------------------------------------------------------------

    def record_served(self, digest: str, pack: str, refs: list[dict[str, Any]]) -> None:
        """Keep what a served-set hash stands for (the first time it is seen)."""
        self.db.execute(
            "INSERT OR IGNORE INTO served_sets VALUES (?, ?, ?, ?)",
            (digest, pack, canonical(refs), iso(time.time())),
        )

    def served_refs(self, digest: str | None) -> list[dict[str, Any]] | None:
        """The artifact versions a served-set hash stands for, or None when it was never kept."""
        if not digest:
            return None
        row = self.db.one("SELECT refs FROM served_sets WHERE hash = ?", (digest,))
        return json.loads(row["refs"]) if row else None

    # -- generation ----------------------------------------------------------------------------

    def generation(self) -> int:
        row = self.db.one("SELECT value FROM meta WHERE key = 'generation'")
        return int(row["value"]) if row else 0

    def _bump(self) -> None:
        self.db.execute(
            "INSERT INTO meta VALUES ('generation', '1') ON CONFLICT(key)"
            " DO UPDATE SET value = CAST(value AS INTEGER) + 1"
        )

    # -- versions ------------------------------------------------------------------------------

    @staticmethod
    def _version(row: Any) -> Version:
        return Version(
            kind=row["kind"],
            name=row["name"],
            version=row["version"],
            hash=row["hash"],
            body=row["body"],
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
            note=row["note"],
            deleted=bool(row["deleted"]),
            pack=row["pack"],
        )

    def get(self, pack: str, kind: str, name: str, version: int | None = None) -> Version | None:
        scope = scope_of(kind, pack)
        if version is None:
            row = self.db.one(
                "SELECT * FROM artifact_versions WHERE pack = ? AND kind = ? AND name = ?"
                " ORDER BY version DESC LIMIT 1",
                (scope, kind, name),
            )
        else:
            row = self.db.one(
                "SELECT * FROM artifact_versions WHERE pack = ? AND kind = ? AND name = ?"
                " AND version = ?",
                (scope, kind, name, version),
            )
        return self._version(row) if row else None

    def history(self, pack: str, kind: str, name: str) -> list[Version]:
        rows = self.db.query(
            "SELECT * FROM artifact_versions WHERE pack = ? AND kind = ? AND name = ?"
            " ORDER BY version DESC",
            (scope_of(kind, pack), kind, name),
        )
        return [self._version(r) for r in rows]

    def working_set(self, pack: str, *, include_deleted: bool = False) -> list[Version]:
        """The latest version of every artifact of this pack and of the global kinds."""
        rows = self.db.query(
            "SELECT v.* FROM artifact_versions v JOIN ("
            " SELECT pack, kind, name, MAX(version) AS version FROM artifact_versions"
            " WHERE pack IN (?, '') GROUP BY pack, kind, name) last"
            " ON v.pack = last.pack AND v.kind = last.kind AND v.name = last.name"
            " AND v.version = last.version ORDER BY v.kind, v.name",
            (pack,),
        )
        out = [self._version(r) for r in rows]
        # A global row can only belong to a global kind and a pack row to a pack kind.
        out = [v for v in out if v.pack == scope_of(v.kind, pack)]
        return out if include_deleted else [v for v in out if not v.deleted]

    def put(
        self,
        pack: str,
        kind: str,
        name: str,
        body: str,
        *,
        by: str,
        note: str | None = None,
        deleted: bool = False,
    ) -> tuple[Version, Version | None]:
        """Append a version. Returns (the new latest, the one before). Unchanged bodies are not
        appended: the latest comes back as both."""
        previous = self.get(pack, kind, name)
        digest = sha256(body)
        if previous is not None and previous.hash == digest and previous.deleted == deleted:
            return previous, previous
        version = (previous.version if previous else 0) + 1
        now = iso(time.time())
        self.db.execute(
            "INSERT INTO artifact_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (scope_of(kind, pack), kind, name, version, digest, body, int(deleted), now, by, note),
        )
        self._bump()
        return self.get(pack, kind, name), previous  # type: ignore[return-value]

    def sync(self, pack: str, sources: list[Source]) -> list[Version]:
        """Seed new artifacts and import ones whose source changed since the last import."""
        imported: list[Version] = []
        for src in sources:
            scope = scope_of(src.kind, pack)
            digest = sha256(src.body)
            row = self.db.one(
                "SELECT source_hash FROM artifact_sources WHERE pack = ? AND kind = ? AND name = ?",
                (scope, src.kind, src.name),
            )
            if row is not None and row["source_hash"] == digest:
                continue
            existing = self.get(pack, src.kind, src.name)
            by = "seed" if existing is None else "disk"
            note = "seeded from " if existing is None else "changed on disk: "
            new, previous = self.put(
                pack, src.kind, src.name, src.body, by=by, note=note + (src.path or "the profile")
            )
            if previous is None or new.version != previous.version:
                imported.append(new)
            self.db.execute(
                "INSERT OR REPLACE INTO artifact_sources VALUES (?, ?, ?, ?, ?, ?)",
                (scope, src.kind, src.name, digest, src.path, iso(time.time())),
            )
        return imported

    # -- releases -------------------------------------------------------------------------------

    def _release(self, row: Any) -> Release:
        gate = self.db.one(
            "SELECT gate FROM release_gates WHERE release_id = ?", (row["release_id"],)
        )
        return Release(
            release_id=row["release_id"],
            pack=row["pack"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            note=row["note"],
            artifacts=json.loads(row["artifacts"]),
            prev_hash=row["prev_hash"],
            hash=row["hash"],
            seq=row["seq"],
            gate=json.loads(gate["gate"]) if gate else None,
        )

    def releases(self, pack: str) -> list[Release]:
        rows = self.db.query("SELECT * FROM releases WHERE pack = ? ORDER BY seq DESC", (pack,))
        return [self._release(r) for r in rows]

    def release(self, release_id: str) -> Release | None:
        row = self.db.one("SELECT * FROM releases WHERE release_id = ?", (release_id,))
        return self._release(row) if row else None

    def previous_release(self, rel: Release) -> Release | None:
        row = self.db.one(
            "SELECT * FROM releases WHERE pack = ? AND seq < ? ORDER BY seq DESC LIMIT 1",
            (rel.pack, rel.seq),
        )
        return self._release(row) if row else None

    def create_release(
        self,
        pack: str,
        *,
        by: str,
        note: str | None = None,
        gate: dict[str, Any] | None = None,
    ) -> Release:
        """Snapshot the working set and make it the active release. P5: with the eval gate's
        result, which the release hash covers."""
        last = self.db.one("SELECT hash, seq FROM releases ORDER BY seq DESC LIMIT 1")
        last_in_pack = self.db.one(
            "SELECT hash FROM releases WHERE pack = ? ORDER BY seq DESC LIMIT 1", (pack,)
        )
        seq = (last["seq"] if last else 0) + 1
        fields = {
            "release_id": f"r{seq}",
            "pack": pack,
            "created_at": iso(time.time()),
            "created_by": by,
            "note": note,
            "artifacts": [v.ref() for v in self.working_set(pack)],
            "prev_hash": last_in_pack["hash"] if last_in_pack else GENESIS,
            "gate": gate,
        }
        digest = release_hash(fields)
        self.db.execute(
            "INSERT INTO releases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                seq,
                fields["release_id"],
                pack,
                fields["created_at"],
                by,
                note,
                canonical(fields["artifacts"]),
                fields["prev_hash"],
                digest,
            ),
        )
        if gate is not None:
            self.db.execute(
                "INSERT OR REPLACE INTO release_gates VALUES (?, ?)",
                (fields["release_id"], canonical(gate)),
            )
        self.activate(pack, fields["release_id"], by=by)
        return self.release(fields["release_id"])  # type: ignore[return-value]

    def activate(
        self, pack: str, release_id: str, *, by: str, rollback_of: str | None = None
    ) -> None:
        row = {
            "pack": pack,
            "release_id": release_id,
            "at": iso(time.time()),
            "by": by,
            "rollback_of": rollback_of,
        }
        with self.db.transaction() as conn:
            last = conn.execute(
                "SELECT hash FROM activations WHERE pack = ? ORDER BY seq DESC LIMIT 1", (pack,)
            ).fetchone()
            prev = last["hash"] if last and last["hash"] else GENESIS
            conn.execute(
                "INSERT INTO activations (pack, release_id, at, by, rollback_of, prev_hash, hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pack, release_id, row["at"], by, rollback_of, prev, activation_hash(row, prev)),
            )
        self._bump()

    def active_release_id(self, pack: str) -> str | None:
        row = self.db.one(
            "SELECT release_id FROM activations WHERE pack = ? ORDER BY seq DESC LIMIT 1", (pack,)
        )
        return row["release_id"] if row else None

    def activations(self, pack: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT release_id, at, by, rollback_of FROM activations WHERE pack = ?"
            " ORDER BY seq DESC LIMIT ?",
            (pack, limit),
        )
        return [dict(r) for r in rows]

    def verify(self, pack: str) -> dict[str, Any]:
        """Recompute every release hash of the pack, oldest first, and check the links."""
        prev = GENESIS
        rels = list(reversed(self.releases(pack)))
        for rel in rels:
            fields = rel.to_dict()
            if rel.prev_hash != prev or release_hash(fields) != rel.hash:
                return {"ok": False, "releases": len(rels), "broken_at": rel.release_id}
            # The chain covers the artifact hashes; the stored bodies must still match them, or
            # an edited row would be served while the chain looked fine.
            for ref in rel.artifacts:
                v = self.get(pack, ref["kind"], ref["name"], ref["version"])
                if v is None or v.hash != ref["hash"] or sha256(v.body) != v.hash:
                    return {
                        "ok": False,
                        "releases": len(rels),
                        "broken_at": rel.release_id,
                        "artifact": f"{ref['kind']}/{ref['name']}@v{ref['version']}",
                    }
            prev = rel.hash
        chain = self.verify_activations(pack)
        if not chain["ok"]:
            return {"releases": len(rels), **chain}
        return {"ok": True, "releases": len(rels), "activations": chain["activations"]}

    def close(self) -> None:
        self.db.close()
