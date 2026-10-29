"""Policy: may this call run? Decided by rules in config/policy.yaml, on every call.

Contract: spec/contracts/policy.md. Node: `policy` (control plane, kind config).

Order of evaluation:

1. Disabled (dev and private only): allow with rule `disabled`, span `skipped`.
2. The agent allowlist (`agent.allow_tools`): a tool outside it is denied, rule
   `agent.allowlist`.
3. The user side of the intersection (`roles.<role>.allow_action_classes`): a class none of the
   user's roles allows is denied, rule `roles`.
4. The tenant's tool-call budget (`budgets`, P3): a call past it is denied, rule
   `budget.tool_calls_per_turn` or `budget.tool_calls_per_hour`, and the loop then stops.
5. `rules`, in file order; the first match wins.
6. No match: deny, rule `default.deny`.

Action classes: `read`, `write`, `destructive`, `external` and `memory.write` (the memory
writer's gate). The decision is pure: prompt text and tool results never reach this function;
the budget state is an input like the call. Every deny and needs_approval, and every allow of a
non-read class, must be audited. Each decision carries `why`, a sentence for the inspector.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harnesswalk.execution.registry import ToolSpec
from harnesswalk.nodes import node, register
from harnesswalk.types import ACTION_CLASSES, ConfigError, ToolCall, TurnContext

register("policy", __name__)

RISKY = ("destructive", "external")
VERB = {"allow": "allows", "deny": "denies", "needs_approval": "needs a human for"}


@dataclass
class Decision:
    decision: str  # allow | deny | needs_approval
    rule: str
    action_class: str
    reason: str  # the `why`: short, safe to show
    audit: bool
    limit: str | None = None  # a loop exit reason when a budget decided (budget_tool_calls)
    budget: dict[str, Any] = field(default_factory=dict)


def _conditions(rule: dict[str, Any]) -> str:
    parts = []
    for key, label in (("modes", "modes"), ("action_classes", "classes"), ("tools", "tools")):
        if rule.get(key):
            parts.append(f"{label} {', '.join(rule[key])}")
    if rule.get("roles"):
        parts.append(f"roles {', '.join(rule['roles'])}")
    return f" ({'; '.join(parts)})" if parts else ""


class Policy:
    def __init__(self, cfg: dict[str, Any], mode: str, *, record_read_allows: bool = False):
        self.cfg = cfg
        self.mode = mode
        self.enabled = bool(cfg.get("enabled", True)) or mode == "public"
        self.allow_tools: list[str] | None = (cfg.get("agent") or {}).get("allow_tools")
        self.roles: dict[str, Any] = cfg.get("roles") or {}
        self._rules: list[dict[str, Any]] = list(cfg.get("rules") or [])
        self.record_read_allows = record_read_allows
        for rule in self._rules:
            for cls in rule.get("action_classes") or []:
                if cls not in ACTION_CLASSES:
                    raise ConfigError(f"policy rule {rule.get('id')}: unknown class {cls}")

    def rules(self) -> list[dict[str, Any]]:
        return list(self._rules)

    def _needs_audit(self, decision: str, action_class: str) -> bool:
        if decision != "allow" or action_class != "read":
            return True
        return self.record_read_allows

    def _make(self, decision: str, rule: str, action_class: str, reason: str, **kw) -> Decision:
        audit = self._needs_audit(decision, action_class)
        return Decision(decision, rule, action_class, reason, audit, **kw)

    def decide_class(
        self, roles: list[str], name: str, action_class: str, budget: Any = None
    ) -> Decision:
        """The pure decision for an action of a class on a named target (a tool or a store)."""
        if (
            action_class != "memory.write"
            and self.allow_tools is not None
            and name not in self.allow_tools
        ):
            return self._make(
                "deny", "agent.allowlist", action_class, f"{name} is not in the agent's allowlist"
            )
        allowed: set[str] = set()
        for role in roles or ["user"]:
            role_cfg = self.roles.get(role)
            classes = role_cfg.get("allow_action_classes") if role_cfg else None
            allowed |= set(classes if classes is not None else ACTION_CLASSES)
        if action_class not in allowed:
            return self._make(
                "deny", "roles", action_class, f"no role of this user allows {action_class}"
            )
        spent = None
        if budget is not None and action_class != "memory.write":
            spent = budget.tool_call_limit()
        if spent:
            key, limit, used = spent
            per = "turn" if key.endswith("turn") else "hour"
            return self._make(
                "deny",
                f"budget.{key}",
                action_class,
                f"the tenant's tool-call budget is {int(limit)} per {per} and {used} already ran",
                limit="budget_tool_calls",
                budget={"name": key, "limit": int(limit), "used": used},
            )
        for rule in self._rules:
            if rule.get("modes") and self.mode not in rule["modes"]:
                continue
            if rule.get("tools") and name not in rule["tools"]:
                continue
            if rule.get("action_classes") and action_class not in rule["action_classes"]:
                continue
            if rule.get("roles") and not set(rule["roles"]) & set(roles or ["user"]):
                continue
            reason = (
                f"rule {rule['id']}{_conditions(rule)} {VERB[rule['decision']]} "
                f"{action_class} calls"
            )
            return self._make(rule["decision"], rule["id"], action_class, reason)
        return self._make(
            "deny", "default.deny", action_class, "no rule matched, so the call is denied"
        )

    def decide(
        self, roles: list[str], call: ToolCall, spec: ToolSpec, budget: Any = None
    ) -> Decision:
        """The pure decision for a tool call. Same inputs and rules give the same answer."""
        return self.decide_class(roles, call.name, spec.action_class, budget)

    def decide_memory_write(self, roles: list[str], store: str = "") -> Decision:
        """The memory writer's gate (class `memory.write`), without a span. Pure.

        Matched as a `remember` call of the user's roles, so rules naming the tool or the roles
        apply to the writer exactly as to the call (P3 review). `store` is for the reason only.
        """
        return self.decide_class(roles, "remember", "memory.write")

    async def check(self, ctx: TurnContext, call: ToolCall, spec: ToolSpec) -> Decision:
        async with node(ctx, "policy") as span:
            if (not self.enabled or ctx.is_disabled("policy")) and ctx.mode != "public":
                decision = Decision(
                    "allow",
                    "disabled",
                    spec.action_class,
                    "policy is disabled, so every call is allowed and non-read calls are audited",
                    spec.action_class != "read",
                )
                span.skip(disabled=True)
            else:
                try:
                    roles = ctx.identity.roles if ctx.identity else ["user"]
                    decision = self.decide(roles, call, spec, ctx.budget)
                except Exception:
                    decision = Decision(
                        "deny", "policy.error", spec.action_class, "policy failed", True
                    )
            span.set(
                tool=call.name,
                action_class=spec.action_class,
                decision=decision.decision,
                rule=decision.rule,
                why=decision.reason,
            )
            if decision.budget:
                span.set(budget=decision.budget)
        return decision

    def check_public_mode(self, tools: list[ToolSpec]) -> None:
        """Public mode refuses to start when a rule could let a risky class through."""
        if self.mode != "public":
            return
        for rule in self._rules:
            if rule.get("modes") and "public" not in rule["modes"]:
                continue
            classes = rule.get("action_classes") or list(ACTION_CLASSES)
            risky = set(RISKY) & set(classes)
            if risky and rule["decision"] in ("allow", "needs_approval"):
                raise ConfigError(
                    f"public mode: rule {rule['id']} would let {sorted(risky)} through"
                )
        for spec in tools:
            if spec.action_class in RISKY:
                verdict = self.decide(["user"], ToolCall("check", spec.name, {}), spec)
                if verdict.decision != "deny":
                    raise ConfigError(f"public mode: tool {spec.name} is not denied")
