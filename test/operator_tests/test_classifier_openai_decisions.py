"""OpenAI Decisions contracts, through the real ``openai`` SDK and a local HTTP service."""

from __future__ import annotations

import sys

import pytest

import avalanche as ava
from avalanche.classifier import capture_classifier_evidence
from avalanche.evaluations import EvaluationError, Evaluations, Metric

from .test_classifier_provider_scenario import (
    ProviderService,
    openai_decisions_response,
    openai_decisions_service,  # noqa: F401 - pytest fixture
)

pytest.importorskip("openai")

OPENAI = ava.classifiers.OpenAIDecisions()
STATE = {"ticket": "charged twice, state-only-marker"}
QUESTIONS = {
    "department / routing": {
        "type": "choice",
        "instructions": {"task": "Route the ticket"},
        "criteria": {"billing": "charges and refunds", "technical": None},
    },
    "urgent?": {"type": "noul", "instructions": "Is the customer blocked?"},
    "severity": {"type": "score", "criteria": ["minor", "major", "critical"]},
}


@pytest.fixture
def service(openai_decisions_service) -> ProviderService:  # noqa: F811
    return openai_decisions_service


async def call(questions=QUESTIONS, state=STATE, **settings):
    """Run one classifier step and return (result or error, recorded evidence)."""

    @ava.classifier_step(classifier=OPENAI, retries=0, **settings)
    async def classify(*, classifier: ava.Classifier):
        return await classifier(state=state, questions=questions)

    observed = []
    with capture_classifier_evidence(observed.append):
        try:
            outcome = await classify.fn()
        except ava.ClassifierStepExecutionError as error:
            outcome = error
    assert [record.status for record in observed][0] == "running"
    return outcome, observed[-1]


@pytest.mark.asyncio
async def test_questions_go_unnamed_in_order_and_answers_come_back_under_their_names(service):
    result, record = await call()

    [request] = service.bodies()
    assert request == {
        "model": "gpt-6-luna",
        "input": [{"role": "user", "content": [{"type": "input_json", "data": STATE}]}],
        "questions": [
            {
                "type": "choice",
                "instructions": [{"type": "input_json", "data": {"task": "Route the ticket"}}],
                "choices": [
                    {"value": "billing", "description": "charges and refunds"},
                    {"value": "technical"},
                ],
            },
            {"type": "predicate", "instructions": "Is the customer blocked?"},
            {
                "type": "score",
                "instructions": "",
                "levels": [{"label": "minor"}, {"label": "major"}, {"label": "critical"}],
            },
        ],
    }
    assert record.status == "success" and record.result == result
    assert result.model == "gpt-6-luna"
    assert list(result.answers) == list(QUESTIONS)
    department = result.choices["department / routing"]
    assert (department.choice, department.confidence) == ("billing", 0.6)
    assert department.probabilities == {"billing": 0.8, "technical": 0.2}
    assert result.nouls["urgent?"].noul == 0.91
    severity = result.scores["severity"]
    assert severity.score == 0.75
    assert severity.legend == {"0": "minor", "1": "major", "2": "critical"}
    assert severity.probabilities == {"0": 0.25, "1": 0.75, "2": 0.0}
    assert (result.usage.input_tokens, result.usage.output_tokens) == (371, 0)


@pytest.mark.asyncio
async def test_text_state_is_sent_as_text_and_the_model_can_be_pinned(service):
    result, _ = await call(state="charged twice", model="gpt-6-luna-pinned")
    [request] = service.bodies()
    assert (request["model"], request["input"]) == ("gpt-6-luna-pinned", "charged twice")
    assert result.model == "gpt-6-luna-pinned"


