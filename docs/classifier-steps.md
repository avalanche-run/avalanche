# Classifier steps

For all decorator parameters, question/result fields, and errors, see the
[classifier API reference](api/classifiers.md).
See the [package API index](api/README.md) for other interfaces.

`@ava.classifier_step` is an ordinary Avalanche step with typed questions and
an injected, awaitable `ava.Classifier`. The questions are answered by a
decision service, the classifier's **provider**: [TypeSafe](https://typesafe.ai/)
by default. Questions can be decorator defaults or supplied per call.
Its Python body prepares state and questions, calls the classifier, and
chooses what to return or persist. It is not an agent loop: there are no tools,
automatic routes, confidence thresholds, or implicit table writes.

TypeSafe support is included in the base Avalanche installation through
`typesafe-sdk`; no optional extra is required.

## Quick start

Set `TYPESAFE_API_KEY` in the executing environment or your project's `.env` file.
Then save and run this Python file:

```python
import avalanche as ava


QUESTIONS = {
    "department": {
        "type": "choice",
        "instructions": "Which team should review this support ticket?",
        "criteria": {
            "billing": "Payments, invoices, or refunds.",
            "technical": "Product failures or service availability.",
        },
    },
    "urgent": {
        "type": "noul",
        "instructions": "Does this need immediate attention?",
        "criteria": {
            "true": "The customer cannot use the service.",
            "false": "The request can wait for normal support.",
        },
    },
    "severity": {
        "type": "score",
        "instructions": "How severe is the customer impact?",
        "criteria": ["Minor inconvenience", "Service unavailable"],
    },
}


@ava.classifier_step(questions=QUESTIONS, timeout=20.0, slug="classify-ticket")
async def classify_ticket(ticket: str, *, classifier: ava.Classifier) -> ava.ClassificationResult:
    return await classifier(state={"ticket": ticket})


@ava.workflow(classifier_defaults={"model": "jev-latest", "timeout": 30.0})
def ticket_workflow():
    return classify_ticket("Our team cannot sign in to the service.")


if __name__ == "__main__":
    result = ticket_workflow().run(executor=ava.LocalExecutor()).result()
    print(result.choices["department"].choice)
    print(result.nouls["urgent"].noul)
    print(result.scores["severity"].score)
    print(result.model_dump_json(indent=2))
```

The required keyword-only `classifier: ava.Classifier` parameter has no default
and is injected by Avalanche. Workflow callers pass only ordinary inputs; they
cannot supply their own classifier. A call accepts `state=` and optional
`questions=`. State is a string, JSON object, or JSON array.
Nested values must also be JSON-compatible:
string keys, finite numbers, booleans, null, objects, and arrays. Python-only
objects such as sets, datetimes, or Pydantic models must be converted explicitly.

The standalone [classifier workflow example](../examples/classifier_workflow.py)
exports `classifier_workflow`, with `load_ticket` and `classify_ticket` nodes.
From the repository root, after setting the key:

```bash
uv run python examples/classifier_workflow.py
uv run ava dev examples/classifier_workflow.py
```

The first command prints the typed result as JSON. The second starts the operator
workspace: inspect the questions before launching the workflow,
then inspect the run's invocations. Discovery itself does not require a key or
contact TypeSafe. Live classification requires valid credentials and model
access; example answers are not guaranteed.

## Step schemas and classifier input models

A classifier has two separate data contracts:

- **Step inputs and output** come from the Python function's parameter and return
  annotations. They describe data received and returned by the workflow node.
- **Classifier input** describes the `state=` sent on each call. Declare it with
  `input_model=`, independently of the step's annotations.

For example, a step can receive a batch while classifying one item at a time:

```python
from pydantic import BaseModel, ConfigDict, Field


class TicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ticket: str = Field(min_length=1, description="The customer message to classify.")


@ava.classifier_step(input_model=TicketInput, questions=QUESTIONS)
async def classify_tickets(
    tickets: list[str], *, classifier: ava.Classifier
) -> list[ava.ClassificationResult]:
    return [await classifier(state={"ticket": ticket}) for ticket in tickets]
```

