"""The sandbox child: runs one tool call in its own process. Standard library only.

Started by `sandbox.py` as `python -I -B -m harnesswalk.execution.sandbox_child` in a fresh
work directory with a scrubbed environment. Protocol: one JSON request on stdin, one JSON
result on a private copy of the original stdout. What the tool prints goes to stderr, never
into the result.

Request: {"entrypoint": "module:function" | "/abs/file.py:function", "data": {...} | null,
          "arguments": {...}, "memory_mb": int, "cpu_s": int, "network": bool,
          "workdir": "/abs/path"}
Result:  {"outcome": "ok" | "error", "content": "..."}

Isolation inside the child, set up before the tool module is imported:

- resource limits: address space (Linux), CPU seconds, file size, open files, no core dumps;
- a watcher thread that exits with code 137 once resident memory passes `memory_mb` (the
  memory cap on macOS, where RLIMIT_AS is not enforced);
- a Python audit hook (it cannot be removed once added) that refuses sockets unless `network`
  is true, process spawning, `ctypes` library loading, importing the raw process and foreign
  function modules (`_posixsubprocess`, `_ctypes`), and every file change outside `workdir`:
  opening for write, truncate, rename, remove, rmdir, mkdir, chmod, chown, utime, links;
- no new processes at all (`RLIMIT_NPROC` 0, set once the watcher thread runs), so a spawn
  the hook cannot see still fails.

The parent adds the outer walls: the wall-clock timeout, the output cap, the process group
kill, and an OS network jail where one is available.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import os
import sys
import threading
import time
from collections.abc import Callable

MEMORY_EXIT = 137
SPAWN_EVENTS = (
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.fork",
    "os.forkpty",
    "pty.spawn",
    "ctypes.dlopen",
)
NET_PREFIXES = ("socket.", "ssl.")
# Modules that reach fork/exec or foreign code without an audit event of their own. They are
# dropped from sys.modules before the hook goes in, so importing them again is refused.
BLOCKED_MODULES = ("_posixsubprocess", "_ctypes", "ctypes", "subprocess", "multiprocessing")
# File-changing audit events: the positions of their path arguments, then of their dir_fd
# arguments (os module audit events, Python 3.12+).
PATH_EVENTS: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {
    "os.truncate": ((0,), ()),
    "os.rename": ((0, 1), (2, 3)),
    "os.remove": ((0,), (1,)),
    "os.rmdir": ((0,), (1,)),
    "os.mkdir": ((0,), (2,)),
    "os.chmod": ((0,), (2,)),
    "os.chown": ((0,), (3,)),
    "os.chflags": ((0,), ()),
    "os.utime": ((0,), (3,)),
    "os.link": ((0, 1), (2, 3)),
    "os.symlink": ((1,), (2,)),
    "os.mkfifo": ((0,), (2,)),
    "os.mknod": ((0,), (3,)),
    "shutil.rmtree": ((0,), (1,)),
}


class SandboxViolation(PermissionError):
    pass


def _limits(memory_mb: int, cpu_s: int) -> None:
    try:
        import resource
    except ImportError:  # not POSIX: the parent's timeout still applies
        return
    caps = {
        "RLIMIT_CPU": cpu_s,
        "RLIMIT_FSIZE": 16 * 1024 * 1024,
        "RLIMIT_NOFILE": 64,
        "RLIMIT_CORE": 0,
    }
    if sys.platform.startswith("linux"):
        caps["RLIMIT_AS"] = memory_mb * 1024 * 1024 + 512 * 1024 * 1024  # interpreter headroom
    for name, value in caps.items():
        limit = getattr(resource, name, None)
        if limit is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            _, hard = resource.getrlimit(limit)
            soft = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(limit, (soft, hard))


def _resident_reader() -> Callable[[], int] | None:
    """Linux: the current resident memory, from /proc/self/statm. ru_maxrss is no use there:
    exec keeps the parent's high-water mark, so the child of a server that once held more than
    the cap would start over it. The file is opened here, before the audit hook exists."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        fd = os.open("/proc/self/statm", os.O_RDONLY)
    except OSError:
        return None
    page = os.sysconf("SC_PAGE_SIZE")
    return lambda: int(os.pread(fd, 128, 0).split()[1]) * page


