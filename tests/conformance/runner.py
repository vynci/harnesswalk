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

P5: `send.session_id` sends a turn in that session (one made with `POST /v1/sessions`), and
later turns without one continue it.

P7: the same cases against any HTTP implementation (spec/conformance/README.md, "Running the
suite against your implementation"). `--command` starts it once per case from a command
template, so every case runs, restarts included; `--base-url` drives one server that is already
running in dev mode with the conformance profile, sends each turn's fixture in the request
body, and skips the cases that need their own server (another mode, config overrides, a
restart, or server-wide artifact and release state).

No network beyond 127.0.0.1, no Docker, no model. Run the whole suite and print one line per
case with:

    uv run python -m tests.conformance.runner
    uv run python -m tests.conformance.runner --base-url http://127.0.0.1:8000
    uv run python -m tests.conformance.runner --command "my-harness serve --port {port}"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import shlex
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


# Requests to these paths change state the whole server shares (artifacts and releases are not
# per tenant yet), so a case that makes them needs a server of its own.
SERVER_WIDE = ("/v1/artifacts", "/v1/releases")


@dataclass
class Target:
    """Where the cases run. Neither set: the in-process reference harness, one per case.

    `command`: a command template started once per case (spec/conformance/README.md lists the
    placeholders and variables). `base_url`: one implementation that is already running.
    `tenant_suffix` is appended to every case's tenant, so reruns against one long-lived
    server do not see each other's records."""

    base_url: str | None = None
    command: str | None = None
    tenant_suffix: str = ""

    @property
    def remote(self) -> bool:
        return self.base_url is not None


@dataclass
class CaseResult:
    case_id: str
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    streams: list[list[dict[str, Any]]] = field(default_factory=list)
    skipped: str | None = None

    @property
    def passed(self) -> bool:
        return not self.problems and self.skipped is None

    def line(self) -> str:
        if self.skipped is not None:
            head = f"SKIP {self.case_id} {self.skipped}"
        elif self.passed:
            head = f"PASS {self.case_id}"
        else:
            head = f"FAIL {self.case_id} {self.problems[0]}"
        return head + "".join(f"\n  warning: {w}" for w in self.warnings)


def needs_own_server(case: dict[str, Any]) -> str | None:
    """Why a case cannot run against a shared, already running server, or None."""
    if case.get("two_process"):
        return "restarts the harness (use --command)"
    if case.get("mode", "dev") != "dev":
        return f"runs in {case['mode']} mode (use --command)"
    if case.get("config"):
        return f"overrides {', '.join(sorted(case['config']))} (use --command)"
    for turn in case["turns"]:
        for req in turn.get("before", []) + turn.get("after", []):
            if str(req.get("path", "")).startswith(SERVER_WIDE):
                return f"changes server-wide state at {req['path']} (use --command)"
    return None


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
            env={"HARNESSWALK_SECRET": "conformance-secret"},  # public mode's cookie secret
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
    """The harness as its own OS process: the reference for `two_process` cases (P3), or, with a
    command template (P7), any implementation, for every case."""

    def __init__(self, case: dict[str, Any], data_dir: Path, command: str | None = None) -> None:
        overridable = set(load_yaml(PROFILE)["overridable"])
        extra = set(case.get("config", {})) - overridable
        if extra:
            raise ValueError(f"case overrides keys outside the profile: {sorted(extra)}")
        self.case = case
        self.data_dir = data_dir
        self.command = command
        self.proc: subprocess.Popen | None = None
        self.base_url = ""
        self.harness = None  # nothing to read unused fixture responses from

    def _external(self, port: int) -> tuple[list[str], dict[str, str]]:
        """The command template filled in, and the variables it runs with (README, P7)."""
        config = self.case.get("config", {})
        fields = {
            "port": str(port),
            "data_dir": str(self.data_dir),
            "fixture": self.case["fixture"],
            "mode": self.case.get("mode", "dev"),
            "profile": str(PROFILE),
            "root": str(ROOT),
            "python": sys.executable,
            "config_json": json.dumps(config, sort_keys=True),
        }
        quoted = {k: shlex.quote(v) for k, v in fields.items()}
        quoted["set_args"] = " ".join(
            shlex.quote(f"--set={key}={json.dumps(value)}") for key, value in config.items()
        )
        assert self.command is not None
        cmd = shlex.split(self.command.format(**quoted))
        env = dict(os.environ)
        env.update(
            HARNESSWALK_MODE=fields["mode"],
            HARNESSWALK_PROVIDER="fake",
            HARNESSWALK_FIXTURE=fields["fixture"],
            HARNESSWALK_PROFILE=fields["profile"],
            HARNESSWALK_DATA_DIR=fields["data_dir"],
            HARNESSWALK_PORT=fields["port"],
            HARNESSWALK_CONFORMANCE_CONFIG=fields["config_json"],
        )
        return cmd, env

    def start(self) -> None:
        port = _free_port()
        if self.command:
            cmd, env = self._external(port)
            self._spawn(cmd, env, port)
            return
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
            "HARNESSWALK_SECRET": "conformance-secret",
        }
        self._spawn(cmd, env, port)

    def _spawn(self, cmd: list[str], env: dict[str, str], port: int) -> None:
        log = open(self.data_dir / "server.log", "ab")  # noqa: SIM115 - lives with the process
        self.proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=log, cwd=str(ROOT))
        self.base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                tail = (self.data_dir / "server.log").read_text(errors="replace")[-400:]
                raise RuntimeError(f"harness process exited {self.proc.returncode}: {tail}")
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


