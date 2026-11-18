"""The provider scenario: one workflow, a classifier step and an evaluated agent step.

Every provider runs this same scenario through the local operator. A provider adds a
``Provider`` entry: the expression users write, its key variable and a local HTTP
service speaking the vendor's wire shape, driven through the real vendor SDK.
"""

from __future__ import annotations

import json
import queue
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from textwrap import dedent

import pytest

import avalanche as ava
from avalanche.classifier.models import ClassifierDeclaration, ClassifierInvocation
from avalanche.classifier.providers import ClassifierProvider
from avalanche.evaluations import EvaluationDeclaration
from runtime.operator import Operator
from runtime.operator.evaluation_models import EvaluationRecord
from runtime.operator.models import RunState, RunStatus

QUESTIONS = {
    "department": {
        "type": "choice",
        "instructions": "Route the ticket",
        "criteria": {"billing": "charges and refunds", "technical": None},
    },
    "urgent": {"type": "noul", "instructions": "Is the customer blocked?"},
    "severity": {
        "type": "score",
        "instructions": "How severe is the problem?",
        "criteria": ["minor", "major", "critical"],
    },
}


@dataclass
class ProviderService:
    """What the fake vendor service received: each request body, in order."""

    api_key: str
    requests: queue.Queue = field(default_factory=queue.Queue)
    # Replaces the default answer of the next requests: body -> (HTTP status, JSON body).
    respond: Callable[[dict], tuple[int, dict]] | None = None

    def bodies(self) -> list[dict]:
        bodies = []
        while not self.requests.empty():
            bodies.append(self.requests.get_nowait())
        return bodies

    def models(self) -> list[str]:
        return [body["model"] for body in self.bodies()]


@dataclass(frozen=True)
class Provider:
    expression: str
    value: ClassifierProvider
    key_variable: str
    default_model: str
    pinned_model: str
    # A question this provider cannot take, as the ``questions=`` of a classifier call.
    refused_questions: str
    # Whether the refusal names the question and the provider's limit.
    refusal_names_question: bool
    service: str


TYPESAFE = Provider(
    expression="ava.classifiers.TypeSafe()",
    value=ava.classifiers.TypeSafe(),
    key_variable="TYPESAFE_API_KEY",
    default_model="jev-latest",
    pinned_model="jev-1.14.0",
    # TypeSafe has no limit of its own; a one-level score is one no provider takes.
    refused_questions='{"levels": {"type": "score", "criteria": ["only"]}}',
    refusal_names_question=False,
    service="typesafe_service",
)
OPENAI_DECISIONS = Provider(
    expression="ava.classifiers.OpenAIDecisions()",
    value=ava.classifiers.OpenAIDecisions(),
    key_variable="OPENAI_API_KEY",
    default_model="gpt-6-luna",
    pinned_model="gpt-6-luna-2026-10-01",
    refused_questions=(
        '{"levels": {"type": "score", "criteria": [str(level) for level in range(11)]}}'
    ),
    refusal_names_question=True,
    service="openai_decisions_service",
)
GEMINI_DECISION = Provider(
    expression="ava.classifiers.GeminiDecision()",
    value=ava.classifiers.GeminiDecision(),
    key_variable="GEMINI_API_KEY",
    default_model="gemini-decision-1",
    pinned_model="gemini-decision-1-preview-scenario",
    # Gemini takes text instructions only.
    refused_questions='{"levels": {"type": "noul", "instructions": {"rule": "refund policy"}}}',
    refusal_names_question=True,
    service="gemini_decision_service",
)
PROVIDERS = [
    pytest.param(TYPESAFE, id="typesafe"),
    pytest.param(OPENAI_DECISIONS, id="openai_decisions"),
    pytest.param(GEMINI_DECISION, id="gemini_decision"),
]


