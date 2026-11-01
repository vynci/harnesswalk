"""Payloads: what each content-bearing hop of a turn actually carried, as typed detail records.

Contract: spec/contracts/tracer.md, "Payloads". Not a node: the gateway, the input guardrails,
the context builder, the model layer and the output guardrails call it to build the record
they hand to their span (`span.detail`, or `enter_detail` on `node()`), and the emitter stores
it through the tracer's `put_detail` when `trace.capture_content` is on.

Every record is `{"payload": {...}}` (beside whatever keys the node already kept in its detail),
with `node`, `at` (`enter` or `exit`) and `version`. Every text is a `Text` object: `text`
(at most `MAX_CHARS` characters), `chars` (the full length) and `truncated`.

Events stay small: nothing here goes into a summary. A payload is the user's own text after the
input guardrails replaced their personal data with placeholders; the text as typed is kept only
where the PII restore rule is on for the mode (`input_guardrails.pii.restore_modes`, dev by
default, never public), or when the guardrails did not change it.
"""

from __future__ import annotations

import json
from typing import Any

from harnesswalk.context.tokens import estimate
from harnesswalk.types import Message, TurnContext

VERSION = 1
MAX_CHARS = 32_000
NODES = ("gateway", "input_guardrails", "context_builder", "model_layer", "output_guardrails")
KINDS = ("system", "skill", "retrieved", "history", "user", "assistant", "tool")


def capturing(ctx: TurnContext) -> bool:
    """Whether a detail would be stored at all (`trace.capture_content` on for this turn)."""
    return getattr(ctx.emit, "detail_sink", None) is not None


def text(value: str | None) -> dict[str, Any]:
    """A capped text: the first MAX_CHARS characters, the full length, and whether it was cut."""
    value = value or ""
    return {"text": value[:MAX_CHARS], "chars": len(value), "truncated": len(value) > MAX_CHARS}


def record(node_id: str, at: str, **fields: Any) -> dict[str, Any]:
    return {"node": node_id, "at": at, "version": VERSION, **fields}


def raw_allowed(mode: str, restore_modes: list[str] | tuple[str, ...]) -> bool:
    """Whether the text as typed (before redaction) may be kept: the PII restore rule's modes."""
    return mode != "public" and mode in restore_modes


def placeholders(vault: Any) -> dict[str, Any]:
    """How many values the input guardrails replaced, by kind, and the placeholder tokens.
    Never the values."""
    if vault is None or not len(vault):
        return {"count": 0, "kinds": {}, "tokens": []}
    return {
        "count": len(vault),
        "kinds": dict(vault.counts),
        "tokens": sorted(vault.by_placeholder),
    }


def kept_original(
    original: str, changed: bool, allowed: bool
) -> tuple[dict[str, Any] | None, bool]:
    """The original text when it may be kept, else None and `withheld: true`. A text the
    guardrails did not change is the same as the clean one, so it is always kept."""
    if not changed or allowed:
        return text(original), False
    return None, True


# -- sections ---------------------------------------------------------------------------------


def section(
    message: int,
    role: str,
    kind: str,
    label: str,
    body: str,
    estimator: str = "chars_div_4",
    sources: list[dict[str, Any]] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One section of an assembled context, uncapped (`capped` makes the stored shape)."""
    out: dict[str, Any] = {
        "message": message,
        "role": role,
        "kind": kind,
        "label": label,
        "sources": sources or [],
        "body": body,
        "tokens": estimate(body, estimator),
    }
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def capped(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The stored shape: `index`, the text capped, everything else as is."""
    out = []
    for i, s in enumerate(sections):
        item = {k: v for k, v in s.items() if k != "body"}
        item["index"] = i
        item.update(text(s["body"]))
        if "tool_calls" in item:
            item["tool_calls"] = [tool_call(c) for c in item["tool_calls"]]
        out.append(item)
    return out


def tool_call(call: Any) -> dict[str, Any]:
    """A tool call with its arguments; arguments over the cap become a capped JSON text."""
    data = call if isinstance(call, dict) else call.__dict__
    out = {"call_id": data.get("call_id"), "name": data.get("name")}
    args = data.get("arguments")
    dumped = json.dumps(args, default=str, ensure_ascii=False)
    if len(dumped) > MAX_CHARS:
        out["arguments"] = None
        out["arguments_text"] = text(dumped)
    else:
        out["arguments"] = args
    return out


def calls_of(message: Message) -> list[dict[str, Any]]:
    return [
        {"call_id": c.call_id, "name": c.name, "arguments": c.arguments} for c in message.tool_calls
    ]


def derived_section(index: int, message: Message, estimator: str) -> dict[str, Any]:
    """A section for a message the context builder did not assemble (a later loop step)."""
    if message.role == "tool":
        sources = [{"label": message.tool_call_id or "", "call_id": message.tool_call_id}]
        label = f"{message.name or 'tool'} result"
        return section(index, "tool", "tool", label, message.content, estimator, sources)
    if message.role == "assistant":
        calls = calls_of(message)
        label = "Model step with tool calls" if calls else "Model step"
        return section(
            index, "assistant", "assistant", label, message.content, estimator, tool_calls=calls
        )
    if message.role == "system":
        return section(index, "system", "system", "System", message.content, estimator)
    return section(index, message.role, "user", "User message", message.content, estimator)


def request_sections(ctx: TurnContext, messages: list[Message]) -> list[dict[str, Any]]:
    """The sections of a model request: the context builder's sections for the messages it
    assembled (when they are still the same), derived ones for what the loop added after."""
    assembled = ctx.payload_context or {}
    estimator = assembled.get("estimator", "chars_div_4")
    known: list[str] = assembled.get("contents", [])
    by_message: dict[int, list[dict[str, Any]]] = {}
    for s in assembled.get("sections", []):
        by_message.setdefault(s["message"], []).append(s)
    out: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if i < len(known) and known[i] == m.content and i in by_message:
            out.extend(by_message[i])
        else:
            out.append(derived_section(i, m, estimator))
    return out


def sections_payload(sections: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sections": capped(sections),
        "total_tokens": sum(s["tokens"] for s in sections),
        "messages": len({s["message"] for s in sections}),
    }


def request_payload(ctx: TurnContext, request: Any) -> dict[str, Any]:
    """A model request: its sections (as the context builder labelled them), their total, and
    the names of the tools offered."""
    return {
        **sections_payload(request_sections(ctx, list(request.messages))),
        "tools": [t.name for t in request.tools],
    }
