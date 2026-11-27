# Agent steps

An agent step is a workflow step whose work is done by an LLM agent. You declare a typed
contract (a `Signature`), Avalanche injects an `agent` you call once, and the outputs come
back validated against the contract, with a trace of the call.

## When to use it

- The task needs reasoning over content, several model turns, tool calls, or reading/writing files.
- The output is free text or a structured object the model must compose.

Use something else when:

| Need | Use | File |
|---|---|---|
| Deterministic logic, I/O, formatting, API calls | `@ava.step` | [python-steps.md](python-steps.md) |
| A label, a yes/no or a score with a probability | `@ava.classifier_step` (one cheap call, no agent loop) | [classifier-steps.md](classifier-steps.md) |
| A person must decide | a decision | [../orchestration/approvals.md](../orchestration/approvals.md) |
| The same agent over many items | `ava.agent.step.map(...)` or `.map` on the step | [../orchestration/control-flow.md](../orchestration/control-flow.md) |

Needs the `agents` extra (`avalanche-ai[agents]`); the `claude` and `openai` extras include it.

## Interface

### `@ava.agent_step`

```python
ava.agent_step(
    signature,                    # positional, required
    *,
    runtime=None,
    tools=(),
    skills=(),
    integrations=(),
    evaluations=None,
    retries=7,
    on_uncertain="retry",
)
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `signature` | `ava.Signature` subclass, or `ava.Signature("...", "...")` | required | The contract: instructions, input fields, output fields. |
| `runtime` | `ava.runtimes.PredictRLM(...)`, `ClaudeAgent(...)` or `OpenAIAgent(...)` | `None` | Which agent runs the step and with which model. `None`: the workflow's `agent_defaults` runtime, else `PredictRLM()`. See [runtimes.md](runtimes.md). |
| `tools` | sequence of callables | `()` | Python functions the agent may call. See [tools-and-skills.md](tools-and-skills.md). |
| `skills` | sequence of `ava.agent.Skill` | `()` | Agent Skills the agent loads when a task needs them. Not supported by `PredictRLM`. |
| `integrations` | sequence of `ava.Connector` / `ava.MCP` / `ava.catalog.*` instances | `()` | Outside services whose tools the agent gets as `<integration>__<tool>`. See [../connect/integrations.md](../connect/integrations.md). |
| `evaluations` | `ava.Evaluations` | `None` | Judgments recorded on each successful call. See [evaluations.md](evaluations.md). |
| `retries` | `int >= 0` | `7` | Extra executions of the whole step after a failure. Capped at 7 (8 attempts). |
| `on_uncertain` | `"retry"`, `"hold"`, `"fail"` | `"retry"` | What to do when the step raises `ava.OutcomeUnknownError`. |

Model, iteration limits, sandbox and similar settings are **not** step options: they belong
to the runtime. `ava.agent_step(Sig, lm="...")` raises
`TypeError: ava.agent_step no longer accepts lm=; pass them to the runtime: runtime=ava.runtimes.PredictRLM(lm=...)`.

### The step function

```python
@ava.agent_step(MySignature)
async def do_thing(a: A, b: B, *, agent: ava.Agent) -> Out:
    """One-line docstring."""
    result = await agent(field_one=a, field_two=b)
    return result.outputs.some_output
```

- `agent` must be keyword-only, annotated exactly `ava.Agent`, with no default. It is injected; callers never pass it.
- The other parameters are ordinary step inputs (upstream outputs, workflow input, context), typed as in [python-steps.md](python-steps.md).
- `await agent(**inputs)`: keyword arguments only, one per signature input field, names matching exactly.
- It returns an `AgentResult`: `result.outputs.<field>` (validated instance of the signature's outputs) and `result.trace` (see [evaluations.md](evaluations.md)).
- The function returns whatever the next step needs. It may reshape outputs, but the agent is called **once** per execution.

### `ava.Signature`

Class form (preferred: named, documented, typed):

```python
class TriageTicket(ava.Signature):
    """Instructions for the agent. First line is shown in the UI."""

    ticket: Ticket = ava.InputField(desc="What this input is.")
    triage: Triage = ava.OutputField(desc="What to produce.")
