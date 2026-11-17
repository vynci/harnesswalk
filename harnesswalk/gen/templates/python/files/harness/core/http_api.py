"""HTTP: the subset of spec/contracts/http_api.md the web app and the conformance runner use.

Standard library only (`http.server`, one thread per request; a turn runs on its own thread
and its events stream as Server-Sent Events). GENERATION_NOTES.md lists the routes served.
Dev mode: the tenant is `dev`, or the `X-Harnesswalk-Tenant` header.

Lesson: the event stream is the API. Point the Harnesswalk web app at this server and the
diagram lights up for your harness, because it speaks the same contract as the reference.
"""

from __future__ import annotations

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from harness.core import config, evals, gateway
from harness.core.events import NODES

ROUTES: list[tuple[str, re.Pattern[str], Any]] = []


def route(method: str, pattern: str):
    def wrap(fn: Any) -> Any:
        ROUTES.append((method, re.compile("^" + re.sub(r"{(\w+)}", r"(?P<\1>[^/]+)", pattern) + "$"), fn))
        return fn
    return wrap


class HTTPError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.extra = status, code, extra or {}


def turn_summary(h: Any, turn_id: str, tenant: str) -> dict[str, Any]:
    rec = h.db.one("turns", turn=turn_id)
    if rec is None or rec["tenant"] != tenant:
        raise HTTPError(404, "turn_not_found", f"No turn {turn_id} for this tenant.")
    events = h.trace.events(turn_id)
    end = events[-1] if events and events[-1]["type"] == "turn_end" else None
    paused = pending = None
    for e in events:
        if e["type"] == "node_paused":
            paused = e["node_id"]
        elif e["type"] == "node_resumed":
            paused = None
        elif e["type"] == "approval_requested":
            pending = e["summary"]["approval_id"]
        elif e["type"] == "approval_decided":
            pending = None
    hist = h.db.one("history", turn=turn_id)
    cp = h.sessions.checkpoint(turn_id)
    start = (events[0].get("summary") or {}) if events else {}
    return {"turn_id": turn_id, "session_id": rec["session"], "tenant_id": tenant,
            "trace_id": rec.get("trace"), "turn_index": rec["index"], "pack": rec["pack"],
            "status": "done" if end else ("paused" if paused else "running"),
            "exit": end["summary"].get("exit") if end else None,
            "reply": end["summary"].get("reply") if end else None,
            "message": (hist or {}).get("user") if end else None,
            "started_at": events[0]["ts"] if events else None, "ended_at": end["ts"] if end else None,
            "cost": end["cost"] if end else gateway.total_cost(events), "event_count": len(events),
            "paused_at_node": paused, "pending_approval_id": pending, "feedback": None,
            "scores": None, "release": start.get("release"),
            "checkpoint": {"step": cp["data"].get("step"), "at": cp["at"], "restored": cp["restored"]}
            if cp else None}  # fmt: skip


@route("GET", "/healthz")
def healthz(h: Any, r: dict[str, Any]):
    out = {"status": "ok", "version": config.VERSION, "spec_version": config.SPEC_VERSION,
           "mode": h.settings.mode, "provider": h.settings.provider, "checks": {"trace_store": "ok"}}  # fmt: skip
    if h.settings.provider == "fake":
        out["fixture"] = h.settings.fixture
    return 200, out


@route("GET", "/v1/capabilities")
def capabilities(h: Any, r: dict[str, Any]):
    models = h.settings.get("models.models", {})
    return 200, {"mode": h.settings.mode, "version": config.VERSION, "spec_version": config.SPEC_VERSION,
                 "providers": [{"name": p, "configured": p in h.models.providers,
                                "default": p == h.settings.provider,
                                "models": [a for a, m in models.items() if m["provider"] == p]}
                               for p in config.PROVIDERS],
                 "fixtures": h.models.providers["fake"].names(),
                 "features": {"step_mode": True, "approvals": "approval" in config.GENERATED,
                              "message_delta": False, "cancel": True, "byo_key": False,
                              "disable_nodes": True, "knowledge": "knowledge" in config.GENERATED,
                              "memory_review": False, "evals": True, "judge": False,
                              "release": config.RELEASE == "chain", "artifact_edit": config.RELEASE == "chain",
                              "feedback": False, "step_over": True, "packs": True, "diagnose": False},
                 "limits": {"max_message_chars": 32000, "max_steps": h.settings.get("loop.max_steps", 8),
                            "session_ttl_seconds": None},
                 "packs": {"default": h.settings.default_pack, "ids": sorted(h.settings.packs)},
                 "repo_url": None}  # fmt: skip


