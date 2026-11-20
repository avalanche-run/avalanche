"""Read-only Asana catalog tool for the existing operator and browser E2Es.

Models the exercised subset of Nango's search-tasks-in-workspace template.
Custom MCP scenarios keep their separate knowledge-base search tool.
"""

from __future__ import annotations

import os
import sys
from typing import Annotated

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp_test_server import _journal, _RequireKey
from pydantic import BaseModel, Field

server = MCPServer("asana-catalog")


class Task(BaseModel):
    gid: str
    name: str
    resource_type: str = "task"


class SearchResult(BaseModel):
    tasks: list[Task]


@server.tool(name="search-tasks-in-workspace")
def search_tasks_in_workspace(
    workspace_gid: Annotated[str, Field(min_length=1)],
    assignee: str = "me",
    limit: Annotated[int, Field(ge=1, le=100)] = 2,
) -> SearchResult:
    """Search tasks in a workspace with an assignee filter."""
    _journal(
        event="call",
        tool="search-tasks-in-workspace",
        workspace_gid=workspace_gid,
        assignee=assignee,
        limit=limit,
    )
    return SearchResult(
        tasks=[
            Task(
                gid=str(1202775892569400 + index),
                name=f"{os.environ.get('CATALOG_ACCOUNT_MARKER', '')}refunds-{index}",
            )
            for index in range(limit)
        ]
    )


@server.tool(name="get-team-info")
def get_team_info() -> dict[str, str]:
    """Return the workspace identity of this account service."""
    _journal(event="call", tool="get-team-info")
    return {"team": os.environ["CATALOG_ACCOUNT_MARKER"] + "refunds-0"}


if __name__ == "__main__":
    uvicorn.run(
        _RequireKey(server.streamable_http_app(), sys.argv[3]),
        host="127.0.0.1",
        port=int(sys.argv[2]),
        log_level="warning",
    )
