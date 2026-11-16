"""Flows whose steps use accounts connected through the browser.

A Connector that declares its login, an MCP server whose login is discovered
from the server, and a second MCP server that takes a client ID from its
declaration. The service addresses come from the environment because the test
starts the services on free ports; no client secret or token is in this file.
"""

from __future__ import annotations

import os
from pathlib import Path

from dspy import JSONAdapter
from dspy.utils import DummyLM
from predict_rlm.backends import DirectPythonBackend

import avalanche as ava

_SERVICE = os.environ.get("LOGIN_SERVICE_URL", "http://127.0.0.1:9")
_LOCKED_SERVICE = os.environ.get("LOCKED_LOGIN_SERVICE_URL", "http://127.0.0.1:9")


class AcmeCalendar(ava.Connector):
    name = "acme-calendar"
    display_name = "Acme Calendar"
    auth = ava.OAuth2(
        authorize_url=f"{_SERVICE}/authorize",
        token_url=f"{_SERVICE}/token",
        scopes=["notes"],
        client_auth="post",
    )
    base_url = _SERVICE

    @ava.tool
    def region(self, email: str) -> str:
        """Find the region of the customer with this email address."""
        response = self.client.get("/crm/customers", params={"email": email})
        response.raise_for_status()
        return response.json()["region"]


calendar = AcmeCalendar()
# The server's login service registers a client by itself.
notes = ava.MCP("notes", url=f"{_SERVICE}/mcp", allow=["search"])
# This one does not: the client registered with it is named here.
archive = ava.MCP(
    "archive", url=f"{_LOCKED_SERVICE}/mcp", allow=["search"], client_id="avalanche-at-notes"
)


class Lookup(ava.BaseInput):
    email: str
    journal: str


def _journal(path: str, line: str) -> None:
    with Path(path).open("a") as journal:
        journal.write(line + "\n")


@ava.step(integrations=[calendar])
def lookup(payload: Lookup) -> dict[str, str]:
    _journal(payload.journal, "lookup started")
    return {"journal": payload.journal, "region": calendar.region(payload.email)}


@ava.workflow(input=Lookup)
def calendar_region():
    return lookup()


class Find(ava.Signature):
    """Search the notes and the archive for a topic."""

    topic: str = ava.InputField()
    found: str = ava.OutputField()


def _scripted_agent() -> ava.runtimes.PredictRLM:
    model = DummyLM(
        [
            {
                "plan": "Search both servers and report what they found.",
                "code": (
                    "first = await notes__search(query=topic)\n"
                    "second = await archive__search(query=topic)\n"
                    "SUBMIT(found=str(first) + str(second))"
                ),
            }
        ],
        adapter=JSONAdapter(),
    )
    return ava.runtimes.PredictRLM(
        lm=model, sub_lm=model, interpreter=DirectPythonBackend(), max_iterations=1
    )


@ava.agent_step(Find, runtime=_scripted_agent(), integrations=[notes, archive])
async def find(payload: Lookup, *, agent: ava.Agent) -> str:
    _journal(payload.journal, "find started")
    return (await agent(topic=payload.email)).outputs.found


@ava.workflow(input=Lookup)
def notes_search():
    return find()
