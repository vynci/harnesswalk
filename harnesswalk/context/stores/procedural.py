"""Procedural memory: the system prompt (skill zero) and the skills.

Contract: spec/contracts/store.md. Node: `procedural` (expert child of `memory`, kind prompt).

The prompt and the skills index (name and one line per skill) are in the `always` tier. A skill
body is `on_demand`: the model fetches it by exact name with the `read_skill` tool. Everything
is loaded through `release.current()`, and only release may change it.

A skill is a markdown file with front matter:

    ---
    name: returns
    description: How to handle a return or refund request.
    ---
    Body...
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from harnesswalk.context.tokens import estimate
from harnesswalk.nodes import node, register
from harnesswalk.ops import release
from harnesswalk.types import ConfigError, TurnContext

register("procedural", __name__)


@dataclass
class Skill:
    name: str
    description: str
    body: str
    version: str


def parse_skill(path_name: str, text: str, version: str) -> Skill:
    meta: dict[str, Any] = {}
    body = text
    if text.startswith("---\n"):
        head, _, body = text[4:].partition("\n---\n")
        meta = yaml.safe_load(head) or {}
    lines = [ln for ln in body.strip().splitlines() if ln.strip()]
    description = meta.get("description") or (lines[0].lstrip("# ") if lines else "")
    return Skill(meta.get("name") or Path(path_name).stem, description, body.strip(), version)


class ProceduralStore:
    kind = "procedural"

    def __init__(self, system_prompt: str, prompt_version: str, skills: list[Skill]) -> None:
        self.system_prompt = system_prompt.strip()
        self.prompt_version = prompt_version
        self.skills = {s.name: s for s in skills}

    @classmethod
    def from_settings(cls, settings: Any) -> ProceduralStore:
        inline = settings.get("procedural", {}) or {}
        pack = f"datasets/{settings.get('dataset', '_starter')}"
        if "system_prompt" in inline:
            prompt = inline["system_prompt"]
            version = release.version_of("system_prompt", prompt)
        else:
            prompt, version = release.current(settings.root, f"{pack}/system_prompt.md")
        skills: list[Skill] = []
        if "skills" in inline:
            for item in inline["skills"] or []:
                skills.append(
                    Skill(item["name"], item.get("description", ""), item.get("body", ""), "inline")
                )
        else:
            for path in sorted((settings.root / pack / "skills").glob("*.md")):
                rel = str(path.relative_to(settings.root))
                text, ver = release.current(settings.root, rel)
                skills.append(parse_skill(path.name, text, ver))
        store = cls(prompt, version, skills)
        budget = settings.get("context.slices.procedural", 3000)
        estimator = settings.get("context.token_estimator", "chars_div_4")
        if store.tokens(estimator) > budget:
            raise ConfigError(
                f"procedural memory needs {store.tokens(estimator)} tokens,"
                f" over its {budget} budget"
            )
        return store

    def index_text(self) -> str:
        if not self.skills:
            return ""
        lines = [f"- {s.name}: {s.description}" for s in self.skills.values()]
        return "Skills you can read in full with the read_skill tool:\n" + "\n".join(lines)

    def always_text(self) -> str:
        """Skill zero, then the skills index: the stable prefix of every context."""
        return "\n\n".join(part for part in (self.system_prompt, self.index_text()) if part)

    def tokens(self, estimator: str = "chars_div_4") -> int:
        return estimate(self.always_text(), estimator)

    def skill_body(self, name: str) -> str | None:
        skill = self.skills.get(name)
        return skill.body if skill else None

    def write(self, *args: Any, **kwargs: Any) -> None:
        raise PermissionError("procedural memory is written by release only")


async def run_procedural(ctx: TurnContext, store: ProceduralStore, estimator: str) -> str:
    async with node(ctx, "procedural") as span:
        text = store.always_text()
        span.set(
            prompt_version=store.prompt_version,
            skills_indexed=len(store.skills),
            tokens=estimate(text, estimator),
        )
    return text
