"""Shutdown with event streams open: one Ctrl+C stops the server even while a browser tab holds
`GET /v1/events` (and a turn's stream) open (control/sse.py, Harness.request_stop)."""

import asyncio
import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from harnesswalk.control.gateway import GatewayError, TurnRequest
from harnesswalk.control.http_api import create_app
from harnesswalk.control.sse import until_stopped
from harnesswalk.runtime import Harness
from harnesswalk.types import Identity
from tests.spec.support import ROOT
from tests.unit.support import settings
from tests.unit.test_p4_ops import _free_port, _healthy
from tests.unit.test_restart import wait_for

TENANT = "t1"


async def open_stream(app: Any, path: str, query: str = "") -> tuple[asyncio.Task, list[dict]]:
    """A GET on the ASGI app whose client never goes away; the messages it sent, as it sends."""
    sent: list[dict] = []
    gone = asyncio.Event()  # never set: the browser tab stays open
    requested = False

    async def receive() -> dict:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(b"host", b"test"), (b"x-harnesswalk-tenant", TENANT.encode())],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
        "state": {},
    }
    task = asyncio.create_task(app(scope, receive, send))
    return task, sent


def body_of(sent: list[dict]) -> str:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body").decode()


@pytest.mark.anyio
async def test_request_stop_ends_the_ops_and_turn_streams_and_frees_their_slots(
    tmp_path: Path,
) -> None:
    h = Harness(settings(tmp_path, "plain_answer"))
    app = create_app(h)
    await h.start()  # the lifespan's start-up: it creates `stopping` on this loop
    turn_id = ""
    try:
        # A turn in step mode waits at its first breakpoint: its stream stays open.
        state = await h.gateway.start_turn(
            TurnRequest(
                message="What are your support hours?",
                identity=Identity(TENANT, None, ["user"], anonymous=False),
                step_mode=True,
            )
        )
        turn_id = state.ctx.turn_id
        ops, ops_sent = await open_stream(app, "/v1/events")
        turn, turn_sent = await open_stream(app, f"/v1/turns/{turn_id}/events")
        await wait_for(
            lambda: ": ops events" in body_of(ops_sent) and "node_paused" in body_of(turn_sent)
        )
        streams = app.state.streams
        assert len(streams) == 2 and len(h.ops._wakers) == 1 and turn_id in h.tracer._live
        assert not ops.done() and not turn.done()

        started = time.monotonic()
        h.request_stop()
        await asyncio.wait_for(asyncio.gather(ops, turn), 1.0)
        assert time.monotonic() - started < 1.0

        for sent in (ops_sent, turn_sent):  # each response completed normally, no exception
            assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 200
            assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}
        assert len(streams) == 0  # the stream slots were given back
        assert h.ops._wakers == [] and turn_id not in h.tracer._live  # finally blocks ran

        # A stream asked for after the stop ends at once.
        late, late_sent = await open_stream(app, "/v1/events")
        await asyncio.wait_for(late, 1.0)
        assert late_sent[-1]["more_body"] is False and len(streams) == 0
    finally:
        with contextlib.suppress(GatewayError):
            if turn_id:
                h.gateway.cancel(TENANT, turn_id)
        await h.aclose()


@pytest.mark.anyio
async def test_until_stopped_passes_items_through_and_closes_its_source() -> None:
    closed: list[str] = []

    async def source():
        try:
            yield 1
            yield 2
            await asyncio.Event().wait()  # waits forever, like a live stream
        finally:
            closed.append("source")

    stopping = asyncio.Event()
    got: list[int] = []

    async def consume() -> None:
        async for item in until_stopped(source(), stopping):
            got.append(item)

    task = asyncio.create_task(consume())
    await wait_for(lambda: got == [1, 2])
    stopping.set()
    await asyncio.wait_for(task, 1.0)
    assert closed == ["source"]

    # Without an event (a harness that was never started) the source is passed through as is.
    assert [i async for i in until_stopped(_count(3), None)] == [0, 1, 2]


async def _count(n: int):
    for i in range(n):
        yield i


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT to a child process is POSIX only")
def test_one_sigint_stops_serve_with_event_streams_open(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    port = _free_port()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HARNESSWALK_ROOT": str(ROOT),
        "HARNESSWALK_DATA_DIR": str(data_dir),
        "HARNESSWALK_WEB_DIST": str(tmp_path / "no-web"),
        "HARNESSWALK_EMBEDDINGS": "fake",
    }
    cmd = [sys.executable, "-m", "harnesswalk", "serve", "--provider", "fake",
           "--port", str(port)]  # fmt: skip
    proc = subprocess.Popen(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(tmp_path)
    )
    clients: list[httpx.Client] = []
    responses: list[httpx.Response] = []
    bodies = []
    try:
        assert _healthy(port), "the server never answered /healthz"
        for _ in range(2):  # two browser tabs
            c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10)
            clients.append(c)
            r = c.send(c.build_request("GET", "/v1/events"), stream=True)
            responses.append(r)
            assert r.status_code == 200
            body = r.iter_text()
            bodies.append(body)
            assert next(body).startswith(": ops events")
        started = time.monotonic()
        proc.send_signal(signal.SIGINT)
        code = proc.wait(5)
        assert time.monotonic() - started < 5
        assert code == 0
        for body in bodies:  # each stream ended cleanly (no reset mid-chunk)
            assert "".join(body) == ""
    finally:
        for r in responses:
            r.close()
        for c in clients:
            c.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(5)
    log = proc.stdout.read().decode() if proc.stdout else ""
    assert "Application shutdown complete" in log, log
    assert "Exception in ASGI application" not in log, log
    assert "graceful shutdown exceeded" not in log, log  # the grace timeout cut nothing off
