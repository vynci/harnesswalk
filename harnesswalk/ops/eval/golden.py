"""Golden sets: one JSON object per line in `datasets/<pack>/golden.jsonl`.

Contract: spec/contracts/evaluator.md (P4 additions). A case is a prompt, how the `fake`
provider answers it (`fixture`), and what the turn must show (`expect`): the node path, the
citations, the tool calls, a structured reply, the answer. `rubric` tells the judge what a good
answer says. The P0 spellings (`input`, `path_includes`, `must_cite`, `judge.rubric`) are read
as aliases.

P5 integration: `checks` (router rules and retrieved memory, scored by the `checks` scorer),
`modes` (the harness modes a case applies in; absent means every mode) and `after` (the
earlier case whose memory this one reads; cases always run in file order).

P2 (spec 0.2.0, additive): a case may carry `turns: [{user, expect?, checks?}]` instead of a
`prompt`. The turns run in one session, in order, so memory and compaction are testable;
`fixture` is one name for every turn (turn `n` reads its line `n`) or a list aligned with the
turns. `roles` sets the roles of the identity the case runs as (default `["user"]`).
`checks` gains `compacted` (the context builder folded old turns into the summary) and
`summary` (a substring the running summary in context holds).
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
    "tool_calls_mode",  # exact | ordered: how a `model` run grades tool_calls (evaluator.md)
}


MODES = ("dev", "public", "private")
CHECK_KEYS = ("router_rules", "retrieved", "compacted", "summary")
TURN_KEYS = {"user", "expect", "checks"}
ROLE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
CHECK_STORES = ("semantic", "episodic", "knowledge")


class GoldenError(ValueError):
    """The golden set cannot be read (a run cannot start)."""


@dataclass
class GoldenTurn:
    """One turn of a multi-turn case: what the user says, and what that turn must show."""

    user: str
    expect: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, Any] | None = None


@dataclass
class GoldenCase:
    id: str
    title: str
    prompt: str
    fixture: str | list[str] | None = None  # a list: one per turn (P2)
    expect: dict[str, Any] = field(default_factory=dict)
    rubric: str | None = None
    output_schema: dict[str, Any] | None = None
    approval: str = "deny"
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    checks: dict[str, Any] | None = None
    modes: list[str] | None = None
    after: str | None = None
    turns: list[GoldenTurn] = field(default_factory=list)  # empty: a one-prompt case
    roles: list[str] | None = None

    @property
    def multi_turn(self) -> bool:
        return bool(self.turns)

    def fixture_for(self, n: int) -> str | None:
        """The fixture turn `n` answers from: the one name, or the list's item `n`."""
        if isinstance(self.fixture, list):
            return self.fixture[n] if n < len(self.fixture) else None
        return self.fixture

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)

    def applies_in(self, mode: str) -> bool:
        return self.modes is None or mode in self.modes