@pytest.mark.parametrize(
    "question",
    [
        pytest.param(
            {"type": "score", "criteria": [str(level) for level in range(11)]}, id="11-levels"
        ),
        pytest.param(
            {"type": "score", "criteria": ["low", {"level": "high"}]}, id="json-level"
        ),
        pytest.param({"type": "choice", "criteria": {"only": None}}, id="1-option"),
        pytest.param(
            {"type": "choice", "criteria": {str(option): None for option in range(256)}},
            id="256-options",
        ),
        pytest.param(
            {"type": "choice", "criteria": {"a": {"examples": ["x"]}, "b": None}},
            id="json-option-criteria",
        ),
        pytest.param(
            {"type": "noul", "instructions": "Blocked?", "criteria": {"true": "cannot log in"}},
            id="noul-criteria",
        ),
        pytest.param({"type": "noul", "instructions": ["Blocked?", 3]}, id="scalar-list"),
    ],
)
@pytest.mark.asyncio
async def test_a_question_the_api_cannot_take_is_refused_before_any_request(service, question):
    naming = "question 'asked' cannot be sent to OpenAI Decisions"
    with pytest.raises(ava.ClassifierStepError, match=naming):
        ava.classifier_step(questions={"asked": question}, classifier=OPENAI)
    with pytest.raises(EvaluationError, match=naming):
        Evaluations(
            metrics={"asked": Metric(state=lambda ctx: ctx.output, question=question)},
            classifier=OPENAI,
        )
    error, record = await call(questions={"asked": question})
    assert naming in str(error) and record.status == "failed"
    assert service.bodies() == []


@pytest.mark.asyncio
async def test_more_questions_than_one_call_takes_are_refused(service):
    questions = {f"q{index}": {"type": "noul", "instructions": "?"} for index in range(401)}
    with pytest.raises(ava.ClassifierStepError, match="at most 400 questions"):
        ava.classifier_step(questions=questions, classifier=OPENAI)
    assert service.bodies() == []


@pytest.mark.asyncio
async def test_a_state_the_api_cannot_take_is_refused_before_any_request(service):
    error, record = await call(state=["first", {"second": 2}])
    assert "list of JSON objects" in str(error) and record.status == "failed"
    assert service.bodies() == []


@pytest.mark.asyncio
async def test_a_refused_question_fails_the_whole_call_naming_it_without_the_state(service):
    def refuse_second(payload):
        body = openai_decisions_response(payload)
        body["answers"][1] = {"type": "refusal", "name": None}
        return 200, body

    service.respond = refuse_second
    error, record = await call()
    assert isinstance(error, ava.ClassifierStepExecutionError)
    assert "'urgent?'" in str(error) and "refused" in str(error)
    assert record.status == "failed" and record.result is None
    assert "state-only-marker" not in str(error) + record.error


def _drop_last(body):
    body["answers"].pop()


def _boolean_option(body):
    body["answers"][0]["choice"] = True
    body["answers"][0]["probabilities"][0]["value"] = True


def _swap_types(body):
    body["answers"][1], body["answers"][2] = body["answers"][2], body["answers"][1]


def _not_a_distribution(body):
    body["answers"][0]["probabilities"][0]["probability"] = 0.7


def _repeated_level(body):
    body["answers"][2]["probabilities"][2]["value"] = 1


def _score_off_its_levels(body):
    body["answers"][2]["score"] = 1.4


def _no_usage(body):
    del body["usage"]


@pytest.mark.parametrize(
    "damage",
    [
        _drop_last,
        _boolean_option,
        _swap_types,
        _not_a_distribution,
        _repeated_level,
        _score_off_its_levels,
        _no_usage,
    ],
)
@pytest.mark.asyncio
async def test_a_response_that_does_not_answer_the_questions_fails_the_call(service, damage):
    def respond(payload):
        body = openai_decisions_response(payload)
        damage(body)
        return 200, body

    service.respond = respond
    error, record = await call()
    assert isinstance(error, ava.ClassifierStepExecutionError)
    assert "Invalid OpenAI Decisions response" in str(error)
    assert record.status == "failed" and record.result is None


@pytest.mark.asyncio
async def test_an_api_error_names_its_status_and_never_the_request_or_the_body(service):
    service.respond = lambda payload: (
        400,
        {
            "error": {
                "message": f"Invalid input: {payload['input']}",
                "type": "invalid_request_error",
                "param": "input",
                "code": "echoed-body-marker",
            }
        },
    )
    error, record = await call()
    assert isinstance(error, ava.ClassifierStepExecutionError)
    assert "HTTP 400" in str(error) and record.status == "failed"
    for private in ("state-only-marker", "echoed-body-marker", service.api_key):
        assert private not in str(error) + record.error


@pytest.mark.asyncio
async def test_a_missing_openai_package_names_the_extra_to_install(service, monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    error, record = await call()
    assert isinstance(error, ava.ClassifierStepExecutionError)
    assert "avalanche-ai[openai]" in str(error) and record.status == "failed"
    assert service.bodies() == []
