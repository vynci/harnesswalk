"""Load the four config files, an optional conformance profile, env vars and overrides.

Precedence, lowest first: `config/*.yaml`, the profile (`HARNESSWALK_PROFILE`), environment
variables, explicit arguments, dotted overrides. Lists replace, mappings merge.

0.2.0 (harness profiles, spec/harness.md section 10): `HARNESSWALK_PROFILE` and `--profile`
take either a config overlay (a conformance profile, as before) or a harness profile, by name
(`config/profiles/<name>.yaml`) or by path; a file with a `rung` is a harness profile. The
harness profile of the loaded pack is, first found: the dotted override `profile`, a harness
profile named by `HARNESSWALK_PROFILE` or `--profile`, the pack's `pack.yaml` `profile`, then
`operated`. It lands on `Settings.profile`; its `overrides` are set on top of the files (a
dotted override of the same key still wins), and `context.stores` keeps only its `stores`.

P6 (public mode): `HARNESSWALK_SECRET` lands on `Settings.secret`, never in `data` (the
settings are shown by the inspectors); `HARNESSWALK_PUBLIC_BUDGET_USD`, `HARNESSWALK_TRUST_PROXY`
and `HARNESSWALK_LOG_FORMAT` override `public.hosted_budget_usd_per_day`, `gateway.trust_proxy`
and `logging.format`. The router rule whose `use` is `public.models` gets that list, the default
model first, so `public:` in config/harness.yaml is the one source of the public allowlist.

Every file is read through `ops.release.current()`, the one door through which the harness
loads its artifacts (spec/contracts/release.md). Without a served view (`artifacts`) it reads
the working tree; the harness passes the release store's served set (P4), so an edited or
rolled-back policy, context or models file is what the settings are built from. The profile,
environment variables and overrides still apply on top. `load_args` keeps the arguments so the
harness can build the settings again after a release change (`reload`).
"""

from __future__ import annotations

import copy
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from harnesswalk import profiles as profiles_mod
from harnesswalk.ops import release
from harnesswalk.paths import PACKAGE_ROOT, default_data_dir
from harnesswalk.types import ConfigError

PROVIDERS = ("fake", "ollama", "anthropic", "openai_compatible")
MODES = ("dev", "public", "private")
PUBLIC_RULE_MARKER = "public.models"  # a router rule's `use` filled from `public.models`
HOSTED_PROVIDERS = ("anthropic", "openai_compatible")  # need a key or URL, cost money
LOG_FORMATS = ("text", "json")
_MISSING = object()


def deep_merge(base: dict, extra: Mapping) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def set_dotted(data: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


@dataclass
class Settings:
    """The merged configuration plus where things live on disk."""

    data: dict[str, Any]
    root: Path
    data_dir: Path
    provider: str | None = None  # when set, the only configured provider
    versions: dict[str, str] = field(default_factory=dict)  # artifact -> version
    load_args: dict[str, Any] | None = None  # what load_settings was called with (P4 reload)
    served: Any = None  # ops.release.Served the settings were built from, or None (the files)
    # HARNESSWALK_SECRET (P6): signs the public tenant cookie. Kept off `data` and out of repr.
    secret: str | None = field(default=None, repr=False)
    # 0.2.0: the harness profile the pack runs with, and the server-wide choice that replaced
    # every pack's own (`HARNESSWALK_PROFILE`, `--profile`, `--set profile=...`), or None.
    profile: Any = None
    profile_override: str | None = None
    configured_stores: list[str] | None = None  # `context.stores` before the profile kept its

    def reload(self, served: Any) -> Settings:
        """The same settings built again on another served artifact set."""
        if self.load_args is None:
            return self
        return load_settings(**self.load_args, artifacts=served)

    def get(self, dotted: str, default: Any = _MISSING) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, Mapping) or part not in node:
                if default is _MISSING:
                    raise KeyError(dotted)
                return default
            node = node[part]
        return node

    @property
    def mode(self) -> str:
        return self.data["mode"]

    def resolve(self, rel: str | Path) -> Path:
        """`data/...` lives under the data dir; everything else under the repo root."""
        rel = Path(rel)
        if rel.is_absolute():
            return rel
        if rel.parts and rel.parts[0] == "data":
            return self.data_dir.joinpath(*rel.parts[1:])
        return self.root / rel

    def fixture(self) -> str | None:
        return self.get("models.providers.fake.fixture", None)

    def public_models(self) -> list[str]:
        """`public.models`: the aliases a public turn may use (P6)."""
        return list(self.get("public.models", []) or [])

    def public_providers(self) -> set[str]:
        """The providers behind `public.models`."""
        models = self.get("models.models", {})
        return {models[a].get("provider", "") for a in self.public_models() if a in models}

    def provider_configured(self, name: str) -> bool:
        """A provider is configured when it was selected, or when its prerequisites exist.

        Public mode (P6): exactly the providers behind `public.models` are built, so a public
        server never routes to anything else. `public.models` implies the provider: a hosted
        one is built whatever its `enabled` flag says, because the site's key (or a learner's
        BYO key) is what decides whether it can answer (`control/public.py`), and `fake` is
        built for `fake-small` without being selected.
        """
        if self.provider:
            return name == self.provider
        if self.mode == "public":
            if name not in self.public_providers():
                return False
            if name in ("fake", *HOSTED_PROVIDERS):
                return name in self.get("models.providers", {})
            return self.provider_prerequisites(name)
        if name == "fake":
            return False  # the fake provider is used only when selected explicitly
        return self.provider_prerequisites(name)

    def provider_prerequisites(self, name: str) -> bool:
        """Whether a provider could be built, whichever one was selected at start-up.

        `fake` only needs its config section (the fixture is checked when it loads). Hosted
        providers still need `enabled: true` plus their key or URL.
        """
        providers = self.get("models.providers", {})
        if name not in providers:
            return False
        if name == "fake":
            return True
        if name == "ollama":
            return bool(providers["ollama"].get("base_url"))
        # Hosted providers cost money, so they need an explicit opt-in (`enabled: true`) on top of
        # their key or URL. A key that merely happens to be in the shell never routes a call.
        cfg = providers[name]
        if not cfg.get("enabled", False):
            return False
        if name == "anthropic":
            return bool(os.environ.get(cfg.get("api_key_env", "")))
        if name == "openai_compatible":
            return bool(cfg.get("base_url") or os.environ.get(cfg.get("base_url_env", "")))
        return False


