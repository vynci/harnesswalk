"""Release: the one door through which the harness loads its artifacts, and how they change.

Contract: spec/contracts/release.md (P4 additions). Node: `release` (LLM Ops, kind code).

`current()` is the door: `config/loader.py` and the procedural store read every prompt, skill,
policy, context, models and tools file through it. With a `Served` view (built here from the
artifact store) it answers from the served set; without one it reads the file, which is what
P1 to P3 did and what the read-only CLI commands still do.

The served set is the active release, or the working set (the latest version of every
artifact) when `release.use_working_set` is on, which is the default in dev so an edit applies
to the next turn. `ReleaseManager` seeds the store from the files at start-up, answers the
HTTP and CLI operations (edit, create, rollback, diff), and tells the harness when what it
serves has changed so it can rebuild the components that read artifacts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonschema
import yaml

from harnesswalk.nodes import register
from harnesswalk.ops.artifacts import (
    KINDS,
    SKILL_NAME,
    YAML_KINDS,
    ArtifactStore,
    Release,
    Source,
    Version,
    canonical,
    sha256,
    unified_diff,
)

if TYPE_CHECKING:
    from harnesswalk.config.loader import Settings

register("release", __name__)

CONFIG_PATHS = {
    "policy": "config/policy.yaml",
    "context": "config/context.yaml",
    "models": "config/models.yaml",
}
MAX_BODY_CHARS = 200_000
# Artifact names (the UI's): the system prompt and the tool file of the loaded pack, and the
# config files, each have one fixed name; skills are named after their file.
PROMPT_NAME = "system_prompt"
TOOLS_NAME = "tools"
FIXED_NAMES = {
    "prompt": PROMPT_NAME,
    "tools": TOOLS_NAME,
    "policy": "policy",
    "context": "context",
    "models": "models",
}

POLICY_RULE = {
    "type": "object",
    "required": ["id", "decision"],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "decision": {"enum": ["allow", "deny", "needs_approval"]},
        "modes": {"type": "array", "items": {"enum": ["dev", "public", "private"]}},
        "action_classes": {
            "type": "array",
            "items": {"enum": ["read", "write", "destructive", "external", "memory.write"]},
        },
        "tools": {"type": "array", "items": {"type": "string"}},
    },
}
POLICY_SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {"type": "array", "items": POLICY_RULE},
        "agent": {"type": "object"},
        "roles": {"type": "object"},
        "budgets": {"type": "object"},
        "approval": {"type": "object"},
    },
}


class ArtifactInvalid(ValueError):
    """A candidate body that fails validation (HTTP 422 invalid_artifact)."""


class ArtifactNotFound(KeyError):
    """No such kind or name (HTTP 404 artifact_not_found)."""


def version_of(name: str, content: str) -> str:
    """The content stamp traces and inspectors show: `<stem>@<sha256 prefix>`."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    return f"{Path(name).stem}@{digest}"


def path_of(kind: str, name: str, pack: str) -> str:
    """The repository path an artifact stands for."""
    if kind == "prompt":
        return f"datasets/{pack}/system_prompt.md"
    if kind == "skill":
        return f"datasets/{pack}/skills/{name}.md"
    if kind == "tools":
        return f"datasets/{pack}/tools.yaml"
    return CONFIG_PATHS[kind]


@dataclass
class Served:
    """What the harness loads: artifact bodies by repository path, plus the skills list."""

    pack: str
    files: dict[str, str] = field(default_factory=dict)
    versions: dict[str, int] = field(default_factory=dict)  # path -> artifact version
    skills: list[tuple[str, str, int]] = field(default_factory=list)  # (name, body, version)

    @classmethod
    def of(cls, pack: str, versions: list[Version]) -> Served:
        served = cls(pack)
        for v in versions:
            path = path_of(v.kind, v.name, pack)
            served.files[path] = v.body
            served.versions[path] = v.version
            if v.kind == "skill":
                served.skills.append((v.name, v.body, v.version))
        served.skills.sort()
        return served

    def read(self, artifact: str) -> str | None:
        return self.files.get(artifact)

    @property
    def prompt_path(self) -> str:
        return path_of("prompt", PROMPT_NAME, self.pack)


def current(root: Path, artifact: str, served: Served | None = None) -> tuple[str, str]:
    """The current content and content stamp of an artifact (a path relative to the repo root).

    With a served view, an artifact it holds comes from the store; anything else (a profile, a
    file that is not an artifact) is read from disk.
    """
    content = served.read(artifact) if served is not None else None
    if content is None:
        content = (root / artifact).read_text(encoding="utf-8")
    return content, version_of(artifact, content)


