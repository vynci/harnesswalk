"""Words and hash embeddings for the retrieving stores. No model, no network, deterministic.

Each word is hashed into one of 256 signed buckets and the vector is normalised: texts that
share words get a high cosine, texts that share none get about zero. Swap `embed` for a real
embedding model (Ollama's /api/embed) when you need meaning rather than shared words.
"""

from __future__ import annotations

import hashlib
import math
import re

STOPWORDS = frozenset(
    """a an and are as at be but by can could do does for from has have how i if in into is it
    its me my no not of on or our please so that the their them then there these they this to
    us was we were what when where which who why will with would you your about tell know""".split()  # noqa: SIM905
)
TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
ENTITY = re.compile(r"\b[A-Za-z]{1,6}-\d{2,}\b")


def words(text: str) -> list[str]:
    """Lowercase word tokens without stopwords, with a light plural fold (returns -> return)."""
    out = []
    for word in TOKEN.findall(text.lower()):
        if word in STOPWORDS or len(word) < 2:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss") and "-" not in word:
            word = word[:-1]
        out.append(word)
    return out


def embed(text: str, dim: int = 256) -> list[float]:
    vec = [0.0] * dim
    for word in words(text):
        digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
        vec[int.from_bytes(digest[:4], "big") % dim] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def entities(text: str) -> set[str]:
    return {m.upper() for m in ENTITY.findall(text)}
