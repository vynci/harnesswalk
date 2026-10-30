"""The conformance runner (spec/conformance/README.md, "Protocol").

For each case: start the reference harness in the case's mode (default dev) with the
conformance profile, the fake provider and the case's fixture and config overrides, on a real
loopback HTTP server in a background thread. A `two_process` case runs it as an OS process
(`python -m harnesswalk serve`) instead, so a `restart` action can kill it with SIGKILL and
start a new one on the same data directory. Then talk to it only through the HTTP API: post
each turn, read its SSE stream, perform the case's actions (approve, deny, resume, cancel,
restart) when their matchers fire, and assert the stream: schema, stream rules, path, statuses,
ordered events, absent events, cost, reply, then the turn's HTTP checks.

P4: a turn's `before` and `after` requests (GET, POST, PUT) run around it, with `save` to keep
values from a response and `${name}` to use them anywhere later in the case (`${turn_id}` and
`${session_id}` name the current turn), and `wait` to poll until a request matches.

No network beyond 127.0.0.1, no Docker, no model. Run the whole suite and print one line per
case with:

    uv run python -m tests.conformance.runner
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from tests.spec.support import (
    ROOT,
    check_expectations,
    check_stream,
    load_yaml,
    matches,
    subset,
)

CONF = ROOT / "spec" / "conformance"
PROFILE = CONF / "profile.yaml"
TURN_LIMIT_S = 30.0


def case_paths() -> list[Path]:
    return sorted((CONF / "cases").glob("*.yaml"))


@dataclass
class CaseResult:
    case_id: str
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    streams: list[list[dict[str, Any]]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.problems

    def line(self) -> str:
        head = f"PASS {self.case_id}" if self.passed else f"FAIL {self.case_id} {self.problems[0]}"
        return head + "".join(f"\n  warning: {w}" for w in self.warnings)


class LiveHarness:
    """The reference harness behind a real HTTP server on a free loopback port."""

    def __init__(self, case: dict[str, Any], data_dir: Path) -> None:
        from harnesswalk.config.loader import load_settings
        from harnesswalk.control.http_api import create_app
        from harnesswalk.runtime import Harness

        overridable = set(load_yaml(PROFILE)["overridable"])
        extra = set(case.get("config", {})) - overridable
        if extra:
            raise ValueError(f"case overrides keys outside the profile: {sorted(extra)}")
        settings = load_settings(
            ROOT,
            mode=case.get("mode", "dev"),
            provider="fake",
            fixture=case["fixture"],
            profile=PROFILE,
            overrides=case.get("config", {}),
            data_dir=data_dir,
            env={},
        )
        self.harness = Harness(settings)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.base_url = f"http://127.0.0.1:{self.sock.getsockname()[1]}"
        config = uvicorn.Config(create_app(self.harness), log_level="warning", lifespan="on")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.sock]})

    def __enter__(self) -> LiveHarness:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("harness did not start")
            time.sleep(0.01)
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(10)
        self.sock.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveProcess:
    """The reference harness as its own OS process, for `two_process` cases (P3)."""

    def __init__(self, case: dict[str, Any], data_dir: Path) -> None:
        overridable = set(load_yaml(PROFILE)["overridable"])
        extra = set(case.get("config", {})) - overridable
        if extra:
            raise ValueError(f"case overrides keys outside the profile: {sorted(extra)}")
        self.case = case
        self.data_dir = data_dir
        self.proc: subprocess.Popen | None = None
        self.base_url = ""
        self.harness = None  # nothing to read unused fixture responses from

    def start(self) -> None:
        port = _free_port()
        cmd = [
            sys.executable,
            "-m",
            "harnesswalk",
            "serve",
            "--provider",
            "fake",
            "--fixture",
            self.case["fixture"],
            "--profile",
            str(PROFILE),
            "--mode",
            self.case.get("mode", "dev"),
            "--port",
            str(port),
            "--log-level",
            "warning",
        ]
        for key, value in self.case.get("config", {}).items():
            cmd += ["--set", f"{key}={json.dumps(value)}"]
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HARNESSWALK_ROOT": str(ROOT),
            "HARNESSWALK_DATA_DIR": str(self.data_dir),
            "HARNESSWALK_WEB_DIST": str(self.data_dir / "no-web"),
        }
        log = open(self.data_dir / "server.log", "ab")  # noqa: SIM115 - lives with the process
        self.proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=log, cwd=str(ROOT))
        self.base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"harness process exited {self.proc.returncode}")
            try:
                if httpx.get(f"{self.base_url}/healthz", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        raise RuntimeError("harness process did not start")

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)  # a crash: no shutdown code runs
            self.proc.wait(10)

    def __enter__(self) -> LiveProcess:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.kill()


class Driver:
    """The live harness and a client for it; a restart swaps both."""

    def __init__(self, live: Any, headers: dict[str, str]) -> None:
        self.live = live
        self.headers = headers
        self.client = self._client()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.live.base_url, headers=self.headers, timeout=TURN_LIMIT_S
        )

    async def restart(self) -> None:
        if not isinstance(self.live, LiveProcess):
            raise AssertionError("restart needs a two_process case")
        cookies = self.client.cookies
        await self.client.aclose()
        self.live.kill()
        await asyncio.to_thread(self.live.start)
        self.client = self._client()
        self.client.cookies = cookies

    async def aclose(self) -> None:
        await self.client.aclose()


async def read_turn(
    driver: Driver, turn_id: str, actions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Read the SSE stream until turn_end, firing actions as their matchers fire.

    After a `restart` action the stream is reopened on the new process with `?after=` the
    last seq seen, and the remaining actions for the same event fire against it.
    """
    events: list[dict[str, Any]] = []
    fired: set[int] = set()
    cancelled = False  # a cancelled turn takes no further actions (README, Act)
    while True:
        after = events[-1]["seq"] if events else None
        url = f"/v1/turns/{turn_id}/events" + (f"?after={after}" if after is not None else "")
        restarted = False
        async with driver.client.stream("GET", url) as response:
            if response.status_code != 200:
                raise AssertionError(f"events stream returned HTTP {response.status_code}")
            data: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    data.append(line[5:].strip())
                    continue
                if line or not data:
                    continue
                event = json.loads("\n".join(data))
                data = []
                events.append(event)
                if not cancelled:
                    did = await fire(driver, turn_id, actions, fired, event)
                    restarted, cancelled = did == "restart", did == "cancel"
                if event["type"] == "turn_end" or restarted:
                    break
        if not restarted:
            return events
        await check_after_restart(driver, turn_id, events[-1])
        cancelled = await fire(driver, turn_id, actions, fired, events[-1]) == "cancel"