def render_skill(item: dict[str, Any]) -> str:
    """An inline profile skill as the markdown file it would be."""
    head = yaml.safe_dump(
        {"name": item["name"], "description": item.get("description", "")}, sort_keys=False
    )
    return f"---\n{head}---\n{item.get('body', '')}".rstrip() + "\n"


def sources(settings: Settings) -> list[Source]:
    """Every artifact the files (or the inline profile values) define, for seeding."""
    root = settings.root
    pack = settings.get("dataset", "_starter")
    out: list[Source] = []
    for kind, rel in CONFIG_PATHS.items():
        if (root / rel).is_file():
            out.append(Source(kind, kind, (root / rel).read_text(encoding="utf-8"), rel))
    inline = settings.get("procedural", {}) or {}
    pack_dir = root / "datasets" / pack
    if "system_prompt" in inline:
        out.append(Source("prompt", PROMPT_NAME, str(inline["system_prompt"]), None))
    elif (pack_dir / "system_prompt.md").is_file():
        rel = f"datasets/{pack}/system_prompt.md"
        out.append(Source("prompt", PROMPT_NAME, (root / rel).read_text(encoding="utf-8"), rel))
    if "skills" in inline:
        for item in inline["skills"] or []:
            out.append(Source("skill", item["name"], render_skill(item), None))
    else:
        for path in sorted((pack_dir / "skills").glob("*.md")):
            rel = str(path.relative_to(root))
            out.append(Source("skill", path.stem, path.read_text(encoding="utf-8"), rel))
    if (pack_dir / "tools.yaml").is_file():
        rel = f"datasets/{pack}/tools.yaml"
        out.append(Source("tools", TOOLS_NAME, (root / rel).read_text(encoding="utf-8"), rel))
    return out


def validate_body(kind: str, name: str, body: Any) -> None:
    """Checks that need nothing but the text. The harness then dry-runs the candidate."""
    if not isinstance(body, str):
        raise ArtifactInvalid("body must be a string")
    if len(body) > MAX_BODY_CHARS:
        raise ArtifactInvalid(f"body is over {MAX_BODY_CHARS} characters")
    if kind in ("prompt", "skill") and not body.strip():
        raise ArtifactInvalid(f"a {kind} cannot be empty")
    if kind == "skill" and body.startswith("---\n"):
        head, sep, _ = body[4:].partition("\n---\n")
        if not sep:
            raise ArtifactInvalid("the skill's front matter has no closing ---")
        try:
            meta = yaml.safe_load(head) or {}
        except yaml.YAMLError as exc:
            raise ArtifactInvalid(f"front matter is not YAML: {exc}") from exc
        if not isinstance(meta, dict):
            raise ArtifactInvalid("front matter must be a mapping")
        if meta.get("name") not in (None, name):
            raise ArtifactInvalid(f"front matter name {meta['name']!r} is not {name!r}")
    if kind in YAML_KINDS:
        try:
            data = yaml.safe_load(body)
        except yaml.YAMLError as exc:
            raise ArtifactInvalid(f"not valid YAML: {exc}") from exc
        if kind == "tools":
            if not isinstance(data, list):
                raise ArtifactInvalid("tools must be a YAML list of tool specs")
            from harnesswalk.execution.registry import ToolRegistry
            from harnesswalk.types import ConfigError

            try:  # on its own too: a profile may load other tool files instead of this one
                ToolRegistry().load_text(body, Path("tools.yaml"))
            except (ConfigError, ValueError, KeyError, TypeError, jsonschema.SchemaError) as exc:
                raise ArtifactInvalid(f"tools: {exc}") from exc
        elif not isinstance(data, dict):
            raise ArtifactInvalid(f"{kind} must be a YAML mapping")
        if kind == "policy":
            errors = sorted(
                jsonschema.Draft202012Validator(POLICY_SCHEMA).iter_errors(data),
                key=lambda e: list(e.path),
            )
            if errors:
                where = "/".join(str(p) for p in errors[0].path) or "policy"
                raise ArtifactInvalid(f"policy {where}: {errors[0].message}")


class Busy(Exception):
    """A change was asked for while a turn or an eval run is running (HTTP 409 turn_running)."""


