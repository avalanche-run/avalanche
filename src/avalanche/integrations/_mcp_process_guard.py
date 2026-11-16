"""Run a local MCP server so that it cannot outlive the process that started it.

Usage: ``python _mcp_process_guard.py PARENT_PID COMMAND [ARGUMENT ...]``

A step closes its MCP servers when it ends, and the MCP library then stops the
server's whole process group. That cannot happen when the step's own process
is killed first, for example when a run is cancelled while its agent is still
working. The server is in a session of its own, so nothing else would stop it.

This guard is that session's first process. It starts the server with its own
standard input and output, so the two talk to the step directly, and it kills
the session's process group as soon as the process that started it is gone.
It runs as a script, with the standard library only, and imports nothing from
Avalanche.
"""

import os
import signal
import subprocess
import sys

_POLL_SECONDS = 0.2


def main() -> int:
    parent = int(sys.argv[1])
    command = sys.argv[2:]
    try:
        server = subprocess.Popen(command)
    except OSError as exc:
        print(f"could not start {command[0]!r}: {exc}", file=sys.stderr)
        return 127
    while True:
        try:
            code = server.wait(timeout=_POLL_SECONDS)
        except subprocess.TimeoutExpired:
            # An orphan is adopted by another process: the parent changed.
            if os.getppid() != parent:
                os.killpg(os.getpgrp(), signal.SIGKILL)
            continue
        return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
