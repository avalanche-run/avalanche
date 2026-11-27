# Classifier steps

A classifier step asks a decision model fixed questions about some state and gets typed
answers with probabilities: a choice among options, a yes/no, or a score on a scale.
One request, no agent loop, no tools.

## When to use it

- Routing, triage, tagging, gating: the answer is one of a few options, a yes/no, or a level.
- You need a probability or confidence to compare with a threshold in code.
- Many small judgments per run: it is one cheap call, and one call can carry many questions.

A classifier beats an agent step when the answer space is closed and the evidence fits in
one request. Use something else when:

| Need | Use | File |
|---|---|---|
| Free text, extraction into a rich object, tool use, multi-step reasoning | `@ava.agent_step` | [agent-steps.md](agent-steps.md) |
| A rule you can write in code | `@ava.step` | [python-steps.md](python-steps.md) |
| Scoring an agent step's quality without changing the flow | `ava.Evaluations` | [evaluations.md](evaluations.md) |
| Branching on the answer | A classifier step, then branch | [../orchestration/control-flow.md](../orchestration/control-flow.md) |

## Interface

### `@ava.classifier_step`

```python
ava.classifier_step(
    *,
    questions=None,
    input_model=None,
    classifier=None,
    model=None,
    timeout=None,
    slug=None,
    retries=7,
    on_uncertain="retry",
)
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `questions` | `dict[str, dict]` | `None` | Default questions, by name. `None`: every call must pass `questions=`. |
| `input_model` | Pydantic model class | `None` | Schema of the state. Each call's state is validated against it and the schema is shown in the UI. |
| `classifier` | `ava.classifiers.TypeSafe()`, `OpenAIDecisions()`, `GeminiDecision()` | `None` | Provider. `None`: the workflow's `classifier_defaults`, else TypeSafe. |
| `model` | `str` | `None` | One of the provider's models. `None`: workflow default, else the provider's default model. |
| `timeout` | `float > 0` | `None` | Seconds per request. `None`: workflow default, else `10.0`. |
| `slug` | `str` | `None` | Stable step slug; see [workflows.md](workflows.md). |
| `retries` | `int >= 0` | `7` | Extra executions of the step after a failure (8 attempts at most). |
| `on_uncertain` | `"retry"`, `"hold"`, `"fail"` | `"retry"` | Policy when the step raises `ava.OutcomeUnknownError`; see [../orchestration/retries.md](../orchestration/retries.md). |

All arguments are keyword-only. Needs the provider's extra (table below).

### The step function

```python
@ava.classifier_step(questions={...})
async def route_ticket(ticket: Ticket, *, classifier: ava.Classifier) -> Routing:
    """One-line docstring."""
    result = await classifier(state=ticket.model_dump(mode="json"))
    return ...