class ReleaseManager:
    """The artifact store as the harness sees it: seeding, the served set, and changes."""

    def __init__(self, settings: Settings) -> None:
        self.pack = settings.get("dataset", "_starter")
        self.mode = settings.mode
        default_ws = settings.mode == "dev"
        self.use_working_set = bool(settings.get("release.use_working_set", default_ws))
        path = settings.resolve(settings.get("release.sqlite_path", "data/releases.db"))
        self.store = ArtifactStore(str(path))
        self.imported = self.store.sync(self.pack, sources(settings))
        if self.store.active_release_id(self.pack) is None:
            self.store.create_release(self.pack, by="seed", note="initial")
        elif self.imported and not self.use_working_set:
            # Files are what was deployed: outside dev a changed file is released at start-up.
            names = ", ".join(f"{v.kind}/{v.name}" for v in self.imported)
            self.store.create_release(self.pack, by="disk", note=f"imported at start-up: {names}")
        self.seen_generation = self.store.generation()
        self.served = self._compute_served()

    # -- what is served -------------------------------------------------------------------------

    def _release_versions(self, rel: Release) -> list[Version]:
        out = []
        for ref in rel.artifacts:
            v = self.store.get(self.pack, ref["kind"], ref["name"], ref["version"])
            if v is not None:
                out.append(v)
        return out

    def served_versions(self) -> list[Version]:
        if self.use_working_set:
            return self.store.working_set(self.pack)
        active = self.store.active_release_id(self.pack)
        rel = self.store.release(active) if active else None
        return self._release_versions(rel) if rel else self.store.working_set(self.pack)

    def _compute_served(self) -> Served:
        return Served.of(self.pack, self.served_versions())

    def served_hash(self, versions: list[Version] | None = None) -> str:
        versions = self.served_versions() if versions is None else versions
        return sha256(canonical(sorted((v.kind, v.name, v.hash) for v in versions)))[:16]

    def refresh(self) -> bool:
        """Recompute the served set. True when its content changed (the harness must reload)."""
        before = self.served_hash(self._served_list())
        self.served = self._compute_served()
        self.seen_generation = self.store.generation()
        return self.served_hash(self._served_list()) != before

    def _served_list(self) -> list[Version]:
        out = []
        for path, body in self.served.files.items():
            kind, name = self._kind_name(path)
            out.append(Version(kind, name, 0, sha256(body), body, "", ""))
        return out

    def _kind_name(self, path: str) -> tuple[str, str]:
        for kind, rel in CONFIG_PATHS.items():
            if path == rel:
                return kind, kind
        if path.endswith("/system_prompt.md"):
            return "prompt", PROMPT_NAME
        if path.endswith("/tools.yaml"):
            return "tools", TOOLS_NAME
        return "skill", Path(path).stem

    def changed_elsewhere(self) -> bool:
        """Another process wrote the store since we last looked."""
        return self.store.generation() != self.seen_generation

    def status(self) -> dict[str, Any]:
        active = self.store.active_release_id(self.pack)
        return {
            "active_release_id": active,
            "working_set_dirty": self.working_set_dirty(active),
            "use_working_set": self.use_working_set,
        }

    def working_set_dirty(self, active: str | None = None) -> bool:
        active = active or self.store.active_release_id(self.pack)
        rel = self.store.release(active) if active else None
        if rel is None:
            return True
        released = sorted((a["kind"], a["name"], a["hash"]) for a in rel.artifacts)
        working = sorted((v.kind, v.name, v.hash) for v in self.store.working_set(self.pack))
        return released != working

    def stamp(self) -> dict[str, Any]:
        """What a turn runs on, for `turn_start.summary.release`."""
        return {
            "release_id": self.store.active_release_id(self.pack),
            "working_set": self.use_working_set,
            "artifacts_hash": self.served_hash(self._served_list()),
        }

    # -- reading ------------------------------------------------------------------------------

    def check_kind(self, kind: str, name: str, *, creating: bool = False) -> None:
        if kind not in KINDS:
            raise ArtifactNotFound(f"no artifact kind {kind!r}")
        if kind == "skill":
            if not SKILL_NAME.match(name):
                raise ArtifactNotFound(f"skill name {name!r} is not valid")
            if not creating and self.store.get(self.pack, kind, name) is None:
                raise ArtifactNotFound(f"no skill {name!r}")
            return
        expected = FIXED_NAMES[kind]
        if name != expected or self.store.get(self.pack, kind, name) is None:
            raise ArtifactNotFound(f"no {kind} artifact named {name!r}")

    def listing(self) -> list[dict[str, Any]]:
        served = {(v.kind, v.name): v.version for v in self.served_versions()}
        out = []
        for v in self.store.working_set(self.pack):
            item = v.meta()
            item["served_version"] = served.get((v.kind, v.name))
            out.append(item)
        return out

    def artifact(self, kind: str, name: str, version: int | None = None) -> dict[str, Any]:
        self.check_kind(kind, name)
        v = self.store.get(self.pack, kind, name, version)
        if v is None:
            raise ArtifactNotFound(f"{kind}/{name} has no version {version}")
        latest = self.store.get(self.pack, kind, name)
        out = v.to_dict()
        out["latest_version"] = latest.version if latest else v.version
        out["path"] = path_of(kind, name, self.pack)
        out["history"] = [h.meta() for h in self.store.history(self.pack, kind, name)]
        return out

    # -- changing -------------------------------------------------------------------------------

    def candidate(self, kind: str, name: str, body: str) -> Served:
        """The served view with one artifact replaced, for the harness's dry run."""
        versions = [
            v for v in self.store.working_set(self.pack) if (v.kind, v.name) != (kind, name)
        ]
        versions.append(Version(kind, name, 0, sha256(body), body, "", ""))
        return Served.of(self.pack, versions)

    def edit(self, kind: str, name: str, body: str, *, by: str, note: str | None) -> dict[str, Any]:
        new, previous = self.store.put(self.pack, kind, name, body, by=by, note=note)
        unchanged = previous is not None and new.version == previous.version
        out = new.to_dict()
        out["path"] = path_of(kind, name, self.pack)
        out["previous_version"] = previous.version if previous is not None else None
        if unchanged:  # the same body again: no new version, nothing to diff
            out["unchanged"] = True
            out["diff"] = ""
        else:
            out["diff"] = unified_diff(
                previous.body if previous else None, body, path_of(kind, name, self.pack)
            )
        return out

    def create(self, *, by: str, note: str | None) -> Release:
        return self.store.create_release(self.pack, by=by, note=note)

    def rollback(self, release_id: str, *, by: str) -> tuple[Release, list[dict[str, Any]]]:
        rel = self.store.release(release_id)
        if rel is None or rel.pack != self.pack:
            raise KeyError(release_id)
        wanted = {(a["kind"], a["name"]): a for a in rel.artifacts}
        reset = []
        for v in self.store.working_set(self.pack, include_deleted=True):
            key = (v.kind, v.name)
            if key in wanted:
                old = self.store.get(self.pack, v.kind, v.name, wanted[key]["version"])
                if old is None or (old.hash == v.hash and not v.deleted):
                    continue
                new, _ = self.store.put(
                    self.pack,
                    v.kind,
                    v.name,
                    old.body,
                    by="rollback",
                    note=f"rollback to {release_id}",
                )
                reset.append(
                    {
                        "kind": v.kind,
                        "name": v.name,
                        "from_version": v.version,
                        "to_version": new.version,
                        "restored_version": old.version,
                    }
                )
            elif not v.deleted:
                new, _ = self.store.put(
                    self.pack,
                    v.kind,
                    v.name,
                    v.body,
                    by="rollback",
                    note=f"not in {release_id}",
                    deleted=True,
                )
                reset.append(
                    {
                        "kind": v.kind,
                        "name": v.name,
                        "from_version": v.version,
                        "to_version": new.version,
                        "deleted": True,
                    }
                )
        current = self.store.active_release_id(self.pack)
        self.store.activate(self.pack, release_id, by=by, rollback_of=current)
        return rel, reset

    # -- releases as the API shows them ---------------------------------------------------------

    def releases(self) -> dict[str, Any]:
        active = self.store.active_release_id(self.pack)
        rels = []
        for rel in self.store.releases(self.pack):
            item = rel.to_dict()
            item["active"] = rel.release_id == active
            rels.append(item)
        return {
            "pack": self.pack,
            "releases": rels,
            "active_release_id": active,
            "working_set_dirty": self.working_set_dirty(active),
            "use_working_set": self.use_working_set,
            "verify": self.store.verify(self.pack),
            "activations": self.store.activations(self.pack, 20),
        }

    def release_detail(self, release_id: str) -> dict[str, Any] | None:
        rel = self.store.release(release_id)
        if rel is None or rel.pack != self.pack:
            return None
        previous = self.store.previous_release(rel)
        out = rel.to_dict()
        out["active"] = rel.release_id == self.store.active_release_id(self.pack)
        out["previous_release_id"] = previous.release_id if previous else None
        out["diff_from_previous"] = self.diff(previous, rel)
        return out

    def diff(self, before: Release | None, after: Release) -> list[dict[str, Any]]:
        old = {(a["kind"], a["name"]): a for a in (before.artifacts if before else [])}
        new = {(a["kind"], a["name"]): a for a in after.artifacts}
        out = []
        for key in sorted(set(old) | set(new)):
            a, b = old.get(key), new.get(key)
            if a and b and a["hash"] == b["hash"]:
                continue
            kind, name = key
            body_a = self.store.get(self.pack, kind, name, a["version"]).body if a else None
            body_b = self.store.get(self.pack, kind, name, b["version"]).body if b else None
            out.append(
                {
                    "kind": kind,
                    "name": name,
                    "from_version": a["version"] if a else None,
                    "to_version": b["version"] if b else None,
                    "diff": unified_diff(body_a, body_b, path_of(kind, name, self.pack)),
                }
            )
        return out

    def close(self) -> None:
        self.store.close()


