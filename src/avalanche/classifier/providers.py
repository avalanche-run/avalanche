"""The closed set of classifier providers, passed as ``classifier=``.

A provider is the vendor's decision service. The model is a separate setting, so
one provider value can be combined with any of that vendor's models. Each value is
frozen and JSON-serializable: it travels in classifier and evaluation declarations.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Provider(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class TypeSafe(_Provider):
    """TypeSafe's decision service. Default model ``jev-latest``; key ``TYPESAFE_API_KEY``."""

    kind: Literal["typesafe"] = "typesafe"


class OpenAIDecisions(_Provider):
    """OpenAI's Decisions API. Default model ``gpt-6-luna``; key ``OPENAI_API_KEY``.

    Needs the ``openai`` extra.
    """

    kind: Literal["openai_decisions"] = "openai_decisions"


class GeminiDecision(_Provider):
    """Google's Gemini Decision service, in beta on the Gemini Developer API.

    Default model ``gemini-decision-1``; key ``GEMINI_API_KEY``; needs the ``gemini`` extra.
    """

    kind: Literal["gemini_decision"] = "gemini_decision"


ClassifierProvider = Annotated[
    TypeSafe | OpenAIDecisions | GeminiDecision, Field(discriminator="kind")
]