```

String form:

```python
ava.Signature(
    "ticket: Ticket, hint -> triage: Triage, confidence: float",
    "Instructions for the agent.",
    signature_name="TriageTicket",
    custom_types={"Ticket": Ticket, "Triage": Triage},
)
```

| Part | Rule |
|---|---|
| Docstring / 2nd argument | The agent's instructions. String form without one gets ``Given the fields `a`, produce the fields `b`.`` |
| `"inputs -> outputs"` | Comma-separated `name: type`. A field without a type is `str`. |
| `custom_types` | Required for any type that is not a builtin or from `typing`; otherwise `ValueError: Unknown name: Ticket`. |
| `signature_name` | Class name of a string signature. Default `"StringSignature"`. |
| `ava.InputField(desc=...)`, `ava.OutputField(desc=...)` | `desc` (or `description`) describes the field to the agent and in the UI. Pydantic constraints such as `ge=0` are kept. |
| Field types | Anything Pydantic validates: `str`, `int`, `float`, `bool`, `Literal[...]`, `list[...]`, `dict[...]`, Pydantic models, `ava.File`, `ava.Workspace`. |

Every field is required: a default on a field is not applied. At least one output field is needed.
`ava.Signature`, `ava.InputField`, `ava.OutputField`, `ava.Agent` are the same objects as `ava.agent.Signature` and so on.

### Outputs

- One output field per value you need. Use a Pydantic model for structured results, `Literal` for closed sets.
- Outputs are validated in strict mode against the declared types. A mismatch raises `AgentOutputError` and the step fails (then retries).
- Inputs are validated too: a wrong type raises `AgentStepError`.

### Files and workspaces

Type a signature field `ava.File`, `list[ava.File]` or `ava.Workspace` and pass the object in the agent call.

| | What the agent gets | Result |
|---|---|---|
| `ava.File` input | A path it can read (OpenAI without sandbox tools: the file attached to the message). | — |
| `ava.Workspace` input | A folder to work in. | — |
| `ava.File` / `list[ava.File]` output | A folder to write into. | `ava.File` references under `.outputs/<step>/<attempt>/<call>/` in the run workspace. |
| `ava.Workspace` output | (Claude, OpenAI) the workspace it worked in. | The same `ava.Workspace`. |

Support differs per runtime (PredictRLM needs a non-default sandbox backend, and with `DirectPythonBackend` an input field typed with a Pydantic model fails with `NameError`); see [runtimes.md](runtimes.md#files).
`ava.File` and `ava.Workspace` themselves are covered in [inputs-and-files.md](inputs-and-files.md).

### Workflow-wide defaults

```python
@ava.workflow(agent_defaults={"runtime": ava.runtimes.PredictRLM(lm="openai/gpt-5.6-terra")})
def my_flow(): ...
```

`agent_defaults` accepts only the key `runtime` (or an `ava.agent.AgentDefaults(runtime=...)`).
A step runtime of the same kind is merged over it field by field (the step wins for fields it sets);
a step runtime of another kind replaces it. Tools, skills and evaluations are always per step.

### Inline form

Inside a workflow body, `ava.agent.step` creates an agent node without a function body:

```python
ava.agent.step(Signature, inputs={"text": some_future}, slug=None, runtime=None, tools=(),
               skills=(), integrations=(), evaluations=None, retries=7, on_uncertain="retry")