def _read_yaml(root: Path, artifact: str, versions: dict[str, str], served: Any = None) -> dict:
    content, version = release.current(root, artifact, served)
    versions[artifact] = version
    data = yaml.safe_load(content) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{artifact} must hold a mapping")
    return data


def _normalize_profile(profile: dict) -> dict:
    """Map a conformance profile (spec/conformance/profile.yaml) onto the settings shape."""
    out: dict[str, Any] = {}
    for key in (
        "mode",
        "loop",
        "approval",
        "input_guardrails",
        "output_guardrails",
        "stores",
        "memory_writer",
        "evals",
        "release",
        "packs",
        "tune",
    ):
        if key in profile:
            out[key] = profile[key]
    if "models" in profile:
        out["models"] = profile["models"]
    if "context" in profile:
        out["context"] = profile["context"]
    if "procedural" in profile:
        out["procedural"] = profile["procedural"]
    if "policy" in profile:
        out["policy"] = profile["policy"]
    if "tools" in profile:
        files = profile["tools"]
        out["tools"] = {"files": [files] if isinstance(files, str) else list(files)}
    return out


def load_settings(
    root: Path | None = None,
    *,
    mode: str | None = None,
    provider: str | None = None,
    fixture: str | None = None,
    profile: str | Path | None = None,
    overrides: Mapping[str, Any] | None = None,
    data_dir: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    artifacts: Any = None,
) -> Settings:
    load_args = {
        "root": root,
        "mode": mode,
        "provider": provider,
        "fixture": fixture,
        "profile": profile,
        "overrides": dict(overrides) if overrides else None,
        "data_dir": data_dir,
        "env": env,
    }
    env = os.environ if env is None else env
    root = Path(root) if root else Path(env.get("HARNESSWALK_ROOT") or PACKAGE_ROOT)
    versions: dict[str, str] = {}

    data: dict[str, Any] = _read_yaml(root, "config/harness.yaml", versions)
    policy = _read_yaml(root, "config/policy.yaml", versions, artifacts)
    data["approval"] = policy.pop("approval", {})
    data["policy"] = policy
    data.update(_read_yaml(root, "config/context.yaml", versions, artifacts))
    data["models"] = _read_yaml(root, "config/models.yaml", versions, artifacts)
    if (root / "config/trainers.yaml").is_file():  # 0.2.0: process config, not an artifact
        trainers = _read_yaml(root, "config/trainers.yaml", versions)
        tune = data.setdefault("tune", {})
        tune["trainers"] = trainers.get("trainers") or {}
        tune["students"] = trainers.get("students") or {}

    profile = profile or env.get("HARNESSWALK_PROFILE")
    harness_profile: str | None = None  # a harness profile named by env or argument
    if profile:
        if _names_harness_profile(root, profile):
            harness_profile = str(profile)
        else:
            path = Path(profile)
            rel = path.relative_to(root) if path.is_absolute() else path
            data = deep_merge(data, _normalize_profile(_read_yaml(root, str(rel), versions)))

    mode = mode or env.get("HARNESSWALK_MODE") or data.get("mode", "dev")
    provider = provider or env.get("HARNESSWALK_PROVIDER") or None
    fixture = fixture or env.get("HARNESSWALK_FIXTURE") or None
    data["mode"] = mode
    if env.get("HARNESSWALK_DATASET"):
        data["dataset"] = env["HARNESSWALK_DATASET"]
    if env.get("HARNESSWALK_VECTORS"):
        set_dotted(data, "storage.vectors", env["HARNESSWALK_VECTORS"])
    # `models.providers.ollama.base_url_env` (default HARNESSWALK_OLLAMA_URL): the variable,
    # when set and not empty, wins over `base_url`, so a remote Ollama needs no `--set`.
    ollama = ((data.get("models") or {}).get("providers") or {}).get("ollama") or {}
    ollama_env = ollama.get("base_url_env") or "HARNESSWALK_OLLAMA_URL"
    if env.get(ollama_env):
        set_dotted(data, "models.providers.ollama.base_url", env[ollama_env])
    if fixture:
        set_dotted(data, "models.providers.fake.fixture", fixture)
    elif mode == "public" and (data.get("public") or {}).get("fake_fixture"):
        # fake-small on a public server: a fixture that never runs out (P6, cycle)
        set_dotted(data, "models.providers.fake.fixture", data["public"]["fake_fixture"])
    if env.get("HARNESSWALK_PUBLIC_BUDGET_USD"):
        raw = env["HARNESSWALK_PUBLIC_BUDGET_USD"]
        try:
            set_dotted(data, "public.hosted_budget_usd_per_day", float(raw))
        except ValueError as exc:
            raise ConfigError("HARNESSWALK_PUBLIC_BUDGET_USD must be a number") from exc
    if env.get("HARNESSWALK_TRUST_PROXY"):
        trust = env["HARNESSWALK_TRUST_PROXY"].strip().lower() in ("1", "true", "yes", "on")
        set_dotted(data, "gateway.trust_proxy", trust)
    if env.get("HARNESSWALK_LOG_FORMAT"):
        set_dotted(data, "logging.format", env["HARNESSWALK_LOG_FORMAT"].strip().lower())
    for dotted, value in (overrides or {}).items():
        if dotted != "profile":
            set_dotted(data, dotted, value)
    _fill_public_rule(data)
    chosen = (overrides or {}).get("profile") or harness_profile
    if chosen is not None and not isinstance(chosen, str):
        raise ConfigError("profile must be a profile name or a path")

    data_dir = Path(data_dir or env.get("HARNESSWALK_DATA_DIR") or default_data_dir(root))
    settings = Settings(
        data=data,
        root=root,
        data_dir=data_dir,
        provider=provider,
        versions=versions,
        load_args=load_args,
        served=artifacts,
        secret=env.get("HARNESSWALK_SECRET") or None,
        profile_override=chosen,
    )
    apply_profile(settings, set(overrides or {}))
    validate(settings)
    return settings