def released_summary(rel: Release, rollback_of: str | None = None) -> dict[str, Any]:
    """The summary of an `artifact_released` ops event (events.md section 6)."""
    out: dict[str, Any] = {
        "release_id": rel.release_id,
        "artifacts": rel.artifacts,
        "hash": rel.hash,
        "prev_hash": rel.prev_hash,
        "pack": rel.pack,
    }
    if rel.note:
        out["note"] = rel.note[:200]
    if rollback_of is not None:
        out["rollback_of"] = rollback_of
    return out


def body_from_json(value: Any) -> str:
    """PUT bodies arrive as JSON strings; anything else is a 400 upstream."""
    return value if isinstance(value, str) else json.dumps(value)


# -- operations: what the HTTP API and the CLI call --------------------------------------------
#
# Each one refuses while the harness is busy (a running turn or eval run), changes the store,
# emits its ops event on the caller's `ops` stream, and rebuilds the artifact-backed components
# when what the harness serves changed.


def _apply(harness: Any) -> bool:
    """Reload the harness when the served set changed. True when it did."""
    if harness.release.refresh():
        harness.reload()
        return True
    return False


def apply_edit(
    harness: Any, tenant_id: str, kind: str, name: str, body: Any, *, note: str | None = None
) -> dict[str, Any]:
    from harnesswalk.ops.ops_events import OPS_STREAM

    rm: ReleaseManager = harness.release
    rm.check_kind(kind, name, creating=kind == "skill")
    validate_body(kind, name, body)
    if harness.busy():
        raise Busy("a turn or an eval run is running; try again when it ends")
    try:
        harness.dry_run(rm.candidate(kind, name, body))
    except Exception as exc:  # what would break at load time is the learner's 422
        raise ArtifactInvalid(f"{type(exc).__name__}: {exc}") from exc
    out = rm.edit(kind, name, body, by=f"user:{tenant_id}", note=note)
    if not out.get("unchanged"):
        summary: dict[str, Any] = {
            "kind": kind,
            "name": name,
            "version": out["version"],
            "hash": out["hash"],
            "pack": rm.pack,
        }
        if out["previous_version"] is not None:
            summary["previous_version"] = out["previous_version"]
        if note:
            summary["note_chars"] = len(note)
        harness.ops.emit(tenant_id, OPS_STREAM, "artifact_changed", "release", "done", summary)
    out["applied"] = _apply(harness)
    out["release"] = rm.status()
    return out