@route("GET", "/v1/diagram")
def diagram(h: Any, r: dict[str, Any]):
    pack = r["query"].get("pack")
    if pack and pack not in h.settings.packs:
        raise HTTPError(422, "invalid_request", f"unknown pack {pack}")
    return 200, config.diagram_for(h.settings, pack)


@route("GET", "/v1/profiles")
def profiles(h: Any, r: dict[str, Any]):
    return 200, {"profiles": [{"id": p.id, "title": p.title, "summary": p.summary, "rung": p.rung,
                               "disable": sorted(p.disable), "off": sorted(p.off()),
                               "generated": not (({i for i, n in NODES.items() if n.get("can_disable")}
                                                  - p.disable) - config.GENERATED)}
                              for p in config.all_profiles()],
                 "active": h.settings.profile.id}  # fmt: skip


def pack_item(h: Any, pid: str) -> dict[str, Any]:
    card = h.settings.packs[pid].card
    return {"id": pid, "title": card.get("title", pid), "domain": card.get("domain"),
            "feature": card.get("feature"), "profile": h.settings.profile.id,
            "prompts": card.get("prompts") or [], "default": pid == h.settings.default_pack,
            "loaded": True, "fixtures": h.models.providers["fake"].names()}  # fmt: skip


@route("GET", "/v1/packs")
def packs(h: Any, r: dict[str, Any]):
    return 200, {"default": h.settings.default_pack, "packs": [pack_item(h, p) for p in h.settings.packs]}


@route("GET", "/v1/packs/{pack}")
def pack_one(h: Any, r: dict[str, Any]):
    if r["pack"] not in h.settings.packs:
        raise HTTPError(404, "pack_not_found", "No such pack.")
    return 200, {**pack_item(h, r["pack"]), "summary": h.settings.packs[r["pack"]].card.get("summary")}


@route("POST", "/v1/sessions")
def new_session(h: Any, r: dict[str, Any]):
    pack = (r["body"] or {}).get("pack") or h.settings.default_pack
    if pack not in h.settings.packs:
        raise HTTPError(422, "invalid_request", f"unknown pack {pack}")
    s = h.sessions.create(r["tenant"], pack)
    return 201, {"session_id": s["session"], "pack": pack, "tenant_id": r["tenant"], "created_at": s["created_at"]}


@route("GET", "/v1/sessions/{sid}")
def session(h: Any, r: dict[str, Any]):
    s = h.sessions.get(r["tenant"], r["sid"])
    if s is None:
        raise HTTPError(404, "session_not_found", "No such session for this tenant.")
    return 200, {"session_id": s["session"], "pack": s["pack"], "tenant_id": r["tenant"],
                 "created_at": s["created_at"], "turns": len(h.sessions.turns(r["tenant"], r["sid"]))}  # fmt: skip


@route("GET", "/v1/sessions/{sid}/turns")
def session_turns(h: Any, r: dict[str, Any]):
    if h.sessions.get(r["tenant"], r["sid"]) is None:
        raise HTTPError(404, "session_not_found", "No such session for this tenant.")
    turns = h.sessions.turns(r["tenant"], r["sid"])[::-1][: int(r["query"].get("limit", 50))]
    return 200, [turn_summary(h, t["turn"], r["tenant"]) for t in turns]