def _names_harness_profile(root: Path, ref: str | Path) -> bool:
    """A bare name, or a path to a file with a `rung` (a config overlay has none)."""
    ref_s = str(ref)
    if profiles_mod.PROFILE_ID.match(ref_s):
        return True
    path = Path(ref_s) if Path(ref_s).is_absolute() else root / ref_s
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    return profiles_mod.is_profile_file(data)


def pack_profile_name(settings: Settings, pack: str) -> str:
    """The profile `pack` names in its `pack.yaml`, else the default (`operated`)."""
    from harnesswalk import packs as packs_mod

    info = packs_mod.find(settings, pack)
    named = info.meta.get("profile") if info is not None else None
    return named if isinstance(named, str) and named else profiles_mod.DEFAULT_PROFILE


def profile_for(settings: Settings, pack: str) -> Any:
    """The harness profile `pack` runs with on these settings' server (section 10)."""
    ref = settings.profile_override or pack_profile_name(settings, pack)
    return profiles_mod.load(settings.root, ref)


def apply_profile(settings: Settings, explicit: set[str]) -> None:
    """Resolve the loaded pack's harness profile, set its overrides (keys the caller set
    explicitly win) and keep only its stores in `context.stores`."""
    profile = profile_for(settings, settings.get("dataset", "_starter"))
    settings.profile = profile
    data = settings.data
    data["profile"] = profile.id
    for dotted, value in profile.overrides.items():
        if dotted not in explicit:
            set_dotted(data, dotted, copy.deepcopy(value))
    context = data.setdefault("context", {})
    stores = list(context.get("stores", ["procedural", "session"]))
    settings.configured_stores = stores
    context["stores"] = [s for s in stores if s in profile.stores]


