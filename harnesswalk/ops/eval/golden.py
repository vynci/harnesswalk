"""Golden sets: one JSON object per line in `datasets/<pack>/golden.jsonl`.

Contract: spec/contracts/evaluator.md (P4 additions). A case is a prompt, how the `fake`
provider answers it (`fixture`), and what the turn must show (`expect`): the node path, the
citations, the tool calls, a structured reply, the answer. `rubric` tells the judge what a good
answer says. The P0 spellings (`input`, `path_includes`, `must_cite`, `judge.rubric`) are read
as aliases.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

CASE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
EXPECT_KEYS = {
    "path",
    "citations",
    "tool_calls",
    "answer_contains",
    "answer_exact",
    "answer_regex",
}


class GoldenError(ValueError):
    """The golden set cannot be read (a run cannot start)."""


@dataclass
class GoldenCase:
    id: str
    title: str
    prompt: str
    fixture: str | None = None
    expect: dict[str, Any] = field(default_factory=dict)
    rubric: str | None = None
    output_schema: dict[str, Any] | None = None
    approval: str = "deny"
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)


def normalize(raw: dict[str, Any], where: str) -> GoldenCase:
    if not isinstance(raw, dict):
        raise GoldenError(f"{where}: a case must be a JSON object")
    case_id = raw.get("id")
    if not isinstance(case_id, str) or not CASE_ID.match(case_id):
        raise GoldenError(f"{where}: id must match {CASE_ID.pattern}")
    prompt = raw.get("prompt", raw.get("input"))
    if not isinstance(prompt, str) or not prompt.strip():
        raise GoldenError(f"{where}: case {case_id} needs a prompt")
    expect = dict(raw.get("expect") or {})
    if "path_includes" in expect and "path" not in expect:
        expect["path"] = expect.pop("path_includes")
    if expect.pop("must_cite", False) and "citations" not in expect:
        expect["citations"] = [{}]
    unknown = set(expect) - EXPECT_KEYS - {"path_excludes", "exit_reason"}
    if unknown:
        raise GoldenError(f"{where}: case {case_id} has unknown expect keys {sorted(unknown)}")
    for key in ("answer_contains",):
        if key in expect and isinstance(expect[key], str):
            expect[key] = [expect[key]]
    if "answer_regex" in expect:
        try:
            re.compile(expect["answer_regex"])
        except re.error as exc:
            raise GoldenError(f"{where}: case {case_id} answer_regex: {exc}") from exc
    schema = raw.get("output_schema")
    if schema is not None:
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            raise GoldenError(f"{where}: case {case_id} output_schema is not valid") from exc
    rubric = raw.get("rubric") or (raw.get("judge") or {}).get("rubric")
    approval = raw.get("approval", "deny")
    if approval not in ("approve", "deny"):
        raise GoldenError(f"{where}: case {case_id} approval must be approve or deny")
    return GoldenCase(
        id=case_id,
        title=str(raw.get("title") or case_id),
        prompt=prompt,
        fixture=raw.get("fixture"),
        expect=expect,
        rubric=rubric,
        output_schema=schema,
        approval=approval,
        tags=list(raw.get("tags") or []),
        raw=raw,
    )


def golden_path(settings: Any, pack: str) -> Path:
    """`evals.golden_path` with `{pack}` replaced; by default the pack's own `golden.jsonl`."""
    rel = settings.get("evals.golden_path", None)
    if rel is None:
        from harnesswalk.packs import pack_dir

        return pack_dir(settings, pack) / "golden.jsonl"
    return settings.resolve(str(rel).replace("{pack}", pack))


def load_golden(path: Path) -> list[GoldenCase]:
    if not path.is_file():
        raise GoldenError(f"no golden set at {path}")
    cases: list[GoldenCase] = []
    seen: set[str] = set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldenError(f"{path.name}:{n}: not JSON: {exc}") from exc
        case = normalize(raw, f"{path.name}:{n}")
        if case.id in seen:
            raise GoldenError(f"{path.name}:{n}: case id {case.id} is used twice")
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise GoldenError(f"{path} has no cases")
    return cases
