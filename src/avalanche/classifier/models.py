"""Validated, JSON-serializable declarations and results for classifier steps."""

from __future__ import annotations

import math
from typing import Annotated, Literal, Self, TypedDict

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)

from ..step_interface import StepInput, StepOutput
from .providers import ClassifierProvider, TypeSafe


def _validate_json(value: object, ancestors: set[int] | None = None) -> object:
    """Reject Python-only values instead of silently JSON-coercing them."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if type(value) not in (list, dict):
        raise ValueError("Expected a JSON-compatible value")
    ancestors = set() if ancestors is None else ancestors
    identity = id(value)
    if identity in ancestors:
        raise ValueError("JSON values cannot contain circular references")
    ancestors.add(identity)
    try:
        if isinstance(value, dict):
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError("JSON object keys must be strings")
                _validate_json(item, ancestors)
        elif isinstance(value, list):
            for item in value:
                _validate_json(item, ancestors)
    finally:
        ancestors.remove(identity)
    return value


def _validate_content(value: object) -> object:
    if type(value) not in (str, dict, list):
        raise ValueError("Expected text, a JSON object, or a JSON array")
    return _validate_json(value)


JSONContent = Annotated[
    str | dict[str, JsonValue] | list[JsonValue], BeforeValidator(_validate_content)
]
Entry = JSONContent | None
NonemptyString = Annotated[str, Field(min_length=1)]
Probability = Annotated[float, Field(ge=0, le=1)]


class _ClassifierModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class ChoiceQuestion(_ClassifierModel):
    type: Literal["choice"]
    instructions: Entry = None
    criteria: Annotated[dict[NonemptyString, Entry], Field(min_length=1)]


class NoulQuestion(_ClassifierModel):
    type: Literal["noul"]
    instructions: Entry = None
    criteria: dict[Literal["true", "false"], Entry] | None = None


class ScoreQuestion(_ClassifierModel):
    type: Literal["score"]
    instructions: Entry = None
    criteria: Annotated[list[JSONContent], Field(min_length=2)]


Question = Annotated[ChoiceQuestion | NoulQuestion | ScoreQuestion, Field(discriminator="type")]
Questions = Annotated[dict[NonemptyString, Question], Field(min_length=1)]
_QUESTIONS = TypeAdapter(Questions, config=ConfigDict(strict=True))
_STATE = TypeAdapter(JSONContent, config=ConfigDict(strict=True, allow_inf_nan=False))


def validate_questions(value: object) -> dict[str, Question]:
    _validate_json(value)
    return _QUESTIONS.validate_python(value)


def validate_state(value: object) -> JSONContent:
    return _STATE.validate_python(value)


class ClassifierDefaults(TypedDict, total=False):
    """The keys of ``@ava.workflow(classifier_defaults={...})``."""

    classifier: ClassifierProvider
    model: str
    timeout: float


class ClassifierSettings(_ClassifierModel):
    """What one level sets: a step, an ``Evaluations`` declaration or workflow defaults."""

    classifier: ClassifierProvider | None = None
    model: NonemptyString | None = None
    timeout: Annotated[float, Field(gt=0)] | None = None

    @field_validator("model")
    @classmethod
    def meaningful_model(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("model must not be blank")
        return value

    @model_validator(mode="before")
    @classmethod
    def no_explicit_none(cls, value: object) -> object:
        # Leaving a setting out means "not set here"; None is not a setting.
        if isinstance(value, dict) and any(item is None for item in value.values()):
            raise ValueError("classifier settings cannot be None; omit them instead")
        return value


class ClassifierRuntime(_ClassifierModel):
    """The resolved provider, model and timeout of one classifier step or evaluation."""

    # Declarations recorded before providers existed name no classifier and were
    # all TypeSafe calls, so these defaults are the TypeSafe ones.
    classifier: ClassifierProvider = Field(default_factory=TypeSafe)
    model: NonemptyString = "jev-latest"
    # typesafe-sdk 0.6.0's documented per-HTTP-operation timeout, in seconds.
    timeout: Annotated[float, Field(gt=0)] = 10.0

    @field_validator("model")
    @classmethod
    def meaningful_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model must not be blank")
        return value


def validate_runtime_defaults(value: object) -> dict[str, JsonValue]:
    """Validate only the supported settings, retaining explicit ones as owned JSON."""
    settings = ClassifierSettings.model_validate(value)
    return settings.model_dump(mode="json", exclude_none=True)


def resolve_runtime(*levels: ClassifierSettings) -> ClassifierRuntime:
    """Resolve settings given from the lowest level (workflow) to the highest (step, eval).

    The highest level that sets a classifier decides it, and its model comes from
    that same level or from a higher one, else from the classifier's default model:
    a model set below for another classifier is never applied to it. Without any
    classifier the provider is TypeSafe and the highest model wins. The timeout
    resolves on its own.
    """
    from .adapter import adapter_for

    classifier: ClassifierProvider | None = None
    model: str | None = None
    timeout: float | None = None
    for level in levels:
        if level.classifier is not None:
            classifier = level.classifier
            model = level.model
        elif level.model is not None:
            model = level.model
        if level.timeout is not None:
            timeout = level.timeout
    if classifier is None:
        classifier = TypeSafe()
    resolved: dict[str, object] = {
        "classifier": classifier,
        "model": model if model is not None else adapter_for(classifier).default_model,
    }
    if timeout is not None:
        resolved["timeout"] = timeout
    return ClassifierRuntime.model_validate(resolved)


class ClassifierDeclaration(_ClassifierModel):
    questions: Questions | None
    runtime: ClassifierRuntime
    input_schema: dict[str, JsonValue] | None = None
    step_inputs: list[StepInput] = Field(default_factory=list)
    step_output: StepOutput = Field(default_factory=StepOutput)


class ClassificationUsage(_ClassifierModel):
    input_tokens: Annotated[int, Field(ge=0)] | None = None
    output_tokens: Annotated[int, Field(ge=0)] | None = None


def _validate_distribution(probabilities: dict[str, float], tolerance: float) -> None:
    # Bound accumulated rounding error without renormalizing the original evidence.
    total = math.fsum(probabilities.values())
    if total <= 0 or not math.isclose(total, 1.0, rel_tol=0, abs_tol=tolerance):
        raise ValueError("probabilities must sum to 1 within rounding precision")


class ChoiceAnswer(_ClassifierModel):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, Probability]
    confidence: Probability

    @model_validator(mode="after")
    def valid_distribution(self) -> Self:
        if self.choice not in self.probabilities:
            raise ValueError("choice must identify a returned option")
        if not math.isclose(
            self.probabilities[self.choice],
            max(self.probabilities.values()),
            rel_tol=1e-5,
            abs_tol=1e-5,
        ):
            raise ValueError("choice must identify a highest-probability option")
        return self


class NoulAnswer(_ClassifierModel):
    type: Literal["noul"]
    noul: Probability


class ScoreAnswer(_ClassifierModel):
    type: Literal["score"]
    score: float
    legend: Annotated[dict[str, JSONContent], Field(min_length=2)]
    probabilities: dict[str, Probability]
    confidence: Probability

    @model_validator(mode="after")
    def valid_distribution(self) -> Self:
        levels = {str(index) for index in range(len(self.legend))}
        if set(self.legend) != levels or set(self.probabilities) != levels:
            raise ValueError(
                "score legend and probabilities must contain every zero-based level"
            )
        if not 0 <= self.score <= len(levels) - 1:
            raise ValueError("score must be within the declared levels")
        return self


Answer = Annotated[ChoiceAnswer | NoulAnswer | ScoreAnswer, Field(discriminator="type")]


def _same_json(left: object, right: object) -> bool:
    """Compare structured rubric values without Python's bool/int equivalence."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same_json(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_json(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


