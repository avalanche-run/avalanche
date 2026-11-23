"""Flows whose steps use one Connector class through two accounts.

The agent runs on a scripted model, so the scenario needs no model API key. The
service URL comes from the environment because the test starts the service on a
free port; the accounts are never in this file.
"""

from __future__ import annotations

import os
from pathlib import Path

from dspy import JSONAdapter
from dspy.utils import DummyLM
from predict_rlm.backends import DirectPythonBackend
from pydantic import BaseModel

import avalanche as ava


class Customer(BaseModel):
    email: str
    region: str


class AcmeCrm(ava.Connector):
    name = "acme-crm"
    display_name = "Acme CRM"
    icon = "assets/acme.svg"
    auth = ava.ApiKey(header="X-Api-Key")
    base_url = os.environ.get("ACME_CRM_URL", "http://127.0.0.1:9")

    @ava.tool
    def find_customer(self, email: str) -> Customer:
        """Find the customer with this email address."""
        response = self.client.get("/customers", params={"email": email})
        response.raise_for_status()
        return Customer.model_validate(response.json())


    @ava.tool
    def fetch(self, path: str) -> str:
        """Fetch a path of the CRM, following redirects."""
        return self.client.get(path, follow_redirects=True).text


crm = AcmeCrm()
crm_eu = AcmeCrm("acme-crm-eu", display_name="Acme CRM (EU)")
# A remote MCP server that wants a key; the test starts it on a free port.
kb = ava.MCP(
    "kb", url=os.environ.get("ACME_KB_URL", "http://127.0.0.1:9/mcp"), allow=["search"]
)


class Lookup(ava.BaseInput):
    email: str
    journal: str


def _journal(path: str, line: str) -> None:
    with Path(path).open("a") as journal:
        journal.write(line + "\n")


@ava.step(integrations=[crm, crm_eu])
def lookup(payload: Lookup) -> dict[str, str]:
    _journal(payload.journal, "lookup started")
    return {
        "email": payload.email,
        "journal": payload.journal,
        "us": crm.find_customer(payload.email).region,
        "eu": crm_eu.find_customer(payload.email).region,
    }


def tidy_email(text: str) -> str:
    """Trim and lowercase an email address."""
    return text.strip().lower()


class Triage(ava.Signature):
    """Report the region of the customer with this email address."""

    email: str = ava.InputField()
    region: str = ava.OutputField()


def _scripted_agent() -> ava.runtimes.PredictRLM:
    model = DummyLM(
        [
            {
                "plan": "Tidy the address, look the customer up, search, report the region.",
                "code": (
                    "address = await tidy_email(text=email)\n"
                    "customer = await acme_crm__find_customer(email=address)\n"
                    "await kb__search(query=customer['region'], limit=1)\n"
                    "SUBMIT(region=customer['region'])"
                ),
            }
        ],
        adapter=JSONAdapter(),
    )
    # The direct backend runs the agent's code in this process and needs no
    # sandbox; it records each turn's tool calls like the sandbox does.
    return ava.runtimes.PredictRLM(
        lm=model, sub_lm=model, interpreter=DirectPythonBackend(), max_iterations=1
    )


@ava.agent_step(Triage, runtime=_scripted_agent(), tools=[tidy_email], integrations=[crm, kb])
async def triage(found: dict[str, str], *, agent: ava.Agent) -> dict[str, str]:
    _journal(found["journal"], "triage started")
    result = await agent(email=f" {found['email'].upper()} ")
    return {"us": found["us"], "eu": found["eu"], "agent": result.outputs.region}


@ava.workflow(input=Lookup)
def customer_regions():
    return triage(lookup())


@ava.step
def known_customer(payload: Lookup) -> dict[str, str]:
    return {"email": payload.email, "journal": payload.journal, "us": "", "eu": ""}


@ava.workflow(input=Lookup)
def agent_only():
    return triage(known_customer())


class Elsewhere(ava.BaseInput):
    other_url: str


@ava.step(integrations=[crm])
def reach_elsewhere(payload: Elsewhere) -> dict[str, str]:
    """Ask the CRM's client for another origin, directly and through a redirect."""
    outcomes = {}
    for label, path in (
        ("absolute", f"{payload.other_url}/customers"),
        ("redirect", f"/moved?to={payload.other_url}/customers"),
    ):
        try:
            outcomes[label] = crm.fetch(path)
        except ava.integrations.IntegrationError as refused:
            outcomes[label] = f"refused: {refused}"
    return outcomes


@ava.workflow(input=Elsewhere)
def elsewhere():
    return reach_elsewhere()
