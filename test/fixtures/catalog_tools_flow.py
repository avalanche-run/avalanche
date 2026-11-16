"""Host-independent built-in example, exercised on all three agent runtimes."""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

from dspy import JSONAdapter
from dspy.utils import DummyLM
from predict_rlm.backends import DirectPythonBackend

import avalanche as ava
from avalanche.catalog import asana


class Topic(ava.BaseInput):
    journal: str


class Research(ava.Signature):
    topic: str = ava.InputField()
    findings: list[str] = ava.OutputField()


CALLS = [("asana__search", {"query": "refunds", "limit": 2})]


def _scripted(code: str) -> ava.runtimes.PredictRLM:
    model = DummyLM([{"plan": "Follow the script.", "code": code}], adapter=JSONAdapter())
    return ava.runtimes.PredictRLM(
        lm=model, sub_lm=model, interpreter=DirectPythonBackend(), max_iterations=1
    )


async def _research(payload: Topic, agent: ava.Agent) -> list[str]:
    with Path(payload.journal).open("a") as journal:
        journal.write("body started\n")
    return (await agent(topic="refunds")).outputs.findings


@ava.agent_step(
    Research,
    runtime=_scripted("SUBMIT(findings=[str(await asana__search(query='refunds', limit=2))])"),
    integrations=[asana],
    retries=2,
)
async def research_on_predict_rlm(payload: Topic, *, agent: ava.Agent) -> list[str]:
    return await _research(payload, agent)


@ava.agent_step(
    Research,
    runtime=ava.runtimes.ClaudeAgent(model="claude-haiku-4-5", narrate=False),
    integrations=[asana],
    retries=0,
)
async def research_on_claude(payload: Topic, *, agent: ava.Agent) -> list[str]:
    return await _research(payload, agent)


@ava.agent_step(
    Research,
    runtime=ava.runtimes.OpenAIAgent(model="gpt-5.4-mini", narrate=False),
    integrations=[asana],
    retries=0,
)
async def research_on_openai(payload: Topic, *, agent: ava.Agent) -> list[str]:
    return await _research(payload, agent)


@ava.workflow(input=Topic)
def on_predict_rlm():
    return research_on_predict_rlm()


@ava.workflow(input=Topic)
def on_claude():
    return research_on_claude()


@ava.workflow(input=Topic)
def on_openai():
    return research_on_openai()


def asana__search(query: str, limit: int = 2) -> str:
    return "own tool"


@ava.agent_step(
    Research,
    runtime=_scripted("SUBMIT(findings=[])"),
    tools=[asana__search],
    integrations=[asana],
    retries=0,
)
async def clash_step(payload: Topic, *, agent: ava.Agent) -> list[str]:
    return await _research(payload, agent)


@ava.workflow(input=Topic)
def clash():
    return clash_step()


@ava.agent_step(
    Research,
    runtime=_scripted("SUBMIT(findings=[str(await asana__search(query='late'))])"),
    integrations=[asana],
    retries=0,
)
async def expired_pass_step(payload: Topic, *, agent: ava.Agent) -> list[str]:
    # The body runs only after the MCP handshake. Revoke the pass on demand,
    # not on a deadline that can race with worker or model startup.
    with urllib.request.urlopen(os.environ["CATALOG_PROOF_URL"] + "/expire-pass") as reply:
        assert reply.status == 200
    return await _research(payload, agent)


@ava.workflow(input=Topic)
def expired_pass():
    return expired_pass_step()