The Definition panel shows `tickets: list[str]` as the step input,
`list[ClassificationResult]` as its output, and `TicketInput` as the separate
classifier input. Expand models to inspect nested fields, required/optional
fields, descriptions, defaults, and JSON Schema constraints.

`input_model` accepts a Pydantic model class, including `RootModel` for text or
array state. Calls still pass JSON-compatible values, not model instances.
Avalanche validates state using the declared model's configuration, then sends
its JSON serialization with aliases and defaults applied. Validation fails
before client creation or a TypeSafe request; rejected input is not retained.
Without `input_model`, the existing text/object/array state contract remains.

Schemas are captured during decoration without executing the step or calling
TypeSafe. Injected classifier, context, run-input, and provider parameters are
excluded from step data inputs. Missing annotations appear as unspecified;
annotations without a JSON Schema retain their type name. JSON Schema cannot
express every custom Python validator. These displays apply only to classifier
steps; ordinary and agent-step displays are unchanged.

## Question declarations

`questions` is a nonempty JSON-shaped dictionary keyed by nonempty question IDs.
Each value has a `type`, optional `instructions`, and the corresponding
`criteria`. IDs connect returned answers to their questions; declaration order
is preserved. The quick start shows all three shapes.

| Type | Criteria | Answer |
| --- | --- | --- |
| `"choice"` | Required nonempty object mapping option names to descriptions | Selected option, all option probabilities, and confidence |
| `"noul"` | Optional object with string keys `"true"` and/or `"false"`, or null | Probability of true, not a Boolean decision |
| `"score"` | Required ordered array of at least two level descriptions | Probability-weighted position on the zero-based levels, legend, all level probabilities, and confidence |

Instructions may be text, a JSON object, a JSON array, or null; omission means
null. Choice and Noul criterion descriptions accept the same forms. Each Score
level must be text, an object, or an array, not null. Structured content is
passed as JSON and displayed as structured values, not converted to a Python
string representation. Extra question fields and unknown question types are
rejected.

Default questions are validated and snapshotted when the decorator runs. Later
mutation of the original dictionary, including nested instructions or criteria,
does not change those defaults. Runtime questions are validated and snapshotted
for each invocation before any service request.

### Runtime questions

Use `@ava.classifier_step()` without defaults when candidate options, question
IDs, or rubrics depend on runtime inputs. Build the mapping inside the step or
receive it from an upstream node, then pass it as `questions=`:

```python
@ava.classifier_step()
async def select_passage(
    query: str,
    passages: list[str],
    *,
    classifier: ava.Classifier,
) -> ava.ClassificationResult:
    return await classifier(
        state={"query": query, "passages": passages},
        questions={
            "best_match": {
                "type": "choice",
                "instructions": (
                    "Which passage best answers `query`? "
                    "Select none if no passage answers it."
                ),
                "criteria": {
                    **{
                        f"passage_{i}": f"The passage at `passages[{i}]`."
                        for i in range(len(passages))
                    },
                    "none": "None of the supplied passages answers the query.",
                },
            },
        },
    )
```

| Decorator questions | Call questions | Behavior |
| --- | --- | --- |
| Provided | Omitted or `None` | Use decorator defaults |
| Omitted or `None` | Provided | Use runtime questions |
| Provided | Provided | Replace the entire mapping for this call |
| Omitted or `None` | Omitted or `None` | Fail before client creation |

An explicitly empty or malformed mapping fails; it never falls back to defaults.
There is no implicit merge. Combine mappings explicitly in Python if desired.
Overrides do not alter defaults or other concurrent calls. A second call can use
an earlier answer to construct new questions, for example the next level of a
taxonomy. Runtime work belongs in the step body, not the workflow's DAG builder.
`model`, `timeout`, and `input_model` remain step/workflow configuration.

## Configuration and client lifecycle

The complete decorator configuration is:

- `questions=`: optional default question dictionary; omitted or `None` requires
  questions on each call.
- `input_model=`: optional Pydantic model class for each call's state.
- `classifier=`: optional provider, such as `ava.classifiers.TypeSafe()`.
- `model=`: optional nonblank model name of the provider in use.
- `timeout=`: optional positive, finite number of seconds.
- `slug=`: optional node identifier, following the [DAG API](dag-api.md).

