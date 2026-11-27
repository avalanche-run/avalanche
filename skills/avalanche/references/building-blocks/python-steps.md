# Python steps

A Python step is a function decorated with `@ava.step`, `@ava.source` or `@ava.dest`. It is one node of
a workflow: it receives typed inputs, runs ordinary Python (sync or async) and returns one value.

## When to use it

- Deterministic work: parsing, API calls, calculations, reads and writes to your systems.
- `@ava.source` for a step that brings data in, `@ava.dest` for a step that sends data out,
  `@ava.step` for everything else. The three run the same way; the node type differs.
- Not for work that needs a model's judgment: use an agent step ([agent-steps.md](agent-steps.md)) or a
  classifier step ([classifier-steps.md](classifier-steps.md)).
- Not for pausing: a step must not `time.sleep` for long or poll for a human. Use
  [../orchestration/waits.md](../orchestration/waits.md) and
  [../orchestration/approvals.md](../orchestration/approvals.md).

## Interface

```python
@ava.step                          # bare, or with options:
@ava.step(num_returns=1, slug=None, retries=7, on_uncertain="retry", emits=(), integrations=())
@ava.source(num_returns=1, slug=None, retries=7, on_uncertain="retry")
@ava.dest(num_returns=1, slug=None, retries=7, on_uncertain="retry")
def verb_object(data: T, ...) -> R: ...        # or: async def
```

| Option | On | Type | Default | Meaning |
|---|---|---|---|---|
| `num_returns` | all | `int` | `1` | Number of separate outputs. With `N > 1` the function must return a tuple of exactly `N` items; each is its own output (`x[0]`, `x[1]`). |
| `slug` | all | `str \| None` | function name | Stable name used to address the step in reruns. Two different steps with the same explicit slug in one workflow raise `ValueError`. |
| `retries` | all | `int >= 0` | `7` | Extra attempts after a failure. Values above 7 act as 7. See [../orchestration/retries.md](../orchestration/retries.md). |
| `on_uncertain` | all | `"retry" \| "hold" \| "fail"` | `"retry"` | What to do when an attempt may have completed without the host knowing. See [../orchestration/retries.md](../orchestration/retries.md). |
| `emits` | `step` | `tuple[str, ...]` | `()` | Event names the step may return with `ava.event(name, output)`. Unique and non-empty. See [state-machines.md](state-machines.md). |
| `integrations` | `step` | sequence of `ava.Connector` instances | `()` | Outside services the step calls. See [../connect/integrations.md](../connect/integrations.md). |

`ava.transform` is the same object as `ava.step`; write `ava.step`.

The decorator returns a node object, not a function. Its useful attributes: `.fn` (the original
function, callable in unit tests), `.name`, `.slug`, `.map(...)` (see
[../orchestration/control-flow.md](../orchestration/control-flow.md)).

### Parameters

| Parameter kind | How it is filled |
|---|---|
| Data parameter | By an explicit argument in the workflow function, or by `>>` (see [workflows.md](workflows.md)). |
| Annotated with the workflow's input model (an `ava.BaseInput` subclass) | The validated run input, injected. See [inputs-and-files.md](inputs-and-files.md). |
| Annotated `ava.RunContext` | Run metadata, injected. |
| Annotated with the workflow's `context=` model (an `ava.BaseContext` subclass) | The validated run context, injected. |
| Default value `ava.Logger()` | A logger bound to the step, injected. |

Injection is by annotation or default, never by name, and only when the call leaves the parameter
unbound. Annotated data arguments are validated with Pydantic before the function runs: a `dict` becomes
the annotated model, a wrong type fails the step with `ValidationError`.

### `ava.RunContext`