@route("POST", "/v1/turns")
def post_turn(h: Any, r: dict[str, Any]):
    try:
        turn = gateway.start_turn(h, r["tenant"], r["body"] or {})
    except gateway.Refused as exc:
        raise HTTPError(exc.status, exc.code, str(exc)) from exc
    tid = turn.ids["turn_id"]
    return 202, {"turn_id": tid, "trace_id": turn.ids["trace_id"], "session_id": turn.ids["session_id"],
                 "events_url": f"/v1/turns/{tid}/events"}  # fmt: skip


@route("GET", "/v1/turns/{tid}")
def get_turn(h: Any, r: dict[str, Any]):
    return 200, turn_summary(h, r["tid"], r["tenant"])


@route("GET", "/v1/turns/{tid}/events")
def events(h: Any, r: dict[str, Any]):
    turn_summary(h, r["tid"], r["tenant"])  # 404 for another tenant
    cursors = [r["query"].get("after"), r["query"].get("after_seq"), r["headers"].get("Last-Event-ID")]
    after = max([int(c) for c in cursors if c not in (None, "")] or [-1])
    return 200, ("sse", r["tid"], after, r["query"].get("follow", "true") != "false")


def live_turn(h: Any, r: dict[str, Any]) -> Any:
    summary = turn_summary(h, r["tid"], r["tenant"])
    turn = h.live.get(r["tid"])
    if summary["status"] == "done" or turn is None:
        raise HTTPError(409, "turn_finished", "The turn already ended.", {"turn": summary})
    return turn


@route("POST", "/v1/turns/{tid}/resume")
def resume(h: Any, r: dict[str, Any]):
    mode = (r["body"] or {}).get("mode", "step")
    if mode not in ("step", "continue", "step_over"):
        raise HTTPError(400, "invalid_request", "mode is step, continue or step_over")
    turn = live_turn(h, r)
    node = turn.paused_at
    if node is None or turn.pause_reason != "breakpoint":
        raise HTTPError(409, "turn_not_paused", "The turn is not at a breakpoint.")
    turn.resume(mode)
    return 200, {"turn_id": r["tid"], "resumed_node_id": node, "mode": mode}


@route("POST", "/v1/turns/{tid}/cancel")
def cancel(h: Any, r: dict[str, Any]):
    live_turn(h, r).cancel()
    return 202, {"turn_id": r["tid"], "cancel_requested": True}


@route("GET", "/v1/nodes/{node}")
def node(h: Any, r: dict[str, Any]):
    n = NODES.get(r["node"])
    if n is None:
        raise HTTPError(404, "node_not_found", "No such node.")
    path = config.ROOT / "harness" / "core" / f"{r['node']}.py"
    return 200, {**{k: n.get(k) for k in ("id", "label", "kind", "group", "lesson", "can_disable")},
                 "implemented": r["node"] in config.GENERATED or not n.get("can_disable"),
                 "inspector": {"type": "source"}, "recent": [],
                 "source": {"path": str(path.relative_to(config.ROOT)), "language": "python",
                            "content": path.read_text()} if path.exists() else None}  # fmt: skip


@route("GET", "/v1/events")
def ops_events(h: Any, r: dict[str, Any]):
    """The tenant's ops events (http_api.md, P4 integration). This repo emits none, so the
    stream only keeps the connection alive; the web app listens here for releases and runs."""
    return 200, ("idle",)


@route("GET", "/v1/lessons")
def lessons(h: Any, r: dict[str, Any]):
    return 200, {"lessons": [], "walk_order": config.DIAGRAM.get("walk_order", [])}


@route("POST", "/v1/evals/runs")
def eval_run(h: Any, r: dict[str, Any]):
    body = r["body"] or {}
    if body.get("pack") not in h.settings.packs or body.get("suite") != "golden":
        raise HTTPError(422 if body.get("suite") == "golden" else 400, "invalid_request",
                        "pack must be a loaded pack and suite golden")  # fmt: skip
    run_id = evals.start(h, r["tenant"], body["pack"], body.get("cases"))
    return 202, {"run_id": run_id, "events_url": f"/v1/evals/runs/{run_id}/events"}