def _checks(raw: Any, where: str, case_id: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - set(CHECK_KEYS):
        raise GoldenError(f"{where}: case {case_id} checks may only hold {list(CHECK_KEYS)}")
    rules = raw.get("router_rules")
    if rules is not None and (
        not isinstance(rules, list) or not all(isinstance(r, str) for r in rules)
    ):
        raise GoldenError(f"{where}: case {case_id} checks.router_rules must be a list of ids")
    retrieved = raw.get("retrieved")
    if retrieved is not None and (
        not isinstance(retrieved, dict)
        or set(retrieved) - set(CHECK_STORES)
        or not all(isinstance(v, str) and v for v in retrieved.values())
    ):
        raise GoldenError(
            f"{where}: case {case_id} checks.retrieved maps {list(CHECK_STORES)} to a substring"
        )
    if "compacted" in raw and not isinstance(raw["compacted"], bool):
        raise GoldenError(f"{where}: case {case_id} checks.compacted must be true or false")
    if "summary" in raw and not (isinstance(raw["summary"], str) and raw["summary"]):
        raise GoldenError(f"{where}: case {case_id} checks.summary must be a substring")
    return dict(raw)


def _expect(raw: Any, where: str, case_id: str) -> dict[str, Any]:
    """An `expect` block, with the P0 aliases read and its keys and regex checked."""
    expect = dict(raw or {})
    if "path_includes" in expect and "path" not in expect:
        expect["path"] = expect.pop("path_includes")
    if expect.pop("must_cite", False) and "citations" not in expect:
        expect["citations"] = [{}]
    unknown = set(expect) - EXPECT_KEYS - {"path_excludes", "exit_reason"}
    if unknown:
        raise GoldenError(f"{where}: case {case_id} has unknown expect keys {sorted(unknown)}")
    if expect.get("tool_calls_mode", "ordered") not in ("exact", "ordered"):
        raise GoldenError(f"{where}: case {case_id} tool_calls_mode must be exact or ordered")
    for key in ("answer_contains",):
        if key in expect and isinstance(expect[key], str):
            expect[key] = [expect[key]]
    if "answer_regex" in expect:
        try:
            re.compile(expect["answer_regex"])
        except re.error as exc:
            raise GoldenError(f"{where}: case {case_id} answer_regex: {exc}") from exc
    return expect


def _turns(raw: Any, where: str, case_id: str) -> list[GoldenTurn]:
    if not isinstance(raw, list) or not raw:
        raise GoldenError(f"{where}: case {case_id} turns must be a non-empty list")
    out = []
    for n, item in enumerate(raw, start=1):
        at = f"{where} turn {n}"
        if not isinstance(item, dict) or set(item) - TURN_KEYS:
            raise GoldenError(f"{at}: a turn may only hold {sorted(TURN_KEYS)}")
        user = item.get("user")
        if not isinstance(user, str) or not user.strip():
            raise GoldenError(f"{at}: case {case_id} turn needs a user message")
        out.append(
            GoldenTurn(
                user=user,
                expect=_expect(item.get("expect"), at, case_id),
                checks=_checks(item.get("checks"), at, case_id),
            )
        )
    return out


def normalize(raw: dict[str, Any], where: str) -> GoldenCase:
    if not isinstance(raw, dict):
        raise GoldenError(f"{where}: a case must be a JSON object")
    case_id = raw.get("id")
    if not isinstance(case_id, str) or not CASE_ID.match(case_id):
        raise GoldenError(f"{where}: id must match {CASE_ID.pattern}")
    turns: list[GoldenTurn] = []
    if "turns" in raw:
        if "prompt" in raw or "input" in raw:
            raise GoldenError(f"{where}: case {case_id} has both a prompt and turns")
        turns = _turns(raw["turns"], where, case_id)
        prompt = turns[0].user
    else:
        prompt = raw.get("prompt", raw.get("input"))
    if not isinstance(prompt, str) or not prompt.strip():
        raise GoldenError(f"{where}: case {case_id} needs a prompt")
    fixture = raw.get("fixture")
    if isinstance(fixture, list) and (
        not turns or len(fixture) != len(turns) or not all(isinstance(f, str) for f in fixture)
    ):
        raise GoldenError(f"{where}: case {case_id} fixture list must name one per turn")
    roles = raw.get("roles")
    if roles is not None and (
        not isinstance(roles, list)
        or not roles
        or not all(isinstance(r, str) and ROLE.match(r) for r in roles)
    ):
        raise GoldenError(f"{where}: case {case_id} roles must be a list of role names")
    expect = _expect(raw.get("expect"), where, case_id)
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
    modes = raw.get("modes")
    if modes is not None and (
        not isinstance(modes, list) or not modes or not set(modes) <= set(MODES)
    ):
        raise GoldenError(f"{where}: case {case_id} modes must be a list of {list(MODES)}")
    after = raw.get("after")
    if after is not None and (not isinstance(after, str) or not CASE_ID.match(after)):
        raise GoldenError(f"{where}: case {case_id} after must be a case id")
    return GoldenCase(
        id=case_id,
        title=str(raw.get("title") or case_id),
        prompt=prompt,
        fixture=fixture,
        expect=expect,
        rubric=rubric,
        output_schema=schema,
        approval=approval,
        tags=list(raw.get("tags") or []),
        raw=raw,
        checks=_checks(raw.get("checks"), where, case_id),
        modes=list(modes) if modes is not None else None,
        after=after,
        turns=turns,
        roles=list(roles) if roles is not None else None,
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
        if case.after is not None and case.after not in seen:
            raise GoldenError(
                f"{path.name}:{n}: case {case.id} runs after {case.after!r}, which must be an"
                " earlier case"
            )
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise GoldenError(f"{path} has no cases")
    return cases