Only `classifier`, `model` and `timeout` are accepted in
`@ava.workflow(classifier_defaults={...})`. `model` and `timeout` resolve as:

1. Explicit non-`None` value on `@ava.classifier_step`.
2. The same field in `classifier_defaults`.
3. Avalanche defaults: model `"jev-latest"`, timeout `10.0` seconds.

### Choosing the provider

The provider is TypeSafe unless `classifier=` says otherwise. `model=` and
`timeout=` on their own keep meaning TypeSafe, so existing flows do not change.

```python
@ava.classifier_step(
    questions=QUESTIONS, classifier=ava.classifiers.TypeSafe(), model="jev-1.14.0"
)
async def classify_ticket(ticket: str, *, classifier: ava.Classifier): ...


@ava.workflow(classifier_defaults={"classifier": ava.classifiers.TypeSafe()})
def ticket_workflow(): ...
```

The step's `classifier=` wins over the workflow's. A level that sets
`classifier=` takes its model from that same level, else from that provider's
default model: a model set in the workflow defaults is never applied to a
classifier chosen on the step. The provider is set in code only; no environment
variable selects it, and there is no automatic fallback to another provider.
The provider, model and timeout are recorded in the step's declaration, and
every recorded classifier call shows them.

A question the provider cannot take is refused with an error naming the question
and the limit, when the step is declared if possible, otherwise at the call.

The three providers side by side:

| | TypeSafe (default) | OpenAI Decisions | Gemini Decision |
| --- | --- | --- | --- |
| `classifier=` | `ava.classifiers.TypeSafe()` | `ava.classifiers.OpenAIDecisions()` | `ava.classifiers.GeminiDecision()` |
| Service | TypeSafe API | OpenAI API, `POST /v1/decisions` | Gemini Developer API, `POST /v1beta/decisions` (not Vertex AI) |
| Stability | Stable | Stable | Beta: limits and response may change |
| Default model | `jev-latest` | `gpt-6-luna`, the only model served | `gemini-decision-1` |
| Key | `TYPESAFE_API_KEY` | `OPENAI_API_KEY` | `GEMINI_API_KEY` |
| Install | base install | `avalanche-ai[openai]` (`openai` 3.37 or later) | `avalanche-ai[gemini]` (`google-genai` 3.2.0 or later) |
| Instructions | optional; text or JSON | optional; text, a JSON object or a list of JSON objects | required on every question; text only |
| Choice | 1 option or more; criteria are text, JSON or `None` | 2 to 255 options; criteria are text or `None` | 2 to 255 options; criteria are text or `None` |
| Noul criteria | `true`/`false` descriptions, text or JSON | none: put them in the instructions | `true`/`false` text descriptions |
| Score | 2 levels or more, text or JSON | 2 to 10 text levels | 2 to 255 text levels |
| Questions per call | no Avalanche limit | 400 | 1,000 |
| State | text, a JSON object or a JSON array | text, a JSON object or a list of JSON objects | sent as text; JSON is serialized |
| Probabilities | two decimals | two decimals | not rounded; a noul stays within 0.01 to 0.99 |
| `result.model` | the model that answered, for example `jev-1.14.0` | the model OpenAI reports | the model you requested |

Questions written for one provider may be refused by another, so check this table
before switching. Questions with text instructions on every question, text
criteria, a choice of at least 2 options and a score of at most 10 levels work
with all three. Different providers also give different probabilities for the
same question: re-check any threshold your flow compares against.

The [provider example](../examples/classifier_provider_workflow.py) asks such
portable questions and names OpenAI Decisions or Gemini Decision when that key is
set, else TypeSafe:

```bash
uv run python examples/classifier_provider_workflow.py
```

### OpenAI Decisions

```python
@ava.classifier_step(questions=QUESTIONS, classifier=ava.classifiers.OpenAIDecisions())
async def classify_ticket(ticket: str, *, classifier: ava.Classifier): ...
```

`OpenAIDecisions()` calls OpenAI's Decisions API and returns the same
`ava.ClassificationResult` as TypeSafe. Install the `openai` extra and set
`OPENAI_API_KEY`. Its limits, each refused before any request with the question
named:

