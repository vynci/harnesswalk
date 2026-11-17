"""The conformance runner, vendored: the Harnesswalk suite against this repo, over HTTP only.

Protocol: tests/conformance/suite/spec/conformance/README.md (a copy of the spec's suite at
the version in harness.lock). For each case: start `python -m harness serve` with the suite's
profile and the case's fixture, drive it through the HTTP API (turns, the SSE stream,
approvals, resume, cancel, restart), and assert the stream: schema, stream rules, path,
statuses, ordered events, absent events, cost, reply, then the turn's HTTP checks.

    python -m tests.conformance.runner --profile @@profile@@          # the fake provider
    python -m tests.conformance.runner --profile @@profile@@ --provider ollama   # a real model

A case is SKIP, never FAIL, when the profile switches off a node it requires, when it runs in
a mode this repo does not generate (public), when it needs the release chain and this repo
has the file door, or when it drives the tuning HTTP API (`/v1/tune`), which is not generated. With a real model, `cost` assertions (scripted token counts) are dropped
and a case whose fixture scripts tool calls is marked `scripted`: its walk depends on the model
choosing the same tool, so a FAIL there is the model's choice, not a broken harness.

Derived from Harnesswalk's tests/conformance/runner.py (Apache-2.0).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "tests" / "conformance" / "suite"
CONF = SUITE / "spec" / "conformance"
PROFILE = CONF / "profile.yaml"
TURN_LIMIT_S = 60.0
POINT = {"approval_requested", "approval_decided", "memory_proposed", "memory_written",
         "artifact_released", "message_delta", "node_paused", "node_resumed"}  # fmt: skip
DIAGRAM = json.loads((SUITE / "spec" / "diagram" / "diagram.json").read_text())
NODES = {n["id"]: n for n in DIAGRAM["nodes"]}
SIMPLE = {i for i, n in NODES.items() if n["view"] == "simple"}
EDGES = {e["id"] for e in DIAGRAM["edges"]}
VALIDATOR = jsonschema.Draft202012Validator(json.loads((SUITE / "spec" / "events.schema.json").read_text()))
MANIFEST = yaml.safe_load((ROOT / "harness.yaml").read_text())


# -- the stream rules and matchers (spec/conformance/README.md, "Assert") ---------------------
def check_stream(events: list[dict[str, Any]]) -> list[str]:
    problems = [f"seq {e.get('seq')}: schema: {err.message}" for e in events for err in VALIDATOR.iter_errors(e)]
    if problems:
        return problems
    if not events or events[0]["type"] != "turn_start" or events[-1]["type"] != "turn_end":
        problems.append("the stream does not run from turn_start to turn_end")
    for key in ("trace_id", "turn_id", "session_id", "tenant_id"):
        if any(e[key] != events[0][key] for e in events):
            problems.append(f"{key} changes within the turn")
    stack: list[str] = []
    paused: set[str] = set()
    prev = None
    for i, e in enumerate(events):
        ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        if e["seq"] != i:
            problems.append(f"event {i}: seq is {e['seq']}")
        if prev and ts < prev:
            problems.append(f"seq {i}: ts goes backwards")
        prev, node = ts, e.get("node_id")
        if node is not None and node not in NODES:
            problems.append(f"seq {i}: unknown node_id {node}")
            continue
        if e["type"] == "edge" and e["edge_id"] not in EDGES:
            problems.append(f"seq {i}: unknown edge_id {e['edge_id']}")
        if e["type"] == "turn_end" and stack:
            problems.append(f"seq {i}: turn_end with open nodes {stack}")
        if e["type"] == "node_enter":
            parent = NODES[node].get("parent")
            if parent and parent not in stack:
                problems.append(f"seq {i}: expert node {node} entered outside {parent}")
            stack.append(node)
        elif e["type"] in ("node_exit", "error") and node:
            if not stack or stack[-1] != node:
                problems.append(f"seq {i}: {e['type']} {node} does not close the top node")
            else:
                stack.pop()
                if node in paused and e["type"] != "error":
                    problems.append(f"seq {i}: {node} closed while paused")
                paused.discard(node)
        elif e["type"] in POINT:
            if node not in stack:
                problems.append(f"seq {i}: {e['type']} for {node} outside its span")
            if e["type"] == "node_paused":
                paused.add(node)
            elif e["type"] == "node_resumed":
                paused.discard(node)
    return problems


def subset(want: Any, got: Any) -> bool:
    if isinstance(want, dict):
        return isinstance(got, dict) and all(k in got and subset(v, got[k]) for k, v in want.items())
    return want == got


def matches(m: dict[str, Any], e: dict[str, Any]) -> bool:
    return all(k in e and subset(v, e[k]) for k, v in m.items())


def closing(events: list[dict[str, Any]]) -> list[str]:
    stack, closed = [], {}
    for i, e in enumerate(events):
        if e["type"] == "node_enter":
            stack.append(i)
        elif e["type"] in ("node_exit", "error") and e.get("node_id"):
            closed[stack.pop()] = e["status"]
    return [closed[i] for i, e in enumerate(events) if e["type"] == "node_enter" and e["node_id"] in SIMPLE]


def check_expect(expect: dict[str, Any], events: list[dict[str, Any]], model_run: bool) -> list[str]:
    out = []
    path = [e["node_id"] for e in events if e["type"] == "node_enter" and e["node_id"] in SIMPLE]
    if path != expect["path"]:
        out.append(f"path: got {path}, expected {expect['path']}")
    if "statuses" in expect:
        got, want = closing(events), expect["statuses"]
        want = [want] * len(got) if isinstance(want, str) else want
        if got != want:
            out.append(f"statuses: got {got}, expected {want}")
    cursor = 0
    for m in expect.get("events", []):
        while cursor < len(events) and not matches(m, events[cursor]):
            cursor += 1
        if cursor == len(events):
            out.append(f"events: no match in order for {m}")
            break
        cursor += 1
    for m in expect.get("absent", []):
        hits = [e["seq"] for e in events if matches(m, e)]
        if hits:
            out.append(f"absent: {m} matched seq {hits}")
    end = events[-1]
    for k, v in ({} if model_run else expect.get("cost", {})).items():
        if end.get("cost", {}).get(k) != v:
            out.append(f"cost: {k} is {end.get('cost', {}).get(k)}, expected {v}")
    reply = (end.get("summary") or {}).get("reply", "")
    out += [f"reply_contains: {t!r} not in reply {reply[:120]!r}" for t in expect.get("reply_contains", [])
            if t not in reply]
    return out


# -- running a case ------------------------------------------------------------------------------
def profile_off(name: str) -> set[str]:
    return set(yaml.safe_load((ROOT / "config" / "profiles" / f"{name}.yaml").read_text()).get("disable") or [])


def skip_reason(case: dict[str, Any], profile: str) -> str | None:
    if case.get("profile") and case["profile"] != profile:
        return f"runs under profile {case['profile']} only"
    if case.get("mode", "dev") != "dev":
        return f"runs in {case['mode']} mode, which this repo does not generate (modes: [dev])"
    off = sorted(set(case.get("requires") or []) & profile_off(profile))
    if off:
        return f"needs {', '.join(off)}, off in profile {profile}"
    paths = [r.get("path", "") for t in case["turns"] for r in t.get("before", []) + t.get("after", [])]
    if MANIFEST.get("release") != "chain" and any(p.startswith(("/v1/artifacts", "/v1/releases")) for p in paths):
        return "needs the release chain (this repo has release: file_door)"
    if any(p.startswith("/v1/tune") for p in paths):  # GENERATION_NOTES.md, "Not mapped"
        return "needs the tuning HTTP API (/v1/tune: compare, promote, canary), which is not generated"
    return None


def scripted(case: dict[str, Any]) -> bool:
    path = CONF / "fixtures" / f"{case['fixture']}.yaml"
    turns = yaml.safe_load(path.read_text())["turns"] if path.exists() else []
    return any(r.get("tool_calls") for t in turns for r in t["responses"])


class Harness:
    def __init__(self, case: dict[str, Any], data: Path, profile: str, provider: str) -> None:
        self.case, self.data, self.profile, self.provider = case, data, profile, provider
        self.proc: subprocess.Popen | None = None
        self.url = ""

    def start(self) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cfg = {**self.case.get("config", {}), "profile": self.profile}
        cmd = [sys.executable, "-m", "harness", "serve", "--port", str(port), "--data", str(self.data),
               "--fixture", self.case["fixture"], "--profile", str(PROFILE), "--provider", self.provider,
               *[f"--set={k}={json.dumps(v)}" for k, v in cfg.items()]]  # fmt: skip
        log = open(self.data / "server.log", "ab")  # noqa: SIM115 - lives with the process
        env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESSWALK_") or k == "HARNESSWALK_OLLAMA_URL"}
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=log, env=env)
        self.url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("harness exited: " + (self.data / "server.log").read_text()[-400:])
            try:
                if httpx.get(f"{self.url}/healthz", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.05)
        raise RuntimeError("harness did not start")

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait(10)


VAR = re.compile(r"\$\{([a-z_][a-z0-9_]*)\}")


def sub(value: Any, saved: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = VAR.fullmatch(value)
        if whole and whole.group(1) in saved:
            return saved[whole.group(1)]
        return VAR.sub(lambda m: str(saved.get(m.group(1), m.group(0))), value)
    if isinstance(value, list):
        return [sub(v, saved) for v in value]
    if isinstance(value, dict):
        return {k: sub(v, saved) for k, v in value.items()}
    return value


def request(c: httpx.Client, req: dict[str, Any], saved: dict[str, Any]) -> None:
    req = sub(req, saved)
    deadline = time.monotonic() + float((req.get("wait") or {}).get("timeout_s", 0))
    while True:
        r = c.request(req["method"], req["path"], json=req.get("body"))
        body = r.json() if r.content else None
        ok = r.status_code == req.get("status", 200) and ("match" not in req or subset(req["match"], body))
        if ok or time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    if not ok:
        raise AssertionError(f"{req['method']} {req['path']} returned {r.status_code}: {r.text[:300]}")
    for name, dotted in (req.get("save") or {}).items():
        value = body
        for part in dotted.split("."):
            value = value[int(part)] if isinstance(value, list) else value[part]
        saved[name] = value


def read_turn(live: Harness, client: list[httpx.Client], turn_id: str, actions: list[dict[str, Any]]):
    events: list[dict[str, Any]] = []
    fired: set[int] = set()
    cancelled = False
    deadline = time.monotonic() + TURN_LIMIT_S
    while True:
        after = f"?after={events[-1]['seq']}" if events else ""
        restarted = False
        with client[0].stream("GET", f"/v1/turns/{turn_id}/events{after}") as resp:
            data: list[str] = []
            for line in resp.iter_lines():
                if time.monotonic() > deadline:
                    raise TimeoutError("turn did not end")
                if line.startswith("data:"):
                    data.append(line[5:].strip())
                    continue
                if line or not data:
                    continue
                event = json.loads("\n".join(data))
                data = []
                events.append(event)
                if not cancelled:
                    did = fire(live, client, turn_id, actions, fired, event)
                    restarted, cancelled = did == "restart", did == "cancel"
                if event["type"] == "turn_end" or restarted:
                    break
        if not restarted:
            return events
        summary = client[0].get(f"/v1/turns/{turn_id}").json()
        if summary["status"] == "done" or summary.get("paused_at_node") != events[-1].get("node_id"):
            raise AssertionError(f"after restart the turn is {summary['status']}, not paused")
        cancelled = fire(live, client, turn_id, actions, fired, events[-1]) == "cancel"


def fire(live: Harness, client: list[httpx.Client], turn_id: str, actions: list[dict[str, Any]],
         fired: set[int], event: dict[str, Any]) -> str | None:  # fmt: skip
    for i, action in enumerate(actions):
        if (i in fired and not action.get("repeat")) or not matches(action["when"], event):
            continue
        fired.add(i)
        do = action["do"]
        if do.get("restart"):
            headers = client[0].headers
            client[0].close()
            live.kill()
            live.start()
            client[0] = httpx.Client(base_url=live.url, headers=headers, timeout=TURN_LIMIT_S)
            return "restart"
        if "approval" in do:
            r = client[0].post(f"/v1/approvals/{event['summary']['approval_id']}", json={"decision": do["approval"]})
        elif "resume" in do:
            r = client[0].post(f"/v1/turns/{turn_id}/resume", json={"mode": do["resume"]})
        else:
            r = client[0].post(f"/v1/turns/{turn_id}/cancel", json={})
            if r.status_code != 202:
                raise AssertionError(f"cancel returned {r.status_code}")
            return "cancel"
        if r.status_code != 200:
            raise AssertionError(f"action {do} returned {r.status_code}: {r.text[:200]}")
    return None


def run_case(path: Path, profile: str, provider: str) -> dict[str, Any]:
    case = yaml.safe_load(path.read_text())
    out = {"case": case["id"], "status": "PASS", "scripted": provider != "fake" and scripted(case)}
    if reason := skip_reason(case, profile):
        return {**out, "status": "SKIP", "detail": reason}
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="hw-conf-") as tmp:
        live = Harness(case, Path(tmp), profile, provider)
        try:
            live.start()
            client = [httpx.Client(base_url=live.url, timeout=TURN_LIMIT_S,
                                   headers={"X-Harnesswalk-Tenant": f"conformance-{case['id']}"})]  # fmt: skip
            health = client[0].get("/healthz").json()
            if health.get("provider") != provider:
                raise AssertionError(f"healthz: {health}")
            session, saved = None, {}
            for n, raw in enumerate(case["turns"]):
                for req in raw.get("before", []):
                    request(client[0], req, saved)
                turn = sub(raw, saved)
                body: dict[str, Any] = {"message": turn["send"]["message"]}
                if turn["send"].get("options"):
                    body["options"] = turn["send"]["options"]
                session = turn["send"].get("session_id") or session
                if session:
                    body["session_id"] = session
                r = client[0].post("/v1/turns", json=body)
                if r.status_code != 202:
                    raise AssertionError(f"turn {n}: POST /v1/turns returned {r.status_code} {r.text[:200]}")
                session = r.json()["session_id"]
                saved.update(turn_id=r.json()["turn_id"], session_id=session)
                turn = sub(raw, saved)
                events = read_turn(live, client, r.json()["turn_id"], turn.get("actions", []))
                problems += [f"turn {n}: {p}" for p in check_stream(events) or
                             check_expect(turn["expect"], events, provider != "fake")]  # fmt: skip
                for chk in turn.get("http", []):
                    chk = sub(chk, saved)
                    got = client[0].get(chk["get"])
                    if got.status_code != chk.get("status", 200) or ("match" in chk and not subset(chk["match"], got.json())):
                        problems.append(f"turn {n}: http {chk['get']} does not match {chk.get('match')}")
                for req in raw.get("after", []):
                    request(client[0], req, saved)
        except (AssertionError, TimeoutError, RuntimeError, httpx.HTTPError) as exc:
            problems.append(f"{type(exc).__name__}: {exc}")
        finally:
            live.kill()
    return {**out, "status": "FAIL", "detail": problems[0]} if problems else out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tests.conformance.runner")
    ap.add_argument("--profile", default=MANIFEST["profile"])
    ap.add_argument("--provider", default="fake")
    ap.add_argument("--case", action="append", default=[])
    ap.add_argument("--json", help="write the results to this file")
    args = ap.parse_args(argv)
    paths = sorted((CONF / "cases").glob("*.yaml"))
    paths = [p for p in paths if not args.case or p.stem in args.case]
    results = [run_case(p, args.profile, args.provider) for p in paths]
    for r in results:
        tag = " [scripted]" if r["scripted"] else ""
        print(f"{r['status']} {r['case']}{tag}" + (f" {r['detail']}" if r.get("detail") else ""))
    ran = [r for r in results if r["status"] != "SKIP"]
    passed = [r for r in ran if r["status"] == "PASS"]
    print(f"{len(passed)}/{len(ran)} cases passed, {len(results) - len(ran)} skipped under profile "
          f"{args.profile} on provider {args.provider}")  # fmt: skip
    if args.provider != "fake":
        model = [r for r in ran if not r["scripted"]]
        print(f"model cases {sum(r['status'] == 'PASS' for r in model)}/{len(model)}; scripted cases "
              f"{sum(r['status'] == 'PASS' for r in ran if r['scripted'])}/{len(ran) - len(model)}"
              " (their walk needs the fixture's scripted tool calls)")  # fmt: skip
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
    failed = [r for r in ran if r["status"] == "FAIL" and not r["scripted"]]
    return 1 if (failed if args.provider != "fake" else len(passed) != len(ran)) else 0


if __name__ == "__main__":
    sys.exit(main())
