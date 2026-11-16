"""A small MCP server for the integrations scenarios, built on the MCP library.

``python mcp_test_server.py`` talks MCP over its standard input and output, as
a local-process server does. ``python mcp_test_server.py --http PORT KEY``
serves the same tools over HTTP and answers only requests that carry ``KEY``
as a bearer token, as a remote server that wants a login does. With
``--linger`` the local-process server does not exit when its input is closed.

When ``MCP_TEST_SERVER_JOURNAL`` names a file, the server appends one JSON line
when it starts, with its process ID, and one per HTTP request and tool call.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Annotated

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from pydantic import Field


def _journal(**entry: object) -> None:
    path = os.environ.get("MCP_TEST_SERVER_JOURNAL")
    if path:
        with open(path, "a") as journal:
            journal.write(json.dumps(entry) + "\n")


def _window(from_: Annotated[str, Field(alias="from")], until: str = "now") -> str:
    """List the titles changed in a period."""
    raise NotImplementedError("describes the arguments only")


def _changed(**period: str) -> str:
    _journal(event="call", tool="window", period=period)
    return f"{period['from']}..{period.get('until', 'now')}"


def ping() -> str:
    """Say pong."""
    _journal(event="call", tool="ping")
    return "pong"


# Two tools a Python function cannot describe as it is: one with an argument
# named like a Python keyword, and one whose schema says nothing but "object".
server = MCPServer(
    "knowledge-base",
    tools=[
        Tool.from_function(_window, name="window").model_copy(update={"fn": _changed}),
        Tool.from_function(ping).model_copy(update={"parameters": {"type": "object"}}),
    ],
)


@server.tool()
def search(query: str, limit: int = 2) -> list[str]:
    """Search the knowledge base and return the titles found."""
    _journal(event="call", tool="search", query=query, limit=limit)
    return [f"{query}-{index}" for index in range(limit)]


@server.tool()
def broken(reason: str) -> str:
    """Look up the archive, which is always offline."""
    _journal(event="call", tool="broken", reason=reason)
    raise ToolError(f"the archive is offline ({reason})")


@server.tool()
async def wait(seconds: float) -> str:
    """Wait before answering."""
    _journal(event="call", tool="wait", seconds=seconds)
    await anyio.sleep(seconds)
    return "waited"


@server.tool(name="delete-everything")
def delete_everything() -> str:
    """Delete the knowledge base."""
    _journal(event="call", tool="delete-everything")
    return "deleted"


class _RequireKey:
    """Answer 401 to an HTTP request that does not carry the key."""

    def __init__(self, app, key: str) -> None:
        self._app = app
        self._expected = f"Bearer {key}".encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        authorized = dict(scope["headers"]).get(b"authorization") == self._expected
        _journal(event="request", authorized=authorized)
        if authorized:
            await self._app(scope, receive, send)
            return
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"www-authenticate", b"Bearer"), (b"content-length", b"0")],
            }
        )
        await send({"type": "http.response.body", "body": b""})


if __name__ == "__main__":
    _journal(event="started", pid=os.getpid(), transport=sys.argv[1:2] or ["stdio"])
    if sys.argv[1:2] == ["--http"]:
        import uvicorn

        uvicorn.run(
            _RequireKey(server.streamable_http_app(), sys.argv[3]),
            host="127.0.0.1",
            port=int(sys.argv[2]),
            log_level="warning",
        )
    else:
        try:
            server.run("stdio")
        finally:
            if sys.argv[1:2] == ["--linger"]:
                # A badly behaved server: it stays after its input and output close.
                time.sleep(600)
