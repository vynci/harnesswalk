"""Policy allows, denies and asks for approval by rules, never by prompt text."""

import pytest

from harnesswalk.control.policy import Policy
from harnesswalk.execution.builtin_tools import CALCULATOR
from harnesswalk.execution.registry import ToolSpec
from harnesswalk.nodes import node
from harnesswalk.types import ConfigError, ToolCall
from tests.unit.support import make_ctx


def tool(name: str, action_class: str) -> ToolSpec:
    return ToolSpec(name, "d", {"type": "object"}, action_class, 1000, impl=lambda a: "")


CFG = {
    "agent": {"allow_tools": ["lookup_order", "cancel_order", "send_email", "update_note"]},
    "roles": {"viewer": {"allow_action_classes": ["read"]}},
    "rules": [
        {
            "id": "read.allow",
            "modes": ["dev", "public"],
            "action_classes": ["read"],
            "decision": "allow",
        },
        {"id": "write.allow", "modes": ["dev"], "action_classes": ["write"], "decision": "allow"},
        {
            "id": "destructive.approve",
            "modes": ["dev"],
            "action_classes": ["destructive"],
            "decision": "needs_approval",
        },
    ],
}


def call(name: str) -> ToolCall:
    return ToolCall("call_1", name, {})


def test_decisions() -> None:
    p = Policy(CFG, "dev")
    d = p.decide(["user"], call("lookup_order"), tool("lookup_order", "read"))
    assert (d.decision, d.rule, d.audit) == ("allow", "read.allow", False)
    d = p.decide(["user"], call("update_note"), tool("update_note", "write"))
    assert (d.decision, d.audit) == ("allow", True)  # non-read allows are audited
    d = p.decide(["user"], call("cancel_order"), tool("cancel_order", "destructive"))
    assert (d.decision, d.rule, d.audit) == ("needs_approval", "destructive.approve", True)
    d = p.decide(["user"], call("send_email"), tool("send_email", "external"))
    assert (d.decision, d.rule, d.audit) == ("deny", "default.deny", True)
    d = p.decide(["user"], call("calculator"), CALCULATOR)
    assert (d.decision, d.rule) == ("deny", "agent.allowlist")
    d = p.decide(["viewer"], call("update_note"), tool("update_note", "write"))
    assert (d.decision, d.rule) == ("deny", "roles")


def test_mode_changes_the_answer() -> None:
    d = Policy(CFG, "public").decide(
        ["user"], call("cancel_order"), tool("cancel_order", "destructive")
    )
    assert (d.decision, d.rule) == ("deny", "default.deny")


def test_public_mode_refuses_risky_rules() -> None:
    risky = {"rules": [{"id": "all", "decision": "allow"}]}
    with pytest.raises(ConfigError):
        Policy(risky, "public").check_public_mode([])
    Policy(risky, "dev").check_public_mode([])  # fine outside public


@pytest.mark.anyio
async def test_check_emits_and_disabled_is_skipped() -> None:
    p = Policy(CFG, "dev")
    ctx, got = make_ctx()
    async with node(ctx, "tools"):
        d = await p.check(ctx, call("lookup_order"), tool("lookup_order", "read"))
    ex = [e for e in got.events if e["type"] == "node_exit" and e["node_id"] == "policy"][0]
    assert ex["summary"] == {
        "tool": "lookup_order",
        "action_class": "read",
        "decision": "allow",
        "rule": "read.allow",
        "why": "rule read.allow (modes dev, public; classes read) allows read calls",
    }
    ctx.disabled_nodes = frozenset({"policy"})
    async with node(ctx, "tools"):
        d = await p.check(ctx, call("cancel_order"), tool("cancel_order", "destructive"))
    assert (d.decision, d.rule, d.audit) == ("allow", "disabled", True)
    ex = [e for e in got.events if e["type"] == "node_exit" and e["node_id"] == "policy"][-1]
    assert ex["status"] == "skipped" and ex["summary"]["disabled"] is True