```

- `classifier` must be keyword-only, annotated exactly `ava.Classifier`, with no default. It is injected.
- `await classifier(*, state, questions=None)` returns an `ava.ClassificationResult`.
- `state`: what the questions are about. A `str`, or a JSON `dict` / `list` (plain JSON values only).
- `questions`: replaces the declared questions for this call. Use it for questions built at run time.
- The classifier may be called several times in one step; each call is recorded.
- Return anything: the whole `ClassificationResult`, or your own typed value built from it.

### Question types

Each question is a dict with a `type`. `instructions` and criteria entries are text, or a JSON object or array.

| `type` | Shape | Answer |
|---|---|---|
| `"choice"` | `{"type": "choice", "instructions": ..., "criteria": {"<option>": "<what it means>" \| None, ...}}` | One of the options, with a probability per option. |
| `"noul"` (yes / no / unclear) | `{"type": "noul", "instructions": ..., "criteria": None \| {"true": "...", "false": "..."}}` | `noul`: probability from 0 (no) to 1 (yes); near 0.5 is unclear. |
| `"score"` | `{"type": "score", "instructions": ..., "criteria": ["<level 0>", "<level 1>", ...]}` | `score`: a float from 0 to N-1 over the N levels. |

- `instructions` is optional on TypeSafe and OpenAI Decisions, required on Gemini Decision.
- `"choice"` needs at least one option (two on OpenAI Decisions and Gemini Decision). `"score"` needs at least two levels, lowest first.
- No other keys are allowed in a question.

### `ava.ClassificationResult`

| Field | Meaning |
|---|---|
| `model` | The model that answered. |
| `answers` | `{question name: answer}`, exactly the questions asked. |
| `usage` | `input_tokens`, `output_tokens` (each may be `None`). |
| `choices`, `nouls`, `scores` | The answers of that type, by question name. |

| Answer | Fields |
|---|---|
| Choice (`result.choices[name]`) | `choice` (the most probable option), `probabilities` (`{option: p}`, sum 1), `confidence` (0 to 1). |
| Noul (`result.nouls[name]`) | `noul` (0 to 1). |
| Score (`result.scores[name]`) | `score` (probability-weighted level, 0 to N-1), `legend` (`{"0": level, ...}`), `probabilities` (`{"0": p, ...}`), `confidence` (0 to 1). |

It is a Pydantic model: a step may return it as is.

### Providers

| | `ava.classifiers.TypeSafe()` | `ava.classifiers.OpenAIDecisions()` | `ava.classifiers.GeminiDecision()` |
|---|---|---|---|
| Service | TypeSafe decision service | OpenAI Decisions API | Gemini Decision (beta, Gemini Developer API) |
| Extra | `avalanche-ai[typesafe]` | `avalanche-ai[openai]` | `avalanche-ai[gemini]` |
| Key variable | `TYPESAFE_API_KEY` | `OPENAI_API_KEY` | `GEMINI_API_KEY` |
| Default model | `jev-latest` | `gpt-6-luna` | `gemini-decision-1` |
| Questions per call | — | at most 400 | at most 1000 |
| `instructions` | text or JSON, optional | text, a JSON object or a list of objects; optional | text, required |
| Choice | any options | 2 to 255 options, text criteria | 2 to 255 options, text criteria; option name at most 128 characters, no control characters or surrounding spaces |
| Noul criteria | `"true"` / `"false"` allowed | not allowed: put them in the instructions | `"true"` / `"false"` text allowed |
| Score | 2+ levels | at most 10 text levels | at most 255 non-empty text levels |
| State | text or JSON | text, a JSON object or a list of JSON objects | text or JSON |
| Probabilities rounded to | 2 decimals | 2 decimals | not rounded |

Providers take no arguments. Different providers give different probabilities for the same
question: name one provider per workflow rather than picking by which key is present.

The key is read from the environment of the process that runs the step; when the variable is
absent, the nearest `.env` file (searched upward from the working directory) is loaded.

### Workflow defaults

```python
@ava.workflow(classifier_defaults={"classifier": ava.classifiers.TypeSafe(), "model": "jev-latest", "timeout": 30.0})
```

Keys: `classifier`, `model`, `timeout`; all optional, none may be `None`. They also apply to
`ava.Evaluations`. A step's own setting wins. A `model` set for one provider is never applied
to another: a step that sets `classifier=` without `model=` gets that provider's default model.

### Errors

| Error | Base | When |
|---|---|---|
| `ava.ClassifierStepError` | `RuntimeError` | Invalid declaration: bad questions or settings, wrong `classifier` parameter, a question the chosen provider cannot take. Mostly raised when the module loads. |
| `ava.ClassifierStepExecutionError` | `RuntimeError` | A call failed: message `classifier step '<name>' failed: <reason>`. Covers a missing key, a missing extra, a provider error, a refused or blocked input, an invalid response, and an invalid `state` or `questions` passed to the call. |

Messages never contain the state, the questions or the provider's reply, only an error type or
status: `invalid classifier declaration: ValidationError`, `<ErrorType> (HTTP <status>)`.

### `ava.Classifier` outside a step

Normally you never build one: the step injects it and closes it. For a script or a test,
build it from a declaration and close it yourself. Nothing is recorded in a run.

```python
import asyncio

import avalanche as ava
from avalanche.classifier import Classifier, ClassifierDeclaration, ClassifierRuntime


async def ask_refund(text: str) -> ava.ClassificationResult:
    classifier = Classifier(
        declaration=ClassifierDeclaration(questions=None, runtime=ClassifierRuntime(timeout=20.0)),
        step_name="adhoc",
    )
    try:
        return await classifier(
            state=text,
            questions={
                "refund": {"type": "noul", "instructions": "Is the customer asking for a refund?"}
            },
        )
    finally:
        await classifier.aclose()


if __name__ == "__main__":
    print(asyncio.run(ask_refund("Charged twice this month.")).nouls["refund"].noul)
```

`ClassifierRuntime(classifier=TypeSafe(), model="jev-latest", timeout=10.0)` are the defaults.

## Example

```python
from typing import Literal

from pydantic import BaseModel

import avalanche as ava


class Ticket(BaseModel):
    subject: str
    body: str


