"""Harness profiles: a named harness shape, the nodes it switches off and the config it sets.

Contract: spec/harness.md section 10 ("Profiles"). Not a node: a profile is what a pack runs
with. The six profiles of the ladder live in `config/profiles/<id>.yaml`; a pack names one with
the `profile` key of its `pack.yaml` (spec/packs.md), and `HARNESSWALK_PROFILE` (a name or a
path), `--profile` or the dotted override `profile` replace it for every pack of the process.

A profile file:

    id        the profile id (the file stem when loaded by name)
    title     a short name for the UI chip
    summary   one sentence: what this shape is for
    rung      1 to 6, its place on the ladder (minimal 1, operated 6)
    disable   node ids switched off; every one must have `can_disable` in diagram.json
    stores    the context stores that exist: the five minus the disabled store nodes
    overrides dotted config keys under OVERRIDE_PREFIXES, set on top of the config files

Switched-off nodes are dimmed, never hidden: the served diagram keeps every node, and a node
that is off still enters and exits its span where it would have run, with status `skipped` and
`summary.disabled: true`. `off(mode)` is what a server in that mode actually turns off: public
mode keeps the controls (PUBLIC_KEEP) on whatever the profile says.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from harnesswalk import diagram
from harnesswalk.types import ConfigError

PROFILES_DIR = ("config", "profiles")
DEFAULT_PROFILE = "operated"
PROFILE_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
RUNGS = range(1, 7)
STORES = ("procedural", "session", "semantic", "episodic", "knowledge")
ALWAYS_STORES = ("procedural", "session")  # the system prompt and the history every turn needs
# Public mode never switches these off, whatever the profile says (harness.md section 7).
PUBLIC_KEEP = ("input_guardrails", "output_guardrails", "policy", "approval", "audit")
# Policy, approval and audit are on or off together in a profile: approval without audit
# cannot answer "who approved this", and policy without audit loses its denials.
GOVERNANCE = ("policy", "approval", "audit")
# The config a profile may set. Never the router, the models or the policy rules.
OVERRIDE_PREFIXES = (
    "context.",
    "loop.",
    "input_guardrails.",
    "output_guardrails.",
    "memory_writer.",
    "approval.timeout_s",
)
KEYS = ("id", "title", "summary", "rung", "disable", "stores", "overrides")


@dataclass(frozen=True)
class Profile:
    id: str
    title: str
    summary: str
    rung: int
    disable: frozenset[str] = frozenset()
    stores: tuple[str, ...] = STORES
    overrides: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)
    path: str | None = None

    def off(self, mode: str = "dev") -> frozenset[str]:
        """The nodes a server in `mode` switches off for this profile."""
        if mode == "public":
            return frozenset(n for n in self.disable if n not in PUBLIC_KEEP)
        return self.disable

    def enabled(self, node_id: str, mode: str = "dev") -> bool:
        return node_id not in self.off(mode)

    def stamp(self, mode: str = "dev") -> dict[str, Any]:
        """`turn_start.summary.profile`: which shape the turn ran on (events.md section 6)."""
        return {"id": self.id, "rung": self.rung, "off": sorted(self.off(mode))}

    def describe(self, mode: str = "dev") -> dict[str, Any]:
        """One item of `GET /v1/profiles`."""
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "rung": self.rung,
            "disable": sorted(self.disable),
            "off": sorted(self.off(mode)),
            "stores": list(self.stores),
            "overrides": dict(self.overrides),
        }


def profiles_dir(root: Path) -> Path:
    return Path(root).joinpath(*PROFILES_DIR)


def is_profile_file(data: Any) -> bool:
    """A harness profile has a `rung`; a conformance profile (a config overlay) does not."""
    return isinstance(data, dict) and "rung" in data


def parse(data: Any, *, name: str | None = None, path: str | None = None) -> Profile:
    """Validate a profile mapping. Raises ConfigError naming the first problem."""
    label = name or (data.get("id") if isinstance(data, dict) else None) or path or "?"
    where = f"profile {label!r}"
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must hold a mapping")
    extra = sorted(set(data) - set(KEYS))
    if extra:
        raise ConfigError(f"{where}: unknown keys {extra}")
    pid = data.get("id")
    if not isinstance(pid, str) or not PROFILE_ID.match(pid):
        raise ConfigError(f"{where}: id must match {PROFILE_ID.pattern}")
    if name is not None and pid != name:
        raise ConfigError(f"{where}: id {pid!r} must be the file name {name!r}")
    for key in ("title", "summary"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ConfigError(f"{where}: {key} must be a non-empty string")
    rung = data.get("rung")
    if not isinstance(rung, int) or isinstance(rung, bool) or rung not in RUNGS:
        raise ConfigError(f"{where}: rung must be an integer from 1 to 6")

    nodes = diagram.nodes_by_id()
    disable = data.get("disable") or []
    if not isinstance(disable, list) or not all(isinstance(n, str) for n in disable):
        raise ConfigError(f"{where}: disable must be a list of node ids")
    for node_id in disable:
        if node_id not in nodes:
            raise ConfigError(f"{where}: disable names unknown node {node_id!r}")
        if not nodes[node_id].get("can_disable"):
            raise ConfigError(f"{where}: node {node_id!r} cannot be disabled (can_disable)")
    off = frozenset(disable)
    for node_id in off:  # a node that is off takes its switchable children with it
        for child in (n for n in nodes.values() if n.get("parent") == node_id):
            if child.get("can_disable") and child["id"] not in off:
                raise ConfigError(
                    f"{where}: {node_id!r} is off, so its child {child['id']!r} must be off too"
                )
    governance = off & set(GOVERNANCE)
    if governance and governance != set(GOVERNANCE):
        raise ConfigError(f"{where}: policy, approval and audit are switched off together")

    stores = data.get("stores", list(STORES))
    if not isinstance(stores, list) or not all(s in STORES for s in stores):
        raise ConfigError(f"{where}: stores must list stores from {list(STORES)}")
    expected = [s for s in STORES if s not in off]
    if sorted(stores) != sorted(expected):
        raise ConfigError(f"{where}: stores must be {expected} (the stores not disabled)")

    overrides = data.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise ConfigError(f"{where}: overrides must be a mapping of dotted keys")
    for key in overrides:
        if not isinstance(key, str) or not key.startswith(OVERRIDE_PREFIXES):
            raise ConfigError(
                f"{where}: override {key!r} is not allowed (only {list(OVERRIDE_PREFIXES)})"
            )
    return Profile(
        id=pid,
        title=data["title"].strip(),
        summary=" ".join(data["summary"].split()),
        rung=rung,
        disable=off,
        stores=tuple(s for s in STORES if s in stores),
        overrides=dict(overrides),
        path=path,
    )


def _read(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read profile {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"profile {path} is not valid YAML: {exc}") from exc


def load(root: Path, ref: str | Path) -> Profile:
    """A profile by name (`config/profiles/<name>.yaml`) or by path (relative to `root`)."""
    ref_s = str(ref)
    if PROFILE_ID.match(ref_s):
        path = profiles_dir(root) / f"{ref_s}.yaml"
        if not path.is_file():
            known = ", ".join(sorted(p.stem for p in profiles_dir(root).glob("*.yaml")))
            raise ConfigError(f"no profile {ref_s!r} under config/profiles (known: {known})")
        return parse(_read(path), name=ref_s, path=str(path))
    path = Path(ref_s)
    if not path.is_absolute():
        path = Path(root) / path
    data = _read(path)
    if not is_profile_file(data):
        raise ConfigError(f"{ref_s} is not a harness profile (it has no rung)")
    return parse(data, path=str(path))


def all_profiles(root: Path) -> list[Profile]:
    """Every profile under config/profiles, by rung."""
    out = [parse(_read(p), name=p.stem, path=str(p)) for p in profiles_dir(root).glob("*.yaml")]
    return sorted(out, key=lambda p: (p.rung, p.id))


def lowest_rung_with(root: Path, nodes: list[str] | set[str], mode: str = "dev") -> Profile | None:
    """The lowest profile that has every node on (the Guide's "switch to" hint)."""
    for profile in all_profiles(root):
        if not (set(nodes) & profile.off(mode)):
            return profile
    return None
