"""Shared records of the store contract (spec/contracts/store.md), and the text helpers the
retrieving stores use: tokens, entities, cosine and reciprocal rank fusion.

Not a node. Every store imports from here so the ranking rules read the same everywhere.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from harnesswalk.types import TurnContext

# Pack content (documents, seed facts and seed episodes) is released to every tenant that runs
# the pack. It lives under this reserved scope, never under a real tenant, and a tenant id that
# starts with "_" may not write anything (spec/harness.md section 5: generic content is shared,
# memories are per tenant).
PACK_SCOPE = "_pack"

STOPWORDS = frozenset(
    """a an and are as at be but by can could do does for from has have how i if in into is it
    its me my no not of on or our please so that the their them then there these they this to
    us was we were what when where which who why will with would you your about tell know""".split()  # noqa: SIM905
)
TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
ENTITY_ID = re.compile(r"\b[A-Za-z]{1,6}-\d{2,}\b")


def require_tenant(ctx: TurnContext) -> str:
    """Every store call carries a tenant (store.md). A missing one is a programming error."""
    tenant = getattr(ctx, "tenant_id", None)
    if not tenant:
        raise ValueError("a store call needs ctx.tenant_id")
    return tenant


def tokens(text: str) -> list[str]:
    """Lowercase word tokens without stopwords, with a light plural fold (returns -> return)."""
    out = []
    for word in TOKEN.findall(text.lower()):
        if word in STOPWORDS or len(word) < 2:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss") and "-" not in word:
            word = word[:-1]
        out.append(word)
    return out


def entities(text: str) -> set[str]:
    """Id-like entities (A-1001, S-3) in lowercase. Cheap and deterministic on purpose."""
    return {m.group(0).lower() for m in ENTITY_ID.finditer(text)}


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine of two vectors. Stored vectors are unit length, so this is a dot product."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def rrf(rankings: Iterable[Sequence[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion: score(id) = sum over lists of 1 / (k + rank), rank from 1."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


@dataclass
class Query:
    text: str
    now: float = field(default_factory=time.time)
    top_k: int = 5
    max_tokens: int = 800
    entities: list[str] = field(default_factory=list)
    filters: dict[str, str] = field(default_factory=dict)  # knowledge: doc, pack

    def all_entities(self) -> set[str]:
        return entities(self.text) | {e.lower() for e in self.entities}


@dataclass
class SourceRef:
    store: str
    id: str
    title: str | None = None
    uri: str | None = None
    version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Hit:
    id: str
    kind: str
    text: str
    score: float
    tokens: int
    source: SourceRef
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "text": self.text,
            "score": round(self.score, 6),
            "tokens": self.tokens,
            "source": self.source.to_dict(),
            **({"meta": self.meta} if self.meta else {}),
        }


def trim_to_budget(hits: list[Hit], top_k: int, max_tokens: int) -> tuple[list[Hit], int]:
    """Keep the best hits that fit: at most top_k, at most max_tokens. Returns (kept, dropped)."""
    kept: list[Hit] = []
    used = 0
    for hit in hits:
        if len(kept) >= top_k:
            break
        if used + hit.tokens > max_tokens:
            continue
        kept.append(hit)
        used += hit.tokens
    return kept, len(hits) - len(kept)
