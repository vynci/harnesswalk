"""Output guardrails: check the answer before it leaves.

Contract: spec/contracts/guardrail.md. Node: `output_guardrails` (execution plane, kind code).

Checks, in configured order:

- `schema`: when the turn asked for a structured reply (`output_schema`), the answer must be
  JSON that validates against it. An answer that wraps it (prose and a fenced JSON block, or
  JSON with a sentence before or after, as a real model often writes after a tool call) is
  repaired to the first JSON value in it that validates (`schema_extracted` in the exit
  payload). Otherwise it is blocked with a safe message (the loop is not re-entered).
- `content`: literal blocklist patterns block the answer.
- `length`: an answer over `output_guardrails.max_chars` is repaired by truncation.
- `citations`: when knowledge chunks were in context (`ctx.citations`, numbered by the context
  builder) and the answer makes a claim from them (a sentence that shares enough content
  words with a chunk, and more than with the turn's tool results or recalled facts), count
  `[n]` markers, markers that name no chunk, and drawn claims without a marker. An answer
  that cites nothing on a turn that ran tools is the tools' answer and needs no citation.
  No citation at all is repaired once with a notice, or blocked (`on_missing: block`, or
  `block_public` in public mode). An answer that draws nothing from
  the chunks, or no chunks at all, reports `required: false`.
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
from harnesswalk.ops import payloads
from harnesswalk.types import TurnContext

register("output_guardrails", __name__)

SAFE_SCHEMA_MESSAGE = "I could not produce a reply in the required format."
FENCE = re.compile(r"```[ \t]*(?:json|JSON)?[ \t]*\n(.*?)```", re.S)
SAFE_CONTENT_MESSAGE = "I cannot share that reply."
SAFE_CITATION_MESSAGE = "I could not back this answer with a cited source, so I will not give it."
SAFE_PII_MESSAGE = "I cannot share that reply because it contains personal data."
MISSING_NOTICE = "\n\n(No sources were cited for this answer.)"
STRENGTH = {"pass": 0, "repair": 1, "block": 2}
MARKER = re.compile(r"\[(\d{1,3})\]")
SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
CLAIM_WORDS = 6


STOPWORDS = frozenset(
    [
        "about",
        "above",
        "after",
        "again",
        "also",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "could",
        "does",
        "doing",
        "down",
        "during",
        "each",
        "from",
        "further",
        "have",
        "having",
        "here",
        "into",
        "itself",
        "just",
        "more",
        "most",
        "must",
        "only",
        "other",
        "over",
        "same",
        "should",
        "some",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "under",
        "until",
        "very",
        "what",
        "when",
        "where",
        "which",
        "while",
        "will",
        "with",
        "would",
        "your",
        "yours",
        "please",
        "thanks",
        "thank",
    ]
)
DRAWN_MIN_WORDS = 3
DRAWN_MIN_SHARE = 0.4


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 4} - STOPWORDS


def _overlap(words: set[str], texts: list[str]) -> int:
    return max((len(words & _words(t)) for t in texts), default=0)


def draws_from(
    sentence: str, chunks: list[dict[str, Any]], others: list[str] | None = None
) -> bool:
    """Whether a sentence makes a claim from a chunk. Deterministic, crude on purpose:

    - it shares at least three content words with one chunk, or two in five of its own
      content words;
    - and the turn's other sources do not explain it as well (P5 integration): a sentence that
      shares as many words with a tool result, or with a fact recalled from semantic memory,
      as with the chunk is that source's answer, not a claim from the documents. Episodes are
      not such a source: they hold earlier replies, so an uncited answer would explain itself
      the next time."""
    words = _words(sentence)
    if not words:
        return False
    best = 0
    for chunk in chunks:
        shared = len(words & _words(str(chunk.get("text", ""))))
        if shared >= DRAWN_MIN_WORDS or shared / len(words) >= DRAWN_MIN_SHARE:
            best = max(best, shared)
    if not best:
        return False
    return not others or _overlap(words, others) < best


def citation_report(
    text: str, chunks: list[dict[str, Any]], others: list[str] | None = None
) -> dict[str, int]:
    """Markers present, markers naming no chunk, claims drawn from the chunks, and the drawn
    claims (sentences of six or more words) without a marker. `others` are the turn's other
    sources: tool results and recalled facts (see `draws_from`).

    A chunk without its text (a caller that only numbers sources) counts every claim sentence
    as drawn from it, the strict reading.
    """
    known = {int(c.get("n", i + 1)) for i, c in enumerate(chunks)}
    markers = [int(m) for m in MARKER.findall(text)]
    has_text = any(c.get("text") for c in chunks)
    drawn = uncited = 0
    for sentence in SENTENCE.findall(text):
        s = sentence.strip()
        if s.endswith("?") or len(s.split()) < CLAIM_WORDS:
            continue
        cited = bool(MARKER.search(s))
        if cited or not has_text or draws_from(s, chunks, others):
            drawn += 1
            if not cited:
                uncited += 1
    return {
        "present": len(markers),
        "invalid": sum(1 for n in markers if n not in known),
        "drawn_claims": drawn,
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
    schema_extracted: bool = False  # the JSON reply was taken out of prose or a code fence
    # Per check, in order: {check, verdict (pass, repair, block, skipped), note}. Payloads only.
    verdicts: list[dict[str, Any]] = field(default_factory=list)


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
        observations: list[str] | None = None,
        recalled: list[str] | None = None,
    ) -> OutputResult:
        """`observations` are this turn's tool results, `recalled` the facts semantic memory put
        in context: sources an answer may use without a citation."""
        chunks = knowledge_chunks or []
        action, reasons = "pass", []
        result_citations: dict[str, Any] = {"required": False, "present": citations or 0}
        schema_state, leaks = "none", {}
        extracted = False
        ran: list[str] = []
        verdicts: list[dict[str, Any]] = []

        def said(check: str, verdict: str, note: str) -> None:
            verdicts.append({"check": check, "verdict": verdict, "note": note})

        def blocked(result: OutputResult, note: str) -> OutputResult:
            said(ran[-1], "block", note)
            result.verdicts = verdicts
            return result

        for check_id in self.checks:
            ran.append(check_id)
            if check_id == "schema":
                if output_schema is None:
                    said("schema", "skipped", "no structured reply was asked for")
                    continue
                try:
                    jsonschema.validate(json.loads(text), output_schema)
                    schema_state = "valid"
                    said("schema", "pass", "valid JSON for the requested schema")
                except (json.JSONDecodeError, jsonschema.ValidationError):
                    found = extract_json(text, output_schema)
                    if found is not None:
                        text, schema_state, extracted = found, "valid", True
                        said("schema", "repair", "the valid JSON value taken out of the reply")
                        action = max(action, "repair", key=STRENGTH.__getitem__)
                        reasons.append("schema")
                        continue
                    return blocked(
                        OutputResult(
                            "block", SAFE_SCHEMA_MESSAGE, ran, ["schema"], schema="invalid"
                        ),
                        "not valid for the requested schema; a safe message replaces it",
                    )
            elif check_id == "content":
                if any(p and p in text for p in self.blocklist):
                    return blocked(
                        OutputResult("block", SAFE_CONTENT_MESSAGE, ran, ["content"]),
                        "a blocklisted pattern; a safe message replaces it",
                    )
                said("content", "pass", f"no blocklisted text ({len(self.blocklist)} patterns)")
            elif check_id == "length" and len(text) > self.max_chars:
                said("length", "repair", f"{len(text)} characters, cut to {self.max_chars}")
                text = text[: self.max_chars] + " [reply truncated]"
                action, reasons = "repair", [*reasons, "length"]
            elif check_id == "length":
                said("length", "pass", f"{len(text)} characters, the limit is {self.max_chars}")
            elif check_id == "citations":
                report = citation_report(text, chunks, [*(observations or []), *(recalled or [])])
                if citations is not None:
                    report["present"] = max(report["present"], citations)
                # Required only when the answer makes a claim from the chunks (guardrail.md):
                # knowledge in context that the answer does not use is not penalised. An
                # answer that cites nothing on a turn that ran tools is the tools' answer (a
                # lookup), so it is not required to cite the documents either (P5 integration).
                from_tools = bool(observations) and report["present"] == 0
                required = (
                    bool(chunks)
                    and bool(self.citations_cfg.get("required", True))
                    and (report["drawn_claims"] > 0 or report["present"] > 0)
                    and not from_tools
                )
                result_citations = {"required": required, **report}
                if from_tools and report["drawn_claims"]:
                    result_citations["from_tools"] = True
                if not required:
                    why = "the answer uses no knowledge passage" if chunks else "no passages"
                    said("citations", "pass", f"not required: {why}")
                    continue
                blocking = self.citations_cfg.get("on_missing") == "block" or (
                    self.mode == "public" and self.citations_cfg.get("block_public", False)
                )
                if report["present"] == 0:
                    if blocking:
                        return blocked(
                            OutputResult(
                                "block", SAFE_CITATION_MESSAGE, ran, ["citations"], result_citations
                            ),
                            "no citation for a claim from the passages",
                        )
                    said("citations", "repair", "no citation: a notice was added")
                    text += MISSING_NOTICE
                    action = max(action, "repair", key=STRENGTH.__getitem__)
                    reasons.append("citations")
                elif report["uncited_claims"] and blocking and self.mode == "public":
                    return blocked(
                        OutputResult(
                            "block",
                            SAFE_CITATION_MESSAGE,
                            ran,
                            ["uncited_claims"],
                            result_citations,
                        ),
                        f"{report['uncited_claims']} claims without a citation",
                    )
                else:
                    said("citations", "pass", f"{report['present']} citation markers")
            elif check_id == "pii":
                findings = self.pii.find(text)  # this turn's placeholders are never findings
                if findings:
                    leaks = PiiDetector.counts(findings)
                    kinds = ", ".join(
                        f"{n} {k.replace('_', ' ')}" for k, n in sorted(leaks.items())
                    )
                    if self.pii_action == "block":
                        return blocked(
                            OutputResult(
                                "block",
                                SAFE_PII_MESSAGE,
                                ran,
                                ["pii"],
                                result_citations,
                                leaks=leaks,
                            ),
                            f"personal data not from the input ({kinds})",
                        )
                    said("pii", "repair", f"redacted personal data not from the input ({kinds})")
                    text = PiiDetector.redact(text, findings)
                    action = max(action, "repair", key=STRENGTH.__getitem__)
                    reasons.append("pii")
                else:
                    said("pii", "pass", "no personal data beyond this turn's placeholders")
        result = OutputResult(action, text, ran, reasons, result_citations, schema_state, leaks)
        result.verdicts = verdicts
        result.schema_extracted = extracted
        return result

    async def run(
        self,
        ctx: TurnContext,
        text: str,
        output_schema: dict[str, Any] | None = None,
        knowledge_chunks: list[dict[str, Any]] | None = None,
        observations: list[str] | None = None,
    ) -> OutputResult:
        capture = payloads.capturing(ctx)
        about = None
        if capture:
            about = {
                "payload": payloads.record(
                    "output_guardrails", "enter", before=payloads.text(payloads.outside(ctx, text))
                )
            }
        async with node(ctx, "output_guardrails", enter_detail=about) as span:
            if (not self.enabled or ctx.is_disabled("output_guardrails")) and ctx.mode != "public":
                restored_text, restored = self._restore(ctx, text)
                span.skip(disabled=True, action="pass", restored=restored)
                skipped = OutputResult("pass", restored_text, [], restored=restored)
                if capture:
                    span.detail = self.payload(ctx, text, skipped, disabled=True)
                return skipped
            try:
                result = self.check(
                    text,
                    output_schema,
                    knowledge_chunks,
                    observations=observations,
                    recalled=list(ctx.recalled),
                )
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
            if capture:
                span.detail = self.payload(ctx, text, result)
        return result

    def payload(
        self, ctx: TurnContext, before: str, result: OutputResult, disabled: bool = False
    ) -> dict[str, Any]:
        """The exit payload: the answer before and the reply after, every check's verdict, the
        repairs made and how many placeholders were put back (tracer.md, Payloads)."""
        out = payloads.record(
            "output_guardrails",
            "exit",
            before=payloads.text(payloads.outside(ctx, before)),
            after=payloads.text(result.text),
            action=result.action,
            repairs=list(result.reasons) if result.action == "repair" else [],
            reasons=list(result.reasons),
            checks=list(result.verdicts),
            restored=result.restored,
            restore_mode=ctx.mode in self.restore_modes,
        )
        if disabled:
            out["disabled"] = True
        if result.schema_extracted:
            out["schema_extracted"] = True
        return {"payload": out}

    def _restore(self, ctx: TurnContext, text: str) -> tuple[str, int]:
        vault = ctx.pii_vault
        if vault is None or not len(vault) or ctx.mode not in self.restore_modes:
            return text, 0
        return vault.restore(text)


def extract_json(text: str, schema: dict[str, Any]) -> str | None:
    """The first JSON value in `text` that validates against `schema`, as JSON text: the
    fenced blocks first (in order), then every object or array that starts in the text. None
    when nothing validates."""
    candidates = [m.group(1).strip() for m in FENCE.finditer(text or "")]
    decoder = json.JSONDecoder()
    for start, char in enumerate(text or ""):
        if char in "{[":
            try:
                _, end = decoder.raw_decode(text, start)
            except json.JSONDecodeError:
                continue
            candidates.append(text[start:end])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            jsonschema.validate(value, schema)
        except (json.JSONDecodeError, jsonschema.ValidationError):
            continue
        return candidate
    return None
