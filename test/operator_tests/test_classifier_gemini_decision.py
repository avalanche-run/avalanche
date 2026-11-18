"""Gemini Decision contracts, through the real ``google-genai`` SDK and a local HTTP service."""

from __future__ import annotations

import math
import sys

import pytest

import avalanche as ava
from avalanche.classifier import capture_classifier_evidence

from .test_classifier_provider_scenario import (
    GEMINI_BLOCKED_INPUT,
    ProviderService,
    gemini_decision_service,  # noqa: F401 - pytest fixture
)

pytest.importorskip("google.genai")

GEMINI = ava.classifiers.GeminiDecision()
SPACED, LONG = "is it urgent? (yes / no)", "q" * 80
QUESTIONS = {
    SPACED: {
        "type": "noul",
        "instructions": "Is the customer blocked?",
        "criteria": {"true": "cannot use the product", "false": None},
    },
    LONG: {
        "type": "choice",
        "instructions": "Route the ticket",
        # Declared out of alphabetical order, with one option left undescribed.
        "criteria": {"technical": None, "billing": "charges", "account access": "login"},
    },
    "q0": {"type": "score", "instructions": "How severe?", "criteria": ["low", "high"]},
}


@pytest.fixture
def service(gemini_decision_service) -> ProviderService:  # noqa: F811
    return gemini_decision_service


async def call(questions=QUESTIONS, state="charged twice", **settings):
    """Run one classifier step and return (result or error, recorded evidence)."""

    @ava.classifier_step(classifier=GEMINI, retries=0, **settings)
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
async def test_question_names_options_and_unrounded_probabilities_come_back_as_declared(
    service,
):
    result, record = await call(state={"ticket": "charged twice"})

    [request] = service.bodies()
    # Gemini takes question IDs of 1-64 characters in [A-Za-z0-9_-] and text input.
    assert list(request["questions"]) == ["q0", "q1", "q2"]
    assert request["input"] == '{"ticket": "charged twice"}'
    assert request["questions"]["q0"]["criteria"] == {
        "true_description": "cannot use the product"
    }
    assert request["questions"]["q1"]["criteria"]["technical"] == ""

    assert record.status == "success"
    assert list(result.answers) == [SPACED, LONG, "q0"]
    assert result.model == "gemini-decision-1"
    assert result.nouls[SPACED].model_dump() == {"type": "noul", "noul": 0.91}
    choice = result.choices[LONG]
    assert choice.choice == "account access"
    assert list(choice.probabilities) == ["technical", "billing", "account access"]
    assert choice.probabilities["account access"] == 0.7826952510481585
    assert math.isclose(sum(choice.probabilities.values()), 1.0, abs_tol=1e-12)
    assert result.scores["q0"].legend == {"0": "low", "1": "high"}
    assert (result.usage.input_tokens, result.usage.output_tokens) == (196, 0)


@pytest.mark.asyncio
async def test_a_safety_block_fails_the_call_with_the_reason_and_never_the_input(service):
    questions = {"urgent": {"type": "noul", "instructions": "Is the customer blocked?"}}
    error, record = await call(questions, state=f"secret ticket {GEMINI_BLOCKED_INPUT}")

    assert isinstance(error, ava.ClassifierStepExecutionError)
    assert "safety" in str(error) and "HTTP 400" in str(error)
    assert record.status == "failed"
    assert "secret ticket" not in str(error) + record.error
    assert error.__cause__ is None


def _unknown_option(body):
    body["answers"]["q1"]["probabilities"]["other"] = 0.0


def _not_a_distribution(body):
    body["answers"]["q1"]["probabilities"]["billing"] += 0.001


def _missing_answer(body):
    del body["answers"]["q2"]


def _blocked_without_answers(body):
    body["answers"] = {}
    body["prompt_feedback"] = {"block_reason": "safety", "block_reason_message": "secret"}


@pytest.mark.parametrize(
    "damage", [_unknown_option, _not_a_distribution, _missing_answer, _blocked_without_answers]
)
@pytest.mark.asyncio
async def test_a_response_that_does_not_answer_the_questions_fails_the_call(service, damage):
    from .test_classifier_provider_scenario import gemini_decision_response

    def respond(payload):
        body = gemini_decision_response(payload)
        damage(body)
        return 200, body

    service.respond = respond
    error, record = await call()

    assert isinstance(error, ava.ClassifierStepExecutionError)
    assert record.status == "failed" and record.result is None
    assert "secret" not in str(error)


@pytest.mark.parametrize(
    ("question", "limit"),
    [
        ({"type": "noul"}, "instructions are required"),
        ({"type": "noul", "instructions": ["a", "b"]}, "must be text"),
        (
            {"type": "noul", "instructions": "ok?", "criteria": {"true": {"k": 1}}},
            "criteria must be text",
        ),
        ({"type": "choice", "instructions": "pick", "criteria": {"only": None}}, "2 and 255"),
        (
            {"type": "choice", "instructions": "pick", "criteria": {"a ": None, "b": None}},
            "start or end with whitespace",
        ),
        (
            {"type": "choice", "instructions": "pick", "criteria": {"a": {"k": 1}, "b": None}},
            "criteria must be text",
        ),
        (
            {"type": "score", "instructions": "rate", "criteria": ["low", {"level": "high"}]},
            "levels must be non-empty text",
        ),
        (
            {"type": "score", "instructions": "rate", "criteria": ["level"] * 256},
            "at most 255 levels",
        ),
    ],
)
def test_a_question_gemini_cannot_take_is_refused_when_the_step_is_declared(question, limit):
    with pytest.raises(ava.ClassifierStepError, match=limit) as raised:
        ava.classifier_step(questions={"the question": question}, classifier=GEMINI)
    assert "question 'the question' cannot be sent to Gemini Decision" in str(raised.value)


def test_more_questions_than_one_call_takes_are_refused():
    questions = {
        f"question {index}": {"type": "noul", "instructions": "ok?"} for index in range(1001)
    }
    with pytest.raises(ava.ClassifierStepError, match="at most 1000 questions"):
        ava.classifier_step(questions=questions, classifier=GEMINI)


@pytest.mark.asyncio
async def test_a_missing_google_genai_package_names_the_extra_to_install(service, monkeypatch):
    # What Python does when google-genai is not installed.
    monkeypatch.setitem(sys.modules, "google.genai", None)
    error, record = await call()

    assert isinstance(error, ava.ClassifierStepExecutionError)
    assert "avalanche-ai[gemini]" in str(error)
    assert record.status == "failed"
    assert service.bodies() == []
