"""The Gemini Decision provider, through the process-local ``google-genai`` SDK.

Gemini Decision is in beta on the ``v1beta`` Gemini Developer API. The limits and
the wire shape below were checked against that API with ``google-genai`` 3.2.0.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .adapter import ClassifierAdapter, ClassifierConnection, ProbabilityRounding
from .errors import ClassifierStepError, ClassifierStepExecutionError
from .models import (
    Answer,
    ChoiceAnswer,
    ChoiceQuestion,
    ClassificationResult,
    ClassificationUsage,
    JSONContent,
    NoulAnswer,
    NoulQuestion,
    Question,
    ScoreAnswer,
)

if TYPE_CHECKING:
    from google import genai
    from google.genai import decisions

_API_VERSION = "v1beta"
_MAX_QUESTIONS = 1000
_MAX_CHOICES = 255
_MAX_LEVELS = 255
_MAX_OPTION_CHARACTERS = 128
_MAX_OPTION_BYTES = 512
# The machine codes Gemini returns when it blocks an input, and its request-shape
# code. They carry no request content, unlike the message beside them.
_BLOCK_CODES = frozenset(
    {"safety", "prohibited_content", "blocklist", "image_safety", "content_blocked", "other"}
)
_PRIVATE_CODES = _BLOCK_CODES | {"invalid_request"}


class _Wire(BaseModel):
    # The beta API may add fields; the adapter validates the ones it uses.
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True, allow_inf_nan=False)


class _WireNoul(_Wire):
    type: Literal["noul"]
    noul: float


class _WireChoice(_Wire):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float


class _WireScore(_Wire):
    type: Literal["score"]
    score: float
    legend: dict[str, str]
    probabilities: dict[str, float]
    confidence: float


class _WireUsage(_Wire):
    total_input_tokens: int | None = None
    total_output_tokens: int | None = None


class _WireFeedback(_Wire):
    block_reason: str | None = None


class _WireDecision(_Wire):
    answers: dict[
        str, Annotated[_WireNoul | _WireChoice | _WireScore, Field(discriminator="type")]
    ] = Field(default_factory=dict)
    usage: _WireUsage = Field(default_factory=_WireUsage)
    prompt_feedback: _WireFeedback | None = None


class _WireErrorDetail(_Wire):
    code: str | int | None = None


class _WireError(_Wire):
    error: _WireErrorDetail


def _blocked(code: str) -> ClassifierStepExecutionError:
    return ClassifierStepExecutionError(f"Gemini Decision blocked the input ({code})")


def _option_limit(option: str) -> str | None:
    """Return the Gemini option-ID rule that ``option`` breaks, if any."""
    if len(option) > _MAX_OPTION_CHARACTERS or len(option.encode()) > _MAX_OPTION_BYTES:
        return (
            f"a choice option takes at most {_MAX_OPTION_CHARACTERS} characters "
            f"and {_MAX_OPTION_BYTES} UTF-8 bytes"
        )
    if option != option.strip():
        return "a choice option cannot start or end with whitespace"
    if any(
        unicodedata.category(character).startswith("C")
        or (character.isspace() and character != " ")
        for character in option
    ):
        return "a choice option cannot contain control characters or non-ASCII spaces"
    return None


class _GeminiConnection(ClassifierConnection):
    def __init__(
        self, adapter: GeminiDecisionAdapter, client: genai.Client, model: str
    ) -> None:
        self._adapter = adapter
        self._client = client
        self._model = model

    async def classify(
        self, *, state: JSONContent, questions: Mapping[str, Question]
    ) -> ClassificationResult:
        # Gemini takes question IDs of 1-64 characters in [A-Za-z0-9_-]. Send
        # positional IDs and map the answers back, so users keep any names.
        names = {f"q{index}": name for index, name in enumerate(questions)}
        wire_questions: dict[str, decisions.QuestionParam] = {}
        for key, name in names.items():
            wire_questions[key] = self._adapter.wire_question(name, questions[name])
        response = await self._client.aio.decisions.with_raw_response.create(
            model=self._model,
            # Gemini's input is text; its guide sends JSON serialized in a text block.
            input=state if isinstance(state, str) else json.dumps(state, ensure_ascii=False),
            questions=wire_questions,
        )
        # Validate the original HTTP body: the SDK decodes responses without
        # validation and keeps answer types it does not know.
        try:
            decision = _WireDecision.model_validate_json(response.http_response.content)
        except ValueError as error:
            raise self._adapter.invalid_response(error) from None
        if decision.prompt_feedback is not None and decision.prompt_feedback.block_reason:
            reason = decision.prompt_feedback.block_reason
            raise _blocked(reason if reason in _BLOCK_CODES else "blocked")
        try:
            if decision.answers.keys() != names.keys():
                raise ValueError("answers must match exactly the question IDs sent")
            return ClassificationResult(
                # Gemini does not report the model that answered; the requested one did.
                model=self._model,
                answers={
                    name: _answer(questions[name], decision.answers[key])
                    for key, name in names.items()
                },
                usage=ClassificationUsage(
                    input_tokens=decision.usage.total_input_tokens,
                    output_tokens=decision.usage.total_output_tokens,
                ),
            )
        except (ValueError, KeyError) as error:
            raise self._adapter.invalid_response(error) from None

    async def aclose(self) -> None:
        await self._client.aio.aclose()


def _answer(question: Question, wire: _WireNoul | _WireChoice | _WireScore) -> Answer:
    """Translate one Gemini answer; ``ClassificationResult`` checks it against the question."""
    if isinstance(wire, _WireNoul):
        # Gemini's extra noul confidence, max(noul, 1 - noul), is dropped.
        return NoulAnswer(type="noul", noul=wire.noul)
    if isinstance(wire, _WireChoice):
        options = question.criteria if isinstance(question, ChoiceQuestion) else {}
        if wire.probabilities.keys() != options.keys():
            raise ValueError("a choice answer must contain exactly the declared options")
        return ChoiceAnswer(
            type="choice",
            choice=wire.choice,
            # Gemini orders options its own way; the result keeps the declared order.
            probabilities={option: wire.probabilities[option] for option in options},
            confidence=wire.confidence,
        )
    levels = [str(index) for index in range(len(wire.legend))]
    return ScoreAnswer(
        type="score",
        score=wire.score,
        legend={level: wire.legend[level] for level in levels},
        probabilities={level: wire.probabilities[level] for level in levels},
        confidence=wire.confidence,
    )


class GeminiDecisionAdapter(ClassifierAdapter):
    label: ClassVar[str] = "Gemini Decision"
    key_variable: ClassVar[str] = "GEMINI_API_KEY"
    default_model: ClassVar[str] = "gemini-decision-1"
    # Gemini returns unrounded double-precision probabilities. Nine decimals is far
    # above floating-point error and far below any difference that changes a decision.
    rounding: ClassVar[ProbabilityRounding] = ProbabilityRounding(decimals=9)
    extra: ClassVar[str | None] = "gemini"

    def check_questions(self, questions: Mapping[str, Question]) -> None:
        if len(questions) > _MAX_QUESTIONS:
            raise ClassifierStepError(
                f"{len(questions)} questions cannot be sent to {self.label}: "
                f"a call takes at most {_MAX_QUESTIONS} questions"
            )
        for name, question in questions.items():
            self.wire_question(name, question)

    def wire_question(self, name: str, question: Question) -> decisions.QuestionParam:
        """Build Gemini's question, refusing any shape Gemini does not take."""
        instructions = question.instructions
        if instructions is None or instructions == "":
            self.refuse(name, "instructions are required")
        if not isinstance(instructions, str):
            self.refuse(name, "instructions must be text, not structured JSON")
        if isinstance(question, NoulQuestion):
            descriptions: decisions.NoulCriteriaParam = {}
            for outcome, description in (question.criteria or {}).items():
                if description is None:
                    continue
                if not isinstance(description, str):
                    self.refuse(name, "criteria must be text, not structured JSON")
                if outcome == "true":
                    descriptions["true_description"] = description
                else:
                    descriptions["false_description"] = description
            if descriptions:
                return {"type": "noul", "instructions": instructions, "criteria": descriptions}
            return {"type": "noul", "instructions": instructions}
        if isinstance(question, ChoiceQuestion):
            if not 2 <= len(question.criteria) <= _MAX_CHOICES:
                self.refuse(name, f"a choice needs between 2 and {_MAX_CHOICES} options")
            options: dict[str, str] = {}
            for option, description in question.criteria.items():
                limit = _option_limit(option)
                if limit is not None:
                    self.refuse(name, limit)
                if description is not None and not isinstance(description, str):
                    self.refuse(name, "criteria must be text, not structured JSON")
                # Gemini's own form for an option that needs no description.
                options[option] = "" if description is None else description
            return {"type": "choice", "instructions": instructions, "criteria": options}
        if len(question.criteria) > _MAX_LEVELS:
            self.refuse(name, f"a score takes at most {_MAX_LEVELS} levels")
        levels: list[str] = []
        for level in question.criteria:
            if not isinstance(level, str) or not level:
                self.refuse(name, "score levels must be non-empty text, not structured JSON")
            levels.append(level)
        return {"type": "score", "instructions": instructions, "criteria": levels}

    def connect(self, *, api_key: str, model: str, timeout: float) -> ClassifierConnection:
        # Not ``from google import genai``: with another Google package installed that
        # fails with a plain ImportError, which is not reported as a missing extra.
        import google.genai
        from google.genai import types

        client = google.genai.Client(
            # Passed explicitly, so the SDK's own GOOGLE_API_KEY lookup never applies.
            api_key=api_key,
            http_options=types.HttpOptions(
                api_version=_API_VERSION,
                # Per HTTP request, in milliseconds; the SDK keeps its own retry policy.
                timeout=max(1, round(timeout * 1000)),
            ),
        )
        return _GeminiConnection(self, client, model)

    def describe_error(self, error: Exception) -> str | None:
        # google-genai exports no public base class for its decision errors.
        if not type(error).__module__.startswith("google.genai"):
            return None
        status = getattr(error, "status_code", None)
        if not isinstance(status, int):
            return type(error).__name__
        # The message may echo the input; surface only a known, payload-free code.
        try:
            code = _WireError.model_validate(getattr(error, "body", None)).error.code
        except ValidationError:
            code = None
        if code in _BLOCK_CODES:
            return f"{self.label} blocked the input (HTTP {status}; {code})"
        suffix = f"; {code}" if code in _PRIVATE_CODES else ""
        return f"{type(error).__name__} (HTTP {status}{suffix})"
