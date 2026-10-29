"""The conformance runner (spec/conformance/README.md, "Protocol").

For each case: start the reference harness in dev mode with the conformance profile, the fake
provider and the case's fixture and config overrides, on a real loopback HTTP server in a
background thread. Then talk to it only through the HTTP API: post each turn, read its SSE
stream, perform the case's actions (approve, deny, resume) when their matchers fire, and assert
the stream: schema, stream rules, path, statuses, ordered events, absent events, cost, reply.

No network beyond 127.0.0.1, no Docker, no model. Run the whole suite and print one line per
case with:

    uv run python -m tests.conformance.runner
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import uvicorn

from tests.spec.support import ROOT, check_expectations, check_stream, load_yaml, matches

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
            mode="dev",
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


async def read_turn(
    client: httpx.AsyncClient, turn_id: str, actions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Read the SSE stream until turn_end, firing actions as their matchers fire."""
    events: list[dict[str, Any]] = []
    fired: set[int] = set()
    async with client.stream("GET", f"/v1/turns/{turn_id}/events") as response:
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
            for i, action in enumerate(actions):
                if (i in fired and not action.get("repeat")) or not matches(action["when"], event):
                    continue
                fired.add(i)
                await perform(client, turn_id, action["do"], event)
            if event["type"] == "turn_end":
                break
    return events


async def perform(
    client: httpx.AsyncClient, turn_id: str, do: dict[str, Any], event: dict[str, Any]
) -> None:
    if "approval" in do:
        approval_id = event["summary"]["approval_id"]
        r = await client.post(f"/v1/approvals/{approval_id}", json={"decision": do["approval"]})
    elif "resume" in do:
        r = await client.post(f"/v1/turns/{turn_id}/resume", json={"mode": do["resume"]})
    else:
        raise AssertionError(f"action {do} is not supported over HTTP in P1")
    if r.status_code != 200:
        raise AssertionError(f"action {do} returned HTTP {r.status_code}: {r.text}")


async def _drive(live: LiveHarness, case: dict[str, Any], result: CaseResult) -> None:
    tenant = load_yaml(PROFILE)["tenant_prefix"] + case["id"]
    headers = {"X-Harnesswalk-Tenant": tenant}
    async with httpx.AsyncClient(
        base_url=live.base_url, headers=headers, timeout=TURN_LIMIT_S
    ) as client:
        health = (await client.get("/healthz")).json()
        if health.get("provider") != "fake" or health.get("fixture") != case["fixture"]:
            result.problems.append(f"healthz: {health}")
            return
        session_id = None
        for n, turn in enumerate(case["turns"]):
            body: dict[str, Any] = {"message": turn["send"]["message"]}
            if turn["send"].get("options"):
                body["options"] = turn["send"]["options"]
            if session_id:
                body["session_id"] = session_id
            r = await client.post("/v1/turns", json=body)
            if r.status_code != 202:
                result.problems.append(
                    f"turn {n}: POST /v1/turns returned {r.status_code} {r.text}"
                )
                return
            session_id = r.json()["session_id"]
            events = await asyncio.wait_for(
                read_turn(client, r.json()["turn_id"], turn.get("actions", [])), TURN_LIMIT_S
            )
            result.streams.append(events)
            for problem in check_stream(events) or check_expectations(turn["expect"], events):
                result.problems.append(f"turn {n}: {problem}")


def run_case(path: Path) -> CaseResult:
    case = load_yaml(path)
    result = CaseResult(case["id"])
    with (
        tempfile.TemporaryDirectory(prefix="harnesswalk-conf-") as tmp,
        LiveHarness(case, Path(tmp)) as live,
    ):
        try:
            asyncio.run(_drive(live, case, result))
        except (AssertionError, TimeoutError) as exc:
            result.problems.append(f"{type(exc).__name__}: {exc}")
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