def _memory_watcher(memory_mb: int) -> None:
    try:
        import resource
    except ImportError:
        return
    scale = 1 if sys.platform == "darwin" else 1024  # ru_maxrss: bytes on macOS, KiB on Linux
    cap = memory_mb * 1024 * 1024
    resident = _resident_reader() or (
        lambda: resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
    )

    def watch() -> None:
        while True:
            if resident() > cap:
                os.write(2, f"harnesswalk sandbox: memory over {memory_mb} MB\n".encode())
                os._exit(MEMORY_EXIT)
            time.sleep(0.005)

    threading.Thread(target=watch, daemon=True).start()


def _no_new_processes() -> None:
    """RLIMIT_NPROC 0: fork fails for a non-root user (root is exempt; the OS jail covers it)."""
    try:
        import resource
    except ImportError:
        return
    limit = getattr(resource, "RLIMIT_NPROC", None)
    if limit is not None:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(limit, (0, resource.getrlimit(limit)[1]))


def _install_hook(network: bool, workdir: str) -> None:
    root = os.path.realpath(workdir)

    def inside(path: object) -> bool:
        if isinstance(path, int):  # an fd (fchmod, ftruncate...): its file is unknown here
            return False
        if not isinstance(path, (str, bytes, os.PathLike)):
            return False
        real = os.path.realpath(os.fsdecode(path))
        return real == root or real.startswith(root + os.sep)

    for name in BLOCKED_MODULES:
        sys.modules.pop(name, None)

    def hook(event: str, args: tuple) -> None:
        if not network and event.startswith(NET_PREFIXES):
            raise SandboxViolation(f"network is not allowed in this sandbox ({event})")
        if event.startswith(SPAWN_EVENTS):
            raise SandboxViolation(f"{event} is not allowed in this sandbox")
        if event == "import" and args and args[0] in BLOCKED_MODULES[:2]:
            raise SandboxViolation(f"importing {args[0]} is not allowed in this sandbox")
        if event in PATH_EVENTS:
            paths, dir_fds = PATH_EVENTS[event]
            # A dir_fd makes a relative path mean "inside that directory": refuse it.
            if any(i < len(args) and isinstance(args[i], int) and args[i] >= 0 for i in dir_fds):
                raise SandboxViolation(f"{event} with a directory fd is not allowed")
            if any(i < len(args) and not inside(args[i]) for i in paths):
                raise SandboxViolation(f"{event} outside the turn's work directory")
        if event == "open" and len(args) >= 2:
            path, mode = args[0], args[1]
            flags = args[2] if len(args) > 2 else 0
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)
            )
            if writing and isinstance(path, (str, bytes)):
                real = os.path.realpath(os.fsdecode(path))
                if real != root and not real.startswith(root + os.sep):
                    raise SandboxViolation("writes outside the turn's work directory")

    sys.addaudithook(hook)


def _load(entrypoint: str):
    target, _, func = entrypoint.rpartition(":")
    if target.endswith(".py"):
        spec = importlib.util.spec_from_file_location("sandbox_tool", target)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {target}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(target)
    return getattr(module, func)


def main() -> int:
    result_fd = os.dup(1)
    os.dup2(2, 1)  # anything the tool writes to fd 1 lands on stderr
    sys.stdout = io.StringIO()
    request = json.loads(sys.stdin.read())
    _limits(int(request["memory_mb"]), int(request["cpu_s"]))
    _memory_watcher(int(request["memory_mb"]))
    _no_new_processes()
    _install_hook(bool(request["network"]), request["workdir"])
    try:
        fn = _load(request["entrypoint"])
        data = request.get("data")
        raw = fn(request["arguments"], data) if data is not None else fn(request["arguments"])
        out = {"outcome": "ok", "content": raw if isinstance(raw, str) else str(raw)}
    except MemoryError:
        out = {"outcome": "error", "content": "the tool ran out of memory"}
    except BaseException as exc:  # a tool error is a result
        out = {"outcome": "error", "content": f"{type(exc).__name__}: {exc}"[:300]}
    with os.fdopen(result_fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
