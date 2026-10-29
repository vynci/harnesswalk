"""The subprocess sandbox: timeout, output cap, memory cap, no network, contained writes."""

import asyncio
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from harnesswalk.execution.registry import ToolSpec
from harnesswalk.execution.sandbox import Sandbox, jail_prefix, os_jail
from harnesswalk.types import ToolCall
from tests.unit.support import exits, harness, make_ctx, run_turn

PROBES = Path(__file__).parent / "sandbox_tools" / "probes.py"


def probe(name: str, action_class: str = "read", timeout_ms: int = 3000) -> ToolSpec:
    return ToolSpec(
        name,
        "probe",
        {"type": "object"},
        action_class,
        timeout_ms,
        impl=lambda a: "in-process",
        entrypoint=f"{PROBES}:{name}",
        sandbox="subprocess",
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(
        "subprocess",
        10000,
        workdir=tmp_path / "work",
        subprocess_cfg={"memory_mb": 128, "output_cap_bytes": 65536},
    )


async def run(sandbox: Sandbox, spec: ToolSpec, args: dict | None = None, **kw):
    ctx, _ = make_ctx()
    return await sandbox.execute(ctx, spec, ToolCall("call_1", spec.name, args or {}), **kw)


@pytest.mark.anyio
async def test_ok_and_tool_errors_are_results(sandbox: Sandbox) -> None:
    ok = await run(sandbox, probe("echo"), {"text": "hi"})
    assert (ok.outcome, ok.content, ok.level) == ("ok", "echo hi", "subprocess")
    bad = await run(sandbox, probe("crash"))
    assert bad.outcome == "error" and "ValueError: bad input" in bad.content


@pytest.mark.anyio
async def test_timeout_kills_the_child(sandbox: Sandbox) -> None:
    loop = asyncio.get_running_loop()
    started = loop.time()
    result = await run(sandbox, probe("slow", timeout_ms=400))
    assert result.outcome == "timeout" and "400 ms" in result.content
    assert loop.time() - started < 3


@pytest.mark.anyio
async def test_output_cap(sandbox: Sandbox) -> None:
    result = await run(sandbox, probe("flood"))
    assert result.outcome == "error" and "65536 byte output cap" in result.content


@pytest.mark.anyio
async def test_memory_cap(sandbox: Sandbox) -> None:
    result = await run(sandbox, probe("hog", timeout_ms=8000))
    assert result.outcome == "error"
    assert "memory" in result.content


@pytest.mark.anyio
async def test_no_network_unless_external_and_allowed(sandbox: Sandbox) -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    try:
        blocked = await run(sandbox, probe("connect"), {"port": port})
        assert blocked.outcome == "error" and "network is not allowed" in blocked.content
        assert blocked.network == "blocked"
        allowed = await run(sandbox, probe("connect", "external"), {"port": port}, network=True)
        assert (allowed.outcome, allowed.network) == ("ok", "allowed")
    finally:
        server.close()


@pytest.mark.anyio
async def test_writes_stay_in_the_turn_directory_and_no_processes(
    sandbox: Sandbox, tmp_path: Path
) -> None:
    outside = tmp_path / "escape.txt"
    escaped = await run(sandbox, probe("write_outside"), {"path": str(outside)})
    assert escaped.outcome == "error" and not outside.exists()
    inside = await run(sandbox, probe("write_inside"))
    assert inside.outcome == "ok" and inside.content.startswith("kept in the turn directory")
    assert str((tmp_path / "work").resolve()) in inside.content
    spawned = await run(sandbox, probe("spawn"))
    assert spawned.outcome == "error" and "not allowed" in spawned.content


@pytest.mark.anyio
async def test_no_file_change_outside_and_no_raw_process_modules(
    sandbox: Sandbox, tmp_path: Path
) -> None:
    """P3 review: the hook covered open() only; rename, truncate, chmod... and the raw
    `_posixsubprocess.fork_exec` (which raises no audit event) went through."""
    victim = tmp_path / "audit.db"
    victim.write_text("rows", "utf-8")
    changed = await run(sandbox, probe("change_outside"), {"path": str(victim)})
    assert (changed.outcome, changed.content) == ("ok", "refused all")
    assert victim.read_text("utf-8") == "rows" and oct(victim.stat().st_mode)[-3:] != "600"
    raw = await run(sandbox, probe("raw_fork"))
    assert raw.outcome == "error" and "_posixsubprocess" in raw.content
    forked = await run(sandbox, probe("fork"))
    assert forked.outcome == "error"


@pytest.mark.skipif(sys.platform != "darwin" or not os_jail(), reason="sandbox-exec only")
def test_the_macos_jail_walls_off_writes_outside_the_work_dir(tmp_path: Path) -> None:
    """The second wall, without the hook: a plain child under the jail cannot write outside."""
    work = (tmp_path / "work").resolve()
    work.mkdir()
    outside = tmp_path.resolve() / "outside.txt"
    script = f"open({str(work / 'in.txt')!r}, 'w').write('ok'); open({str(outside)!r}, 'w')"
    cmd = [*jail_prefix(os_jail(), work, network=False), sys.executable, "-c", script]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    assert (work / "in.txt").read_text() == "ok"
    assert proc.returncode != 0 and not outside.exists()


@pytest.mark.anyio
async def test_scrubbed_environment_and_clean_result(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-never-inherited")
    env = await run(sandbox, probe("environment"))
    assert "ANTHROPIC_API_KEY" not in env.content and "HOME" in env.content
    noisy = await run(sandbox, probe("noisy"))
    assert (noisy.outcome, noisy.content) == ("ok", "clean result")


@pytest.mark.anyio
async def test_levels_public_mode_and_turn_cleanup(tmp_path: Path) -> None:
    public = Sandbox("subprocess", mode="public", workdir=tmp_path / "w")
    assert public.level_for(probe("echo")) == "inprocess"
    dev = Sandbox("subprocess", workdir=tmp_path / "w")
    closure = ToolSpec("c", "d", {"type": "object"}, "read", 1000, impl=lambda a: "x")
    assert dev.level_for(closure) == "inprocess"  # nothing a child could import
    pinned = probe("echo")
    pinned.sandbox = "inprocess"
    assert dev.level_for(pinned) == "inprocess"
    ctx, _ = make_ctx()
    await dev.execute(ctx, probe("write_inside"), ToolCall("c1", "write_inside", {}))
    assert dev.turn_dir(ctx).exists()
    dev.end_turn(ctx)
    assert not dev.turn_dir(ctx).exists()
    with pytest.raises(ValueError):
        Sandbox("container")


@pytest.mark.anyio
async def test_inprocess_timeout() -> None:
    import time

    sb = Sandbox("inprocess")
    spec = ToolSpec("s", "d", {"type": "object"}, "read", 200, impl=lambda a: time.sleep(2))
    assert (await run(sb, spec)).outcome == "timeout"


@pytest.mark.anyio
async def test_a_turn_runs_its_tools_in_subprocesses(tmp_path: Path) -> None:
    h = harness(tmp_path, "tool_call", overrides={"sandbox.level": "subprocess"})
    events = await run_turn(h, "Where is order A-1001?")
    summary = exits(events, "sandbox")[0]["summary"]
    assert summary["level"] == "subprocess" and summary["network"] == "blocked"
    assert summary["outcome"] == "ok" and events[-1]["summary"]["exit"] == "answered"
    assert not any((tmp_path / "data" / "sandbox").glob("*/*"))  # the turn directory is gone
