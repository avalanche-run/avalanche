"""The OpenAI Decisions provider, through the asynchronous ``openai`` SDK.

Questions go to ``POST /v1/decisions`` as an ordered array without names, and the
answers are matched back by position, so question names never reach the vendor.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from .adapter import ClassifierAdapter, ClassifierConnection, ProbabilityRounding
from .errors import ClassifierStepError, ClassifierStepExecutionError
from .models import (
    Answer,
    ChoiceAnswer,
    ChoiceQuestion,
    ClassificationResult,
    ClassificationUsage,
    Entry,
    JSONContent,
    NoulAnswer,
    NoulQuestion,
    Probability,
    Question,
    ScoreAnswer,
    ScoreQuestion,
)

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from openai.types import decision_create_params as params
    from openai.types.shared_params import DecisionQuestionInputPart

MAX_QUESTIONS = 400
MAX_CHOICES = 255
MIN_CHOICES = 2
MAX_SCORE_LEVELS = 10


class _Wire(BaseModel):
    """The fields Avalanche reads from a Decisions response; others are ignored."""

    model_config = ConfigDict(strict=True, frozen=True, allow_inf_nan=False)


class _PredicateAnswer(_Wire):
    type: Literal["predicate"]
    probability: Probability


class _ChoiceProbability(_Wire):
    # Avalanche only sends text options; a boolean value here is a malformed answer.
    value: str
    probability: Probability


class _ChoiceAnswer(_Wire):
    type: Literal["choice"]
    choice: str
    confidence: Probability
    probabilities: list[_ChoiceProbability]


class _ScoreProbability(_Wire):
    value: Annotated[int, Field(ge=0)]
    probability: Probability


class _ScoreAnswer(_Wire):
    type: Literal["score"]
    score: float
    confidence: Probability
    probabilities: list[_ScoreProbability]


class _Refusal(_Wire):
    type: Literal["refusal"]


class _Usage(_Wire):
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]


class _Decision(_Wire):
    model: Annotated[str, Field(min_length=1)]
    answers: list[
        Annotated[
            _PredicateAnswer | _ChoiceAnswer | _ScoreAnswer | _Refusal,
            Field(discriminator="type"),
        ]
    ]
    usage: _Usage


def _is_objects(value: JSONContent) -> bool:
    return isinstance(value, dict) or all(isinstance(item, dict) for item in value)


def _instructions(entry: Entry) -> str | list[DecisionQuestionInputPart]:
    # The API requires instructions and takes the empty text for a question that has none.
    if entry is None:
        return ""
    if isinstance(entry, str):
        return entry
    return [{"type": "input_json", "data": entry}]


def _question(question: Question) -> params.Question:
    instructions = _instructions(question.instructions)
    if isinstance(question, NoulQuestion):
        return {"type": "predicate", "instructions": instructions}
    if isinstance(question, ChoiceQuestion):
        choices: list[params.QuestionQuestionParamChoiceChoice] = []
        for value, description in question.criteria.items():
            if isinstance(description, str):
                choices.append({"value": value, "description": description})
            else:
                choices.append({"value": value})
        return {"type": "choice", "instructions": instructions, "choices": choices}
    return {
        "type": "score",
        "instructions": instructions,
        "levels": [{"label": level} for level in question.criteria],
    }


def _distinct(pairs: list[tuple[str, float]]) -> dict[str, float]:
    probabilities = dict(pairs)
    if len(probabilities) != len(pairs):
        raise ValueError("an answer repeats an option or a level")
    return probabilities


def _answer(
    question: Question, answer: _PredicateAnswer | _ChoiceAnswer | _ScoreAnswer
) -> Answer:
    if isinstance(question, NoulQuestion) and isinstance(answer, _PredicateAnswer):
        return NoulAnswer(type="noul", noul=answer.probability)
    if isinstance(question, ChoiceQuestion) and isinstance(answer, _ChoiceAnswer):
        return ChoiceAnswer(
            type="choice",
            choice=answer.choice,
            probabilities=_distinct(
                [(item.value, item.probability) for item in answer.probabilities]
            ),
            confidence=answer.confidence,
        )
    if isinstance(question, ScoreQuestion) and isinstance(answer, _ScoreAnswer):
        return ScoreAnswer(
            type="score",
            score=answer.score,
            # The API returns only each level's label; the declared levels are the legend.
            legend={str(index): level for index, level in enumerate(question.criteria)},
            probabilities=_distinct(
                [(str(item.value), item.probability) for item in answer.probabilities]
            ),
            confidence=answer.confidence,
        )
    raise ValueError("an answer does not have the type of its question")


class _OpenAIDecisionsConnection(ClassifierConnection):
    def __init__(
        self, adapter: OpenAIDecisionsAdapter, client: AsyncOpenAI, model: str
    ) -> None:
        self._adapter = adapter
        self._client = client
        self._model = model

    async def classify(
        self, *, state: JSONContent, questions: Mapping[str, Question]
    ) -> ClassificationResult:
        request_input: str | list[params.InputUnionMember1]
        if isinstance(state, str):
            request_input = state
        elif _is_objects(state):
            request_input = [
                {"role": "user", "content": [{"type": "input_json", "data": state}]}
            ]
        else:
            raise ClassifierStepError(
                f"state cannot be sent to {self._adapter.label}: "
                "it takes text, a JSON object or a list of JSON objects"
            )
        response = await self._client.decisions.with_raw_response.create(
            model=self._model,
            input=request_input,
            questions=[_question(question) for question in questions.values()],
        )
        # The SDK builds its response models without validating them, so validate the
        # HTTP body itself, once, into the fields Avalanche reads.
        try:
            decision = _Decision.model_validate_json(response.content)
            if len(decision.answers) != len(questions):
                raise ValueError("the response must answer every question once")
            answers: dict[str, Answer] = {}
            for (name, question), answer in zip(
                questions.items(), decision.answers, strict=True
            ):
                if isinstance(answer, _Refusal):
                    raise ClassifierStepExecutionError(
                        f"{self._adapter.label} refused to answer question {name!r}"
                    )
                answers[name] = _answer(question, answer)
            return ClassificationResult(
                model=decision.model,
                answers=answers,
                usage=ClassificationUsage(
                    input_tokens=decision.usage.input_tokens,
                    output_tokens=decision.usage.output_tokens,
                ),
            )
        except ValueError as error:
            raise self._adapter.invalid_response(error) from None

    async def aclose(self) -> None:
        await self._client.close()


class OpenAIDecisionsAdapter(ClassifierAdapter):
    label: ClassVar[str] = "OpenAI Decisions"
    key_variable: ClassVar[str] = "OPENAI_API_KEY"
    default_model: ClassVar[str] = "gpt-6-luna"
    # The Decisions API returns each probability and score with two decimal places.
    rounding: ClassVar[ProbabilityRounding] = ProbabilityRounding(decimals=2)
    extra: ClassVar[str | None] = "openai"

    def check_questions(self, questions: Mapping[str, Question]) -> None:
        if len(questions) > MAX_QUESTIONS:
            raise ClassifierStepError(
                f"{len(questions)} questions cannot be sent to {self.label}: "
                f"a call takes at most {MAX_QUESTIONS} questions"
            )
        for name, question in questions.items():
            instructions = question.instructions
            if not (
                instructions is None
                or isinstance(instructions, str)
                or _is_objects(instructions)
            ):
                self.refuse(name, "JSON instructions must be an object or a list of objects")
            if isinstance(question, NoulQuestion):
                if question.criteria:
                    self.refuse(
                        name,
                        "a noul takes no true/false criteria; put them in the instructions",
                    )
            elif isinstance(question, ChoiceQuestion):
                if not MIN_CHOICES <= len(question.criteria) <= MAX_CHOICES:
                    self.refuse(name, f"a choice takes {MIN_CHOICES} to {MAX_CHOICES} options")
                for description in question.criteria.values():
                    if not (description is None or isinstance(description, str)):
                        self.refuse(name, "a choice option's criteria must be text")
            else:
                if len(question.criteria) > MAX_SCORE_LEVELS:
                    self.refuse(name, f"a score takes at most {MAX_SCORE_LEVELS} levels")
                for level in question.criteria:
                    if not isinstance(level, str):
                        self.refuse(name, "a score level must be text")

    def connect(self, *, api_key: str, model: str, timeout: float) -> ClassifierConnection:
        import openai

        return _OpenAIDecisionsConnection(
            self, openai.AsyncOpenAI(api_key=api_key, timeout=timeout), model
        )

    def describe_error(self, error: Exception) -> str | None:
        import openai

        # The API's message can echo request content, so name only the error's
        # class and HTTP status, never its message or body.
        if isinstance(error, openai.APIStatusError):
            return f"OpenAI {type(error).__name__} (HTTP {error.status_code})"
        if isinstance(error, openai.OpenAIError):
            return f"OpenAI {type(error).__name__}"
        return None
