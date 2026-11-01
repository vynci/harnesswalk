"""Input guardrails: deterministic checks before the model sees anything.

Contract: spec/contracts/guardrail.md. Node: `input_guardrails` (control plane, kind code).

Checks, in configured order:

- `control`: strip control characters (except tab, newline, carriage return) and invisible
  bidirectional overrides. The text changes, so the action is `redact`, with a count.
- `size`: block input longer than `input_guardrails.size.max_chars`.
- `pii`: emails, phones, Luhn-valid card numbers, national ids (patterns per pack) and,
  optionally, names (`pii.py`). Each finding becomes a numbered placeholder (`[EMAIL_1]`) kept
  in the turn's vault, so the reply can put it back when the mode allows (output guardrails).
  `pii.action: block` refuses the message instead.
- `injection`: heuristic score in 0..1 (`injection.py`). At or above `block_threshold` the
  mode decides: `warn` (dev by default) passes with a warning, `block` (public, private)
  refuses. An optional classifier hook may add a model's score (off by default).

A block is a decision, not an error: the span closes `done` with `action: block`, and the
gateway answers with a refusal without calling the context, loop or model. Summaries carry
counts, scores and reasons only, never the input or a matched value.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from harnesswalk.control import injection
from harnesswalk.control.pii import PiiDetector, Vault
from harnesswalk.nodes import node, register
from harnesswalk.ops import payloads
from harnesswalk.types import TurnContext

register("input_guardrails", __name__)

STRENGTH = {"pass": 0, "redact": 1, "repair": 1, "block": 2}
KEEP = {"\t", "\n", "\r"}
BIDI = {chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F)}
IMPLEMENTED = ("control", "size", "pii", "injection")
DEFAULT_INJECTION_ACTION = {"dev": "warn", "private": "block", "public": "block"}

# The optional classifier: given the turn and the text, a score in 0..1, or None on failure.
Classifier = Callable[[TurnContext, str], Awaitable[float | None]]


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
    score: float | None = None  # injection score, 0..1
    signals: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    vault: Vault = field(default_factory=Vault)
    classifier_score: float | None = None

    @property
    def reason(self) -> str | None:
        return next((r.reason for r in self.results if r.action == self.action and r.reason), None)


def change_note(result: CheckResult) -> str | None:
    """What a check did to the text, in words, counts only (never a matched value)."""
    if result.action != "redact" or not result.findings:
        return None
    if result.check == "control":
        n = result.findings.get("control", 0)
        return f"removed {n} control character{'s' if n != 1 else ''}"
    parts = [f"{n} {kind.replace('_', ' ')}" for kind, n in sorted(result.findings.items())]
    return f"replaced {', '.join(parts)} with placeholders"


def strip_control(text: str) -> tuple[str, int]:
    out, removed = [], 0
    for ch in text:
        if (unicodedata.category(ch) == "Cc" and ch not in KEEP) or ch in BIDI:
            removed += 1
        else:
            out.append(ch)
    return "".join(out), removed


class InputGuardrails:
    def __init__(
        self, cfg: dict[str, Any], mode: str, classifier: Classifier | None = None
    ) -> None:
        self.cfg = cfg
        self.mode = mode
        self.enabled = bool(cfg.get("enabled", True)) or mode == "public"
        self.checks = list(cfg.get("checks", ["control", "size"]))
        if "control" not in self.checks:
            self.checks.insert(0, "control")  # cheap and always safe; runs first
        self.max_chars = int(cfg.get("size", {}).get("max_chars", 8000))
        pii_cfg = cfg.get("pii") or {}
        self.pii = PiiDetector(pii_cfg)
        self.pii_action = pii_cfg.get("action", "redact")
        self.restore_modes = list(pii_cfg.get("restore_modes", ["dev"]))  # payloads keep raw text
        inj = cfg.get("injection") or {}
        self.block_threshold = float(inj.get("block_threshold", 0.8))
        actions = {**DEFAULT_INJECTION_ACTION, **(inj.get("action") or {})}
        self.injection_action = "block" if mode == "public" else actions.get(mode, "block")
        self.classifier = classifier if (inj.get("classifier") or {}).get("enabled") else None

    def check(self, text: str) -> GuardrailResult:
        """The deterministic checks. The classifier hook, when on, runs in `run`."""
        results: list[CheckResult] = []
        pending: list[str] = []
        out = GuardrailResult("pass", text, results, pending)
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
            elif check_id == "pii":
                findings = self.pii.find(text)
                counts = PiiDetector.counts(findings)
                if not findings:
                    results.append(CheckResult("pii", "pass"))
                elif self.pii_action == "block":
                    kinds = ", ".join(sorted(counts))
                    reason = f"it contains personal data ({kinds})"
                    results.append(CheckResult("pii", "block", counts, reason=reason))
                    break
                else:
                    text = PiiDetector.redact(text, findings, out.vault)
                    results.append(CheckResult("pii", "redact", counts, text))
            elif check_id == "injection":
                report = injection.score(text)
                out.score, out.signals = report.score, report.signals
                if report.score >= self.block_threshold:
                    if self.injection_action == "block":
                        results.append(
                            CheckResult(
                                "injection",
                                "block",
                                report.signals,
                                reason=f"it looks like a prompt injection"
                                f" (score {report.score:.2f})",
                            )
                        )
                        break
                    out.warnings.append("injection")
                results.append(CheckResult("injection", "pass", report.signals))
            else:
                pending.append(check_id)
        out.action = max((r.action for r in results), key=STRENGTH.__getitem__, default="pass")
        out.text = text
        return out

    async def _classify(self, ctx: TurnContext, result: GuardrailResult) -> None:
        """The optional model hook: may raise the score, never lower it."""
        if self.classifier is None or result.action == "block" or "injection" not in self.checks:
            return
        try:
            value = await self.classifier(ctx, result.text)
        except Exception:
            value = None
        if value is None:
            result.warnings.append("classifier_error")
            return
        result.classifier_score = round(max(0.0, min(1.0, value)), 3)
        result.score = max(result.score or 0.0, result.classifier_score)
        if result.score >= self.block_threshold:
            if self.injection_action == "block":
                result.results.append(
                    CheckResult(
                        "injection",
                        "block",
                        reason=f"it looks like a prompt injection (score {result.score:.2f})",
                    )
                )
                result.action = "block"
            elif "injection" not in result.warnings:
                result.warnings.append("injection")

    def would_change(self, text: str) -> bool:
        """Whether the checks would change this text (control characters, personal data), so
        a payload recorded before they ran knows whether the raw text is sensitive."""
        if not self.enabled:
            return False
        try:
            return bool(strip_control(text)[1]) or (
                "pii" in self.checks and bool(self.pii.find(text))
            )
        except Exception:
            return True

    def _original(self, ctx: TurnContext, text: str, changed: bool) -> dict[str, Any]:
        allowed = payloads.raw_allowed(ctx.mode, self.restore_modes)
        kept, withheld = payloads.kept_original(text, changed, allowed)
        return {"input": kept, "withheld": withheld, "chars": len(text)}

    def payload(self, ctx: TurnContext, text: str, result: GuardrailResult) -> dict[str, Any]:
        """The exit payload: the input (or withheld), each check with what it looked at, its
        verdict and what it changed, and the clean text that goes on (tracer.md, Payloads)."""
        checks: list[dict[str, Any]] = []
        chars, stage = len(text), "received"
        for r in result.results:
            verdict = r.action
            if r.check == "injection" and r.action == "pass" and "injection" in result.warnings:
                verdict = "warn"
            item: dict[str, Any] = {
                "check": r.check,
                "verdict": verdict,
                "looked_at": {"stage": stage, "chars": chars},
                "findings": dict(r.findings),
                "change": change_note(r),
                "reason": r.reason,
            }
            if r.check == "injection" and result.score is not None:
                item["score"] = result.score
            checks.append(item)
            if r.replacement is not None:
                chars, stage = len(r.replacement), f"after {r.check}"
        blocked = result.action == "block"
        return {
            "payload": payloads.record(
                "input_guardrails",
                "exit",
                **self._original(ctx, text, blocked or result.text != text),
                output=None if blocked else payloads.text(result.text),
                action=result.action,
                blocked=blocked,
                checks=checks,
                warnings=list(result.warnings),
                placeholders=payloads.placeholders(result.vault),
            )
        }

    async def run(self, ctx: TurnContext, text: str) -> GuardrailResult:
        capture = payloads.capturing(ctx)
        about = None
        if capture:
            about = {
                "payload": payloads.record(
                    "input_guardrails",
                    "enter",
                    **self._original(ctx, text, self.would_change(text)),
                )
            }
        async with node(ctx, "input_guardrails", enter_detail=about) as span:
            if (not self.enabled or ctx.is_disabled("input_guardrails")) and ctx.mode != "public":
                span.skip(disabled=True, action="pass")
                if capture:
                    span.detail = self.payload(ctx, text, GuardrailResult("pass", text, []))
                    span.detail["payload"]["disabled"] = True
                return GuardrailResult("pass", text, [])
            try:
                result = self.check(text)
                await self._classify(ctx, result)
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
                checks=list(dict.fromkeys(r.check for r in result.results)),
                redactions=redactions,
            )
            if result.score is not None:
                span.set(injection_score=result.score, injection_signals=result.signals)
            if result.classifier_score is not None:
                span.set(classifier_score=result.classifier_score)
            if result.warnings:
                span.set(warnings=result.warnings)
            if result.pending:
                span.set(pending_checks=result.pending)
            if result.reason:
                span.set(reason=result.reason)
            if capture:
                span.detail = self.payload(ctx, text, result)
        ctx.pii_vault = result.vault
        return result
