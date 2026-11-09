"""Curate: traces in, a dataset out. Contract: spec/contracts/tuning.md (Curate).

Node: `curate` (expert child of `eval`, kind code). A curation

1. builds scenarios per pack: every golden case, plus `variants` rewrites of each one-prompt
   case's prompt written by the teacher (same records, so the same expectations);
2. runs them as `model`-suite eval runs pinned to the teacher, through the whole harness, on a
   candidate view whose system prompt carries the control arm's addition when
   `teacher_prompt_arm` is on;
3. keeps the case turns every deterministic scorer passed;
4. exports each kept turn from the trace: the last model call's request (system and context as
   the harness built them, with the control arm's addition taken out again), the tool calls and
   tool results, and the reply, plus the JSON schemas of the tools that call offered;
5. deduplicates by content hash, splits off a validation share by the same hash, writes the
   JSONL files under `tune.artifacts_dir` and records a `dataset/<name>` manifest.

Progress is on the ops stream `curate:<curation_id>` with the eval run event types and
`node_id: curate` (events.md section 14).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harnesswalk.execution.providers import ProviderRequest
from harnesswalk.nodes import register
from harnesswalk.ops.artifacts import canonical, sha256
from harnesswalk.ops.eval.golden import GoldenCase
from harnesswalk.types import Message

if TYPE_CHECKING:
    from harnesswalk.ops.tune import Tuning

register("curate", __name__)

REWRITE_SYSTEM = (
    "You write test variations of one customer message for a support assistant's evaluation"
    " set. Rewrite the message {n} different ways: vary the wording, length, tone and sentence"
    " order, as different real customers would write it. Keep the same request and intent, and"
    " keep every identifier exactly as written (order, tracking, account and case ids, names,"
    " dates, amounts). Never answer the message. Reply with JSON only: a list of {n} strings."
)
# The fake teacher's rewrites: fixed, so CI and the public replay are deterministic.
FAKE_TEMPLATES = (
    "Hi there. {prompt}",
    "{prompt} Thanks!",
    "Quick question: {prompt}",
    "Hello, {prompt}",
    "{prompt} Thank you for your help.",
    "Good morning. {prompt}",
    "Sorry to bother you. {prompt}",
    "{prompt} Appreciate it.",
)
SOURCE = "dataset packs only (synthetic, CC0); no customer data and no real tenant's turns"


def stream_of(curation_id: str) -> str:
    return f"curate:{curation_id}"


def rewrite_id(case_id: str, n: int) -> str:
    return f"{case_id}_p{n}"[:64]


def parse_rewrites(text: str | None, n: int) -> list[str]:
    """The teacher's JSON list of rewrites, also when wrapped in a fence or a sentence."""
    match = re.search(r"\[.*\]", text or "", re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    out = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return out[:n]


def keeps_ids(original: str, rewrite: str) -> bool:
    """Every identifier-looking token of the original (letters and digits joined by a dash,
    or a number of three digits or more) is still in the rewrite."""
    tokens = re.findall(r"\b[A-Z]{1,4}-\d[\dA-Z-]*\b|\b\d{3,}(?:\.\d+)?\b", original)
    return all(t in rewrite for t in tokens)


async def rewrites(
    tuning: Tuning, teacher: str, case: GoldenCase, variants: int, seed: int
) -> tuple[list[str], dict[str, int]]:
    """`variants` rewrites of the case's prompt. Returns (rewrites, usage)."""
    usage = {"input_tokens": 0, "output_tokens": 0}
    if variants <= 0:
        return [], usage
    h = tuning.h
    cfg = h.settings.get(f"models.models.{teacher}")
    if cfg.get("provider") == "fake":
        return [t.format(prompt=case.prompt) for t in FAKE_TEMPLATES[:variants]], usage
    provider = h.turn_providers(cfg["provider"], None)[cfg["provider"]]
    params = dict(cfg.get("params") or {})
    if cfg["provider"] == "ollama":
        params["options"] = {**(params.get("options") or {}), "temperature": 0, "seed": seed}
    request = ProviderRequest(
        model=cfg.get("name", teacher),
        messages=[
            Message("system", REWRITE_SYSTEM.format(n=variants)),
            Message("user", case.prompt),
        ],
        tools=[],
        max_output_tokens=200 * variants,
        params=params,
        context_tokens=cfg.get("context_tokens"),
    )
    response = await provider.complete(request)
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    found = [r for r in parse_rewrites(response.text, variants * 2) if keeps_ids(case.prompt, r)]
    return [r for r in found if r != case.prompt][:variants], usage


def scenarios_for(cases: list[GoldenCase], texts: dict[str, list[str]]) -> list[GoldenCase]:
    """Each case followed by its rewrites (same expectations, a new id and prompt)."""
    out: list[GoldenCase] = []
    for case in cases:
        out.append(case)
        for n, text in enumerate(texts.get(case.id, []), start=1):
            out.append(dataclasses.replace(case, id=rewrite_id(case.id, n), prompt=text))
    return out


def chunks(cases: list[GoldenCase], size: int) -> list[list[GoldenCase]]:
    """The scenarios in runs of about `size`, each a fresh scratch tenant (so a long curation
    stays inside the per-tenant hourly budgets a chat tenant has). A case and its rewrites stay
    together, and so do cases linked by `after` (they read each other's memory writes)."""
    groups: list[list[GoldenCase]] = []
    where: dict[str, int] = {}
    for case in cases:
        rewrite = re.match(r"^(.*)_p\d+$", case.id)
        base = rewrite.group(1) if rewrite and rewrite.group(1) in where else case.id
        at = where.get(base)
        if at is None and case.after is not None:
            at = where.get(case.after)
        if at is None:
            groups.append([])
            at = len(groups) - 1
        groups[at].append(case)
        where[case.id] = at
    out: list[list[GoldenCase]] = []
    for group in groups:
        if out and len(out[-1]) + len(group) <= max(1, size):
            out[-1].extend(group)
        else:
            out.append(list(group))
    return out


def to_chat(message: dict[str, Any]) -> dict[str, Any]:
    """A harness message as the common chat format the trainers render (tools.md shapes)."""
    role = message["role"]
    out: dict[str, Any] = {"role": role, "content": message.get("content") or ""}
    if role == "assistant" and message.get("tool_calls"):
        out["tool_calls"] = [
            {
                "id": c.get("call_id") or f"call_{i}",
                "type": "function",
                "function": {"name": c["name"], "arguments": c.get("arguments") or {}},
            }
            for i, c in enumerate(message["tool_calls"], start=1)
        ]
    if role == "tool":
        out["tool_call_id"] = message.get("tool_call_id") or ""
        if message.get("name"):
            out["name"] = message["name"]
    return out


def tool_schema(spec: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def example_of(
    tuning: Tuning, tenant_id: str, turn_id: str, view: Any, arm_text: str | None
) -> dict[str, Any] | None:
    """One supervised example from a stored case turn: the last model call, whole."""
    h = tuning.h
    events = h.tracer.get_turn(tenant_id, turn_id) or []
    exits = [e for e in events if e["type"] == "node_exit" and e.get("node_id") == "model_layer"]
    if not exits:
        return None
    detail = h.tracer.get_detail(tenant_id, exits[-1].get("detail_ref") or "") or {}
    request, response = detail.get("request"), detail.get("response") or {}
    if not request or response.get("tool_calls") or not (response.get("text") or "").strip():
        return None  # no content kept, or the turn did not end on an answer
    messages = []
    for m in request:
        chat = to_chat(m)
        if chat["role"] == "system" and arm_text:
            chat["content"] = chat["content"].replace(arm_text, "")
        messages.append(chat)
    messages.append({"role": "assistant", "content": response["text"]})
    specs = {s.name: s for s in view.registry.all()}
    tools = [tool_schema(specs[name]) for name in detail.get("tools") or [] if name in specs]
    return {"messages": messages, "tools": tools}


def content_hash(example: dict[str, Any]) -> str:
    return sha256(canonical({"messages": example["messages"], "tools": example["tools"]}))


def is_val(digest: str, fraction: float) -> bool:
    return int(digest[:8], 16) / 2**32 < fraction


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    path.write_text(text, encoding="utf-8")
    data = text.encode("utf-8")
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
