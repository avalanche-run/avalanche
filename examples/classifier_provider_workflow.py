"""Classify a support ticket with a provider other than the default.

The provider is chosen in code, when this file loads, from the keys this machine has:

1. OpenAI Decisions, when ``OPENAI_API_KEY`` is set (needs the ``openai`` extra).
2. Gemini Decision, when ``GEMINI_API_KEY`` is set (needs the ``gemini`` extra).
3. TypeSafe, the default provider, otherwise (needs ``TYPESAFE_API_KEY`` to run).

Keys are read from the environment or the project ``.env``. Run it with:
    uv run python examples/classifier_provider_workflow.py
    uv run ava dev examples/classifier_provider_workflow.py

Importing or discovering this workflow needs no key and makes no network call. The
operator UI shows the chosen provider beside the model, on the step and on each call.

Different providers give different probabilities for the same question. A production
flow should name one provider, so a missing key fails the run instead of changing who
answers. This example picks one only so that it runs with whichever key you have.
"""

from __future__ import annotations

import os

from dotenv import find_dotenv, load_dotenv

import avalanche as ava

type ClassifierProvider = (
    ava.classifiers.OpenAIDecisions | ava.classifiers.GeminiDecision | ava.classifiers.TypeSafe
)


def select_classifier() -> ClassifierProvider:
    """Return the first provider whose key is set, else TypeSafe."""
    # Classifier calls load the project .env the same way, without overriding
    # exported values.
    load_dotenv(find_dotenv(usecwd=True), override=False)
    if os.environ.get("OPENAI_API_KEY"):
        return ava.classifiers.OpenAIDecisions()
    if os.environ.get("GEMINI_API_KEY"):
        return ava.classifiers.GeminiDecision()
    return ava.classifiers.TypeSafe()


PROVIDER = select_classifier()

# Every provider takes these shapes: text instructions on each question (Gemini
# Decision requires them), text criteria, and a score of at most 10 levels (the
# OpenAI Decisions limit).
QUESTIONS = {
    "department": {
        "type": "choice",
        "instructions": "Which support team should handle this ticket?",
        "criteria": {
            "billing": "Charges, invoices, and refunds.",
            "technical": "Software bugs and service issues.",
        },
    },
    "urgent": {
        "type": "noul",
        "instructions": "Does the ticket require action today?",
    },
    "severity": {
        "type": "score",
        "instructions": "How severe is the customer impact?",
        "criteria": ["Minor inconvenience", "Degraded service", "Blocked customer"],
    },
}


@ava.source
def load_ticket() -> str:
    """Load a sample customer support ticket."""
    return (
        "Our account was charged twice for this month's subscription. "
        "Please reverse the duplicate charge today; we can still use the service."
    )


# No model= here: the step takes the chosen provider's default model.
@ava.classifier_step(questions=QUESTIONS, classifier=PROVIDER)
async def classify_ticket(
    ticket: str, *, classifier: ava.Classifier
) -> ava.ClassificationResult:
    """Return the typed answers; the same result type for every provider."""
    return await classifier(state=ticket)


@ava.workflow
def classifier_provider_workflow():
    return load_ticket() >> classify_ticket()


def _main() -> None:
    result = classifier_provider_workflow().run(executor=ava.LocalExecutor()).result()
    print(f"provider: {PROVIDER.kind}")
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    _main()