class ClassificationResult(_ClassifierModel):
    model: NonemptyString
    answers: Annotated[dict[str, Answer], Field(min_length=1)]
    usage: ClassificationUsage

    @property
    def choices(self) -> dict[str, ChoiceAnswer]:
        return {
            key: answer
            for key, answer in self.answers.items()
            if isinstance(answer, ChoiceAnswer)
        }

    @property
    def nouls(self) -> dict[str, NoulAnswer]:
        return {
            key: answer
            for key, answer in self.answers.items()
            if isinstance(answer, NoulAnswer)
        }

    @property
    def scores(self) -> dict[str, ScoreAnswer]:
        return {
            key: answer
            for key, answer in self.answers.items()
            if isinstance(answer, ScoreAnswer)
        }

    def validate_declaration(self, declaration: ClassifierDeclaration) -> None:
        """Check the answers against the declared questions and the provider's rounding.

        How far probabilities may be from summing to 1, and a score from its
        weighted levels, depends on how the declared provider rounds them.
        """
        from .adapter import adapter_for

        if declaration.questions is None:
            raise ValueError("successful classifications require resolved questions")
        if self.answers.keys() != declaration.questions.keys():
            raise ValueError("answers must match exactly the declared question IDs")
        rounding = adapter_for(declaration.runtime.classifier).rounding
        for name, question in declaration.questions.items():
            answer = self.answers[name]
            if answer.type != question.type:
                raise ValueError(f"answer {name!r} must have type {question.type!r}")
            if isinstance(question, ChoiceQuestion) and isinstance(answer, ChoiceAnswer):
                if answer.probabilities.keys() != question.criteria.keys():
                    raise ValueError(
                        f"answer {name!r} must contain exactly the declared options"
                    )
                _validate_distribution(
                    answer.probabilities,
                    rounding.distribution_tolerance(len(answer.probabilities)),
                )
            elif isinstance(question, ScoreQuestion) and isinstance(answer, ScoreAnswer):
                legend = {str(index): entry for index, entry in enumerate(question.criteria)}
                if not _same_json(answer.legend, legend):
                    raise ValueError(f"answer {name!r} must preserve the declared score levels")
                levels = len(legend)
                _validate_distribution(
                    answer.probabilities, rounding.distribution_tolerance(levels)
                )
                expected = sum(
                    index * answer.probabilities[str(index)] for index in range(levels)
                )
                if not math.isclose(
                    answer.score,
                    expected,
                    rel_tol=0,
                    abs_tol=rounding.score_tolerance(levels),
                ):
                    raise ValueError("score must equal the probability-weighted level")


class ClassifierInvocation(_ClassifierModel):
    invocation_id: NonemptyString
    invocation_index: Annotated[int, Field(ge=0)]
    status: Literal["running", "success", "failed", "cancelled"]
    started_at: float
    ended_at: float | None
    declaration: ClassifierDeclaration
    input: JSONContent | None
    result: ClassificationResult | None
    error: str | None

    @model_validator(mode="after")
    def valid_lifecycle(self) -> Self:
        if self.status == "running":
            if self.ended_at is not None or self.result is not None or self.error is not None:
                raise ValueError("running invocations cannot contain terminal evidence")
        else:
            if self.ended_at is None or self.ended_at < self.started_at:
                raise ValueError("terminal invocations require an end time after their start")
            if self.status == "success":
                if self.result is None or self.error is not None:
                    raise ValueError("successful invocations require a result and no error")
                self.result.validate_declaration(self.declaration)
            elif self.result is not None or not self.error:
                raise ValueError(
                    "failed or cancelled invocations require an error and no result"
                )
        return self
