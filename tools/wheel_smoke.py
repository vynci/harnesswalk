"""Install a built wheel into a clean virtual environment and prove it runs on its own.

    uv run python tools/wheel_smoke.py dist/harnesswalk-0.1.0-py3-none-any.whl [--web]

Outside the repo, with no HARNESSWALK_* variable set: `harnesswalk --version`, a fake-provider
`turn` from the command line, the starter golden suite, then `harnesswalk serve --provider fake`
on a free loopback port answering `/healthz`, `/v1/diagram`, a turn over HTTP read to `turn_end`
and the code peek of the loop node. With `--web` the wheel must also serve the built app at `/`.
Every store lands in the scratch working directory (`harnesswalk-data/`), never in the
installed package; the check fails if anything was written under it. Exits 1 on the first gap.

Standard library only, so any Python 3.12 or newer can run it. Uses uv to make the venv and
install when uv is on PATH, else `python -m venv` and pip.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


class SmokeError(Exception):
    pass


def run(cmd: list[str], cwd: Path, env: dict[str, str], timeout: float = 300) -> str:
    print("+", " ".join(cmd), flush=True)
    done = subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )
    if done.returncode != 0:
        raise SmokeError(f"{cmd[0]} exited {done.returncode}\n{done.stdout}\n{done.stderr}")
    return done.stdout


def clean_env(venv: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HARNESSWALK_", "VIRTUAL"))}
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PYTHONPATH", "UV_PROJECT_ENVIRONMENT"):
        env.pop(key, None)
    bindir = venv / ("Scripts" if os.name == "nt" else "bin")
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["VIRTUAL_ENV"] = str(venv)
    return env


def make_venv(work: Path, wheel: Path, python: str) -> Path:
    venv = work / "venv"
    uv = shutil.which("uv")
    if uv:
        run([uv, "venv", "--quiet", "--python", python, str(venv)], work, dict(os.environ))
        target = venv / "bin" / "python"
        run([uv, "pip", "install", "--quiet", "--python", str(target), str(wheel)], work,
            dict(os.environ))  # fmt: skip
    else:
        run([python, "-m", "venv", str(venv)], work, dict(os.environ))
        target = venv / "bin" / "python"
        run([str(target), "-m", "pip", "install", "--quiet", str(wheel)], work, dict(os.environ))
    return venv


def get(url: str, timeout: float = 5) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - loopback only
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def post_json(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 - loopback only
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode()}


def read_turn(url: str, limit_s: float = 30) -> list[dict]:
    events: list[dict] = []
    with urllib.request.urlopen(url, timeout=limit_s) as r:  # noqa: S310 - loopback only
        data: list[str] = []
        for raw in r:
            line = raw.decode().rstrip("\r\n")
            if line.startswith("data:"):
                data.append(line[5:].strip())
            elif not line and data:
                events.append(json.loads("\n".join(data)))
                data = []
                if events[-1]["type"] == "turn_end":
                    return events
    raise SmokeError("the event stream closed before turn_end")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def site_bundle(venv: Path) -> Path:
    out = subprocess.run(
        [str(venv / "bin" / "python"), "-c", "import harnesswalk.paths as p; print(p.BUNDLE)"],
        capture_output=True, text=True, check=True, cwd=venv,
    )  # fmt: skip
    return Path(out.stdout.strip())


def smoke(wheel: Path, work: Path, python: str, web: bool) -> None:
    venv = make_venv(work, wheel, python)
    env = clean_env(venv)
    cwd = work / "run"
    cwd.mkdir()
    bundle = site_bundle(venv)
    if not (bundle / "config" / "harness.yaml").is_file():
        raise SmokeError(f"the wheel has no bundled config at {bundle}")
    before = sorted(p.relative_to(bundle) for p in bundle.rglob("*"))

    out = run(["harnesswalk", "--version"], cwd, env)
    print(out.strip())
    out = run(
        ["harnesswalk", "turn", "Where is order A-1001?", "--provider", "fake", "--fixture",
         "tool_call"], cwd, env,
    )  # fmt: skip
    if "tools" not in out or "A-1001" not in out:
        raise SmokeError(f"the CLI turn did not walk the tools node:\n{out}")
    print("cli turn: ok")
    out = run(["harnesswalk", "eval", "_starter", "--suite", "golden"], cwd, env, timeout=600)
    print(out.strip().splitlines()[-1])

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(work / "serve.log", "wb")  # noqa: SIM115 - lives with the process
    proc = subprocess.Popen(
        ["harnesswalk", "serve", "--provider", "fake", "--port", str(port)],
        cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 30
        health: dict = {}
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise SmokeError(f"serve exited {proc.returncode}; see {work / 'serve.log'}")
            try:
                status, body = get(f"{base}/healthz", 1)
                if status == 200:
                    health = json.loads(body)
                    break
            except OSError:
                pass
            time.sleep(0.2)
        else:
            raise SmokeError("serve did not answer /healthz in 30 s")
        if health.get("provider") != "fake" or health.get("version") != wheel_version(wheel):
            raise SmokeError(f"healthz: {health}")
        print(f"healthz: version {health['version']}, spec {health.get('spec_version')}")
        status, body = get(f"{base}/v1/diagram")
        nodes = len(json.loads(body).get("nodes", [])) if status == 200 else 0
        if status != 200 or nodes < 15:
            raise SmokeError(f"/v1/diagram returned {status} with {nodes} nodes")
        print(f"diagram: {nodes} nodes")
        status, turn = post_json(f"{base}/v1/turns", {"message": "What are your support hours?"})
        if status != 202:
            raise SmokeError(f"POST /v1/turns returned {status}: {turn}")
        events = read_turn(base + turn["events_url"])
        end = events[-1]
        reply = (end.get("summary") or {}).get("reply") or ""
        if end["status"] != "done" or not reply:
            raise SmokeError(f"the turn ended {end['status']} with reply {reply!r}")
        print(f"http turn: {len(events)} events, reply {reply[:60]!r}")
        status, body = get(f"{base}/v1/nodes/loop/source")
        if status != 200 or "def " not in json.loads(body).get("source", ""):
            raise SmokeError(f"the loop node's code peek returned {status}")
        print("code peek: ok")
        status, body = get(f"{base}/")
        has_app = status == 200 and b"<html" in body.lower()
        if web and not has_app:
            raise SmokeError(f"--web: GET / returned {status} without the app")
        print("web app at /: " + ("served" if has_app else "not in this wheel (API only)"))
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()

    after = sorted(p.relative_to(bundle) for p in bundle.rglob("*"))
    if after != before:
        added = sorted(set(after) - set(before))[:5]
        raise SmokeError(f"the harness wrote into the installed package: {added}")
    if not (cwd / "harnesswalk-data").is_dir():
        raise SmokeError("no harnesswalk-data/ in the working directory")
    print(f"stores: {cwd / 'harnesswalk-data'} (nothing written into site-packages)")


def wheel_version(wheel: Path) -> str:
    return wheel.name.split("-")[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("wheel", type=Path)
    ap.add_argument("--python", default="3.12", help="the interpreter for the venv (uv spec)")
    ap.add_argument("--web", action="store_true", help="require the app at / as well")
    ap.add_argument("--work", type=Path, help="a scratch directory (default: a new temp dir)")
    ap.add_argument("--keep", action="store_true", help="keep the scratch directory")
    args = ap.parse_args(argv)
    wheel = args.wheel.resolve()
    if not wheel.is_file():
        print(f"no wheel at {wheel}", file=sys.stderr)
        return 1
    work = Path(tempfile.mkdtemp(prefix="harnesswalk-wheel-", dir=args.work))
    try:
        smoke(wheel, work, args.python, args.web)
    except (SmokeError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"WHEEL SMOKE FAILED: {exc}", file=sys.stderr)
        print(f"scratch kept at {work}", file=sys.stderr)
        return 1
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    print(f"WHEEL SMOKE PASSED: {wheel.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