| Field | Type | Meaning |
|---|---|---|
| `run_id` | `str` | Id of the run. |
| `workflow_name` | `str` | Name of the workflow function. |
| `node_id` | `str \| None` | Id of this step call, for example `build_report_1`. |
| `node_name` | `str \| None` | Function name of the step. |
| `node_slug` | `str \| None` | Slug of this step call. |
| `step_key` | `str` (property) | `"<run_id>:<node_id>"`. Same value on every retry of the step in this run; a new run or a rerun gets a new one. An idempotency key for "once per run" only. |
| `rerun` | `ava.Rerun \| None` | Set when the run re-executes part of an earlier run. |
| `executor_type` | `str` | `"local"`. |
| `metadata` | `dict[str, Any]` | Free-form values from the run context payload. |

A custom context is a model the caller fills at run start:

```python
class Tenant(ava.BaseContext):
    tenant: str = "acme"

@ava.workflow(context=Tenant)      # ava run my_flow --context '{"tenant": "globex"}'
def my_flow(): ...
```

A step parameter annotated `Tenant` receives it; a parameter annotated `ava.RunContext` still receives
the run metadata. `ava.BaseContext` and `ava.BaseInput` forbid unknown fields.

### `ava.Logger`

```python
ava.Logger(name: str | None = None, level: int | None = None)
```

Use it only as a parameter default: `logger=ava.Logger()`. The injected object has `debug`, `info`,
`warning` (`warn`), `error`, `critical` and `exception`, each `(msg: str, **kwargs)`.

| Option | Default | Meaning |
|---|---|---|
| `name` | `avalanche.node.<function name>` | Name of the underlying `logging` logger. |
| `level` | `None` | Level set on that logger, for example `logging.DEBUG`. |

Every message is prefixed with `[node=<function>, run=<first 8 characters of the run id>, worker=local]`.

### Return values

On the operator every step output is saved, so it must be one of:

| Allowed | Notes |
|---|---|
| `None`, `bool`, `int`, `str`, finite `float` | |
| `list`, `tuple`, `set`, `frozenset` of allowed values | A set is stored as a sorted list. |
| `dict` with `str` keys | |
| Pydantic model | Stored with `model_dump(mode="json", by_alias=True)`; `datetime`, `Enum`, `UUID` fields are fine. |
| dataclass | Stored through Pydantic's JSON dump of its fields. |
| `ava.File`, `ava.Workspace` | Stored by reference. See [inputs-and-files.md](inputs-and-files.md). |

Limits per value: 8 MiB of encoded JSON, 100 levels of nesting, 100,000 encoded items.

## Example

```python
import asyncio

import avalanche as ava
from pydantic import BaseModel, Field


class TicketInput(ava.BaseInput):
    ticket_id: str
    body: str
    priority: int = Field(default=3, ge=1, le=5)


class Ticket(BaseModel):
    ticket_id: str
    words: int
    urgent: bool


class Receipt(BaseModel):
    ticket_id: str
    queue: str
    step_key: str


@ava.source
def load_ticket(ticket: TicketInput, *, logger=ava.Logger()) -> Ticket:
    """Parse the incoming ticket."""
    logger.info(f"loading {ticket.ticket_id}")
    return Ticket(
        ticket_id=ticket.ticket_id,
        words=len(ticket.body.split()),
        urgent=ticket.priority <= 2,
    )


@ava.step(retries=2)
async def choose_queue(ticket: Ticket) -> str:
    """Pick the queue for the ticket."""
    await asyncio.sleep(0)
    return "urgent" if ticket.urgent else "standard"


@ava.dest(on_uncertain="hold")
def file_ticket(ticket: Ticket, queue: str, ctx: ava.RunContext) -> Receipt:
    """File the ticket in its queue."""
    return Receipt(ticket_id=ticket.ticket_id, queue=queue, step_key=ctx.step_key)


@ava.workflow(input=TicketInput)
def triage_ticket():
    """Route one support ticket to a queue."""
    ticket = load_ticket()
    return file_ticket(ticket, choose_queue(ticket))
```

```bash
ava run triage_ticket --input '{"ticket_id": "T-1", "body": "printer on fire", "priority": 1}'
```