class Remote:
    """An implementation that is already running at `base_url` (P7, `--base-url`)."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.harness = None

    def __enter__(self) -> Remote:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


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
            raise AssertionError("restart needs a two_process case and a process to restart")
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


async def _drive(
    live: Any, case: dict[str, Any], result: CaseResult, target: Target | None = None
) -> None:
    target = target or Target()
    tenant = load_yaml(PROFILE)["tenant_prefix"] + case["id"] + target.tenant_suffix
    driver = Driver(live, {"X-Harnesswalk-Tenant": tenant})
    try:
        health = (await driver.client.get("/healthz")).json()
        # A shared server runs its own start-up fixture; each turn names the case's instead.
        fixture_ok = target.remote or health.get("fixture") == case["fixture"]
        if health.get("provider") != "fake" or not fixture_ok:
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
            if target.remote and "fixture" not in (turn["send"].get("options") or {}):
                body["fixture"] = case["fixture"]  # http_api.md: a per-turn fixture, dev only
            session_id = turn["send"].get("session_id") or session_id
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


def _live(case: dict[str, Any], data_dir: Path, target: Target) -> Any:
    if target.base_url is not None:
        return Remote(target.base_url)
    if target.command:
        return LiveProcess(case, data_dir, target.command)
    return LiveProcess(case, data_dir) if case.get("two_process") else LiveHarness(case, data_dir)


def run_case(path: Path, target: Target | None = None) -> CaseResult:
    target = target or Target()
    case = load_yaml(path)
    result = CaseResult(case["id"])
    if target.remote and (reason := needs_own_server(case)):
        result.skipped = reason
        return result
    with tempfile.TemporaryDirectory(prefix="harnesswalk-conf-") as tmp:
        try:
            with _live(case, Path(tmp), target) as live:
                try:
                    asyncio.run(_drive(live, case, result, target))
                except (AssertionError, TimeoutError, RuntimeError, httpx.HTTPError) as exc:
                    result.problems.append(f"{type(exc).__name__}: {exc}")
                _unused_warnings(live, result)
        except RuntimeError as exc:  # the implementation did not start
            result.problems.append(f"{type(exc).__name__}: {exc}")
    return result


def _unused_warnings(live: Any, result: CaseResult) -> None:
    """Only the in-process reference can say which fixture responses no call read."""
    if live.harness is not None:
        fake = live.harness.providers["fake"]
        unused = fake.unused()  # type: ignore[attr-defined]
        if unused:
            result.warnings.append(f"unused fixture responses: {', '.join(unused)}")


def check_remote(base_url: str) -> str | None:
    """`GET /healthz` of a running implementation: the fake provider and a spec version of the
    same major as the suite. Returns a problem, or None."""
    try:
        r = httpx.get(base_url.rstrip("/") + "/healthz", timeout=5)
        health = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        return f"GET /healthz failed: {exc}"
    if r.status_code != 200 or health.get("provider") != "fake":
        return f"GET /healthz must be 200 with provider fake, got {r.status_code} {health}"
    ours, theirs = str(load_yaml(PROFILE)["spec_version"]), str(health.get("spec_version", ""))
    if theirs.split(".")[0] != ours.split(".")[0]:
        return f"the implementation says spec_version {theirs!r}; this suite is spec {ours}"
    print(f"implementation {health.get('version', '?')} (spec {theirs}) at {base_url}")
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m tests.conformance.runner",
        description="Run spec/conformance/cases against a harness over HTTP.",
    )
    where = ap.add_mutually_exclusive_group()
    where.add_argument(
        "--base-url", help="an implementation already running in dev mode (one shared server)"
    )
    where.add_argument(
        "--command", help="a command template that starts the implementation once per case"
    )
    ap.add_argument("--case", action="append", default=[], help="run only this case id (repeat)")
    ap.add_argument(
        "--tenant-suffix",
        help="appended to each case's tenant (default with --base-url: a random one per run)",
    )
    ap.add_argument("--list", action="store_true", help="print the case ids and exit")
    args = ap.parse_args(argv)

    paths = case_paths()
    if args.case:
        known = {p.stem for p in paths}
        unknown = sorted(set(args.case) - known)
        if unknown:
            ap.error(f"no such case: {', '.join(unknown)}")
        paths = [p for p in paths if p.stem in args.case]
    if args.list:
        print("\n".join(p.stem for p in paths))
        return 0
    suffix = args.tenant_suffix
    if suffix is None:
        suffix = f"-{secrets.token_hex(3)}" if args.base_url else ""
    target = Target(base_url=args.base_url, command=args.command, tenant_suffix=suffix)
    if target.base_url is not None and (problem := check_remote(target.base_url)):
        print(f"FAIL {problem}")
        return 1

    results = [run_case(p, target) for p in paths]
    for r in results:
        print(r.line())
    passed = sum(r.passed for r in results)
    skipped = sum(r.skipped is not None for r in results)
    ran = len(results) - skipped
    tail = f", {skipped} skipped" if skipped else ""
    print(f"{passed}/{ran} cases passed{tail}")
    return 0 if passed == ran else 1


if __name__ == "__main__":
    sys.exit(main())
