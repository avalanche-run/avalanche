import json
import shutil
from pathlib import Path

import pytest

import avalanche as ava
from runtime.operator import WorkflowRegistry

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "classifier_provider_workflow.py"


@pytest.mark.parametrize(
    ("env", "provider"),
    [
        ({"OPENAI_API_KEY": "k", "GEMINI_API_KEY": "k"}, ava.classifiers.OpenAIDecisions()),
        ({"GEMINI_API_KEY": "k"}, ava.classifiers.GeminiDecision()),
        ({"OPENAI_API_KEY": ""}, ava.classifiers.TypeSafe()),
        ({}, ava.classifiers.TypeSafe()),
    ],
    ids=["openai-first", "gemini", "blank-key", "no-key"],
)
def test_operator_discovers_the_provider_whose_key_is_set(monkeypatch, tmp_path, env, provider):
    # Isolate the project .env lookup; discovery needs no key and no network.
    monkeypatch.chdir(tmp_path)
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    target = tmp_path / EXAMPLE.name
    shutil.copyfile(EXAMPLE, target)

    registry = WorkflowRegistry()
    registry.scan([str(target)])

    assert registry.view.diagnostics == ()
    (info,) = registry.list_workflows()
    (declaration,) = dict(info.classifier_metadata_json).values()
    runtime = json.loads(declaration)["runtime"]
    assert runtime["classifier"] == provider.model_dump(mode="json")
    assert (
        runtime["model"]
        == {
            "openai_decisions": "gpt-6-luna",
            "gemini_decision": "gemini-decision-1",
            "typesafe": "jev-latest",
        }[provider.kind]
    )
