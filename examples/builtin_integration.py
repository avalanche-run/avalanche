"""Read Slack team info through the host's connected built-in account.

Install avalanche-ai[agents,integrations], then run with the local operator.
The scripted agent needs no model key; Slack must be connected by the host.
"""

from __future__ import annotations

from typing import TypedDict

from dspy import JSONAdapter
from dspy.utils import DummyLM
from predict_rlm.backends import DirectPythonBackend

import avalanche as ava
from avalanche.catalog import slack


class TeamInfo(ava.Signature):
    """Read team info and return only a small summary, not the team details."""

    query: str = ava.InputField()
    nonempty_team_info: bool = ava.OutputField()
    team_id_prefix: str = ava.OutputField()


class TeamSummary(TypedDict):
    nonempty_team_info: bool
    team_id_prefix: str


model = DummyLM(
    [
        {
            "plan": "Read team info once and submit only a summary.",
            "code": (
                "info = await slack__get_team_info()\n"
                "team = info['team']\n"
                "SUBMIT(nonempty_team_info=bool(team['name'].strip()), "
                "team_id_prefix=team['id'][:3])"
            ),
        }
    ],
    adapter=JSONAdapter(),
)
runtime = ava.runtimes.PredictRLM(
    lm=model, sub_lm=model, interpreter=DirectPythonBackend(), max_iterations=1
)


@ava.agent_step(TeamInfo, runtime=runtime, integrations=[slack], retries=0)
async def read_team_info(*, agent: ava.Agent) -> TeamSummary:
    result = await agent(query="Read the connected Slack workspace's team info.")
    assert result.outputs.nonempty_team_info, "Slack returned empty team info"
    assert len(result.outputs.team_id_prefix) == 3, "Slack returned a short team id"
    return {
        "nonempty_team_info": result.outputs.nonempty_team_info,
        "team_id_prefix": result.outputs.team_id_prefix,
    }


@ava.workflow
def builtin_integration():
    return read_team_info()