@route("GET", "/v1/evals/runs")
def eval_runs(h: Any, r: dict[str, Any]):
    return 200, {"runs": [evals.summary(x) for x in h.db.rows("eval_runs", tenant=r["tenant"])][::-1]}


@route("GET", "/v1/evals/runs/{run}")
def eval_one(h: Any, r: dict[str, Any]):
    run = h.db.one("eval_runs", run_id=r["run"], tenant=r["tenant"])
    if run is None:
        raise HTTPError(404, "run_not_found", "No such run.")
    return 200, {**evals.summary(run), "cases": run.get("cases", []),
                 "regression_cases": run.get("regression_cases", []), "diff": run.get("diff")}  # fmt: skip


@route("GET", "/v1/evals/golden")
def golden(h: Any, r: dict[str, Any]):
    pack = r["query"].get("pack") or h.settings.default_pack
    return 200, {"pack": pack, "cases": evals.load_cases(h, pack)}


#% if has("approval")
@route("POST", "/v1/approvals/{aid}")
def decide(h: Any, r: dict[str, Any]):
    decision = (r["body"] or {}).get("decision")
    if decision not in ("approve", "deny"):
        raise HTTPError(400, "invalid_request", "decision is approve or deny")
    from harness.core import approval

    try:
        rec = approval.decide(h, r["aid"], r["tenant"], decision, f"user:{r['tenant']}")
    except KeyError as exc:
        raise HTTPError(404, "approval_not_found", "No such approval.") from exc
    except ValueError as exc:
        raise HTTPError(409, "approval_already_decided", "The approval was already decided.") from exc
    return 200, {"approval_id": r["aid"], "turn_id": rec["turn"], "decision": decision,
                 "decided_by": rec["decided_by"], "decided_at": rec["decided_at"]}  # fmt: skip


@route("GET", "/v1/audit")
def audit_export(h: Any, r: dict[str, Any]):
    from harness.core import audit

    return 200, audit.export(h.db, r["tenant"], int(r["query"].get("limit", 500)))
#% endif


#% if release == "chain"
def pack_of(h: Any, r: dict[str, Any]) -> str:
    pack = r["query"].get("pack") or (r["body"] or {}).get("pack") or h.settings.default_pack
    if pack not in h.settings.packs:
        raise HTTPError(422, "invalid_request", f"unknown pack {pack}")
    return pack


@route("GET", "/v1/artifacts")
def artifacts(h: Any, r: dict[str, Any]):
    pack = pack_of(h, r)
    arts = [{k: v for k, v in a.items() if k != "body"} for a in h.release.working_set(pack).values()]
    return 200, {"pack": pack, "artifacts": arts, "release": {
        "active_release_id": h.release.info(pack)["release_id"],
        "working_set_dirty": h.release.dirty(pack), "use_working_set": True}}  # fmt: skip


@route("GET", "/v1/artifacts/{kind}/{name}")
def artifact(h: Any, r: dict[str, Any]):
    found = h.release.artifact(pack_of(h, r), r["kind"], r["name"])
    if found is None:
        raise HTTPError(404, "artifact_not_found", "No such artifact.")
    return 200, found


@route("PUT", "/v1/artifacts/{kind}/{name}")
def put_artifact(h: Any, r: dict[str, Any]):
    body = r["body"] or {}
    if not isinstance(body.get("body"), str):
        raise HTTPError(400, "invalid_request", "body is required")
    try:
        out = h.release.put(pack_of(h, r), r["kind"], r["name"], body["body"], body.get("note"))
    except Exception as exc:  # noqa: BLE001 - a YAML parse error
        raise HTTPError(422, "invalid_artifact", str(exc)[:300]) from exc
    if out is None:
        raise HTTPError(404, "artifact_not_found", "No such artifact.")
    return 200, out


@route("GET", "/v1/releases")
def releases(h: Any, r: dict[str, Any]):
    return 200, h.release.listing(pack_of(h, r))


