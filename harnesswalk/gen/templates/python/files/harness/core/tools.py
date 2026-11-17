"""Tools: resolve in the registry, validate, ask policy, wait for approval, run in a sandbox.

Contract: spec/contracts/tool_registry.md and sandbox.md. Node: `tools`, expert children
`registry` and `sandbox`. Always on. Policy, approval and audit are reached through three
seams below (`decide`, `ask`, `record`): when one is off the seam emits its skipped span and
answers the way harness.md section 10.3 says, so the walk keeps its shape on every rung.

Lesson: the model's tool call is a request, not a command. Nothing it asks for runs until the
registry knows the tool, the arguments fit the schema and the policy (and, when it says so, a
human) has allowed it.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import re
import threading
from pathlib import Path
from typing import Any

import yaml

from harness.core.events import Turn, off_node

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"  # your code tools live here
HARNESS_TOOLS = {
    "remember": {"description": "Propose a durable memory. kind=fact needs key and value;"
                 " kind=event needs text.", "action_class": "memory.write", "timeout_ms": 1000,
                 "parameters": {"type": "object", "additionalProperties": False,
                                "required": ["kind"], "properties": {
                                    "kind": {"type": "string", "enum": ["fact", "event"]},
                                    "key": {"type": "string", "minLength": 1, "maxLength": 120},
                                    "value": {"type": "string", "minLength": 1, "maxLength": 500},
                                    "text": {"type": "string", "minLength": 1, "maxLength": 500}}}},
    "read_skill": {"description": "Read a skill listed in the skills index in full, by name.",
                   "action_class": "read", "timeout_ms": 2000,
                   "parameters": {"type": "object", "additionalProperties": False,
                                  "required": ["name"],
                                  "properties": {"name": {"type": "string", "maxLength": 80}}}},
    "read_doc": {"description": "Read a document listed in the documents index in full, by its"
                 " id.", "action_class": "read", "timeout_ms": 2000,
                 "parameters": {"type": "object", "additionalProperties": False,
                                "required": ["doc"],
                                "properties": {"doc": {"type": "string", "maxLength": 80}}}},
}  # fmt: skip


def check_schema(schema: dict[str, Any], value: Any, at: str = "$") -> list[str]:
    """The JSON Schema subset tools and output schemas use: type, enum, required,
    properties, additionalProperties, items, pattern, minLength, maxLength."""
    types = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
             "boolean": bool, "null": type(None)}  # fmt: skip
    t = schema.get("type")
    if t and not isinstance(value, types.get(t, object)) or (t in ("integer", "number")
                                                             and isinstance(value, bool)):
        return [f"{at} is not {t}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{at} is not one of {schema['enum']}"]
    out: list[str] = []
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        out += [f"{at}.{k} is required" for k in schema.get("required") or [] if k not in value]
        if schema.get("additionalProperties") is False:
            out += [f"{at}.{k} is not allowed" for k in value if k not in props]
        for k, sub in props.items():
            if k in value:
                out += check_schema(sub, value[k], f"{at}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            out += check_schema(schema["items"], item, f"{at}[{i}]")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{at} does not match {schema['pattern']}")
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 1e9):
            out.append(f"{at} has the wrong length")
    return out


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Registry:
    """Pack tools from `tools/tools.yaml` (table lookups) plus the harness's own tools."""

    def __init__(self, tools_text: str, builtins: list[str]) -> None:
        self.specs: dict[str, dict[str, Any]] = {}
        for name in builtins:
            if name in HARNESS_TOOLS:
                self.specs[name] = {"name": name, **HARNESS_TOOLS[name]}
        for spec in yaml.safe_load(tools_text) or []:
            if "action_class" not in spec or "timeout_ms" not in spec:
                raise ValueError(f"tool {spec.get('name')} needs action_class and timeout_ms")
            self.specs[spec["name"]] = spec

    def offered(self, turn: Turn, allow: list[str] | None) -> list[dict[str, Any]]:
        return [{"name": s["name"], "description": s.get("description", ""),
                 "parameters": s["parameters"], "action_class": s["action_class"]}
                for s in self.specs.values()
                if (allow is None or s["name"] in allow)
                and not (s["name"] == "read_doc" and turn.is_off("knowledge"))]  # fmt: skip


def run_entrypoint(spec: dict[str, Any], args: dict[str, Any]) -> Any:
    """A tool that is code: `entrypoint: file.py:function` (relative to tools/) or
    `module:function`. The function takes the validated arguments (and `data`, when the tool
    declares it) and returns the observation."""
    target, _, name = spec["entrypoint"].rpartition(":")
    if target.endswith(".py"):
        path = TOOLS_DIR / target
        loaded = importlib.util.spec_from_file_location(f"tools_{path.stem}", path)
        module = importlib.util.module_from_spec(loaded)
        loaded.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    fn = getattr(module, name)
    return fn(args, spec["data"]) if "data" in spec else fn(args)


def run_table(spec: dict[str, Any], args: dict[str, Any]) -> Any:
    key = args.get((spec["parameters"].get("required") or [""])[0])
    return (spec.get("table") or {}).get(key, spec.get("missing", {"error": "not_found"}))


