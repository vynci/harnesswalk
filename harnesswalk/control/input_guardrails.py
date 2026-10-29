"""Input guardrails: deterministic checks before the model sees anything.

Contract: spec/contracts/guardrail.md. Node: `input_guardrails` (control plane, kind code).

P1 checks, in configured order:

- `control`: strip control characters (except tab, newline, carriage return) and invisible
  bidirectional overrides. The text changes, so the action is `redact`, with a count.
- `size`: block input longer than `input_guardrails.size.max_chars`.
- `pii` and `injection` are listed in the config but arrive in P3; P1 reports them as
  `pending_checks` instead of pretending to run them.

A block is a decision, not an error: the span closes `done` with `action: block`, and the
gateway answers with a refusal without calling the context, loop or model. Summaries carry
counts and reasons only, never the input.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("input_guardrails", __name__)

STRENGTH = {"pass": 0, "redact": 1, "repair": 1, "block": 2}
KEEP = {"\t", "\n", "\r"}
BIDI = {chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F)}
IMPLEMENTED = ("control", "size")


@dataclass
class CheckResult:
    check: str
    action: str
    findings: dict[str, int] = field(default_factory=dict)
    replacement: str | None = None
    reason: str | None = None


@dataclass
class GuardrailResult:
    action: str
    text: str
    results: list[CheckResult]
    pending: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str | None:
        return next((r.reason for r in self.results if r.action == self.action and r.reason), None)


def strip_control(text: str) -> tuple[str, int]:
    out, removed = [], 0
    for ch in text:
        if (unicodedata.category(ch) == "Cc" and ch not in KEEP) or ch in BIDI:
            removed += 1
        else:
            out.append(ch)
    return "".join(out), removed


class InputGuardrails:
    def __init__(self, cfg: dict[str, Any], mode: str) -> None:
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", True)) or mode == "public"
        self.checks = list(cfg.get("checks", ["control", "size"]))
        if "control" not in self.checks:
            self.checks.insert(0, "control")  # cheap and always safe; runs first
        self.max_chars = int(cfg.get("size", {}).get("max_chars", 8000))

    def check(self, text: str) -> GuardrailResult:
        results: list[CheckResult] = []
        pending: list[str] = []
        for check_id in self.checks:
            if check_id == "control":
                cleaned, removed = strip_control(text)
                if removed:
                    results.append(CheckResult("control", "redact", {"control": removed}, cleaned))
                    text = cleaned
                else:
                    results.append(CheckResult("control", "pass"))
            elif check_id == "size":
                if len(text) > self.max_chars:
                    results.append(
                        CheckResult(
                            "size",
                            "block",
                            {"chars": len(text)},
                            reason=f"input is {len(text)} characters,"
                            f" the limit is {self.max_chars}",
                        )
                    )
                    break  # a block stops later checks
                results.append(CheckResult("size", "pass"))
            else:
                pending.append(check_id)
        action = max((r.action for r in results), key=STRENGTH.__getitem__, default="pass")
        return GuardrailResult(action, text, results, pending)

    async def run(self, ctx: TurnContext, text: str) -> GuardrailResult:
        async with node(ctx, "input_guardrails") as span:
            if (not self.enabled or ctx.is_disabled("input_guardrails")) and ctx.mode != "public":
                span.skip(disabled=True, action="pass")
                return GuardrailResult("pass", text, [])
            try:
                result = self.check(text)
            except Exception:
                result = GuardrailResult(
                    "block", "", [CheckResult("error", "block", reason="guardrail_error")]
                )
            redactions: dict[str, int] = {}
            for r in result.results:
                if r.action == "redact":
                    redactions.update(r.findings)
            span.set(
                action=result.action,
                checks=[r.check for r in result.results],
                redactions=redactions,
            )
            if result.pending:
                span.set(pending_checks=result.pending)
            if result.reason:
                span.set(reason=result.reason)
        return result