@route("POST", "/v1/releases")
def new_release(h: Any, r: dict[str, Any]):
    return 201, h.release.create(pack_of(h, r), (r["body"] or {}).get("note"))


@route("GET", "/v1/releases/{rid}")
def release_one(h: Any, r: dict[str, Any]):
    found = h.release.get(r["rid"])
    if found is None:
        raise HTTPError(404, "release_not_found", "No such release.")
    return 200, found


@route("POST", "/v1/releases/{rid}/rollback")
def rollback(h: Any, r: dict[str, Any]):
    found = h.release.rollback(r["rid"])
    if found is None:
        raise HTTPError(404, "release_not_found", "No such release.")
    return 200, found
#% endif


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    harness: Any = None

    def log_message(self, *args: Any) -> None:  # quiet; the trace is the log
        return

    def do_GET(self) -> None:
        self.dispatch("GET")

    def do_POST(self) -> None:
        self.dispatch("POST")

    def do_PUT(self) -> None:
        self.dispatch("PUT")

    def do_OPTIONS(self) -> None:
        self.send(204, None)

    def dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        try:
            tenant = self.headers.get("X-Harnesswalk-Tenant") or "dev"
            if tenant.startswith("evalrun-") or not config.ID.match(tenant.replace(".", "_")):
                raise HTTPError(400, "invalid_request", "invalid tenant")
            body = None
            if method in ("POST", "PUT"):
                if "application/json" not in (self.headers.get("Content-Type") or ""):
                    raise HTTPError(415, "unsupported_media_type", "send application/json")
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.loads(raw) if raw.strip() else {}
            for m, rx, fn in ROUTES:
                match = rx.match(url.path)
                if match and m == method:
                    req = {**match.groupdict(), "tenant": tenant, "body": body, "headers": self.headers,
                           "query": {k: v[-1] for k, v in parse_qs(url.query).items()}}  # fmt: skip
                    status, out = fn(self.harness, req)
                    if isinstance(out, tuple) and out and out[0] == "sse":
                        return self.stream(*out[1:])
                    if isinstance(out, tuple) and out == ("idle",):
                        return self.idle()
                    return self.send(status, out)
            raise HTTPError(404, "not_found", f"No route {method} {url.path}.")
        except HTTPError as exc:
            self.send(exc.status, {"error": {"code": exc.code, "message": str(exc)}, **exc.extra})
        except ValueError as exc:
            self.send(400, {"error": {"code": "invalid_request", "message": str(exc)[:200]}})

    def cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin in ("http://127.0.0.1:5173", "http://localhost:5173"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Last-Event-ID, X-Harnesswalk-Tenant")

    def send(self, status: int, payload: Any) -> None:
        data = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.cors()
        self.end_headers()
        self.wfile.write(data)

    def sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.cors()
        self.end_headers()
        self.close_connection = True

    def idle(self) -> None:
        self.sse_headers()
        try:
            while True:
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                time.sleep(15)
        except OSError:  # the client went away
            return

    def stream(self, turn_id: str, after: int, follow: bool) -> None:
        """SSE (events.md section 7): `id` is seq, `event` is type, `data` the event JSON."""
        self.sse_headers()
        h, sent, quiet = self.harness, after, time.monotonic()
        while True:
            turn = h.live.get(turn_id)
            events = list(turn.events) if turn is not None else h.trace.events(turn_id)
            for e in events[sent + 1:]:
                self.wfile.write(f"id: {e['seq']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n".encode())
                sent, quiet = e["seq"], time.monotonic()
            self.wfile.flush()
            if not follow or (events and events[-1]["type"] == "turn_end") or turn is None:
                return
            with turn.cond:
                if len(turn.events) - 1 <= sent:
                    turn.cond.wait(1.0)
            if time.monotonic() - quiet > 15:
                self.wfile.write(b": keepalive\n\n")
                quiet = time.monotonic()


def serve(h: Any, port: int, host: str = "127.0.0.1") -> None:
    Handler.harness = h
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(f"harness listening on http://{host}:{port}", flush=True)
    server.serve_forever()