- a choice takes 2 to 255 options, and an option's criteria is text or `None`;
- a score takes 2 to 10 levels, each of them text;
- a noul takes no `true`/`false` criteria: put them in the instructions;
- instructions are text, a JSON object or a list of JSON objects;
- a call takes at most 400 questions;
- the state is text, a JSON object or a list of JSON objects.

If OpenAI refuses to answer one of the questions, the whole call fails with an
error naming that question. See the
[API reference](api/classifiers.md#openai-decisions) for the mapping.

### Gemini Decision

`ava.classifiers.GeminiDecision()` sends the questions to Google's Gemini
Decision service (default model `gemini-decision-1`). Install the `gemini` extra
(`uv add 'avalanche-ai[gemini]'`, which brings `google-genai` 3.2.0 or later)
wherever the step runs, and set `GEMINI_API_KEY`. Without the package, the call
fails with an error naming the extra. Avalanche passes the key itself, so the
`GOOGLE_API_KEY` variable that `google-genai` also reads is never used.

```python
@ava.classifier_step(questions=QUESTIONS, classifier=ava.classifiers.GeminiDecision())
async def classify_ticket(ticket: str, *, classifier: ava.Classifier): ...
```

Gemini Decision is a beta service on Google's `v1beta` Gemini Developer API
(Vertex AI is not supported), so its limits and response may still change. It
returns the same `ClassificationResult`, with these differences from TypeSafe:

- Every question needs non-empty text `instructions`. Instructions, Noul and
  Choice descriptions and Score levels must be text: structured JSON is refused.
- A Choice needs 2 to 255 options. An option name takes at most 128 characters
  (512 UTF-8 bytes), with no leading or trailing whitespace and no control
  characters. An option with no description is sent with an empty one. A Score
  takes 2 to 255 levels. One call takes at most 1,000 questions.
- Question names are free: Avalanche sends Gemini its own short question IDs and
  maps the answers back to your names. Choice probabilities come back in the
  order you declared the options.
- The state is sent as text. A JSON state is serialized to a JSON string. Images,
  audio, video and PDF input are not supported.
- Noul probabilities lie between `0.01` and `0.99`, so a check such as
  `noul > 0.99` never matches. Gemini's extra Noul confidence is dropped.
- Probabilities are not rounded. Totals must sum to 1 within `5e-10` per option.
- Gemini does not report which model answered, so `result.model` is the model
  that was requested. Output tokens are always `0`.
- An input blocked by Gemini's safety filters fails the call. The error carries
  Gemini's reason code (for example `safety`), never the input.

These limits are checked before any request and refused with an error naming
the question and the limit.

Thus the quick start uses `jev-latest` and a 20-second timeout, not the
workflow's 30 seconds. Omitting a decorator option or setting it to `None`
inherits the workflow/default value; `None` is not a valid value inside
`classifier_defaults`. `agent_defaults` does not affect classifiers. Avalanche
passes the resolved model explicitly, so the SDK's `TYPESAFE_DEFAULT_MODEL`
environment variable does not override it.

`timeout` configures the SDK's HTTP-operation timeout, not an end-to-end step
deadline. The SDK retains its own default request retry policy; Avalanche adds
no second retry layer and exposes no classifier retry, fallback, client,
credential, or per-call model override option.

The provider's async client is created lazily on the first call, inside the
executing process. Calls in one execution of a step share that client. Avalanche
closes it when the step exits, including on error or cancellation, and cancels
outstanding calls during cleanup. Await all calls inside the step; do not retain
the injected classifier for later use. Another step execution gets its own
client.

Credentials come from the key variable of the provider in use
(`TYPESAFE_API_KEY` for TypeSafe, `OPENAI_API_KEY` for OpenAI Decisions,
`GEMINI_API_KEY` for Gemini Decision), not
decorator configuration, workflow
defaults, or serialized declarations. Only that provider's key is required, and
a missing key fails with an error naming the variable. If the variable is absent when
the first call creates a client, Avalanche loads the nearest `.env` found from
the executing process's working directory upward. Existing environment variables
are never overwritten. Operator coordinators search from their workflow import
root and need either exported credentials or an accessible `.env` file.
Importing a workflow, building its DAG, and discovering its questions do not load
`.env`, create a client, or make a model request. The requested model name is
retained in the declaration; the provider's resolved model is retained in the result.

## Typed results and serialization

Every successful `await classifier(state=...)` returns
`ava.ClassificationResult`, a validated Pydantic model with:

- `model`: the model reported by the provider.
- `answers`: question IDs mapped to `ChoiceAnswer`, `NoulAnswer`, or `ScoreAnswer`.
- `usage.input_tokens` and `usage.output_tokens`: token counts, independently
  `None` when the provider does not report them. Unknown counts are not treated as zero.
- `choices`, `nouls`, and `scores`: typed, question-ID-keyed views of the answers.

Answer types are also importable from `avalanche.classifier`. Their fields are:

| Accessor | Fields |
| --- | --- |
| `result.choices["department"]` | `type="choice"`, `choice`, `probabilities`, `confidence` |
| `result.nouls["urgent"]` | `type="noul"`, `noul` |
| `result.scores["severity"]` | `type="score"`, `score`, `legend`, `probabilities`, `confidence` |

Probabilities and confidence lie in `[0, 1]`. Choice and Score preserve the full
probability distribution; Noul has no separate confidence field. Score is a
fractional, probability-weighted level position, not a rounded or selected
level. Score legend and probability keys are JSON strings (`"0"`, `"1"`, ...).
Avalanche validates exact question IDs, answer types, Choice options, and Score
levels against that invocation's resolved questions, rather than silently
dropping or inventing answers.
Distribution totals allow for the provider's rounding: TypeSafe rounds each
probability to two decimal places (up to `0.005` per option), and Gemini
Decision does not round. Returned probabilities are preserved unchanged,
not renormalized; totals outside that rounding allowance are rejected.

For the `result` produced above, serialize or reconstruct without losing those
fields:

```python
payload = result.model_dump(mode="json")
encoded = result.model_dump_json()
restored = ava.ClassificationResult.model_validate_json(encoded)
print(restored.choices["department"].probabilities)
```

Returning this model is optional. The step may return a Boolean, a custom
Pydantic model, a table append receipt, or another ordinary node result. The
body owns any thresholds, routing decisions, validation, and persistence; the
primitive never infers them from probabilities.

## Multiple calls, errors, and evidence

One step may make zero, one, or many calls, each with its own questions or the
decorator defaults. Concurrent calls such as
`await asyncio.gather(classifier(state=first), classifier(state=second))`
receive separate invocation IDs and detached question snapshots. Invocation
indexes are zero-based and allocated when calls start, not when they finish.

Each call emits a `running` snapshot and a terminal `success`, `failed`, or
`cancelled` snapshot. Evidence includes its invocation ID/index, start/end
timestamps, resolved declaration, input state, result on success, and an error
description on failure or cancellation. Valid input is captured as a detached
snapshot of the state sent to TypeSafe, not inferred from the node's inputs.
It remains attached to running and terminal snapshots, including failed and
cancelled calls. A successful result is captured before control returns to the
step body. If later Python postprocessing fails, the node fails but the already
captured input and answers remain inspectable within the operator's size limits. These
records are independent of what the node returns; they are not inferred from
its return value or presented as an agent reasoning trace.

Invalid decorator defaults raise `ava.ClassifierStepError`. Missing credentials,
missing or invalid runtime questions, request failures, invalid state, and
malformed or invocation-mismatched answers surface as
`ava.ClassifierStepExecutionError`; there are no fabricated fallback
answers. Ordinary errors in the step body remain ordinary node errors.
`asyncio.CancelledError` propagates as cancellation, with no result invented for
the cancelled call. Cancellation is cooperative: if an operator worker is
terminated before it publishes terminal evidence, the UI marks the remaining
running invocation as interrupted rather than claiming an answer was returned.

Invocation evidence retains valid input state, which may contain sensitive
application data. If state fails validation before capture, the invocation's
`input` is null; null does not stand for an empty string, array, or object.
If question resolution fails, `declaration.questions` is null and no input or
result is captured. This represents an unresolved request, not an empty rubric
or the decorator defaults. Once resolved, the same questions remain attached to
both lifecycle records; the operator rejects any mid-call change.
Avalanche does not capture credentials from the TypeSafe client or environment,
and exception descriptions remain sanitized. This is not blanket redaction:
secrets explicitly placed in state, ordinary node inputs/outputs, user logging,
question declarations, or returned answer content can still be retained.
State is also sent to TypeSafe to perform the classification.

## Inspecting current workflows and historical runs

Classifier nodes have a distinct cyan identity and filter-list icon in the
operator graph and inspector, separate from agent styling and execution-status
colors:

- **DAG cards:** At detailed zoom, classifier cards show step input names and
  types on the left and the return type on the right, using the same layout as
  agent cards. These describe the workflow step, not each classifier call's state.
  Compact cards hide the field lists when zoomed out. Historical cards use the
  selected run's retained declaration.
- **Current workflow:** the sidebar opens on **Code**. **Definition** shows default
  questions first, or an explanation that questions are supplied at runtime when no
  defaults exist. Questions are grouped with the classifier input beneath them using
  a cyan accent. The last tab, **Step interface**, groups the step's inputs
  and output, using the [same panel as other node types](dag-api.md#inspect-step-interfaces).
  Expand a question row to inspect
  Choice options, Noul True/False meanings, or Score levels in an indented group with
  smaller labels beneath the question definition.
  Missing criterion descriptions stay blank. Structured instructions and criteria
  remain inspectable as JSON.
  **Code** shows the current step's Python source.
- **Selected run:** Calls appear in a compact table with Call, Outputs, Status,
  and Duration columns. Calls are ordered by ascending index, starting with Call 1.
  Previous/Next controls show 25 calls per page, fetching more history as needed.
  Paginated and live activity descriptors supply call numbers, status, and compact
  answers: the selected Choice, Noul probability, or Score value for each question.
  List rows do not show input previews or fetch full invocation bodies.
  The Current State definition tabs are not rendered in run inspection.
  Editing or reloading the workflow does not replace a call's retained answers.
  The bounded 500-call descriptor window keeps the visible page in place while
  admitting live arrivals. **Return to first calls** reloads older history after
  descriptors have left that window.
- **Call detail:** Captured input appears as formatted JSON in a read-only code
  editor with syntax highlighting, line numbers, folding, and bounded scrolling.
  Question rows show names, answers, and types without repeating descriptions from
  Definition. Choice shows its three highest-probability options; expansion adds
  the remaining options to the same list without duplicating results. Choices
  with three or fewer options do not need an expansion control. Noul shows the
  probability of true with a small filled bar. Score shows the fractional value
  and maximum level. Choice and Score also show confidence.
  Call IDs, timestamps, model names, and token counts remain in the evidence.
  Expand **Definition** within a call to inspect its retained schemas and actual
  resolved questions, including overrides. These are not the current workflow's
  defaults. Failed calls with invalid or missing questions show that the request
  questions were not resolved.
  Each call has one expandable table row; lifecycle snapshots are grouped together.
  All calls start collapsed. Only explicitly expanded calls fetch full details.
  Full bodies alone use the eight-entry, 8 MiB browser detail cache. Eviction does
  not remove call numbers or compact answers from the list. Expanding an evicted
  call reloads its body automatically while it remains available on the operator.
- **Availability:** Not invoked, running, failed, cancelled, interrupted, and
  unavailable details are distinct states. History is paged and bodies load on
  demand. Failed requests offer an explicit retry; unavailable bodies are not
  treated as empty results.
  When a terminal record falls outside the loaded history window, its outcome is
  reported as not loaded rather than inferred from an older running record.

These views are local-development inspection, not a durable execution journal.
The entire invocation detail body, including input and results, shares the
operator's event/node/run size limits. Bodies are kept with the run in the operator's
local state database, so they stay readable after the run ends and after an operator
restart; nothing removes them automatically. Size-limited bodies are unavailable, not
empty input or empty answers. Browser caches are bounded independently. The state
database is local, unencrypted development storage, not a durable execution journal:
explicitly persist required business results in your step.

Avalanche remains intended for local development and experimentation. This
feature adds no production authentication boundary or remote-service deployment
guarantee; an embedding host remains responsible for access control.