# -- the seams: policy, approval and audit ----------------------------------------------------
def decide(h: Any, turn: Turn, call: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    if turn.is_off("policy"):
        cls = spec["action_class"]
        off_node(turn, "policy", tool=call["name"], action_class=cls, decision="allow",
                 rule="disabled")  # fmt: skip
        return {"decision": "allow", "rule": "disabled", "audit": cls != "read",
                "reason": "policy is off, so every call is allowed"}  # fmt: skip
#% if has("policy")
    from harness.core import policy  # the seam imports the node where it calls it

    return policy.check(h, turn, call, spec)
#% endif


def ask(h: Any, turn: Turn, call: dict[str, Any], spec: dict[str, Any], decision: dict[str, Any],
        resume: dict[str, Any] | None) -> str:  # fmt: skip
    if turn.is_off("approval"):
        off_node(turn, "approval", tool=call["name"], decision="deny")
        return "deny"
#% if has("approval")
    from harness.core import approval

    return approval.request(h, turn, call, spec, decision, resume)
#% endif


def record(h: Any, turn: Turn, entry: dict[str, Any]) -> None:
    if turn.is_off("audit"):
        off_node(turn, "audit", action=entry["action"], tool=entry.get("tool"),
                 decision=entry.get("decision"))  # fmt: skip
        return
#% if has("audit")
    from harness.core import audit

    audit.append(h, turn, entry)
#% endif


# -- the tools node ---------------------------------------------------------------------------
def invoke(h: Any, turn: Turn, call: dict[str, Any], resume: dict[str, Any] | None = None):
    """One tool call to one observation. `resume` continues a call that a restart left
    waiting on its approval (the tools and approval spans are still open in the stream)."""
    name, args = call["name"], call.get("arguments") or {}
    digest = hashlib.sha256(dumps(args).encode()).hexdigest()[:16]
    base = {"tool": name, "turn_id": turn.ids["turn_id"], "trace_id": turn.ids["trace_id"],
            "args_digest": digest}  # fmt: skip
    with turn.span("tools", resume=bool(resume), tool=name, call_id=call["call_id"]) as span:
        spec = turn.registry.specs.get(name)
        if resume:
            decision = resume["decision"]
        else:
            with turn.span("registry") as r:
                r.set(tool=name, found=spec is not None, action_class=(spec or {}).get("action_class"),
                      timeout_ms=(spec or {}).get("timeout_ms"))  # fmt: skip
            problems = check_schema(spec["parameters"], args) if spec else []
            if spec is None or problems:
                span.set(tool=name, call_id=call["call_id"], outcome="error", executed=False)
                text = f"Invalid arguments: {'; '.join(problems)}"[:500] if spec else f"Unknown tool {name!r}."
                return obs(call, text), "error"
            decision = decide(h, turn, call, spec)
            if decision["audit"]:
                record(h, turn, {**base, "action": "policy_decision", "actor": "agent",
                                 "action_class": spec["action_class"], "rule": decision["rule"],
                                 "decision": decision["decision"],
                                 "executed": False if decision["decision"] == "deny" else None})  # fmt: skip
        verdict = decision["decision"]
        if verdict == "needs_approval":
            answer = ask(h, turn, call, spec, decision, resume)
            entry = {**base, "action": "approval_decision", "decision": answer,
                     "actor": "system" if turn.is_off("approval") else "user",
                     "approver": turn.data.pop("approver", None),
                     "executed": False if answer != "approve" else None}  # fmt: skip
            record(h, turn, entry)
            verdict = "allow" if answer == "approve" else "denied"
            text = f"The action was not approved (decision: {answer})."
        else:
            text = f"Denied by policy ({decision['rule']}): {decision.get('reason', '')}."
        if verdict != "allow":
            span.set(tool=name, call_id=call["call_id"], outcome="denied", executed=False)
            return obs(call, text), decision.get("limit") or "denied"
        result, outcome = sandbox(h, turn, call, spec)
        turn.data["executed"] = turn.data.get("executed", 0) + 1
        if spec["action_class"] not in ("read", "memory.write"):
            before = spec.get("audit", {}).get("before")
            record(h, turn, {**base, "action": "tool_executed", "decision": "executed",
                             "executed": outcome == "ok", "outcome": outcome,
                             "before": run_table(turn.registry.specs[before], args) if before else None,
                             "after": result})  # fmt: skip
        span.set(tool=name, call_id=call["call_id"], outcome=outcome, executed=True)
        return obs(call, result), outcome


def obs(call: dict[str, Any], result: Any, cap: int = 4000) -> dict[str, Any]:
    text = result if isinstance(result, str) else dumps(result)
    return {"role": "tool", "call_id": call["call_id"], "name": call["name"], "content": text[:cap]}


def sandbox(h: Any, turn: Turn, call: dict[str, Any], spec: dict[str, Any]) -> tuple[Any, str]:
    """Run the tool in a worker thread with its timeout. Table tools are pure lookups, so the
    in-process level is enough for them; give a code tool with real side effects a subprocess
    (the reference's `sandbox.level: subprocess`) before it touches anything that matters."""
    out: dict[str, Any] = {}
    timeout = spec["timeout_ms"] / 1000

    def work() -> None:
        try:
            args = call.get("arguments") or {}
            if "table" in spec:
                out["result"] = run_table(spec, args)
            elif "entrypoint" in spec:
                out["result"] = run_entrypoint(spec, args)
            else:
                out["result"] = h.run_builtin(turn, call)
        except Exception as exc:  # a tool error is an observation, not a failed turn
            out["error"] = str(exc)[:200]

    with turn.span("sandbox") as box:
        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(timeout)
        outcome = "timeout" if worker.is_alive() else ("error" if "error" in out else "ok")
        result = out.get("result", {"error": out.get("error", "timeout")})
        box.set(tool=call["name"], call_id=call["call_id"], executed=True, outcome=outcome,
                timeout_ms=spec["timeout_ms"], result_chars=len(dumps(result)), level="inprocess")  # fmt: skip
    return result, outcome
