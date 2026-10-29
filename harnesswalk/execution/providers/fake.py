"""The fake provider: scripted responses from a fixture file. No network, no clock, no cost.

Semantics are normative (spec/contracts/model_provider.md, "The fake provider"):

- Turn `n` of a session reads `turns[n]`; the `k`th model call in that turn reads
  `responses[k]`. A fixture picked per turn (http_api.md, `fixture`) counts `n` over the
  session's turns that ran on it (`relative_turns`), so switching fixture starts at `turns[0]`.
- A response has `text`, `tool_calls`, or both; `finish_reason` follows from them.
- `expect` checks the request (`tools_enabled`, `last_message_role`, `last_message_contains`)
  and fails with `fixture_mismatch`; running out of responses fails with `fixture_exhausted`.

Fixtures are YAML (spec/conformance/fixtures/*.yaml) or JSON Lines, where each line is one turn
(`{"responses": [...]}`), as in datasets/_starter/fixtures/fake/*.jsonl.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from harnesswalk.execution.providers import (
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    ProviderUsage,
)
from harnesswalk.paths import spec_dir
from harnesswalk.types import ConfigError, ToolCall

# A fixture name a turn may ask for (http_api.md), so it can never name a path.
FIXTURE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


@cache
def _fixture_validator() -> jsonschema.Draft202012Validator:
    path = spec_dir() / "conformance" / "fixture.schema.json"
    return jsonschema.Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def load_fixture(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            turns = [json.loads(line) for line in text.splitlines() if line.strip()]
            fixture = {"fixture": path.stem, "turns": turns}
        else:
            fixture = yaml.safe_load(text)
    except (UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"fixture {path} cannot be parsed: {exc}") from exc
    errors = [e.message for e in _fixture_validator().iter_errors(fixture)]
    if errors:
        raise ConfigError(f"fixture {path} is invalid: {errors}")
    if fixture["fixture"] != path.stem:
        raise ConfigError(f"fixture {path} must be named {fixture['fixture']!r}")
    return fixture


def find_fixture(name: str, dirs: list[Path]) -> Path:
    for directory in dirs:
        for suffix in (".yaml", ".jsonl"):
            candidate = directory / f"{name}{suffix}"
            if candidate.exists():
                return candidate
    searched = ", ".join(str(d) for d in dirs)
    raise ConfigError(f"no fixture named {name!r} in {searched}")


def fixture_dirs(settings: Any) -> list[Path]:
    dirs: list[Path] = []
    configured = settings.get("models.providers.fake.fixture_dir", None)
    if configured:
        dirs.append(settings.resolve(configured))
    dirs.append(
        settings.root / "datasets" / settings.get("dataset", "_starter") / "fixtures" / "fake"
    )
    dirs.append(settings.root / "spec" / "conformance" / "fixtures")
    return dirs


def list_fixtures(dirs: list[Path]) -> list[str]:
    """Every fixture name a turn could ask for from these directories, sorted.

    Only names that pass FIXTURE_NAME, since a turn naming any other is a 400.
    """
    names = {
        p.stem
        for directory in dirs
        if directory.is_dir()
        for p in directory.iterdir()
        if p.suffix in (".yaml", ".jsonl") and p.is_file() and FIXTURE_NAME.fullmatch(p.stem)
    }
    return sorted(names)


def fake_embed(text: str, dims: int = 64) -> list[float]:
    """Deterministic hash embedding: sha256 bytes scaled to [-1, 1], 64 dims."""
    out: list[float] = []
    counter = 0
    while len(out) < dims:
        digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
        out.extend((b - 127.5) / 127.5 for b in digest)
        counter += 1
    return out[:dims]


class FakeProvider:
    name = "fake"

    def __init__(self, fixture: dict[str, Any], *, relative_turns: bool = False) -> None:
        self.fixture = fixture
        self.model = fixture.get("model", "fake-small")
        self.relative_turns = relative_turns
        self._calls: dict[str, int] = {}  # turn_id -> calls made
        self._used: set[tuple[str, int, int]] = set()  # (session, turn_index, response_index)
        self._sessions: set[str] = set()
        self._turn_of: dict[str, dict[str, int]] = {}  # session -> turn_id -> index on this fixture

    @classmethod
    def from_settings(cls, settings: Any) -> FakeProvider:
        name = settings.fixture()
        if not name:
            raise ConfigError("the fake provider needs a fixture (HARNESSWALK_FIXTURE)")
        return cls(load_fixture(find_fixture(name, fixture_dirs(settings))))

    @property
    def fixture_name(self) -> str:
        return self.fixture["fixture"]

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        turn = request.turn
        if turn is None:
            raise ProviderError("fixture_mismatch", "fake provider needs the turn reference")
        turns = self.fixture["turns"]
        session_key = f"{turn.tenant_id}/{turn.session_id}"
        self._sessions.add(session_key)
        index = turn.turn_index
        if self.relative_turns:
            seen = self._turn_of.setdefault(session_key, {})
            index = seen.setdefault(turn.turn_id, len(seen))
        k = self._calls.get(turn.turn_id, 0)
        if turn.call_index is not None:  # the model layer's count survives a restart
            k = turn.call_index
        self._calls[turn.turn_id] = k + 1
        if index >= len(turns) or k >= len(turns[index]["responses"]):
            raise ProviderError(
                "fixture_exhausted",
                f"fixture {self.fixture_name} has no response {k} for turn {index}",
                retryable=False,
            )
        response = turns[index]["responses"][k]
        self._used.add((session_key, index, k))
        self._check_expect(response.get("expect", {}), request)
        if response.get("latency_ms"):
            await asyncio.sleep(response["latency_ms"] / 1000)

        calls = [
            ToolCall(call_id=c.get("call_id", ""), name=c["name"], arguments=c["arguments"])
            for c in response.get("tool_calls", [])
        ]
        finish = response.get("finish_reason") or ("tool_calls" if calls else "stop")
        usage = response.get("usage", {})
        return ProviderResponse(
            text=response.get("text"),
            tool_calls=calls,
            finish_reason=finish,
            usage=ProviderUsage(
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                usage.get("cached_tokens", 0),
            ),
        )

    def resume_turn(self, tenant_id: str, session_id: str, turn_id: str, index: int) -> None:
        """A turn re-attached after a restart keeps its place on a per-turn fixture."""
        if self.relative_turns:
            self._turn_of.setdefault(f"{tenant_id}/{session_id}", {})[turn_id] = index

    async def health(self) -> bool:
        return True

    def _check_expect(self, expect: dict[str, Any], request: ProviderRequest) -> None:
        last = request.messages[-1] if request.messages else None
        problems = []
        if "tools_enabled" in expect and expect["tools_enabled"] != bool(request.tools):
            problems.append(f"tools_enabled is {bool(request.tools)}")
        if "last_message_role" in expect and (not last or last.role != expect["last_message_role"]):
            problems.append(f"last message role is {last.role if last else None}")
        if "last_message_contains" in expect and (
            not last or expect["last_message_contains"] not in last.content
        ):
            problems.append(f"last message does not contain {expect['last_message_contains']!r}")
        if problems:
            raise ProviderError(
                "fixture_mismatch",
                f"fixture {self.fixture_name}: " + "; ".join(problems),
                retryable=False,
            )

    def unused(self) -> list[str]:
        """Scripted responses no session consumed, for the conformance report."""
        out = []
        for ti, turn in enumerate(self.fixture["turns"]):
            for ri in range(len(turn["responses"])):
                if not any((s, ti, ri) in self._used for s in self._sessions):
                    out.append(f"turns[{ti}].responses[{ri}]")
        return out

    async def embed(self, texts: list[str], model: str = "fake") -> list[list[float]]:
        return [fake_embed(t) for t in texts]
