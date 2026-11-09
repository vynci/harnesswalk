"""The generator manifest: what `harnesswalk init` builds, beyond the profile's node set.

Spike lesson 3 (plans phase-1 SPIKE.md): a profile says which nodes are off, but a generator
also has to decide the providers, the modes, the transport, the storage and the release door.
`init` takes them as flags and writes them to `harness.yaml` in the output, so `upgrade`
re-renders the same shape.

| Key | Values | Default |
|---|---|---|
| `profile` | minimal, guarded, grounded, remembered, governed | the pack's |
| `pack` | a pack id under datasets/, or a path to a pack directory | required |
| `providers` | fake, plus ollama | [fake, ollama] |
| `modes` | [dev]; public and private mode are never generated | [dev] |
| `transport` | http (the web app and the conformance runner need it), cli | http |
| `storage` | sqlite, files (JSON Lines) | sqlite |
| `release` | file_door (your files, versioned by git), chain (governed and up only) | by rung |
| `tune` | true adds curate and tune (the tuning loop, in miniature) | false |
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PROFILES = ("minimal", "guarded", "grounded", "remembered", "governed")
TRANSPORTS = ("http", "cli")
STORAGES = ("sqlite", "files")
RELEASES = ("file_door", "chain")
HOSTED = ("ollama",)


class ManifestError(ValueError):
    pass


@dataclass
class GenManifest:
    profile: str
    pack: str
    providers: list[str] = field(default_factory=lambda: ["fake", "ollama"])
    modes: list[str] = field(default_factory=lambda: ["dev"])
    transport: str = "http"
    storage: str = "sqlite"
    release: str = ""
    tune: bool = False
    ollama_model: str = "qwen3.5:4b"
    anthropic_model: str = "claude-haiku-5-5"
    template_set: str = "python"

    def check(self, rung: int, diagram_ids: set[str]) -> None:
        if self.profile not in PROFILES:
            raise ManifestError(
                f"profile {self.profile!r} has no template set yet: choose one of"
                f" {', '.join(PROFILES)} (operated needs online eval, diagnose"
                " and the release gate, which P4 does not generate)"
            )
        if self.modes != ["dev"]:
            raise ManifestError("only dev mode is generated; public and private modes are not")
        if "fake" not in self.providers or not set(self.providers) <= {"fake", *HOSTED}:
            raise ManifestError("providers are fake plus ollama")
        if self.transport not in TRANSPORTS or self.storage not in STORAGES:
            raise ManifestError(f"transport is one of {TRANSPORTS}, storage one of {STORAGES}")
        if not self.release:
            self.release = "chain" if rung >= 5 else "file_door"
        if self.release not in RELEASES:
            raise ManifestError(f"release is one of {RELEASES}")
        if self.release == "chain" and rung < 5:
            raise ManifestError("release: chain is for governed (and operated) only")
        if self.tune and "tune" not in diagram_ids:
            raise ManifestError(
                "tune: true needs the tune node (P3) in spec/diagram/diagram.json;"
                " this spec version has none, so it cannot be generated yet"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenManifest:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})