ava.agent.step.map(Signature, items, max_items=None, fail_fast=True, slug=None, ..., **broadcast)
```

- The node is named after the signature class, so use a class or `signature_name=`.
- Its value is the single output field, or a tuple of the output fields in declared order.
- Without `inputs=`, upstream values arrive through `>>` in input-field order.
- `.map` maps input fields positionally over lists; keyword arguments are broadcast to every item.
- Outside a workflow body, `ava.agent.step(Sig, ...)` is the same decorator as `ava.agent_step`.

### Retries and uncertainty

Two different loops:

| Loop | Set by | What repeats |
|---|---|---|
| Inside one agent call | runtime: `max_iterations` (PredictRLM), `max_turns` (Claude, OpenAI) | Model turns and tool calls of one call. |
| Step retries | `retries=` on the step | The whole step function, with a new agent call and a new trace. |

A step is retried after any exception except `ava.NonRetryableError`. That includes
`AgentOutputError`, a missing API key and a declaration error found at run time.

"Uncertain" means the step raised `ava.OutcomeUnknownError`: an outside action may or may not
have happened. Agent runtimes never raise it themselves; raise it from the step body.

| `on_uncertain` | Effect |
|---|---|
| `"retry"` | Treated like any failure: retried while attempts remain. |
| `"hold"` | The step pauses for a person, who retries it once, fails it, or supplies its output. The function needs a return annotation. |
| `"fail"` | The step fails at once, without retry. |

Details: [../orchestration/retries.md](../orchestration/retries.md).

### Errors

| Error | Base | When |
|---|---|---|
| `ava.agent.AgentStepError` | `RuntimeError` | Bad declaration or call: wrong `agent` parameter, wrong input names or types, unsupported feature for the runtime. |
| `ava.agent.AgentStepCallError` | `AgentStepError` | The agent was called a second time in one execution. |
| `ava.agent.AgentStepExecutionError` | `RuntimeError` | The agent call failed (model, CLI, SDK, limits). |
| `ava.agent.AgentOutputError` | `AgentStepExecutionError` | The outputs do not match the signature. |

## Example

```python
from typing import Literal

from pydantic import BaseModel

import avalanche as ava


class Ticket(BaseModel):
    subject: str
    body: str


class Triage(BaseModel):
    category: Literal["billing", "technical", "other"]
    summary: str


class TriageTicket(ava.Signature):
    """Triage one support ticket.

    Read the subject and body, pick the single best category and write a
    one-sentence summary a support agent can act on. Use only the ticket text.
    """

    ticket: Ticket = ava.InputField(desc="The ticket as the customer wrote it.")
    triage: Triage = ava.OutputField(desc="Category and one-sentence summary.")


@ava.source
def load_ticket() -> Ticket:
    """Load one sample support ticket."""
    return Ticket(subject="Charged twice", body="My card shows two charges for May.")


@ava.agent_step(
    TriageTicket,
    runtime=ava.runtimes.PredictRLM(lm="openai/gpt-5.6-terra", max_iterations=10),
    retries=2,
)
async def triage_ticket(ticket: Ticket, *, agent: ava.Agent) -> Triage:
    """Ask the agent for the ticket's category and summary."""
    result = await agent(ticket=ticket)
    return result.outputs.triage


@ava.workflow
def support_triage():
    """Triage a support ticket with one agent step."""
    return load_ticket() >> triage_ticket()
```

A step that takes and returns files (PredictRLM needs a backend with host mounts for this):

```python
from pydantic import BaseModel

import avalanche as ava


class RedactDocument(ava.Signature):
    """Write a copy of the document with every digit replaced by '#'.

    Save the copy as a .txt file and report how many digits were replaced.
    """

    document: ava.File = ava.InputField(desc="The document to redact.")
    redacted: ava.File = ava.OutputField(desc="The redacted copy.")
    digits: int = ava.OutputField(desc="Number of digits replaced.")


class Redaction(BaseModel):
    redacted: ava.File
    digits: int


class RedactInput(ava.BaseInput):
    document: ava.File


@ava.agent_step(
    RedactDocument,
    runtime=ava.runtimes.PredictRLM(
        lm="openai/gpt-5.6-terra",
        interpreter=ava.agent.DirectPythonBackend(),
    ),
)
async def redact_document(payload: RedactInput, *, agent: ava.Agent) -> Redaction:
    """Produce a redacted copy of the uploaded document."""
    result = await agent(document=payload.document)
    return Redaction(redacted=result.outputs.redacted, digits=result.outputs.digits)


@ava.workflow(input=RedactInput)
def redact_upload():
    """Redact digits from an uploaded document."""
    return redact_document()
```

### Running without a model key

A PredictRLM step with no `lm` uses DSPy's current LM, so a test can script the model with
DSPy's `DummyLM`. Each reply is one iteration: a `plan` and the Python `code` to run. The script
below sits in the step body only to make one file that runs; in a project keep it in the test
and keep step bodies free of it ([../run/testing.md](../run/testing.md#agent-and-classifier-steps)).

```python
import dspy
from dspy import JSONAdapter
from dspy.utils import DummyLM

import avalanche as ava


class SumNumbers(ava.Signature):
    """Compute and verify a sum by executing Python before submitting it."""

    task: str = ava.InputField(desc="What to add up.")
    answer: int = ava.OutputField(desc="The verified sum.")