def _serve(
    service: ProviderService,
    path: str,
    answer: Callable[[dict], dict | tuple[int, dict]],
    key_header: str | None = None,
):
    """Serve one authenticated JSON POST route on a local port.

    ``answer`` returns the body of a 200 response, or a status with its body. The key
    arrives as a bearer token unless the vendor uses its own ``key_header``.
    """
    credential = (
        ("Authorization", f"Bearer {service.api_key}")
        if key_header is None
        else (key_header, service.api_key)
    )

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - HTTP server interface
            if self.path != path:
                self.send_error(404)
                return
            if self.headers.get(credential[0]) != credential[1]:
                self.send_error(401)
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            service.requests.put(payload)
            answered = answer(payload) if service.respond is None else service.respond(payload)
            status, body = answered if isinstance(answered, tuple) else (200, answered)
            response = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _typesafe_answer(question: dict) -> dict:
    if question["type"] == "choice":
        first, *others = question["criteria"]
        probabilities = {first: 0.8, **{name: 0.2 / len(others) for name in others}}
        return {
            "type": "choice",
            "choice": first,
            "probabilities": probabilities,
            "confidence": 0.6,
        }
    if question["type"] == "noul":
        return {"type": "noul", "noul": 0.91}
    levels = question["criteria"]
    probabilities = {str(index): 0.0 for index in range(len(levels))}
    probabilities["0"], probabilities["1"] = 0.25, 0.75
    return {
        "type": "score",
        "score": 0.75,
        "legend": {str(index): level for index, level in enumerate(levels)},
        "probabilities": probabilities,
        "confidence": 0.5,
    }


def _typesafe_response(payload: dict) -> dict:
    return {
        # Echo the requested model so results show which one was called.
        "model": payload["model"],
        "answers": {
            name: _typesafe_answer(question) for name, question in payload["questions"].items()
        },
        "usage": {"input_tokens": 37, "output_tokens": 11},
    }


