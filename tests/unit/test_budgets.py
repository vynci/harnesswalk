"""Per-tenant budgets: policy denials on tool calls, loop limits, the hourly ledger."""

import time
from pathlib import Path

import pytest

from harnesswalk.control.budgets import Budgets, HourUsage, TurnBudget
from harnesswalk.control.policy import Policy
from harnesswalk.execution.loop import Limits
from harnesswalk.execution.registry import ToolSpec
from harnesswalk.types import ToolCall
from tests.unit.support import exits, harness, run_turn, write_fixture

RULES = {"rules": [{"id": "read.allow", "action_classes": ["read"], "decision": "allow"}]}


def spec(name: str = "lookup_order", cls: str = "read") -> ToolSpec:
    return ToolSpec(name, "d", {"type": "object"}, cls, 1000, impl=lambda a: "")


def test_policy_denies_past_the_per_turn_and_per_hour_tool_call_budget() -> None:
    p = Policy(RULES, "dev")
    call = ToolCall("c", "lookup_order", {})
    budget = TurnBudget({"tool_calls_per_turn": 2, "tool_calls_per_hour": 10})
    assert p.decide(["user"], call, spec(), budget).decision == "allow"
    budget.tool_calls = 2
    d = p.decide(["user"], call, spec(), budget)
    assert (d.decision, d.rule, d.limit, d.audit) == (
        "deny",
        "budget.tool_calls_per_turn",
        "budget_tool_calls",
        True,
    )
    assert d.budget == {"name": "tool_calls_per_turn", "limit": 2, "used": 2}
    assert "2 per turn" in d.reason
    hourly = TurnBudget({"tool_calls_per_hour": 5}, HourUsage(tool_calls=4), tool_calls=1)
    assert p.decide(["user"], call, spec(), hourly).rule == "budget.tool_calls_per_hour"
    # memory.write is the writer's gate, not a tool call: budgets do not apply to it
    mem = Policy(
        {"rules": [{"id": "m", "action_classes": ["memory.write"], "decision": "allow"}]}, "dev"
    )
    assert mem.decide_memory_write(["user"], "semantic").decision == "allow"


def test_loop_limits_take_the_smallest_budget_and_name_its_source() -> None:
    budget = TurnBudget(
        {"tokens_per_turn": 5000, "tokens_per_hour": 9000, "usd_per_hour": 0.5},
        HourUsage(tokens=6000, usd=0.5),
    )
    limits = Limits.from_config({"budget_tokens": 16000, "budget_usd": 0.05}, budget)
    assert limits.budget_tokens == 3000
    assert limits.budget_source == {
        "tokens": "policy.budgets.tokens_per_hour",
        "usd": "policy.budgets.usd_per_hour",
    }
    assert limits.budget_usd == 0.0  # the hour is spent: the loop stops before any model call
    assert Limits.from_config({"budget_usd": 0}).budget_usd is None  # 0 still means unlimited


def test_ledger_sums_the_last_hour_per_tenant(tmp_path: Path) -> None:
    b = Budgets(
        {"default": {"tool_calls_per_turn": 3}, "tenants": {"t2": {"tool_calls_per_turn": 1}}},
        str(tmp_path / "b.db"),
    )
    b.record("t1", "turn_a", 100, 0.01, 2)
    b.record("t1", "turn_b", 50, 0.02, 1)
    b.record("t2", "turn_c", 999, 1.0, 9)
    b.db.execute("UPDATE ledger SET ts = ? WHERE turn_id = 'turn_b'", (time.time() - 7200,))
    usage = b.hour_usage("t1")
    assert (usage.tokens, usage.tool_calls) == (100, 2) and usage.usd == pytest.approx(0.01)
    assert b.limits("t1") == {"tool_calls_per_turn": 3.0}
    assert b.limits("t2") == {"tool_calls_per_turn": 1.0}
    with pytest.raises(ValueError):
        Budgets({"default": {"tool_calls_per_day": 1}}, str(tmp_path / "c.db"))


@pytest.mark.anyio
async def test_hourly_tool_budget_carries_across_turns(tmp_path: Path) -> None:
    fx = tmp_path / "fx"
    one_call = [
        {"tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "A-1001"}}]},
        {"text": "Shipped."},
    ]
    write_fixture(fx, "hourly", [one_call, one_call])
    h = harness(
        tmp_path,
        "hourly",
        fixtures=fx,
        overrides={"policy.budgets.default": {"tool_calls_per_hour": 1}},
    )
    first = await run_turn(h, "Where is A-1001?", session_id="s1")
    assert first[-1]["summary"]["exit"] == "answered"
    second = await run_turn(h, "And again?", session_id="s1")
    assert exits(second, "policy")[0]["summary"]["rule"] == "budget.tool_calls_per_hour"
    assert exits(second, "loop")[0]["summary"]["exit_reason"] == "budget_tool_calls"
    assert second[-1]["summary"]["exit"] == "limit"