@ava.agent_step(SumNumbers, runtime=ava.runtimes.PredictRLM(max_iterations=3))
async def sum_numbers(*, agent: ava.Agent) -> int:
    """Add three numbers with a scripted model."""
    lm = DummyLM(
        [
            {"plan": "Compute the sum", "code": "total = sum([2, 3, 5])\nprint(total)"},
            {"plan": "Verify and submit", "code": "assert total == 10\nSUBMIT(answer=total)"},
        ],
        adapter=JSONAdapter(),
    )
    with dspy.context(lm=lm, adapter=JSONAdapter()):
        result = await agent(task="Sum 2, 3 and 5; verify the result.")
    return result.outputs.answer


@ava.workflow
def scripted_sum():
    """Run one scripted agent step."""
    return sum_numbers()


if __name__ == "__main__":
    print(scripted_sum().run(executor=ava.LocalExecutor()).result())  # 10
```

This is DSPy's test model, not an Avalanche runtime: it only works with `PredictRLM`. See [../run/testing.md](../run/testing.md).

## Rules

- Call the agent exactly once. A second call raises `AgentStepCallError: agent step '...' called its agent more than once`. Need two calls: make two agent steps.
- Returning before the call is allowed, for a guard such as an empty input (`if not notes: return "no notes"`): the step succeeds and logs the warning `finished without calling its agent`, with no trace and no evaluation. A step that never calls its agent should be an `@ava.step`.
- Make the function `async` and `await agent(...)`; the call returns a coroutine.
- Declare `*, agent: ava.Agent`. Otherwise: `AgentStepError: agent step '...' requires a keyword-only agent parameter` / `agent parameter must be keyword-only` / `must be annotated ava.Agent` / `is framework-injected and cannot have a default`.
- Pass every signature input by its exact name: `AgentStepError: agent step '...': missing input fields ['text']; unexpected input fields ['nope'].`
- Always give a signature class a docstring. Without one, the instructions become `ava.Signature`'s own class docstring.
- Always give fields a `desc`. Without one, the UI shows the placeholder `${field_name}`.
- Pass a signature object, not a bare string: `ava.agent_step("a -> b")` fails later with `TypeError: agent signature must be an ava.Signature subclass, an inline ava.agent.Signature(...), or another DSPy Signature class`.
- Only `desc` and Pydantic constraints on fields. Other DSPy field options raise `TypeError: ... uses unsupported DSPy field options [...]`.
- Do not name an output `run_summary` on `ClaudeAgent` / `OpenAIAgent` with `narrate=True` (the default): `AgentStepError: ... rename the signature's output or set narrate=False`.
- Declare agent steps at module level. Inside a workflow body the decorator raises `AgentStepError: Agent decorators cannot be declared inside a workflow body; use ava.agent.step(Signature, inputs=...) for an inline agent.`
- Put secrets in the environment of the process that runs the step; runtimes read their keys from there.
- Agent calls cost money and the default is 8 attempts. Lower `retries` for expensive steps and raise `ava.NonRetryableError` from the body for failures a retry cannot fix.
- Keep deterministic work (parsing, API calls, formatting) in plain steps before and after; give the agent only the judgment part.

## In the UI

- The node is titled with the function name; under it, the **first non-empty line of the signature's instructions** (not the function's docstring).
- "Agent definition" tab: signature name, full instructions, each input/output field with type and `desc`, the tools (name, docstring, source code), the skills, and the runtime's settings and model. Credentials are never shown.
- In a run: "Trace" (turn by turn), "Run I/O" (the step's inputs and return value), "Evaluations", "Step interface".

## See also

- [runtimes.md](runtimes.md): choosing and configuring the agent runtime.
- [tools-and-skills.md](tools-and-skills.md): tools, Agent Skills, integrations.
- [evaluations.md](evaluations.md): evaluations and traces.
- [classifier-steps.md](classifier-steps.md): the cheaper alternative for labels and scores.
- [inputs-and-files.md](inputs-and-files.md): `ava.File`, `ava.Workspace`.
- [../orchestration/retries.md](../orchestration/retries.md): retry and uncertainty policy.
- [../reference/errors.md](../reference/errors.md): all errors.
