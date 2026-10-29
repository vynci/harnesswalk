"""Output guardrails: check the answer before it leaves.

Contract: spec/contracts/guardrail.md. Node: `output_guardrails` (execution plane, kind code).

Checks, in configured order:

- `schema`: when a structured reply was requested, the answer must be JSON that validates
  against the schema; otherwise it is blocked with a safe message (the loop is not re-entered).
- `content`: literal blocklist patterns block the answer.
- `length`: an answer over `output_guardrails.max_chars` is repaired by truncation.
- `citations`: required when knowledge chunks were in context. Knowledge is a stub in P1, so
  citations are never required yet; the check reports `required: false`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from harnesswalk.nodes import node, register
from harnesswalk.types import TurnContext

register("output_guardrails", __name__)

SAFE_SCHEMA_MESSAGE = "I could not produce a reply in the required format."
SAFE_CONTENT_MESSAGE = "I cannot share that reply."
STRENGTH = {"pass": 0, "repair": 1, "block": 2}


@dataclass
class OutputResult:
    action: str
    text: str
    checks: list[str]
    reasons: list[str] = field(default_factory=list)
    citations: dict[str, Any] = field(default_factory=lambda: {"required": False, "present": 0})


class OutputGuardrails:
    def __init__(self, cfg: dict[str, Any], mode: str) -> None:
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", True)) or mode == "public"
        self.checks = list(cfg.get("checks", ["schema", "content", "length", "citations"]))
        self.max_chars = int(cfg.get("max_chars", 12000))
        self.blocklist = list((cfg.get("content") or {}).get("blocklist") or [])

    def check(
        self,
        text: str,
        output_schema: dict[str, Any] | None = None,
        knowledge_chunks: int = 0,
        citations: int = 0,
    ) -> OutputResult:
        action, reasons = "pass", []
        result_citations = {"required": False, "present": citations}
        for check_id in self.checks:
            if check_id == "schema" and output_schema is not None:
                try:
                    jsonschema.validate(json.loads(text), output_schema)
                except (json.JSONDecodeError, jsonschema.ValidationError):
                    return OutputResult("block", SAFE_SCHEMA_MESSAGE, self.checks, ["schema"])
            elif check_id == "content":
                if any(p and p in text for p in self.blocklist):
                    return OutputResult("block", SAFE_CONTENT_MESSAGE, self.checks, ["content"])
            elif check_id == "length" and len(text) > self.max_chars:
                text = text[: self.max_chars] + " [reply truncated]"
                action, reasons = "repair", [*reasons, "length"]
            elif check_id == "citations":
                required = knowledge_chunks > 0 and bool(
                    (self.cfg.get("citations") or {}).get("required", True)
                )
                result_citations = {"required": required, "present": citations}
                if required and citations == 0:
                    if (self.cfg.get("citations") or {}).get("on_missing") == "block":
                        return OutputResult(
                            "block", SAFE_CONTENT_MESSAGE, self.checks, ["citations"]
                        )
                    text += "\n\n(No sources were cited for this answer.)"
                    action = max(action, "repair", key=STRENGTH.__getitem__)
                    reasons.append("citations")
        return OutputResult(action, text, self.checks, reasons, result_citations)

    async def run(
        self, ctx: TurnContext, text: str, output_schema: dict[str, Any] | None = None
    ) -> OutputResult:
        async with node(ctx, "output_guardrails") as span:
            if (not self.enabled or ctx.is_disabled("output_guardrails")) and ctx.mode != "public":
                span.skip(disabled=True, action="pass")
                return OutputResult("pass", text, [])
            try:
                result = self.check(text, output_schema)
            except Exception:
                result = OutputResult(
                    "block", SAFE_CONTENT_MESSAGE, self.checks, ["guardrail_error"]
                )
            span.set(action=result.action, checks=result.checks, citations=result.citations)
            if result.reasons:
                span.set(reasons=result.reasons)
        return result
