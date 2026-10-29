"""Tool functions the sandbox tests run in a child process. Each misbehaves on purpose."""

import os
import socket
import time


def echo(arguments):
    return f"echo {arguments.get('text', '')}"


def slow(arguments):
    time.sleep(10)
    return "too late"


def flood(arguments):
    return "x" * 500_000


def hog(arguments):
    block = b"x" * (700 * 1024 * 1024)
    time.sleep(0.5)
    return str(len(block))


def connect(arguments):
    socket.create_connection(("127.0.0.1", int(arguments["port"])), timeout=2).close()
    return "connected"


def write_outside(arguments):
    with open(arguments["path"], "w") as f:
        f.write("escaped")
    return "wrote"


def write_inside(arguments):
    with open("note.txt", "w") as f:
        f.write("kept in the turn directory")
    with open("note.txt") as f:
        return f.read() + " | " + os.getcwd()


def spawn(arguments):
    import subprocess

    return subprocess.run(["echo", "hi"], capture_output=True).stdout.decode()


def environment(arguments):
    return ",".join(sorted(os.environ))


def noisy(arguments):
    print("printed noise")
    os.write(1, b"raw noise on fd 1")
    return "clean result"


def crash(arguments):
    raise ValueError("bad input")
