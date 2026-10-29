"""Embeddings for the retrieving stores. Always local: hash embeddings or Ollama.

Config `stores.embeddings` (spec/contracts/store.md):

    provider: auto | fake | ollama
    model: nomic-embed-text

`fake` hashes each word token into a fixed number of signed buckets and normalizes the
vector. Texts that share words get a high cosine, texts that share none get about zero. No
network, no model, deterministic: the conformance suite and every unit test use it. `ollama`
calls `POST /api/embed` on the configured Ollama for real semantic vectors. `auto` picks
`fake` when the harness runs on the fake provider, else `ollama` when a quick probe finds it with
the model pulled, else `fake` with a warning (the summaries name the embedder, so nothing is hidden). The process
variable `HARNESSWALK_EMBEDDINGS` (`fake` or `ollama`) overrides the config; the test suite
sets it to `fake`.

Vectors are stored with the embedder's id (`fake:256`, `ollama:nomic-embed-text`), and a
search only compares vectors of the same id, so switching embedders never mixes spaces.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
from collections.abc import Mapping
from typing import Any

import httpx

from harnesswalk.context.records import tokens
from harnesswalk.types import ConfigError

log = logging.getLogger(__name__)


class Embedder:
    id: str = "none"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class FakeEmbedder(Embedder):
    """Signed feature hashing over word tokens. Deterministic and offline."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.id = f"fake:{dim}"

    def _one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for word in tokens(text):
            digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]


class OllamaEmbedder(Embedder):
    def __init__(self, base_url: str, model: str, timeout_s: float = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.id = f"ollama:{model}"

    def probe(self) -> bool:
        """Ollama answers and has the embedding model pulled."""
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=1.0)
            if r.status_code != 200:
                return False
            names = {m.get("name", "") for m in r.json().get("models", [])}
        except (httpx.HTTPError, ValueError):
            return False
        return any(n == self.model or n.startswith(self.model + ":") for n in names)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        r = httpx.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": texts},
            timeout=self.timeout_s,
        )
        r.raise_for_status()
        vectors = r.json().get("embeddings") or []
        if len(vectors) != len(texts):
            raise RuntimeError("ollama returned a different number of embeddings")
        out = []
        for v in vectors:
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


def build_embedder(settings: Any, env: Mapping[str, str] | None = None) -> Embedder:
    """The configured embedder. `HARNESSWALK_EMBEDDINGS=fake` in the process environment wins
    over the config, so a test run can never reach a local Ollama by accident."""
    cfg = settings.get("stores.embeddings", {}) or {}
    env = os.environ if env is None else env
    provider = env.get("HARNESSWALK_EMBEDDINGS") or cfg.get("provider", "auto")
    if provider == "fake":
        return FakeEmbedder(int(cfg.get("dim", 256)))
    if provider not in ("auto", "ollama"):
        raise ConfigError(f"stores.embeddings.provider must be auto, fake or ollama: {provider}")
    if provider == "auto" and settings.provider == "fake":
        return FakeEmbedder(int(cfg.get("dim", 256)))
    base_url = settings.get("models.providers.ollama.base_url", "") or "http://localhost:11434"
    ollama = OllamaEmbedder(base_url, cfg.get("model", "nomic-embed-text"))
    if provider == "ollama" or ollama.probe():
        return ollama
    log.warning("no Ollama at %s for embeddings: using hash embeddings (fake)", base_url)
    return FakeEmbedder(int(cfg.get("dim", 256)))