async def fire(
    driver: Driver,
    turn_id: str,
    actions: list[dict[str, Any]],
    fired: set[int],
    event: dict[str, Any],
) -> str | None:
    """Fire the actions this event matches, in order. Returns `restart` when one restarted the
    harness (the rest wait for the new process) and `cancel` once the turn was cancelled."""
    for i, action in enumerate(actions):
        if (i in fired and not action.get("repeat")) or not matches(action["when"], event):
            continue
        fired.add(i)
        if action["do"].get("restart"):
            await driver.restart()
            return "restart"
        await perform(driver.client, turn_id, action["do"], event)
        if "cancel" in action["do"]:
            return "cancel"
    return None


async def check_after_restart(driver: Driver, turn_id: str, last: dict[str, Any]) -> None:
    """The new process knows the turn: paused where it was, not done."""
    r = await driver.client.get(f"/v1/turns/{turn_id}")
    if r.status_code != 200:
        raise AssertionError(f"after restart GET /v1/turns returned {r.status_code}")
    summary = r.json()
    if summary["status"] == "done":
        raise AssertionError("after restart the turn was closed instead of resumed")
    if last["type"] == "node_paused" and summary["paused_at_node"] != last["node_id"]:
        raise AssertionError(f"after restart the turn is {summary['status']}, not paused")


async def perform(
    client: httpx.AsyncClient, turn_id: str, do: dict[str, Any], event: dict[str, Any]
) -> None:
    if "approval" in do:
        approval_id = event["summary"]["approval_id"]
        r = await client.post(f"/v1/approvals/{approval_id}", json={"decision": do["approval"]})
    elif "resume" in do:
        r = await client.post(f"/v1/turns/{turn_id}/resume", json={"mode": do["resume"]})
    elif do.get("cancel") is True:
        r = await client.post(f"/v1/turns/{turn_id}/cancel", json={})
        if r.status_code != 202:
            raise AssertionError(f"cancel returned HTTP {r.status_code}: {r.text}")
        return
    else:
        raise AssertionError(f"action {do} is not supported over HTTP")
    if r.status_code != 200:
        raise AssertionError(f"action {do} returned HTTP {r.status_code}: {r.text}")


VAR = re.compile(r"\$\{([a-z_][a-z0-9_]*)\}")


