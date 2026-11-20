"""Dormant named-provider proof: real catalog calls on the local operator."""

from __future__ import annotations

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


def _runtime(*keys: str) -> ava.runtimes.PredictRLM:
    calls = ", ".join(
        f"str(await {key}__search_tasks_in_workspace(workspace_gid='workspace', limit=1))"
        for key in keys
    )
    model = DummyLM(
        [{"plan": "Call each account.", "code": f"SUBMIT(findings=[{calls}])"}],
        adapter=JSONAdapter(),
    )
    return ava.runtimes.PredictRLM(
        lm=model, sub_lm=model, interpreter=DirectPythonBackend(), max_iterations=1
    )


async def _research(payload: Topic, agent: ava.Agent) -> list[str]:
    with Path(payload.journal).open("a") as journal:
        journal.write("named body started\n")
    return (await agent(topic="refunds")).outputs.findings


@ava.agent_step(
    Research,
    runtime=_runtime("office", "partner"),
    integrations=[asana(connection="office"), asana(connection="partner")],
    retries=0,
)
async def two_accounts(payload: Topic, *, agent: ava.Agent) -> list[str]:
    return await _research(payload, agent)


@ava.agent_step(
    Research,
    runtime=_runtime("my_asana"),
    integrations=[asana(connection="my-asana")],
    retries=0,
)
async def personal_account(payload: Topic, *, agent: ava.Agent) -> list[str]:
    return await _research(payload, agent)


@ava.agent_step(
    Research,
    runtime=_runtime("schedule_slot"),
    integrations=[asana(connection="schedule-slot")],
    retries=0,
)
async def scheduled_account(payload: Topic, *, agent: ava.Agent) -> list[str]:
    return await _research(payload, agent)


@ava.agent_step(Research, runtime=_runtime("asana"), integrations=[asana], retries=0)
async def bare_account(payload: Topic, *, agent: ava.Agent) -> list[str]:
    return await _research(payload, agent)


@ava.workflow(input=Topic)
def shared_accounts():
    return two_accounts()


@ava.workflow(input=Topic)
def personal():
    return personal_account()


@ava.workflow(input=Topic, cron="0 0 1 1 *")
def scheduled():
    return scheduled_account()


@ava.workflow(input=Topic)
def ambiguous():
    return bare_account()
