"""Release: the one door through which the harness loads its artifacts.

Contract: spec/contracts/release.md. Node: `release` (LLM Ops). Status in P1: **stub**.

`current()` is real: every prompt, skill, policy and config file is read through it, and it
stamps a version (`<name>@<sha256 prefix>`) that the trace and the inspectors show. Proposing,
gating, shipping and rolling back arrive in P4. A release turn today only opens the `release`
span and exits `skipped`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("release", __name__, stub=True)


def version_of(name: str, content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    return f"{Path(name).stem}@{digest}"


def current(root: Path, artifact: str) -> tuple[str, str]:
    """The current content and version of an artifact (a path relative to the repo root)."""
    path = root / artifact
    content = path.read_text(encoding="utf-8")
    return content, version_of(artifact, content)


async def run_release_node(ctx: TurnContext, artifact: str) -> None:
    """P1 stub: a release turn shows the node and does nothing else."""
    async with node(ctx, "release") as span:
        span.skip(artifact=artifact, stub=True, reason="release arrives in P4")