@pytest.fixture
def typesafe_service(monkeypatch) -> Iterator[ProviderService]:
    """TypeSafe's wire shape over HTTP; the installed SDK makes every request."""
    service = ProviderService(api_key="provider-scenario-test-only-key")
    server, thread = _serve(service, "/v1/systemone", _typesafe_response)
    monkeypatch.setenv("TYPESAFE_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("TYPESAFE_API_KEY", service.api_key)
    try:
        yield service
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _openai_decisions_answer(question: dict) -> dict:
    """One answer in the shape and two-decimal precision the live API returns."""
    if question["type"] == "choice":
        first, *others = [choice["value"] for choice in question["choices"]]
        return {
            "type": "choice",
            "name": None,
            "choice": first,
            "probabilities": [
                {"value": first, "probability": 0.8},
                *({"value": value, "probability": 0.2 / len(others)} for value in others),
            ],
            "confidence": 0.6,
        }
    if question["type"] == "predicate":
        return {"type": "predicate", "name": None, "probability": 0.91}
    probabilities = [0.25, 0.75] + [0.0] * (len(question["levels"]) - 2)
    return {
        "type": "score",
        "name": None,
        "score": 0.75,
        "probabilities": [
            {"value": index, "label": level["label"], "probability": probabilities[index]}
            for index, level in enumerate(question["levels"])
        ],
        "confidence": 0.5,
    }


def openai_decisions_response(payload: dict) -> dict:
    return {
        # Echo the requested model so results show which one was called.
        "model": payload["model"],
        "answers": [_openai_decisions_answer(question) for question in payload["questions"]],
        "usage": {
            "input_tokens": 371,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 0,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 371,
        },
    }


@pytest.fixture
def openai_decisions_service(monkeypatch) -> Iterator[ProviderService]:
    """OpenAI's Decisions wire shape over HTTP; the installed SDK makes every request."""
    pytest.importorskip("openai")
    service = ProviderService(api_key="provider-scenario-test-only-key")
    server, thread = _serve(service, "/v1/decisions", openai_decisions_response)
    monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("OPENAI_API_KEY", service.api_key)
    for scoping in ("OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
        monkeypatch.delenv(scoping, raising=False)
    try:
        yield service
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# What the live Gemini Decision API (v1beta) answers and refuses, as recorded from
# real calls: unrounded probabilities, maps in no particular order, no model field,
# and HTTP 400 with a machine code for a question shape it does not take.
GEMINI_BLOCKED_INPUT = "gemini-fake: input the safety filter blocks"
_GEMINI_QUESTION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _gemini_refusal(payload: dict) -> str | None:
    if not isinstance(payload["input"], str) or not payload["input"]:
        return "input must be non-empty text"
    for key, question in payload["questions"].items():
        if not _GEMINI_QUESTION_ID.fullmatch(key):
            return "question IDs must be 1-64 characters matching [A-Za-z0-9_-]+"
        if not isinstance(question.get("instructions"), str) or not question["instructions"]:
            return f"Question '{key}' must have non-empty instructions."
        criteria = question.get("criteria")
        if question["type"] == "choice":
            described = isinstance(criteria, dict) and all(
                isinstance(text, str) for text in criteria.values()
            )
            if not described or not 2 <= len(criteria) <= 255:
                return "choice.criteria: must have between 2 and 255 options"
        elif question["type"] == "score":
            levels = isinstance(criteria, list) and all(
                isinstance(level, str) and level for level in criteria
            )
            if not levels or not 2 <= len(criteria) <= 255:
                return "score.criteria: must have between 2 and 255 levels"
        elif criteria is not None and not (
            set(criteria) <= {"true_description", "false_description"}
            and all(isinstance(text, str) for text in criteria.values())
        ):
            return "noul.criteria: unknown or non-text description"
    return None


def _gemini_decision_answer(question: dict) -> dict:
    if question["type"] == "choice":
        # Gemini sorts options alphabetically; this fake favours the first one.
        first, *others = sorted(question["criteria"])
        highest = 0.7826952510481585
        probabilities = {name: (1 - highest) / len(others) for name in reversed(others)}
        probabilities[first] = highest
        return {
            "confidence": highest,
            "choice": first,
            "probabilities": probabilities,
            "type": "choice",
        }
    if question["type"] == "noul":
        # A noul carries a confidence, max(noul, 1 - noul), that Avalanche drops.
        return {"confidence": 0.91, "noul": 0.91, "type": "noul"}
    levels = question["criteria"]
    indexes = [str(index) for index in reversed(range(len(levels)))]
    return {
        "confidence": 0.75,
        "score": 0.75,
        "probabilities": {index: {"0": 0.25, "1": 0.75}.get(index, 0.0) for index in indexes},
        "legend": {index: levels[int(index)] for index in indexes},
        "type": "score",
    }


def gemini_decision_response(payload: dict) -> dict | tuple[int, dict]:
    refusal = _gemini_refusal(payload)
    if refusal is not None:
        return 400, {"error": {"message": refusal, "code": "invalid_request"}}
    if GEMINI_BLOCKED_INPUT in payload["input"]:
        # The live message can describe the input; errors must never repeat it.
        message = f"Request blocked due to safety violations: {payload['input']}"
        return 400, {"error": {"code": "safety", "message": message}}
    questions = payload["questions"]
    return {
        "answers": {
            key: _gemini_decision_answer(questions[key]) for key in reversed(questions)
        },
        "usage": {
            "total_tokens": 196,
            "total_input_tokens": 196,
            "input_tokens_by_modality": [{"modality": "text", "tokens": 196}],
            "total_output_tokens": 0,
        },
    }


@pytest.fixture
def gemini_decision_service(monkeypatch) -> Iterator[ProviderService]:
    """Gemini Decision's wire shape over HTTP; google-genai makes every request."""
    pytest.importorskip("google.genai")
    service = ProviderService(api_key="provider-scenario-test-only-key")
    server, thread = _serve(
        service, "/v1beta/decisions", gemini_decision_response, key_header="x-goog-api-key"
    )
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("GEMINI_API_KEY", service.api_key)
    # google-genai prefers this variable on its own; Avalanche must not use it.
    monkeypatch.setenv("GOOGLE_API_KEY", "not-the-key-avalanche-reads")
    try:
        yield service
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def service(request, provider: Provider) -> ProviderService:
    """The fake vendor service of the provider under test."""
    return request.getfixturevalue(provider.service)


_SCENARIO = """
import dspy
import predict_rlm
import avalanche as ava

class Reply(ava.Signature):
    \"\"\"Draft a reply to the ticket.\"\"\"
    department: str = ava.InputField()
    answer: str = ava.OutputField()

class TestPredictor:
    def __init__(self, signature, *, skills=(), tools=(), events=(), **kwargs):
        self.max_iterations = kwargs.get("max_iterations", 2)

    async def acall(self, **inputs):
        return dspy.Prediction(
            answer="Routed to " + inputs["department"],
            trace=predict_rlm.RunTrace(
                status="completed", model="test-predictor", iterations=0,
                max_iterations=self.max_iterations, duration_ms=1,
            ),
            evidence=predict_rlm.RunEvidence(
                run_id="scenario-run", complete=True, terminal_outcome="completed"
            ),
        )

# This replacement exists only in the temporary module's isolated child.
predict_rlm.PredictRLM = TestPredictor

@ava.source
def load() -> dict:
    return {{"ticket": "I was charged twice and cannot log in"}}

@ava.classifier_step(questions={questions!r}, retries=0{step_settings})
async def triage(ticket: dict, *, classifier: ava.Classifier) -> dict:
    result = await classifier(state=ticket{call_questions})
    return {{
        "department": result.choices["department"].choice,
        "urgent": result.nouls["urgent"].noul,
        "severity": result.scores["severity"].score,
    }}

evaluations = ava.Evaluations(
    metrics={{
        "helpful": ava.Metric(
            state=lambda ctx: ctx.output.answer,
            question={{"type": "noul", "instructions": "Does the reply help?"}},
        ),
        "tone": ava.Metric(
            state=lambda ctx: ctx.output.answer,
            question={{
                "type": "score",
                "instructions": "How warm is the reply?",
                "criteria": ["curt", "neutral", "warm"],
            }},
        ),
    }},
    composites={{"quality": lambda answers: answers.nouls["helpful"].noul}}{eval_settings},
)

@ava.step
def route(triaged: dict) -> str:
    return triaged["department"]

@ava.step
def finish(triaged: dict, answer: str) -> dict:
    return {{**triaged, "answer": answer}}

@ava.workflow{workflow_settings}
def flow():
    triaged = triage(load())
    reply = ava.agent.step(
        Reply, inputs={{"department": route(triaged)}}, slug="reply", evaluations=evaluations
    )
    return finish(triaged, reply)
"""


def write_scenario(
    root: Path,
    *,
    step_settings: str = "",
    eval_settings: str = "",
    workflow_settings: str = "",
    call_questions: str = "",
) -> Path:
    workflow = root / "provider_scenario.py"
    workflow.write_text(
        dedent(_SCENARIO).format(
            questions=QUESTIONS,
            step_settings=step_settings,
            eval_settings=eval_settings,
            workflow_settings=workflow_settings,
            call_questions=call_questions,
        )
    )
    return workflow


def wait_terminal(operator: Operator, run_id: str, timeout: float = 30.0) -> RunState:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = operator.get_run(run_id)
        if run is not None and run.status in {
            RunStatus.SUCCESS,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return run
        time.sleep(0.03)
    raise AssertionError(f"run {run_id} did not finish")


def wait_evaluation(operator: Operator, run_id: str, timeout: float = 30.0) -> EvaluationRecord:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = operator.list_evaluations(run_id)
        if records and records[0].status != "pending":
            [record] = records
            return record
        time.sleep(0.03)
    raise AssertionError(f"evaluation for {run_id} did not finish")


def classifier_calls(operator: Operator, run: RunState) -> list[ClassifierInvocation]:
    [node_id] = dict(run.topology.classifier_metadata_json)
    page = operator.list_classifier_events(run.run_id, node_id, page_size=10)
    return [
        ClassifierInvocation.model_validate_json(operator.read_detail(event.body_token))
        for event in page.events
    ]


def declared(run: RunState) -> tuple[ClassifierDeclaration, EvaluationDeclaration]:
    [classifier_json] = dict(run.topology.classifier_metadata_json).values()
    [evaluation_json] = dict(run.topology.evaluation_metadata_json).values()
    return (
        ClassifierDeclaration.model_validate_json(classifier_json),
        EvaluationDeclaration.model_validate_json(evaluation_json),
    )


def _settings(provider: Provider) -> str:
    return f"classifier={provider.expression}, model={provider.pinned_model!r}"


@pytest.mark.parametrize(
    ("provider", "placement"),
    [
        pytest.param(provider, placement, id=f"{name}-{placement}")
        for name, provider in (
            ("typesafe", TYPESAFE),
            ("openai_decisions", OPENAI_DECISIONS),
            ("gemini_decision", GEMINI_DECISION),
        )
        for placement in ("step", "workflow", "evaluations", "unset")
        # A flow that names no classifier calls TypeSafe, whatever else is installed.
        if placement != "unset" or provider is TYPESAFE
    ],
)
def test_provider_and_model_reach_recorded_calls_and_survive_a_restart(
    tmp_path, service, typesafe_service, provider, placement
):
    pinned = provider.pinned_model
    workflow = write_scenario(
        tmp_path,
        step_settings=f", {_settings(provider)}" if placement == "step" else "",
        eval_settings=f", {_settings(provider)}" if placement == "evaluations" else "",
        workflow_settings=(
            f"(classifier_defaults=dict({_settings(provider)}))"
            if placement == "workflow"
            else ""
        ),
    )
    typesafe = TYPESAFE.default_model
    # A level that names no classifier, with none below it, calls TypeSafe's default model.
    step_model, evaluation_model = {
        "step": (pinned, typesafe),
        "workflow": (pinned, pinned),
        "evaluations": (typesafe, pinned),
        "unset": (typesafe, typesafe),
    }[placement]
    step_provider, evaluation_provider = {
        "step": (provider.value, TYPESAFE.value),
        "workflow": (provider.value, provider.value),
        "evaluations": (TYPESAFE.value, provider.value),
        "unset": (TYPESAFE.value, TYPESAFE.value),
    }[placement]
    state_db = tmp_path / "operator.sqlite"

    operator = Operator([str(workflow)], watch=False, schedule=False, state_db=state_db)
    try:
        run = wait_terminal(operator, operator.start_run("flow"))
        assert run.status == RunStatus.SUCCESS
        assert operator.get_run_result(run.run_id) == {
            "department": "billing",
            "urgent": 0.91,
            "severity": 0.75,
            "answer": "Routed to billing",
        }
        evaluation = wait_evaluation(operator, run.run_id)
        assert evaluation.status == "completed", evaluation.error
        # One request for the classifier step, then one batch for both metrics.
        called = [
            (step_provider, step_model),
            (evaluation_provider, evaluation_model),
        ]
        for vendor, fake in {TYPESAFE: typesafe_service, provider: service}.items():
            assert fake.models() == [model for value, model in called if value == vendor.value]
    finally:
        operator.close()

    def assert_recorded(operator: Operator) -> None:
        stored = operator.get_run(run.run_id)
        assert stored is not None
        classifier_declaration, evaluation_declaration = declared(stored)
        assert classifier_declaration.runtime.classifier == step_provider
        assert classifier_declaration.runtime.model == step_model
        assert evaluation_declaration.runtime.classifier == evaluation_provider
        assert evaluation_declaration.runtime.model == evaluation_model

        running, succeeded = classifier_calls(operator, stored)
        assert (running.status, succeeded.status) == ("running", "success")
        assert succeeded.declaration.runtime.classifier == step_provider
        assert succeeded.declaration.runtime.model == step_model
        assert succeeded.result is not None
        assert succeeded.result.model == step_model
        assert succeeded.result.choices["department"].choice == "billing"
        assert succeeded.result.nouls["urgent"].noul == 0.91
        assert succeeded.result.scores["severity"].score == 0.75

        [record] = operator.list_evaluations(run.run_id)
        assert record.result is not None
        assert record.result.classifier == evaluation_provider
        assert record.result.classification.model == evaluation_model
        assert set(record.result.classification.answers) == {"helpful", "tone"}
        assert record.result.composites == {"quality": 0.91}
        assert service.api_key not in (succeeded.model_dump_json() + record.model_dump_json())

    restarted = Operator([], watch=False, schedule=False, state_db=state_db)
    try:
        assert_recorded(restarted)
    finally:
        restarted.close()

    if provider is not TYPESAFE:
        return
    # Records written before classifiers were named carry no classifier at all.
    named = '"classifier":{"kind":"typesafe"},'
    connection = sqlite3.connect(state_db)
    try:
        with connection:
            for table, column in (
                ("runs", "topology_json"),
                ("run_classifier_events", "event_json"),
                ("run_evaluations", "record_json"),
            ):
                naming = f"SELECT count(*) FROM {table} WHERE {column} LIKE '%typesafe%'"
                # The restarted operator read the classifier from these rows.
                assert connection.execute(naming).fetchone() != (0,)
                for text in (named, named.replace('"', '\\"')):
                    connection.execute(
                        f"UPDATE {table} SET {column} = replace({column}, ?, '')", (text,)
                    )
                assert connection.execute(naming).fetchone() == (0,)
    finally:
        connection.close()
    historic = Operator([], watch=False, schedule=False, state_db=state_db)
    try:
        assert_recorded(historic)
    finally:
        historic.close()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_question_the_provider_cannot_take_is_refused_without_a_request(
    tmp_path, service, provider
):
    workflow = write_scenario(
        tmp_path,
        step_settings=f", {_settings(provider)}",
        call_questions=f", questions={provider.refused_questions}",
    )
    operator = Operator([str(workflow)], watch=False, schedule=False, state_db=":memory:")
    try:
        run = wait_terminal(operator, operator.start_run("flow"))
        assert run.status == RunStatus.FAILED
        running, failed = classifier_calls(operator, run)
        assert (running.status, failed.status) == ("running", "failed")
        assert failed.result is None and failed.error
        if provider.refusal_names_question:
            assert "'levels'" in failed.error
        assert failed.declaration.runtime.classifier == provider.value
        assert service.models() == []
        assert operator.list_evaluations(run.run_id) == []
    finally:
        operator.close()


@pytest.mark.parametrize("provider", PROVIDERS)
def test_missing_key_names_the_variable_of_the_provider_in_use(
    tmp_path, service, monkeypatch, provider
):
    workflow = write_scenario(tmp_path, step_settings=f", {_settings(provider)}")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(provider.key_variable)
    operator = Operator([str(workflow)], watch=False, schedule=False, state_db=":memory:")
    try:
        run = wait_terminal(operator, operator.start_run("flow"))
        assert run.status == RunStatus.FAILED
        running, failed = classifier_calls(operator, run)
        assert (running.status, failed.status) == ("running", "failed")
        assert provider.key_variable in failed.error
        assert failed.declaration.runtime.classifier == provider.value
        assert failed.declaration.runtime.model == provider.pinned_model
        assert service.models() == []
    finally:
        operator.close()
