"""Settings: the config files, the profile, the pack, and the conformance overlay.

Where things come from, in order (later wins): `config/harness.yaml` and `policy/policy.yaml`
(yours), a config overlay when `--profile` names a file without a `rung` (the conformance
suite's `spec/conformance/profile.yaml`), the harness profile's `overrides`, then `--set
key=<json>` and `HARNESSWALK_CONFORMANCE_CONFIG`. The harness profile is the dotted override
`profile`, else `--profile` when it names a profile, else the one this repo was generated for.

Lesson: a profile is the node set (spec/harness.md section 10). This repo only contains code
for the nodes in GENERATED, so it refuses a profile that switches on a node it does not have.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from harness.core.events import DIAGRAM, NODES

ROOT = Path(__file__).resolve().parents[2]
SPEC_VERSION = "0.2.0"
VERSION = "@@template_version@@"
PROFILE_ID = "@@profile@@"
PACK_ID = "@@pack@@"
GENERATED = set(@@generated@@)  # the nodes this repo has code for
PROVIDERS = @@providers@@
RELEASE = "@@release@@"  # file_door | chain
OVERLAY_DEFAULT_PACK = "_starter"  # the conformance suite's default pack (pack_switch)


class ConfigError(Exception):
    pass


def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def dotted(cfg: dict[str, Any], key: str, value: Any) -> None:
    *head, last = key.split(".")
    for part in head:
        cfg = cfg.setdefault(part, {})
    cfg[last] = value


def read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None


@dataclass
class Profile:
    id: str
    rung: int
    title: str
    summary: str
    disable: set[str]
    raw: dict[str, Any]

    def off(self, mode: str = "dev") -> set[str]:
        return set(self.disable)  # dev only: public mode is never generated


def all_profiles() -> list[Profile]:
    out = []
    for path in sorted((ROOT / "config" / "profiles").glob("*.yaml")):
        raw = read_yaml(path)
        out.append(Profile(raw["id"], raw["rung"], raw.get("title", raw["id"]),
                           raw.get("summary", ""), set(raw.get("disable") or []), raw))  # fmt: skip
    return sorted(out, key=lambda p: p.rung)


def load_profile(name: str) -> Profile:
    found = [p for p in all_profiles() if p.id == name]
    if not found:
        raise ConfigError(f"unknown profile {name!r}")
    profile = found[0]
    can_disable = {i for i, n in NODES.items() if n.get("can_disable")}
    missing = sorted((can_disable - profile.disable) - GENERATED)
    if missing:
        raise ConfigError(f"profile {name} switches on {', '.join(missing)}, which this repo was"
                          f" generated without (profile {PROFILE_ID}); run harnesswalk init again")
    return profile


@dataclass
class Pack:
    id: str
    dir: Path
    card: dict[str, Any]
    system_prompt: str
    skills: list[tuple[str, str]]  # (file name, text)
    tools_path: Path
    docs_dir: Path | None
    seeds_dir: Path | None
    golden_path: Path | None
    fixture_dirs: list[Path] = field(default_factory=list)


@dataclass
class Settings:
    cfg: dict[str, Any]
    mode: str
    provider: str
    fixture: str
    data_dir: Path
    profile: Profile
    packs: dict[str, Pack]
    default_pack: str
    overlay: Path | None = None

    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self.cfg
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _repo_pack() -> Pack:
    d = ROOT / "packs" / PACK_ID
    skills = [(p.name, p.read_text(encoding="utf-8")) for p in sorted((ROOT / "skills").glob("*.md"))]
    return Pack(PACK_ID, d, read_yaml(d / "pack.yaml") or {},
                (ROOT / "prompts" / "system_prompt.md").read_text(encoding="utf-8"), skills,
                ROOT / "tools" / "tools.yaml", d / "docs", d, d / "golden.jsonl",
                [d / "fixtures" / "fake"])  # fmt: skip


def _overlay_packs(overlay: dict[str, Any], base: Path) -> dict[str, Pack]:
    """Every pack under the overlay's `packs.roots`; the overlay's inline prompt, tools,
    documents, seeds and golden set apply to all of them (spec/conformance/profile.yaml)."""
    proc = overlay.get("procedural") or {}
    skills = [(f"{s['name']}.md", f"---\nname: {s['name']}\ndescription: {s.get('description', '')}"
               f"\n---\n{s.get('body', '')}") for s in proc.get("skills") or []]  # fmt: skip
    stores = overlay.get("stores") or {}
    fx = ((overlay.get("models") or {}).get("providers") or {}).get("fake") or {}

    def rel(value: Any) -> Path | None:
        return (base / value) if value else None

    out: dict[str, Pack] = {}
    for root in (overlay.get("packs") or {}).get("roots") or []:
        for d in sorted((base / root).glob("*")):
            if d.is_dir() and (d / "pack.yaml").exists() and d.name not in out:
                out[d.name] = Pack(d.name, d, read_yaml(d / "pack.yaml") or {},
                                   proc.get("system_prompt", ""), skills,
                                   rel(overlay.get("tools")) or ROOT / "tools" / "tools.yaml",
                                   rel((stores.get("knowledge") or {}).get("docs_dir")),
                                   rel(stores.get("seeds_dir")),
                                   rel((overlay.get("evals") or {}).get("golden_path")),
                                   [p for p in [rel(fx.get("fixture_dir"))] if p])  # fmt: skip
    return out


def load(args: dict[str, Any] | None = None, env: dict[str, str] | None = None) -> Settings:
    """Settings from CLI arguments (`profile`, `set`, `mode`, `provider`, `fixture`, `data`)
    and the HARNESSWALK_* variables the conformance runner sets."""
    args, env = dict(args or {}), dict(os.environ if env is None else env)
    cfg = read_yaml(ROOT / "config" / "harness.yaml") or {}
    cfg["policy"] = read_yaml(ROOT / "policy" / "policy.yaml") or {}
    overrides: dict[str, Any] = {}
    if env.get("HARNESSWALK_CONFORMANCE_CONFIG"):
        overrides.update(json.loads(env["HARNESSWALK_CONFORMANCE_CONFIG"]))
    for item in args.get("set") or []:
        key, _, value = item.partition("=")
        overrides[key] = json.loads(value)
    chosen = overrides.pop("profile", None)  # 10.2: the dotted override wins
    profile_arg = args.get("profile") or env.get("HARNESSWALK_PROFILE")
    overlay_path, profile_name = None, PROFILE_ID
    if profile_arg and str(profile_arg).endswith((".yaml", ".yml")):
        path = Path(profile_arg).resolve()
        raw = read_yaml(path) or {}
        if "rung" in raw:
            profile_name = raw["id"]
        else:
            overlay_path = path
            for key in ("policy", "approval"):  # the overlay's policy merges over yours
                if key in raw:
                    raw[key] = merge(cfg.get(key) or {}, raw[key])
            cfg = merge(cfg, {k: v for k, v in raw.items() if k != "overridable"})
    elif profile_arg:
        profile_name = str(profile_arg)
    profile_name = chosen or profile_name
    profile = load_profile(profile_name)
    for key, value in (profile.raw.get("overrides") or {}).items():
        dotted(cfg, key, value)
    for key, value in overrides.items():
        dotted(cfg, key, value)
    mode = args.get("mode") or env.get("HARNESSWALK_MODE") or cfg.get("mode", "dev")
    if mode != "dev":
        raise ConfigError(f"mode {mode!r} is not generated: this harness runs in dev mode only")
    provider = args.get("provider") or env.get("HARNESSWALK_PROVIDER") or cfg.get("provider", "fake")
    if provider not in PROVIDERS:
        raise ConfigError(f"provider {provider!r} is not generated (have {', '.join(PROVIDERS)})")
    if overlay_path:
        base = overlay_path.parents[2] if len(overlay_path.parents) > 2 else Path.cwd()
        packs = _overlay_packs(cfg, base)
        default = cfg.get("dataset") or OVERLAY_DEFAULT_PACK
    else:
        packs, default = {PACK_ID: _repo_pack()}, PACK_ID
    if default not in packs:
        raise ConfigError(f"default pack {default!r} not found")
    fixture = args.get("fixture") or env.get("HARNESSWALK_FIXTURE") or "plain_answer"
    data = args.get("data") or env.get("HARNESSWALK_DATA_DIR") or str(ROOT / "data")
    return Settings(cfg, mode, provider, fixture, Path(data), profile, packs, default,
                    overlay_path)  # fmt: skip


def profile_view(profile: Profile) -> dict[str, Any]:
    return {"id": profile.id, "rung": profile.rung, "off": sorted(profile.off())}


def diagram_for(settings: Settings, pack: str | None = None) -> dict[str, Any]:
    """GET /v1/diagram: every node served, the switched-off ones dimmed (`disabled`)."""
    doc = json.loads(json.dumps(DIAGRAM))
    off = settings.profile.off() if pack else set()
    for node in doc["nodes"]:
        node["implemented"] = node["id"] in GENERATED or not node.get("can_disable")
        node["disabled"] = node["id"] in off
    if pack:
        doc["profile"] = {**profile_view(settings.profile), "title": settings.profile.title,
                          "pack": pack}  # fmt: skip
    return doc


ID = re.compile(r"^[a-z_][a-z0-9_-]{0,63}$")