Result: `ticket_id` `T-1`, `queue` `urgent`, `step_key` `<run id>:file_ticket_1`.

## Rules

- Type every parameter and the return value. Types drive validation, injection and what the UI shows.
- Return a Pydantic model for structured data. A raw `datetime`, `bytes`, a custom class or a dict with
  non-string keys fails the step on the operator: `TypeError: Operator workflow results support JSON
  scalar/container values, Pydantic models, dataclasses, ava.File values, and ava.Workspace values`.
  `Workflow.run()` in a plain Python process does not check this, so test on the operator.
- A float must be finite: `ValueError: Workflow result floats must be finite`.
- The return annotation is not enforced when the step returns (only `ava.Event[...]` payloads are). Make
  the code return what the annotation says.
- A step that returns a `list` or `tuple` is spread over the next step's parameters by `>>`. Pass it as
  an explicit argument or wrap it in a model. See [workflows.md](workflows.md).
- `num_returns=N` with a different number of returned items fails: `ValueError: Function <step>
  expected to return N values, but returned: ...`. Return a tuple, not a list.
- A step with return annotation `ava.Event[...]` must declare `emits=`: `ValueError: Event-returning
  steps must declare emits`. Returning a name not in `emits` fails with `NonRetryableError: undeclared
  event`. The next step receives the event's `output`, not the event.
- `async def` steps are awaited for you; write `await` directly in the step body.
- A step may run again (retry, rerun, a second run for the same thing). Make outside effects idempotent: key them on a business id (`f"deal-for-lead-{lead.id}"`) when the effect must happen once per thing; `ctx.step_key` covers the retries of one run only. See [../orchestration/retries.md](../orchestration/retries.md).
- Raise `ava.NonRetryableError(...)` for a failure no retry can fix. Call `ava.skip("reason")` to end
  the step as skipped. See [../orchestration/retries.md](../orchestration/retries.md) and
  [../orchestration/control-flow.md](../orchestration/control-flow.md).
- A parameter typed with an input model in a workflow without `input=` is not injected: `TypeError:
  missing 1 required positional argument` (on the operator: `TypeError: missing a required argument:
  '<name>'`). The same error appears when a parameter is only named like an input field.
- Do not keep state in module globals between steps. Pass values through outputs, or files through
  `ava.Workspace()`.
- Read secrets from the environment inside the step, never from parameters or return values: inputs and
  outputs are stored and shown.

## Logging

- `print`, the standard `logging` module and `ava.Logger` all work. On the operator, stdout, stderr and
  `logging` records at `INFO` and above are captured per step and listed in the run's activity.
- `DEBUG` records are not captured on the operator.
- One run keeps at most 10,000 log lines or 4 MiB; after that a single line says
  `[activity truncated: worker log budget exhausted]`.

## In the UI

- Node name: the function name. Node description: the first non-empty line of the docstring.
- Node detail: the function's source code, its data parameters (name, type name, JSON schema, required
  or not) and its return type. Injected parameters are not listed as inputs.
- Each run shows every step's status, saved output and log lines.
- Step names and docstrings are what a reviewer reads first: name steps `verb_object` and write the
  docstring as one plain sentence.

## See also

- [workflows.md](workflows.md): composing steps with `>>` and `&`.
- [inputs-and-files.md](inputs-and-files.md): run input, `ava.File`, `ava.Workspace`.
- [agent-steps.md](agent-steps.md), [classifier-steps.md](classifier-steps.md): model-backed steps.
- [../orchestration/retries.md](../orchestration/retries.md): `retries`, `on_uncertain`, error types.
- [../orchestration/control-flow.md](../orchestration/control-flow.md): `.map`, `ava.skip`, child runs.
- [../connect/integrations.md](../connect/integrations.md): `integrations=`.
- [../reference/python-api.md](../reference/python-api.md): every public name.