def _fill_public_rule(data: dict[str, Any]) -> None:
    """The router rule whose `use` is `public.models` gets `public.models`, default first."""
    public = data.get("public") or {}
    # Only aliases the models file defines: outside public mode an edited models.yaml without
    # them must not fail on this rule (public mode reports unknown aliases in validate_public).
    known = (data.get("models") or {}).get("models") or {}
    aliases = [a for a in public.get("models") or [] if a in known]
    default = public.get("default_model")
    if default in aliases:
        aliases.remove(default)
        aliases.insert(0, default)
    for rule in (data.get("models") or {}).get("router", {}).get("rules", []) or []:
        if rule.get("use") == PUBLIC_RULE_MARKER:
            rule["use"] = list(aliases)


def validate_public(settings: Settings) -> None:
    """The `public:` block (P6): known, non-embedding aliases and sane numbers."""
    models = settings.get("models.models", {})
    aliases = settings.public_models()
    if not aliases:
        raise ConfigError("public.models must list at least one model alias")
    for alias in aliases:
        if alias not in models:
            raise ConfigError(f"public.models names unknown model {alias}")
        if models[alias].get("embedding"):
            raise ConfigError(f"public.models names embedding model {alias}")
    default = settings.get("public.default_model", None)
    if default not in aliases:
        raise ConfigError("public.default_model must be one of public.models")
    for key in ("hosted_budget_usd_per_day", "session_ttl_s", "sweep_interval_s"):
        value = settings.get(f"public.{key}", 0)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"public.{key} must be a number >= 0")
    if float(settings.get("public.session_ttl_s", 7200)) <= 0:
        raise ConfigError("public.session_ttl_s must be greater than 0")
    if int(settings.get("public.rate_limit_max_keys", 10000)) < 1:
        raise ConfigError("public.rate_limit_max_keys must be at least 1")
    if settings.get("public.cookie_secure", "auto") not in ("auto", "always", "never"):
        raise ConfigError("public.cookie_secure must be auto, always or never")


def validate_split(rule: dict[str, Any], models: dict[str, Any]) -> None:
    """A canary rule: `split` instead of `use`, both aliases known, a fraction in [0, 1]."""
    rid = rule.get("id")
    split = rule.get("split")
    if "use" in rule:
        raise ConfigError(f"router rule {rid} has both `use` and `split`")
    if not isinstance(split, dict):
        raise ConfigError(f"router rule {rid}: `split` must be a mapping")
    for key in ("base_alias", "candidate_alias"):
        if split.get(key) not in models:
            raise ConfigError(f"router rule {rid}: split.{key} names unknown model {split[key]!r}")
    fraction = split.get("fraction", 0)
    number = isinstance(fraction, (int, float)) and not isinstance(fraction, bool)
    if not number or not 0 <= fraction <= 1:
        raise ConfigError(f"router rule {rid}: split.fraction must be a number from 0 to 1")


def validate(settings: Settings) -> None:
    """Refuse to start on a configuration the spec forbids. Mode rules live in policy.py."""
    if settings.mode not in MODES:
        raise ConfigError(f"mode must be one of {MODES}, got {settings.mode!r}")
    if settings.provider and settings.provider not in PROVIDERS:
        raise ConfigError(f"provider must be one of {PROVIDERS}, got {settings.provider!r}")
    if settings.get("logging.format", "text") not in LOG_FORMATS:
        raise ConfigError(f"logging.format must be one of {LOG_FORMATS}")
    rules = settings.get("models.router.rules", [])
    if not rules or rules[-1].get("when"):
        raise ConfigError("router.rules must end with a default rule whose `when` is empty")
    models = settings.get("models.models", {})
    for rule in rules:
        if rule.get("split") is not None:  # 0.2.0: the canary rule (router.md)
            validate_split(rule, models)
            continue
        if not isinstance(rule.get("use", []), list):
            raise ConfigError(f"router rule {rule.get('id')} needs a list in `use`")
        for alias in rule.get("use", []):
            if alias not in models:
                raise ConfigError(f"router rule {rule.get('id')} uses unknown model {alias}")
    canary = settings.get("models.router.canary", None)
    if canary is not None:  # 0.2.0: the canary rule the tuning loop manages (router.md)
        if not isinstance(canary, dict) or canary.get("split") is None:
            raise ConfigError("router.canary must be a rule with `split`")
        validate_split(canary, models)
    for rule in settings.get("policy.rules", []):
        if rule.get("decision") not in ("allow", "deny", "needs_approval"):
            raise ConfigError(f"policy rule {rule.get('id')} has an invalid decision")
    if settings.mode == "public":  # the allowlist only binds a public server
        validate_public(settings)
