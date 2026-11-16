"""Flows whose agents use two MCP servers: a remote one and a local process.

The same research step runs on PredictRLM, on the Claude CLI and on the OpenAI
Agents SDK. PredictRLM runs a scripted model; the other two reach a scripted
model API through their usual environment variables. Each makes the calls in
``CALLS`` and answers with what every call returned. The other flows declare a
server that cannot be used, or call a tool that never answers.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dspy import JSONAdapter
from dspy.utils import DummyLM
from predict_rlm.backends import DirectPythonBackend

import avalanche as ava

_SERVER = [sys.executable, str(Path(__file__).with_name("mcp_test_server.py"))]

# The remote server wants a key; ``delete-everything`` is not allowed on either.
kb = ava.MCP(
    "kb",
    url=os.environ.get("MCP_KB_URL", "http://127.0.0.1:9/mcp"),
    allow=["search", "broken", "window"],
    display_name="Knowledge base",
)
local_kb = ava.MCP("local-kb", command=_SERVER, allow=["search", "wait", "ping"])

# The calls every agent makes, in order: one to each server, one whose tool
# fails, one whose arguments do not match the tool's schema, one with an
# argument named like a Python keyword, and one to a tool without arguments.
CALLS: list[tuple[str, dict[str, str | int]]] = [
    ("kb__search", {"query": "refunds", "limit": 2}),
    ("local_kb__search", {"query": "returns"}),
    ("kb__broken", {"reason": "maintenance"}),
    ("local_kb__search", {"limit": "many"}),
    ("kb__window", {"from": "monday"}),
    ("local_kb__ping", {}),
]


class Topic(ava.BaseInput):
    journal: str


class Research(ava.Signature):
    """Search both knowledge bases and report what each call returned."""

    topic: str = ava.InputField()
    findings: list[str] = ava.OutputField()


def _journal(path: str, line: str) -> None:
    with Path(path).open("a") as journal:
        journal.write(line + "\n")


def _scripted(code: str) -> ava.runtimes.PredictRLM:
    model = DummyLM([{"plan": "Follow the script.", "code": code}], adapter=JSONAdapter())
    return ava.runtimes.PredictRLM(
        lm=model, sub_lm=model, interpreter=DirectPythonBackend(), max_iterations=1
    )


def _calls_as_code() -> str:
    lines = ["findings = []"]
    for name, arguments in CALLS:
        lines += [
            "try:",
            f"    findings.append(str(await {name}(**{arguments!r})))",
            "except Exception as error:",
            "    findings.append(str(error))",
        ]
    return "\n".join([*lines, "SUBMIT(findings=findings)"])


@ava.agent_step(
    Research, runtime=_scripted(_calls_as_code()), integrations=[kb, local_kb], retries=0
)
async def research_on_predict_rlm(payload: Topic, *, agent: ava.Agent) -> list[str]:
    _journal(payload.journal, "predict_rlm started")
    return (await agent(topic="refunds")).outputs.findings


@ava.agent_step(
    Research,
    runtime=ava.runtimes.ClaudeAgent(model="claude-haiku-4-5", narrate=False),
    integrations=[kb, local_kb],
    retries=0,
)
async def research_on_claude(payload: Topic, *, agent: ava.Agent) -> list[str]:
    _journal(payload.journal, "claude started")
    return (await agent(topic="refunds")).outputs.findings


@ava.agent_step(
    Research,
    runtime=ava.runtimes.OpenAIAgent(model="gpt-5.4-mini", narrate=False),
    integrations=[kb, local_kb],
    retries=0,
)
async def research_on_openai(payload: Topic, *, agent: ava.Agent) -> list[str]:
    _journal(payload.journal, "openai started")
    return (await agent(topic="refunds")).outputs.findings


@ava.workflow(input=Topic)
def on_predict_rlm():
    return research_on_predict_rlm()


@ava.workflow(input=Topic)
def on_claude():
    return research_on_claude()


@ava.workflow(input=Topic)
def on_openai():
    return research_on_openai()


def _unusable(name: str, server: ava.MCP, *, retries: int = 0):
    """A flow whose step starts the local server, then meets a server it cannot use."""

    async def research(payload: Topic, *, agent: ava.Agent) -> list[str]:
        _journal(payload.journal, f"{name} started")
        return (await agent(topic="refunds")).outputs.findings

    research.__name__ = research.__qualname__ = name
    return ava.agent_step(
        Research,
        runtime=_scripted("SUBMIT(findings=[])"),
        integrations=[local_kb, server],
        retries=retries,
    )(research)


unreachable_step = _unusable(
    "unreachable",
    ava.MCP("down", url=os.environ.get("MCP_DOWN_URL", "http://127.0.0.1:9/mcp")),
    retries=2,
)
missing_program_step = _unusable(
    "missing_program", ava.MCP("ghost", command=["avalanche-no-such-mcp-server"])
)
stops_step = _unusable(
    "stops",
    ava.MCP(
        "crashing",
        command=[
            sys.executable,
            "-c",
            "import sys; print('the index file is missing', file=sys.stderr); sys.exit(3)",
        ],
    ),
)
allow_unknown_step = _unusable(
    "allow_unknown", ava.MCP("strict-kb", command=_SERVER, allow=["search", "summarize"])
)


@ava.workflow(input=Topic)
def unreachable():
    return unreachable_step()


@ava.workflow(input=Topic)
def missing_program():
    return missing_program_step()


@ava.workflow(input=Topic)
def stops():
    return stops_step()


@ava.workflow(input=Topic)
def allow_unknown():
    return allow_unknown_step()


# This server stays when its input is closed, so only being stopped ends it.
lingering_kb = ava.MCP("lingering-kb", command=[*_SERVER, "--linger"], allow=["wait"])


@ava.agent_step(
    Research,
    runtime=_scripted("await lingering_kb__wait(seconds=600)\nSUBMIT(findings=[])"),
    integrations=[lingering_kb],
    retries=0,
)
async def research_that_waits(payload: Topic, *, agent: ava.Agent) -> list[str]:
    return (await agent(topic="refunds")).outputs.findings


@ava.workflow(input=Topic)
def waits():
    return research_that_waits()
