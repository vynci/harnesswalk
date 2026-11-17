"""Loop: call the model, run the tools it asks for, feed back what happened, until it answers.

Contract: spec/contracts/loop.md. Node: `loop`. Always on. Every exit is enforced here:
`max_steps`, `budget_tokens`, `budget_usd`, `budget_tool_calls`, `timeout`, `stuck` and
`interrupted` (a cancel). After `max_steps` or `stuck` one last call with tools off answers;
after the others a fixed notice does. The loop checkpoints after every step and before an
approval pause, so `run(state=...)` can pick a turn up where a restart stopped it.

Lesson: without a ceiling, tool use is a while-true loop that someone else pays for.
"""

from __future__ import annotations

import time
from typing import Any

from harness.core import tools as tool_node
from harness.core.events import Turn

NOTICES = {
    "budget_tokens": "I stopped because this turn used its token budget. Try a narrower question.",
    "budget_usd": "I stopped because this turn used its cost budget. Try a narrower question.",
    "budget_tool_calls": "I stopped because this turn used its tool-call budget. Try a narrower question.",
    "timeout": "I stopped because this turn ran out of time. Please try again.",
    "interrupted": "I stopped because the turn was interrupted.",
    "max_steps": "I stopped after the maximum number of steps without reaching an answer.",
    "stuck": "I stopped because I was repeating the same tool call without progress.",
}  # fmt: skip
KEEP = ("calls", "call_ids", "tool_runs", "executed", "citations", "proposals", "output_schema", "clean")


def used(turn: Turn) -> tuple[int, float]:
    tok, usd = 0, 0.0
    for e in turn.events:
        if e["type"] == "node_exit" and e.get("node_id") == "model_layer" and e.get("cost"):
            tok += e["cost"]["input_tokens"] + e["cost"]["output_tokens"]
            usd += e["cost"]["usd"]
    return tok, usd


def run(h: Any, turn: Turn, state: dict[str, Any]) -> tuple[str, str, int, int]:
    """Returns (answer, exit_reason, steps, tool_calls)."""
    cfg = h.settings.get("loop", {})
    max_steps, repeats = int(cfg.get("max_steps", 8)), int(cfg.get("stuck_repeats", 3))
    budget_tokens, budget_usd = float(cfg.get("budget_tokens", 16000)), float(cfg.get("budget_usd", 0))
    offered = turn.registry.offered(turn, (h.policy_cfg(turn).get("agent") or {}).get("allow_tools"))
    schema = turn.data.get("output_schema")
    resume = state.pop("resume", None)
    started = time.monotonic()

    def checkpoint(**extra: Any) -> None:
        h.sessions.save_checkpoint(turn.ids["turn_id"], {
            **state, **{k: turn.data.get(k) for k in KEEP}, **extra})  # fmt: skip

    turn.data["checkpoint"] = checkpoint
    with turn.span("loop", resume=bool(resume), max_steps=max_steps, budget_tokens=budget_tokens,
                   budget_usd=budget_usd) as span:  # fmt: skip
        reason, answer = None, ""
        while reason is None:
            if not state["pending"]:
                tok, usd = used(turn)
                if turn.cancel_requested:
                    reason = "interrupted"
                elif state["step"] + 1 > max_steps:
                    reason = "max_steps"
                elif tok >= budget_tokens:
                    reason = "budget_tokens"
                elif budget_usd and usd >= budget_usd:
                    reason = "budget_usd"
                elif time.monotonic() - started - turn.paused_s > float(cfg.get("timeout_s", 120)):
                    reason = "timeout"
                if reason:
                    break
                state["step"] += 1
                r = h.models.complete(turn, state["messages"], offered, state["step"], schema)
                state["messages"].append({"role": "assistant", "content": r.get("text") or "",
                                          "tool_calls": r["tool_calls"] or None})  # fmt: skip
                if not r["tool_calls"]:
                    reason, answer = "answer", r.get("text") or ""
                    break
                state["pending"] = list(r["tool_calls"])
            while state["pending"] and reason is None:
                call = state["pending"][0]
                key = f"{call['name']}:{tool_node.dumps(call.get('arguments') or {})}"
                if turn.cancel_requested:
                    reason = "interrupted"
                elif state["seen"].get(key, 0) >= repeats:
                    reason = "stuck"
                if reason:
                    break
                state["seen"][key] = state["seen"].get(key, 0) + 1
                observation, signal = tool_node.invoke(h, turn, call, resume)
                resume = None
                state["pending"].pop(0)
                state["messages"].append(observation)
                state["tool_calls"] += 1
                args = call.get("arguments") or {}
                if call["name"] == "remember" and signal == "ok":
                    store = "semantic" if args.get("kind") == "fact" else "episodic"
                    turn.data.setdefault("proposals", []).append({"store": store, "source": "loop", **args})
                if signal == "budget_tool_calls":
                    reason = "budget_tool_calls"
            checkpoint()
        if reason in ("max_steps", "stuck") and cfg.get("final_call_on_exit", True):
            answer = h.models.complete(turn, state["messages"], [], state["step"] + 1, schema).get("text") or ""
        elif reason != "answer":
            answer = (cfg.get("notices") or {}).get(reason) or NOTICES[reason]
        span.set(steps=state["step"], exit_reason=reason, tool_calls=state["tool_calls"])
    return answer, reason, state["step"], state["tool_calls"]


def new_state(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"messages": messages, "step": 0, "pending": [], "tool_calls": 0, "seen": {}}
