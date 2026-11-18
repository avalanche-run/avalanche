"""The TypeSafe provider, through the process-local asynchronous TypeSafe SDK."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, ClassVar

from .adapter import ClassifierAdapter, ClassifierConnection, ProbabilityRounding
from .models import (
    ChoiceQuestion,
    ClassificationResult,
    JSONContent,
    NoulQuestion,
    Question,
)

if TYPE_CHECKING:
    from typesafe_sdk import AsyncTypeSafeClient


class _TypeSafeConnection(ClassifierConnection):
    def __init__(self, adapter: TypeSafeAdapter, client: AsyncTypeSafeClient) -> None:
        self._adapter = adapter
        self._client = client

    async def classify(
        self, *, state: JSONContent, questions: Mapping[str, Question]
    ) -> ClassificationResult:
        import typesafe_sdk
        from typesafe_sdk import Choice, Noul, NoulCriteria, Score

        sdk_questions: dict[str, typesafe_sdk.Question] = {}
        for name, question in questions.items():
            if isinstance(question, ChoiceQuestion):
                sdk_questions[name] = Choice(
                    instructions=question.instructions, criteria=question.criteria
                )
            elif isinstance(question, NoulQuestion):
                criteria: NoulCriteria | None = None
                if question.criteria is not None:
                    criteria = NoulCriteria()
                    if "true" in question.criteria:
                        criteria["true"] = question.criteria["true"]
                    if "false" in question.criteria:
                        criteria["false"] = question.criteria["false"]
                sdk_questions[name] = Noul(
                    instructions=question.instructions, criteria=criteria
                )
            else:
                sdk_questions[name] = Score(
                    instructions=question.instructions, criteria=question.criteria
                )
        response = await self._client.system_one(state=state, questions=sdk_questions)
        # The SDK exposes the original HTTP body on its typed response. Validate that
        # boundary directly: SDK decoding intentionally drops unknown answer types and
        # normalizes integer Score keys, which must not conceal malformed responses.
        try:
            return ClassificationResult.model_validate_json(response.raw_http_response.content)
        except ValueError as error:
            raise self._adapter.invalid_response(error) from None

    async def aclose(self) -> None:
        await self._client.aclose()


class TypeSafeAdapter(ClassifierAdapter):
    label: ClassVar[str] = "TypeSafe"
    key_variable: ClassVar[str] = "TYPESAFE_API_KEY"
    default_model: ClassVar[str] = "jev-latest"
    # TypeSafe rounds each probability and the final score to two decimal places.
    rounding: ClassVar[ProbabilityRounding] = ProbabilityRounding(decimals=2)

    def connect(self, *, api_key: str, model: str, timeout: float) -> ClassifierConnection:
        import typesafe_sdk

        return _TypeSafeConnection(
            self,
            typesafe_sdk.AsyncTypeSafeClient(api_key=api_key, model=model, timeout=timeout),
        )

    def describe_error(self, error: Exception) -> str | None:
        from typesafe_sdk import TypeSafeAPIError

        if not isinstance(error, TypeSafeAPIError):
            return None
        # The service may echo state in its message; surface only this known,
        # payload-free machine code, never the response body.
        detail = error.body.get("detail") if isinstance(error.body, dict) else None
        suffix = (
            "; max_tokens_exceeded"
            if isinstance(detail, dict) and detail.get("error_type") == "max_tokens_exceeded"
            else ""
        )
        return f"{type(error).__name__} (HTTP {error.status}{suffix})"
