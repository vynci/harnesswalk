"""Output guardrails: check the answer before it leaves.

Contract: spec/contracts/guardrail.md. Node: `output_guardrails` (execution plane, kind code).

Checks, in configured order:

- `schema`: when the turn asked for a structured reply (`output_schema`), the answer must be
  JSON that validates against it; otherwise it is blocked with a safe message (the loop is not
  re-entered).
- `content`: literal blocklist patterns block the answer.
- `length`: an answer over `output_guardrails.max_chars` is repaired by truncation.
- `citations`: when knowledge chunks were in context, count `[n]` markers, markers that name
  no chunk, and uncited claims (sentences of six or more words without a marker). No citation
  at all is repaired once with a notice, or blocked (`on_missing: block`, or `block_public` in
  public mode). Without knowledge chunks the check reports `required: false`.
- `pii`: personal data in the answer that is not one of this turn's input placeholders is a
  leak (from a tool result, say). It is redacted with a typed placeholder, or blocked.

Then, when the mode is in `input_guardrails.pii.restore_modes`, this turn's placeholders are
put back, so the user sees their own email again. The model never saw it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from harnesswalk.control.pii import PiiDetector
from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("output_guardrails", __name__)

SAFE_SCHEMA_MESSAGE = "I could not produce a reply in the required format."
SAFE_CONTENT_MESSAGE = "I cannot share that reply."
SAFE_CITATION_MESSAGE = "I could not back this answer with a cited source, so I will not give it."
SAFE_PII_MESSAGE = "I cannot share that reply because it contains personal data."
MISSING_NOTICE = "\n\n(No sources were cited for this answer.)"
STRENGTH = {"pass": 0, "repair": 1, "block": 2}
MARKER = re.compile(r"\[(\d{1,3})\]")
SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
CLAIM_WORDS = 6


def citation_report(text: str, chunks: list[dict[str, Any]]) -> dict[str, int]:
    """Markers present, markers naming no chunk, and claim sentences with no marker."""
    known = {int(c.get("n", i + 1)) for i, c in enumerate(chunks)}
    markers = [int(m) for m in MARKER.findall(text)]
    uncited = 0
    for sentence in SENTENCE.findall(text):
        s = sentence.strip()
        if s.endswith("?") or len(s.split()) < CLAIM_WORDS:
            continue
        if not MARKER.search(s):
            uncited += 1
    return {
        "present": len(markers),
        "invalid": sum(1 for n in markers if n not in known),
        "uncited_claims": uncited,
    }


@dataclass
class OutputResult:
    action: str
    text: str
    checks: list[str]
    reasons: list[str] = field(default_factory=list)
    citations: dict[str, Any] = field(default_factory=lambda: {"required": False, "present": 0})
    schema: str = "none"
    leaks: dict[str, int] = field(default_factory=dict)
    restored: int = 0


class OutputGuardrails:
    def __init__(
        self, cfg: dict[str, Any], mode: str, pii_cfg: dict[str, Any] | None = None
    ) -> None:
        self.cfg = cfg
        self.mode = mode
        self.enabled = bool(cfg.get("enabled", True)) or mode == "public"
        self.checks = list(cfg.get("checks", ["schema", "content", "length", "citations", "pii"]))
        self.max_chars = int(cfg.get("max_chars", 12000))
        self.blocklist = list((cfg.get("content") or {}).get("blocklist") or [])
        self.citations_cfg = cfg.get("citations") or {}
        self.pii_action = (cfg.get("pii") or {}).get("action", "repair")
        self.pii = PiiDetector(pii_cfg or {})
        self.restore_modes = list((pii_cfg or {}).get("restore_modes", ["dev"]))
        if mode == "public":
            self.restore_modes = [m for m in self.restore_modes if m != "public"]

    def check(
        self,
        text: str,
        output_schema: dict[str, Any] | None = None,
        knowledge_chunks: list[dict[str, Any]] | None = None,
        citations: int | None = None,
    ) -> OutputResult:
        chunks = knowledge_chunks or []
        action, reasons = "pass", []
        result_citations: dict[str, Any] = {"required": False, "present": citations or 0}
        schema_state, leaks = "none", {}
        ran: list[str] = []
        for check_id in self.checks:
            ran.append(check_id)
            if check_id == "schema":
                if output_schema is None:
                    continue
                try:
                    jsonschema.validate(json.loads(text), output_schema)
                    schema_state = "valid"
                except (json.JSONDecodeError, jsonschema.ValidationError):
                    return OutputResult(
                        "block", SAFE_SCHEMA_MESSAGE, ran, ["schema"], schema="invalid"
                    )
            elif check_id == "content":
                if any(p and p in text for p in self.blocklist):
                    return OutputResult("block", SAFE_CONTENT_MESSAGE, ran, ["content"])
            elif check_id == "length" and len(text) > self.max_chars:
                text = text[: self.max_chars] + " [reply truncated]"
                action, reasons = "repair", [*reasons, "length"]
            elif check_id == "citations":
                required = bool(chunks) and bool(self.citations_cfg.get("required", True))
                report = citation_report(text, chunks)
                if citations is not None:
                    report["present"] = max(report["present"], citations)
                result_citations = {"required": required, **report}
                if not required:
                    continue
                blocking = self.citations_cfg.get("on_missing") == "block" or (
                    self.mode == "public" and self.citations_cfg.get("block_public", False)
                )
                if report["present"] == 0:
                    if blocking:
                        return OutputResult(
                            "block", SAFE_CITATION_MESSAGE, ran, ["citations"], result_citations
                        )
                    text += MISSING_NOTICE
                    action = max(action, "repair", key=STRENGTH.__getitem__)
                    reasons.append("citations")
                elif report["uncited_claims"] and blocking and self.mode == "public":
                    return OutputResult(
                        "block", SAFE_CITATION_MESSAGE, ran, ["uncited_claims"], result_citations
                    )
            elif check_id == "pii":
                findings = self.pii.find(text)  # this turn's placeholders are never findings
                if findings:
                    leaks = PiiDetector.counts(findings)
                    if self.pii_action == "block":
                        return OutputResult(
                            "block", SAFE_PII_MESSAGE, ran, ["pii"], result_citations, leaks=leaks
                        )
                    text = PiiDetector.redact(text, findings)
                    action = max(action, "repair", key=STRENGTH.__getitem__)
                    reasons.append("pii")
        return OutputResult(action, text, ran, reasons, result_citations, schema_state, leaks)

    async def run(
        self,
        ctx: TurnContext,
        text: str,
        output_schema: dict[str, Any] | None = None,
        knowledge_chunks: list[dict[str, Any]] | None = None,
    ) -> OutputResult:
        async with node(ctx, "output_guardrails") as span:
            if (not self.enabled or ctx.is_disabled("output_guardrails")) and ctx.mode != "public":
                restored_text, restored = self._restore(ctx, text)
                span.skip(disabled=True, action="pass", restored=restored)
                return OutputResult("pass", restored_text, [], restored=restored)
            try:
                result = self.check(text, output_schema, knowledge_chunks)
            except Exception:
                result = OutputResult(
                    "block", SAFE_CONTENT_MESSAGE, self.checks, ["guardrail_error"]
                )
            if result.action != "block":
                result.text, result.restored = self._restore(ctx, result.text)
            span.set(
                action=result.action,
                checks=result.checks,
                citations=result.citations,
                schema=result.schema,
                restored=result.restored,
            )
            if result.leaks:
                span.set(leaks=result.leaks)
            if result.reasons:
                span.set(reasons=result.reasons)
        return result

    def _restore(self, ctx: TurnContext, text: str) -> tuple[str, int]:
        vault = ctx.pii_vault
        if vault is None or not len(vault) or ctx.mode not in self.restore_modes:
            return text, 0
        return vault.restore(text)