class Routing(BaseModel):
    team: Literal["billing", "technical"]
    team_confidence: float
    urgent: bool
    severity: float


@ava.source
def load_ticket() -> Ticket:
    """Load one sample support ticket."""
    return Ticket(subject="Charged twice", body="Two charges this month. Please fix today.")


@ava.classifier_step(
    questions={
        "team": {
            "type": "choice",
            "instructions": "Which support team should handle this ticket?",
            "criteria": {
                "billing": "Charges, invoices and refunds.",
                "technical": "Bugs, outages and how-to questions.",
            },
        },
        "urgent": {
            "type": "noul",
            "instructions": "Does the customer need an answer today?",
        },
        "severity": {
            "type": "score",
            "instructions": "How severe is the impact on the customer?",
            "criteria": ["No real impact", "Degraded but usable", "Blocked"],
        },
    },
    input_model=Ticket,
    timeout=20.0,
)
async def route_ticket(ticket: Ticket, *, classifier: ava.Classifier) -> Routing:
    """Pick the team, urgency and severity of a ticket."""
    result = await classifier(state=ticket.model_dump(mode="json"))
    team = result.choices["team"]
    return Routing(
        team=team.choice,
        team_confidence=team.confidence,
        urgent=result.nouls["urgent"].noul >= 0.5,
        severity=result.scores["severity"].score,
    )


@ava.workflow
def ticket_routing():
    """Route a support ticket with one classifier call."""
    return load_ticket() >> route_ticket()
```

These questions are valid for all three providers. To use another provider, add
`classifier=ava.classifiers.OpenAIDecisions()` to the decorator.

## Rules

- Declare `*, classifier: ava.Classifier`. Otherwise `ClassifierStepError: classifier step '...' requires a keyword-only classifier parameter` / `classifier parameter must be annotated ava.Classifier` / `classifier is framework-injected and cannot have a default`.
- Make the function `async` and `await classifier(...)`.
- Pass `state=` by keyword, as `str`, `dict` or `list`. A Pydantic model is rejected: use `model.model_dump(mode="json")`. The failure reads only `classifier step '...' failed: ValidationError`.
- Questions must exist at call time: `classifier step '...' failed: questions must be provided on the classifier call or as classifier_step defaults`.
- A wrong question shape fails at import with `ClassifierStepError: invalid classifier declaration: ValidationError`, without naming the key. Check: `type` is `choice`/`noul`/`score`; choice `criteria` is a dict; score `criteria` is a list of 2+; noul `criteria` keys are only `"true"`/`"false"`; no extra keys.
- Provider limits are checked at import when the step names its provider, e.g. `question 'q' cannot be sent to OpenAI Decisions: a score takes at most 10 levels`, `... Gemini Decision: instructions are required`. For portable questions: text instructions on every question, text criteria, no noul criteria, at most 10 score levels.
- Do not pass `None` for a setting; leave it out. `classifier_defaults={"model": None}` raises `classifier settings cannot be None; omit them instead`.
- `noul` is a probability, not a boolean. Choose the threshold in code (`>= 0.5`, or stricter) and handle the unclear middle when it matters.
- A score is a float between levels (1.5 means between level 1 and 2). Compare with thresholds; do not index the legend with it.
- Use option names that are valid identifiers of your domain (`"billing"`); `choice` returns the name, which you can type as a `Literal`.
- Every failure is retried, including a missing key: with the default `retries=7` the step fails after 8 attempts with `TYPESAFE_API_KEY is required to call a classifier`.
- Do not use the classifier after the step returns: `ClassifierStepError: classifier cannot be used after its step has finished`.
- Write instructions as a question about the state, and criteria as what each option or level means. Do not put the state in the instructions.

## In the UI

- The node shows the function name and the first line of its docstring.
- Workflow view: "Code", "Definition" (each question with its instructions and criteria, the decision service, model and timeout) and "Step interface".
- Run view: each classifier call with its input state, the questions asked, and every answer with its probabilities, plus status and error. Question names and option names are shown as written.
- Importing or discovering a workflow needs no key and makes no network call.

## See also

- [agent-steps.md](agent-steps.md): when the task needs an agent.
- [evaluations.md](evaluations.md): the same questions used to score agent steps.
- [../orchestration/control-flow.md](../orchestration/control-flow.md): branching on an answer.
- [../orchestration/retries.md](../orchestration/retries.md): retry and uncertainty policy.
- [../reference/errors.md](../reference/errors.md): all errors.
