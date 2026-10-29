"""Policy: may this tool call run? Decided by rules in config/policy.yaml, on every call.

Contract: spec/contracts/policy.md. Node: `policy` (control plane, kind config).

Order of evaluation:

1. Disabled (dev and private only): allow with rule `disabled`, span `skipped`.
2. The agent allowlist (`agent.allow_tools`): a tool outside it is denied, rule
   `agent.allowlist`.
3. The user side of the intersection (`roles.<role>.allow_action_classes`): a class none of the
   user's roles allows is denied, rule `roles`.
4. `rules`, in file order; the first match wins.
5. No match: deny, rule `default.deny`.

The decision is pure: prompt text and tool results never reach this function. Every deny and
needs_approval, and every allow of a non-read class, must be audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harnesswalk.execution.registry import ToolSpec
from harnesswalk.nodes import node, register
from harnesswalk.types import ACTION_CLASSES, ConfigError, ToolCall, TurnContext

register("policy", __name__)


@dataclass
class Decision:
    decision: str  # allow | deny | needs_approval
    rule: str
    action_class: str
    reason: str
    audit: bool


class Policy:
    def __init__(self, cfg: dict[str, Any], mode: str, *, record_read_allows: bool = False):
        self.cfg = cfg
        self.mode = mode
        self.enabled = bool(cfg.get("enabled", True)) or mode == "public"
        self.allow_tools: list[str] | None = (cfg.get("agent") or {}).get("allow_tools")
        self.roles: dict[str, Any] = cfg.get("roles") or {}
        self._rules: list[dict[str, Any]] = list(cfg.get("rules") or [])
        self.record_read_allows = record_read_allows

    def rules(self) -> list[dict[str, Any]]:
        return list(self._rules)

    def _needs_audit(self, decision: str, action_class: str) -> bool:
        if decision != "allow" or action_class != "read":
            return True
        return self.record_read_allows

    def _make(self, decision: str, rule: str, spec: ToolSpec, reason: str) -> Decision:
        return Decision(
            decision,
            rule,
            spec.action_class,
            reason,
            self._needs_audit(decision, spec.action_class),
        )

    def decide(self, roles: list[str], call: ToolCall, spec: ToolSpec) -> Decision:
        """The pure decision. Same inputs and rules give the same answer."""
        if self.allow_tools is not None and call.name not in self.allow_tools:
            return self._make(
                "deny", "agent.allowlist", spec, f"{call.name} is not in the agent's allowlist"
            )
        allowed: set[str] = set()
        for role in roles or ["user"]:
            role_cfg = self.roles.get(role)
            classes = role_cfg.get("allow_action_classes") if role_cfg else None
            allowed |= set(classes if classes is not None else list(ACTION_CLASSES))
        if spec.action_class not in allowed:
            return self._make(
                "deny", "roles", spec, f"no role of this user allows {spec.action_class}"
            )
        for rule in self._rules:
            if rule.get("modes") and self.mode not in rule["modes"]:
                continue
            if rule.get("tools") and call.name not in rule["tools"]:
                continue
            if rule.get("action_classes") and spec.action_class not in rule["action_classes"]:
                continue
            if rule.get("roles") and not set(rule["roles"]) & set(roles or ["user"]):
                continue
            verb = {"allow": "allows", "deny": "denies", "needs_approval": "needs a human for"}
            reason = f"rule {rule['id']} {verb[rule['decision']]} {spec.action_class} calls"
            return self._make(rule["decision"], rule["id"], spec, reason)
        return self._make("deny", "default.deny", spec, "no rule matched, so the call is denied")

    async def check(self, ctx: TurnContext, call: ToolCall, spec: ToolSpec) -> Decision:
        async with node(ctx, "policy") as span:
            if (not self.enabled or ctx.is_disabled("policy")) and ctx.mode != "public":
                decision = Decision(
                    "allow",
                    "disabled",
                    spec.action_class,
                    "policy is disabled",
                    spec.action_class != "read",
                )
                span.skip(disabled=True)
            else:
                try:
                    roles = ctx.identity.roles if ctx.identity else ["user"]
                    decision = self.decide(roles, call, spec)
                except Exception:
                    decision = Decision(
                        "deny", "policy.error", spec.action_class, "policy failed", True
                    )
            span.set(
                tool=call.name,
                action_class=spec.action_class,
                decision=decision.decision,
                rule=decision.rule,
            )
        return decision

    def check_public_mode(self, tools: list[ToolSpec]) -> None:
        """Public mode refuses to start when a rule could let a risky class through."""
        if self.mode != "public":
            return
        for rule in self._rules:
            if rule.get("modes") and "public" not in rule["modes"]:
                continue
            classes = rule.get("action_classes") or ["read", "write", "destructive", "external"]
            risky = {"destructive", "external"} & set(classes)
            if risky and rule["decision"] in ("allow", "needs_approval"):
                raise ConfigError(
                    f"public mode: rule {rule['id']} would let {sorted(risky)} through"
                )
        for spec in tools:
            if spec.action_class in ("destructive", "external"):
                verdict = self.decide(["user"], ToolCall("check", spec.name, {}), spec)
                if verdict.decision != "deny":
                    raise ConfigError(f"public mode: tool {spec.name} is not denied")
