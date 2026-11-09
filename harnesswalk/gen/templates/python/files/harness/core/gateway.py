"""Gateway: the front door and the exit. It starts the turn, walks it node by node, and ends it.

Contract: spec/contracts/gateway.md and harness.md section 5 (the walk). Node: `gateway`,
entered twice per chat turn (`phase: receive`, then `phase: reply`). Every node a profile
switches off is still visited through its seam, so the walk below is the same on every rung:

    gateway, input_guardrails, context_builder (memory), loop (model_layer, tools ...),
    output_guardrails, gateway, memory_writer (when there are proposals), trace

Lesson: one place assigns the tenant, session, turn and trace ids before anything else runs,
and one place guarantees `turn_end` closes every stream, on success, on error and on cancel.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
import uuid
from typing import Any

from harness.core import context_builder, loop
from harness.core.config import profile_view
from harness.core.events import NODES, Cancelled, NodeError, Span, Turn, compact, iso, off_node
from harness.core.trace import run_trace, total_cost

REFUSAL = "I can't help with that request as written. Please rephrase it."
LIMITS = {"max_steps", "stuck", "budget_tokens", "budget_usd", "budget_tool_calls", "timeout"}


class Refused(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code = status, code


def start_turn(h: Any, tenant: str, body: dict[str, Any], *, kind: str = "chat",
               extra: dict[str, Any] | None = None) -> Turn:  # fmt: skip
    """Validate, resolve the session, record the turn and run it on a thread of its own."""
    opts = {**(body.get("options") or {}), **{k: v for k, v in body.items() if k != "options"}}
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        raise Refused(400, "invalid_request", "message is required")
    disabled = list(dict.fromkeys((opts.get("disable") or []) + (opts.get("disabled_nodes") or [])))
    for node in disabled:
        if not NODES.get(node, {}).get("can_disable"):
            raise Refused(422, "invalid_request", f"{node} cannot be disabled")
    pack = opts.get("pack") or opts.get("dataset")
    sid, session = body.get("session_id"), None
    if sid:  # a known session continues; a new id (a client made it up) starts one
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(sid)):
            raise Refused(400, "invalid_request", "invalid session_id")
        session = h.sessions.get(tenant, sid)
        if session is None and h.db.one("sessions", session=sid) is not None:
            raise Refused(404, "session_not_found", "No such session for this tenant.")
        if session and pack and pack != session["pack"]:
            raise Refused(422, "invalid_request", "a session's pack is fixed")
    if pack and pack not in h.settings.packs:
        raise Refused(422, "invalid_request", f"unknown pack {pack}")
    with h.lock:
        session = session or h.sessions.create(tenant, pack or h.settings.default_pack, sid)
        if any(t.ids["session_id"] == session["session"] and not t.done for t in h.live.values()):
            raise Refused(409, "turn_in_progress", "A turn is already running in this session.")
        index = len(h.sessions.turns(tenant, session["session"]))
        fixture = opts.get("fixture") or (extra or {}).get("fixture")
        per_turn = bool(fixture)
        fixture = fixture or h.settings.fixture
        previous = [t for t in h.sessions.turns(tenant, session["session"]) if t["fixture"] == fixture]
        rec = {"turn": "turn_" + uuid.uuid4().hex[:16], "tenant": tenant,
               "session": session["session"], "pack": session["pack"], "index": index,
               "fixture": fixture, "fixture_turn": len(previous) if per_turn else index,
               "provider": "fake" if per_turn else h.settings.provider,
               "disabled": disabled, "step_mode": bool(opts.get("step") or opts.get("step_mode")),
               "output_schema": opts.get("output_schema"), "kind": kind,
               "roles": (extra or {}).get("roles") or ["user"], "model": opts.get("model"),
               "auto_approval": (extra or {}).get("approval"), "ended": False,
               "started": iso(time.time()), "eval_run_id": (extra or {}).get("eval_run_id")}  # fmt: skip
        h.db.insert("turns", rec)
        turn = attach(h, rec)
        h.live[rec["turn"]] = turn
    turn.emit("turn_start", None, "active", summary=compact(**{
        "kind": kind, "channel": "eval" if kind == "eval" else "web", "mode": h.settings.mode,
        "step_mode": rec["step_mode"], "message_chars": len(message), "disabled": disabled or None,
        "dataset": rec["pack"], "pack": rec["pack"], "turn_index": index,
        "profile": profile_view(h.settings.profile), "release": h.release.info(rec["pack"]),
        "output_schema": True if rec["output_schema"] else None,
        "eval_run_id": rec["eval_run_id"]}))  # fmt: skip
    threading.Thread(target=walk, args=(h, turn, message), daemon=True).start()
    return turn


def attach(h: Any, rec: dict[str, Any]) -> Turn:
    ids = {"trace_id": rec.get("trace") or secrets.token_hex(16), "turn_id": rec["turn"],
           "session_id": rec["session"], "tenant_id": rec["tenant"]}  # fmt: skip
    if "trace" not in rec:
        h.db.update("turns", {"turn": rec["turn"]}, {"trace": ids["trace_id"]})
    off = set(h.settings.profile.off()) | set(rec["disabled"])
    turn = Turn(h.trace, ids, step_mode=rec["step_mode"], off=off)
    turn.registry = h.registry_for(rec["pack"])
    turn.data.update({k: rec[k] for k in ("pack", "fixture", "fixture_turn", "provider", "roles",
                                          "output_schema", "model", "auto_approval")})  # fmt: skip
    if rec.get("provider") == "fake":
        turn.data["provider"] = "fake"
    return turn


def walk(h: Any, turn: Turn, message: str) -> None:
    def run() -> None:
        with turn.span("gateway") as g:
            g.set(phase="receive", channel="web", authenticated=True)
        if turn.is_off("input_guardrails"):
            off_node(turn, "input_guardrails", action="pass")
            clean, blocked = message, False
#% if has("input_guardrails")
        else:
            from harness.core import input_guardrails

            clean, blocked = input_guardrails.run(h, turn, message)
#% endif
        if blocked:
            return end(h, turn, REFUSAL, "blocked", 0)
        turn.data["clean"] = clean
        finish(h, turn, loop.new_state(context_builder.build(h, turn, clean)))

    guarded(h, turn, run)


def finish(h: Any, turn: Turn, state: dict[str, Any]) -> None:
    """From the loop to turn_end. A restarted turn re-enters here with its checkpoint."""
    answer, reason, steps, _ = loop.run(h, turn, state)
    exit_ = "answered" if reason == "answer" else ("cancelled" if reason == "interrupted" else "limit")
    if turn.is_off("output_guardrails"):
        off_node(turn, "output_guardrails", action="pass")
#% if has("output_guardrails")
    else:
        from harness.core import output_guardrails

        answer, blocked = output_guardrails.run(h, turn, answer, turn.data.get("output_schema"))
        exit_ = "blocked" if blocked else exit_
#% endif
    end(h, turn, answer, exit_, steps)


def end(h: Any, turn: Turn, reply: str, exit_: str, steps: int) -> None:
    with turn.span("gateway") as g:
        g.set(phase="reply", channel="web", reply_chars=len(reply))
    turn.past_reply = True
    clean = turn.data.get("clean")
    h.sessions.record(turn.ids["tenant_id"], turn.ids["session_id"], turn.ids["turn_id"], clean,
                      reply, exit_)  # fmt: skip
    proposals = list(turn.data.get("proposals") or [])
    episodes = "episodic" in h.settings.get("context.stores", [])
    if clean is not None and episodes and h.settings.get("memory_writer.auto_episodes", True):
        proposals.append({"store": "episodic", "source": "turn", "kind": "turn", "outcome": exit_,
                          "text": f"Asked: {clean[:200]} Answered: {reply[:240]}"})  # fmt: skip
    if proposals and turn.is_off("memory_writer"):
        n = len(proposals)
        off_node(turn, "memory_writer", proposed=n, written=0, rejected=n, pending=0)
#% if has("memory_writer")
    elif proposals:
        from harness.core import memory_writer

        memory_writer.run(h, turn, proposals)
#% endif
    run_trace(turn, "@@storage@@")
    cited = cited_chunks(reply, turn.data.get("citations") or [])
    close(h, turn, "done", {"exit": exit_, "steps": steps, "reply": reply[:16384],
                            "citations": cited or None})  # fmt: skip


def cited_chunks(reply: str, numbered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import re

    order = list(dict.fromkeys(int(n) for n in re.findall(r"\[(\d{1,2})\]", reply)))
    by_n = {c["n"]: c for c in numbered}
    return [{k: by_n[n][k] for k in ("n", "doc", "chunk", "citation")} for n in order if n in by_n]


def close(h: Any, turn: Turn, status: str, summary: dict[str, Any]) -> None:
    start = turn.events[0]["ts"] if turn.events else iso(time.time())
    from datetime import datetime

    ms = max(0.0, (time.time() - datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()) * 1000)
    turn.emit("turn_end", None, status, summary={k: v for k, v in summary.items() if v is not None},
              cost=total_cost(turn.events), duration_ms=round(ms, 3))  # fmt: skip
    turn.done = True
    h.db.update("turns", {"turn": turn.ids["turn_id"]}, {"ended": True})


def guarded(h: Any, turn: Turn, body: Any) -> None:
    """Run the walk; on a cancel or a failure close every open span, then end the turn."""
    try:
        body()
    except Cancelled:
        fail(h, turn, "cancelled", "The turn was cancelled.", "cancelled")
    except NodeError as exc:
        fail(h, turn, exc.code, str(exc)[:900], "error")
    except Exception as exc:  # noqa: BLE001 - every stream must close
        fail(h, turn, "internal", f"{type(exc).__name__}: {exc}"[:900], "error")


def fail(h: Any, turn: Turn, code: str, message: str, exit_: str) -> None:
    turn.close_open(code, message)
    turn.emit("error", None, "error", error={"code": code, "message": message})
    h.sessions.record(turn.ids["tenant_id"], turn.ids["session_id"], turn.ids["turn_id"], None,
                      "", exit_)  # fmt: skip
    close(h, turn, "error", {"exit": exit_, "reply": ""})


def recover(h: Any) -> None:
    """At start-up (approval_queue.md, P3): a turn paused on a pending approval is re-attached
    and waits again; any other unfinished turn is closed with `restarted`."""
    for rec in h.db.rows("turns", ended=False):
        events = h.trace.events(rec["turn"])
        if not events or events[-1]["type"] == "turn_end":
            continue
        turn = attach(h, rec)
        turn.events = events
        for e in events:  # rebuild the open spans from the stored stream
            if e["type"] == "node_enter":
                turn.stack.append(Span(e["node_id"], time.monotonic()))
            elif e["type"] in ("node_exit", "error") and e.get("node_id"):
                turn.stack.pop()
        cp = h.sessions.checkpoint(rec["turn"])
        pending = cp and cp["data"].get("approval_id") and h.db.one(
            "approvals", approval_id=cp["data"]["approval_id"], status="pending")  # fmt: skip
        h.live[rec["turn"]] = turn
        if not pending:
            threading.Thread(target=fail, args=(h, turn, "restarted", "The harness restarted"
                             " while the turn was running.", "error"), daemon=True).start()  # fmt: skip
            continue
        data = dict(cp["data"])
        kept = {k: data.pop(k) for k in list(data) if k in loop.KEEP}
        turn.data.update({k: v for k, v in kept.items() if v is not None})
        data["resume"] = {"approval_id": data.pop("approval_id"), "decision": data.pop("decision")}
        h.db.update("checkpoints", {"turn": rec["turn"]}, {"restored": True})
        threading.Thread(target=guarded, args=(h, turn, lambda t=turn, d=data: finish(h, t, d)),
                         daemon=True).start()  # fmt: skip
