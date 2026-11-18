"""The internal seam between classifier calls and a provider's decision service.

``Classifier`` and ``Evaluations`` reach a vendor only through this module. One
adapter per provider owns everything vendor-specific: its key variable, default
model, the questions it can take, its client, its response validation, its
probability rounding and its payload-free error descriptions.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, NoReturn

from .errors import ClassifierStepError, ClassifierStepExecutionError
from .providers import ClassifierProvider, GeminiDecision, OpenAIDecisions, TypeSafe

if TYPE_CHECKING:
    from .models import (
        ClassificationResult,
        ClassifierDeclaration,
        ClassifierRuntime,
        JSONContent,
        Question,
    )


@dataclass(frozen=True)
class ProbabilityRounding:
    """How coarsely a provider rounds each probability and score it returns."""

    decimals: int

    @property
    def _half_unit(self) -> float:
        return 0.5 / 10**self.decimals

    def distribution_tolerance(self, count: int) -> float:
        """Bound the error of ``count`` independently rounded probabilities summing to 1."""
        return self._half_unit * count + 1e-12

    def score_tolerance(self, levels: int) -> float:
        """Bound the error between a rounded score and its rounded, level-weighted parts."""
        return self._half_unit * (1 + levels * (levels - 1) / 2) + 1e-12


class ClassifierConnection(ABC):
    """One step-owned client of a provider, bound to a model and a timeout."""

    @abstractmethod
    async def classify(
        self, *, state: JSONContent, questions: Mapping[str, Question]
    ) -> ClassificationResult:
        """Ask the questions about the state and validate the vendor's answer once."""

    @abstractmethod
    async def aclose(self) -> None:
        """Release the client's network resources."""


class ClassifierAdapter(ABC):
    """Everything Avalanche needs to know about one provider."""

    label: ClassVar[str]
    key_variable: ClassVar[str]
    default_model: ClassVar[str]
    rounding: ClassVar[ProbabilityRounding]
    # The avalanche-ai extra that installs the vendor package; None for a base dependency.
    extra: ClassVar[str | None] = None

    def check_questions(self, questions: Mapping[str, Question]) -> None:
        """Refuse, through ``refuse``, any question this provider cannot take."""

    def refuse(self, question: str, limit: str) -> NoReturn:
        raise ClassifierStepError(
            f"question {question!r} cannot be sent to {self.label}: {limit}"
        )

    def invalid_response(self, error: BaseException) -> ClassifierStepExecutionError:
        # Validation messages and locations can contain answer or input values.
        return ClassifierStepExecutionError(
            f"Invalid {self.label} response: {type(error).__name__}"
        )

    @abstractmethod
    def connect(self, *, api_key: str, model: str, timeout: float) -> ClassifierConnection:
        """Build the vendor client. Runs only in the process that makes the call."""

    @abstractmethod
    def describe_error(self, error: Exception) -> str | None:
        """Describe a vendor failure without request state, keys or echoed bodies.

        Return None when the error is not this vendor's; its type name is used then.
        """


def adapter_for(provider: ClassifierProvider) -> ClassifierAdapter:
    """Return the adapter of a provider, without importing any vendor package."""
    if isinstance(provider, TypeSafe):
        from .typesafe import TypeSafeAdapter

        return TypeSafeAdapter()
    if isinstance(provider, OpenAIDecisions):
        from .openai_decisions import OpenAIDecisionsAdapter

        return OpenAIDecisionsAdapter()
    if isinstance(provider, GeminiDecision):
        from .gemini import GeminiDecisionAdapter

        return GeminiDecisionAdapter()
    raise ClassifierStepError("unknown classifier provider")


def provider_key(adapter: ClassifierAdapter) -> str:
    """Read the provider's key from the environment, else from the nearest ``.env``."""
    if adapter.key_variable not in os.environ:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True), override=False)

    api_key = os.environ.get(adapter.key_variable, "")
    if not api_key.strip():
        raise ClassifierStepExecutionError(
            f"{adapter.key_variable} is required to call a classifier"
        )
    return api_key


def connect(adapter: ClassifierAdapter, runtime: ClassifierRuntime) -> ClassifierConnection:
    """Resolve the key and build the provider's client inside the executing process."""
    api_key = provider_key(adapter)
    try:
        return adapter.connect(api_key=api_key, model=runtime.model, timeout=runtime.timeout)
    except ModuleNotFoundError:
        if adapter.extra is None:
            raise
        raise ClassifierStepExecutionError(
            f"{adapter.label} needs the {adapter.extra!r} extra: "
            f"install avalanche-ai[{adapter.extra}]"
        ) from None


async def classify(
    adapter: ClassifierAdapter,
    connection: ClassifierConnection,
    *,
    state: JSONContent,
    declaration: ClassifierDeclaration,
) -> ClassificationResult:
    """Call the provider and return an answer that matches the declared questions."""
    if declaration.questions is None:
        raise ClassifierStepError("a classifier call requires resolved questions")
    try:
        result = await connection.classify(state=state, questions=declaration.questions)
    except (ClassifierStepError, ClassifierStepExecutionError):
        raise
    except Exception as error:
        description = adapter.describe_error(error) or type(error).__name__
        raise ClassifierStepExecutionError(description) from None
    try:
        result.validate_declaration(declaration)
    except ValueError as error:
        raise adapter.invalid_response(error) from None
    return result