def apply_create(harness: Any, tenant_id: str, *, note: str | None = None) -> dict[str, Any]:
    from harnesswalk.ops.ops_events import OPS_STREAM

    rm: ReleaseManager = harness.release
    if not rm.use_working_set and harness.busy():  # it changes what is served
        raise Busy("a turn or an eval run is running; try again when it ends")
    rel = rm.create(by=f"user:{tenant_id}", note=note)
    harness.ops.emit(
        tenant_id, OPS_STREAM, "artifact_released", "release", "done", released_summary(rel)
    )
    _apply(harness)
    return rm.release_detail(rel.release_id) or rel.to_dict()


def apply_rollback(harness: Any, tenant_id: str, release_id: str) -> dict[str, Any]:
    from harnesswalk.ops.ops_events import OPS_STREAM

    rm: ReleaseManager = harness.release
    if rm.store.release(release_id) is None:
        raise KeyError(release_id)
    if harness.busy():
        raise Busy("a turn or an eval run is running; try again when it ends")
    previous = rm.store.active_release_id(rm.pack)
    rel, reset = rm.rollback(release_id, by=f"user:{tenant_id}")
    harness.ops.emit(
        tenant_id,
        OPS_STREAM,
        "artifact_released",
        "release",
        "done",
        released_summary(rel, rollback_of=previous),
    )
    _apply(harness)
    detail = rm.release_detail(release_id) or rel.to_dict()
    return {**detail, "rollback_of": previous, "reset": reset, **rm.status()}