def substitute(value: Any, saved: dict[str, Any]) -> Any:
    """`${name}` in every string of a case value, from the values saved so far."""
    if isinstance(value, str):
        whole = VAR.fullmatch(value)
        if whole and whole.group(1) in saved:
            return saved[whole.group(1)]
        return VAR.sub(lambda m: str(saved.get(m.group(1), m.group(0))), value)
    if isinstance(value, list):
        return [substitute(v, saved) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, saved) for k, v in value.items()}
    return value


def dig(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


async def request(client: httpx.AsyncClient, req: dict[str, Any], saved: dict[str, Any]) -> None:
    """One `before` or `after` request: send, check status and match, save values (P4)."""
    req = substitute(req, saved)
    want = req.get("status", 200)
    deadline = time.monotonic() + float((req.get("wait") or {}).get("timeout_s", 0))
    while True:
        r = await client.request(req["method"], req["path"], json=req.get("body"))
        body = r.json() if r.content else None
        ok = r.status_code == want and ("match" not in req or subset(req["match"], body))
        if ok or time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.1)
    if r.status_code != want:
        raise AssertionError(f"{req['method']} {req['path']} returned {r.status_code}: {r.text}")
    if "match" in req and not subset(req["match"], body):
        raise AssertionError(f"{req['method']} {req['path']} does not match {req['match']}: {body}")
    for name, dotted in (req.get("save") or {}).items():
        saved[name] = dig(body, dotted)


async def _drive(live: Any, case: dict[str, Any], result: CaseResult) -> None:
    tenant = load_yaml(PROFILE)["tenant_prefix"] + case["id"]
    driver = Driver(live, {"X-Harnesswalk-Tenant": tenant})
    try:
        health = (await driver.client.get("/healthz")).json()
        if health.get("provider") != "fake" or health.get("fixture") != case["fixture"]:
            result.problems.append(f"healthz: {health}")
            return
        session_id = None
        saved: dict[str, Any] = {}
        for n, raw_turn in enumerate(case["turns"]):
            for req in raw_turn.get("before", []):
                await request(driver.client, req, saved)
            turn = substitute(raw_turn, saved)
            body: dict[str, Any] = {"message": turn["send"]["message"]}
            if turn["send"].get("options"):
                body["options"] = turn["send"]["options"]
            if session_id:
                body["session_id"] = session_id
            r = await driver.client.post("/v1/turns", json=body)
            if r.status_code != 202:
                result.problems.append(
                    f"turn {n}: POST /v1/turns returned {r.status_code} {r.text}"
                )
                return
            session_id = r.json()["session_id"]
            saved.update(turn_id=r.json()["turn_id"], session_id=session_id)
            turn = substitute(raw_turn, saved)
            events = await asyncio.wait_for(
                read_turn(driver, r.json()["turn_id"], turn.get("actions", [])), TURN_LIMIT_S
            )
            result.streams.append(events)
            for problem in check_stream(events) or check_expectations(turn["expect"], events):
                result.problems.append(f"turn {n}: {problem}")
            for check in turn.get("http", []):
                check = substitute(check, saved)
                got = await driver.client.get(check["get"])
                if got.status_code != check.get("status", 200):
                    result.problems.append(
                        f"turn {n}: http {check['get']} returned {got.status_code}"
                    )
                elif "match" in check and not subset(check["match"], got.json()):
                    result.problems.append(
                        f"turn {n}: http {check['get']} does not match {check['match']}"
                    )
            for req in raw_turn.get("after", []):
                await request(driver.client, req, saved)
    finally:
        await driver.aclose()


def run_case(path: Path) -> CaseResult:
    case = load_yaml(path)
    result = CaseResult(case["id"])
    live_cls = LiveProcess if case.get("two_process") else LiveHarness
    with (
        tempfile.TemporaryDirectory(prefix="harnesswalk-conf-") as tmp,
        live_cls(case, Path(tmp)) as live,
    ):
        try:
            asyncio.run(_drive(live, case, result))
        except (AssertionError, TimeoutError, RuntimeError) as exc:
            result.problems.append(f"{type(exc).__name__}: {exc}")
        if live.harness is not None:
            fake = live.harness.providers["fake"]
            unused = fake.unused()  # type: ignore[attr-defined]
            if unused:
                result.warnings.append(f"unused fixture responses: {', '.join(unused)}")
    return result


def main() -> int:
    results = [run_case(p) for p in case_paths()]
    for r in results:
        print(r.line())
    passed = sum(r.passed for r in results)
    print(f"{passed}/{len(results)} cases passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
