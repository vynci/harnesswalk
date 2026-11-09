"""Policy: decide every tool call from rules in a file, never from the prompt.

Contract: spec/contracts/policy.md. Node: `policy` (on from the `governed` rung). Rules live in
`policy/policy.yaml` (yours): first match wins, no match is a deny (rule `default.deny`).

Lesson: a rule in the system prompt is advice; this is the control. The effective permission
is the user's roles intersected with the agent's allowlist, a tool-call budget is part of the
decision, and nothing the model writes can change the outcome. Remove it only when no tool can
do harm and nobody needs to ask "who allowed this".
"""

from __future__ import annotations

from typing import Any

ACTION_CLASSES = ("read", "write", "destructive", "external", "memory.write")


def decide_class(cfg: dict[str, Any], mode: str, roles: list[str], name: str, cls: str,
                 used: int | None = None, limit: int | None = None) -> dict[str, Any]:  # fmt: skip
    """The pure decision for one call (policy.md): allowlist, roles, budget, then rules."""
    def make(decision: str, rule: str, reason: str, **kw: Any) -> dict[str, Any]:
        return {"decision": decision, "rule": rule, "reason": reason,
                "audit": decision != "allow" or cls != "read", **kw}  # fmt: skip

    allow = (cfg.get("agent") or {}).get("allow_tools")
    if cls != "memory.write" and allow is not None and name not in allow:
        return make("deny", "agent.allowlist", f"{name} is not in the agent's allowlist")
    allowed: set[str] = set()
    for role in roles or ["user"]:
        classes = ((cfg.get("roles") or {}).get(role) or {}).get("allow_action_classes")
        allowed |= set(classes if classes is not None else ACTION_CLASSES)
    if cls not in allowed:
        return make("deny", "roles", f"no role of this user allows {cls}")
    if limit is not None and used is not None and used >= limit and cls != "memory.write":
        return make("deny", "budget.tool_calls_per_turn", f"the tool-call budget is {limit} per"
                    f" turn and {used} already ran", limit="budget_tool_calls",
                    budget={"name": "tool_calls_per_turn", "limit": limit, "used": used})  # fmt: skip
    for rule in cfg.get("rules") or []:
        if rule.get("modes") and mode not in rule["modes"]:
            continue
        if rule.get("tools") and name not in rule["tools"]:
            continue
        if rule.get("action_classes") and cls not in rule["action_classes"]:
            continue
        if rule.get("roles") and not set(rule["roles"]) & set(roles or ["user"]):
            continue
        return make(rule["decision"], rule["id"], f"rule {rule['id']} decides {cls} calls")
    return make("deny", "default.deny", "no rule matched, so the call is denied")


def check(h: Any, turn: Any, call: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    cfg = h.policy_cfg(turn)
    budgets = (cfg.get("budgets") or {}).get("default") or {}
    budgets = {**budgets, **((cfg.get("budgets") or {}).get("tenants") or {}).get(
        turn.ids["tenant_id"], {})}  # fmt: skip
    limit = budgets.get("tool_calls_per_turn")
    used = turn.data.get("tool_runs", 0)
    with turn.span("policy") as span:
        try:
            d = decide_class(cfg, h.settings.mode, turn.data.get("roles") or ["user"],
                             call["name"], spec["action_class"], used,
                             int(limit) if limit is not None else None)  # fmt: skip
        except Exception:  # fail closed
            d = {"decision": "deny", "rule": "policy.error", "reason": "policy failed", "audit": True}
        span.set(tool=call["name"], action_class=spec["action_class"], decision=d["decision"],
                 rule=d["rule"], why=d["reason"], budget=d.get("budget"))  # fmt: skip
    if d["decision"] == "allow" and spec["action_class"] != "memory.write":
        turn.data["tool_runs"] = used + 1
    return d
