# Retries, reruns, run keys and statuses

What happens when a step fails, how to run part of a run again, how to make a start idempotent,
and what each run status means.

---

## Step retries

A step that raises is run again automatically, up to a fixed number of attempts. When the
attempts are used up the step fails and the run fails.

### When to use it

- Leave the default for anything that can fail for a passing cause: network calls, model calls, rate limits.
- Lower `retries` (often to `0`) for steps with side effects that must not repeat: payments, emails, tickets.
- Raise `ava.NonRetryableError` for failures that another attempt cannot fix: bad input, a refusal, a missing record.
- To try again later with a pause, or after a fix, use a [rerun](#reruns), not a retry.

### Interface

The same two options exist on `@ava.step`, `@ava.source`, `@ava.dest`, `@ava.agent_step`,
`ava.agent.step(...)`, `ava.agent.step.map(...)` and `@ava.classifier_step`.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `retries` | `int` >= 0 | `7` | Extra attempts after the first. Total attempts = `retries + 1`. Values above 7 act as 7: a step never gets more than 8 attempts. |
| `on_uncertain` | `"retry"`, `"hold"`, `"fail"` | `"retry"` | What to do when the step raises `ava.OutcomeUnknownError`. See below. |

There is no backoff and no delay option: a failed attempt is run again at once. If you need a
pause, sleep inside the step before raising, or use a rerun.

Exceptions and what they do:

| Raised in a step | Effect |
|---|---|
| Any exception | Retried until attempts run out, then the step and the run fail. |
| `ava.NonRetryableError` (or a subclass) | Never retried. The step and the run fail at once. |
| `ava.OutcomeUnknownError` | The outside action may have happened. Handled by `on_uncertain`. |
| `ava.StepSkipped` (from `ava.skip`) | Not a failure, never retried. See [control flow](control-flow.md#skip). |

`on_uncertain` for `ava.OutcomeUnknownError`:

| Value | Effect |
|---|---|
| `"retry"` | Treated as an ordinary failure and retried. Only safe if the action is idempotent. |
| `"fail"` | The step fails at once, no retry. |
| `"hold"` | The step is parked and the run waits for a person. The step must have a return annotation. |

A held step appears as a decision with the prompt `Reconcile uncertain outcome`. It is answered
like any decision (see [approvals](approvals.md)) at the step's node id, with one of:

| Answer | Effect |
|---|---|
| `{"kind": "done", "output": <value>}` | The step succeeds with `output`, which must fit its return type. |
| `{"kind": "retry"}` | One more attempt. |
| `{"kind": "fail"}` | The step fails. |

Errors that are already non-retryable: a missing or expired integration account
(`IntegrationNotConnectedError`, `IntegrationReconnectError`, `IntegrationError`), a decision
that timed out with no `on_timeout`, a failed child run, and invalid run input.

Idempotency keys for outside calls, by what must happen once:

| The effect must happen once per | Send as the idempotency key |
|---|---|
| Thing (lead, order, invoice), whatever run handles it | A business id you build: `f"deal-for-lead-{lead.id}"`. |
| Run | `ava.RunContext.step_key`: `<run_id>:<node_id>`, plus `:<index>` for a map item. |

`step_key` is the same on every attempt of a step in one run, and only there: a second run of
the same input and a rerun are new runs with a new `step_key`.

### Example

```python
import avalanche as ava


class Report(ava.BaseInput):
    name: str


@ava.step
def fetch_rows(report: Report) -> list[int]:
    """Fetch the report rows."""
    return [1, 2, 3]


@ava.step(retries=2)
def compute_total(rows: list[int]) -> int:
    """Total the rows; tried up to 3 times."""
    return sum(rows)


@ava.step(retries=0, on_uncertain="hold")
def publish_total(total: int, report: Report) -> str:
    """Publish the total once; never repeated automatically."""
    if total < 0:
        raise ava.NonRetryableError("a negative total cannot be published")
    # send f"report-total-{report.name}" (a business id) as the idempotency key of the outside
    # call; raise ava.OutcomeUnknownError("gateway timeout") when it times out mid-call
    return f"published {total}"


@ava.workflow(input=Report)
def build_report():
    """Fetch rows, total them, publish."""
    rows = fetch_rows(ava.input)
    return compute_total(rows) >> publish_total()
```

### How agent-step retries differ

- The options and defaults are the same (`retries=7`, `on_uncertain="retry"`).
- A retry runs the whole agent again from its first turn: new model calls, new tool calls, new
  cost. Nothing from the failed attempt is reused.
- Tools the agent already called are called again. Make tool side effects idempotent or lower `retries`.
- A code mistake in an agent step (wrong signature field, bad output type) is retried 8 times
  by default. While developing, set `retries=0` to fail fast.

### Rules

- Decide `retries` per step by asking "is it safe and useful to run this exact step again right now?".
- Do not write your own retry loop around a whole step body; the step's attempts already do it.
  A loop around one flaky call inside a step is fine.
- Code errors are retried like any other exception: a `TypeError` in a step with default
  settings fails only after 8 attempts.
- `ava.sleep`, `ava.wait_for_event`, `ava.decision`, `ava.call` and `ava.start_run` are never retried.
- Each map item has its own attempts.
- `retries` must be an `int`, not `True`/`"3"`; a negative value is refused when the step is defined.

### What a run looks like after a failure

- The run's status is `failed`. Its error line names the error type and step:
  `RuntimeError in step compute_total_1`.
- The failed step has `error` (`RuntimeError: still down`), `attempt` and `max_attempts`
  (`3` of `3`), and the error of its last attempt.
- Steps that did not finish are shown as `skipped`. Steps that succeeded keep their saved outputs.
- `ava result RUN_ID --json` exits with code 1 and a traceback ending in
  `Run <id> ended with status failed`. Read the failure with `ava run show RUN_ID --json`.
- Nothing resumes by itself. Fix the cause, then rerun from the failed step. A workflow with
  `key=` cannot be rerun: see [run keys](#run-keys).

---

## Reruns

A rerun starts a new run from chosen steps of an earlier run. Steps before them are not run
again: their saved outputs are reused.

### When to use it

- A run failed late and the early steps were slow or costly (agents, uploads).
- You changed one step's code and want to see its effect on the same inputs.
- Not for a run that is still going, not for state machines, and not for a workflow with
  `key=`: a keyed run cannot be rerun, see [run keys](#run-keys).

### Interface

```bash
ava run FLOW --rerun-of RUN_ID --from SLUG [--from SLUG ...] [--only] [--input JSON] [--file FIELD=PATH]
```

| Flag | Meaning |
|---|---|
| `--rerun-of RUN_ID` | The finished, failed or cancelled run to start from. Needs at least one `--from`. |
| `--from SLUG` | A step to run again, together with everything after it. Repeatable. |
| `--only` | Run only the `--from` steps, nothing after them. |
| `--input`, `--file` | Replace the input fields they name. Without them the source run's input is used. |

The command prints the new run id. The operator's web UI has the same action on a run (a rerun
dialog where you pick the steps). The REST API route that starts a run does not take a rerun.

A step's slug is its function name, or the `slug=` given to it. A second use of the same step in
one workflow gets `<slug>_2`. Give steps you expect to rerun from an explicit `slug=`.

In Python the request is `ava.Rerun(run_id=..., start=("slug",), mode="autorun")`; `mode="lazy"`
is `--only`. Inside a step, `ava.RunContext.rerun` holds it, or `None` on a normal run.

What is reused:

| Step | In the new run |
|---|---|
| A `--from` step and every step after it | Runs again, with the current code. |
| Any step before them whose output a running step (or the workflow's return) reads | Not run. Its saved output is loaded and checked against its return type in the current code. Shown as `reused`. |
| Other earlier steps | Not run. Shown as `reused` if the source run has them. |

The rerun is checked before anything starts. If it cannot work it is refused and no run is created:

```text
Rerun refused, nothing was started:
  compute_total: missing_from_source: run run_130396cb has no saved output for this step
Earliest step to rerun from: compute_total
```

| Refusal reason | Meaning |
|---|---|
| `unknown_start_step` | No step with that slug in the current code. |
| `missing_from_source` | A step whose output is needed did not succeed in the source run. Start from it instead. |
| `not_reusable` | The step succeeded but its return value could not be saved as JSON. |
| `output_unavailable` | The saved output or a file it refers to can no longer be loaded. |
| `output_does_not_fit` | The saved output no longer matches the step's return type in the current code. |
| `source_run_active` | The source run is still going. |

`Earliest step to rerun from` names the `--from` that would pass.

### Example

```bash
ava run build_report --input '{"name": "q3"}'          # prints run_130396cb; publish_total fails
# fix the cause
ava run build_report --rerun-of run_130396cb --from publish_total
ava result run_1fd2e13a --wait --json
```

In the new run `fetch_rows` and `compute_total` show `reused`; only `publish_total` runs.

### Rules

- Return typed, JSON-friendly values from steps (Pydantic models, lists, dicts, scalars, `ava.File`)
  so their outputs can be reused.
- A rerun is a new run with a new id; the source run is unchanged. The new run records which
  run it came from.
- The current code runs, not the code of the source run. If you changed a return type upstream,
  start the rerun from that step.
- A reused map counts as one step: its whole collected list is reused.
- `--from` and `--only` without `--rerun-of` fail: `--from and --only need --rerun-of`.
- State machines are not rerun (`Machine reruns are not supported; use a new key or retry an
  interrupted state entry`). See [state machines](../building-blocks/state-machines.md).
- Workflows with `key=` are not rerun either. What `--rerun-of` does on one depends on the input:
  without the key fields, `INVALID_ARGUMENT: missing run key field '<name>'`; with the key of an
  existing run, it prints that run's id, exits 0 and starts nothing; with a new key,
  `FAILED_PRECONDITION: keyed flow runs cannot be rerun from a step`.
- A rerun gives its steps a new `step_key`. An effect that must not repeat across reruns needs
  an idempotency key built from a business id.

---

## Run keys

A run key makes starting a run idempotent: for one workflow, each distinct key value has exactly
one run, forever. Starting again with the same key returns the existing run instead of a new one.

### When to use it

- The same trigger can arrive twice: webhook redelivery, a double click, a retried API call, an overlapping cron.
- "Process each invoice / order / ticket id once."
- Not when the same input should be processable again: a key is permanent. Leave the workflow
  unkeyed, or add a field (a date, an attempt number) to the key.
- A keyed run that failed keeps its key and cannot be rerun. If a failed run must be startable
  again, put an `attempt` field in the key from the start (see Rules).

### Interface

```python
@ava.workflow(input=MyInput, key=("field_a", "field_b"))
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `key` | `tuple[str, ...]` | none | Ordered names of input fields that identify the run. Needs `input=`. |

Field types allowed in a key: `str`, `int`, `float`, `bool`, `None`, `UUID`, `date`, `datetime`,
`Literal`, `Enum`, and `list` / `tuple` / `set` / `dict` / plain nested models of those.

Finding the run of a key:

```bash
ava run show --flow book_invoice --key 'invoice_id="INV-1"' --json
```

```text
GET /api/v1/runs:resolve?workflow_selector=book_invoice&key_fields={"invoice_id":"INV-1"}
```

`--key` is `FIELD=JSON`, repeatable, one per key field. The workflow object also has
`workflow.key(input)`, which returns the key tuple for an input.

### Example

```python
import avalanche as ava


class Invoice(ava.BaseInput):
    invoice_id: str
    amount: float = 0.0


@ava.step
def record_invoice(invoice: Invoice) -> str:
    """Record one invoice."""
    return f"{invoice.invoice_id}:{invoice.amount}"


@ava.workflow(input=Invoice, key=("invoice_id",))
def book_invoice():
    """Book each invoice id once."""
    return record_invoice(ava.input)
```

```bash
ava run book_invoice --input '{"invoice_id": "INV-1", "amount": 5}'     # run_bbf4ba12
ava run book_invoice --input '{"invoice_id": "INV-1", "amount": 999}'   # run_bbf4ba12 again
```

### Rules

- The second start returns the first run whatever its status, including `failed`. Its other
  input fields are ignored: the first run's input stands.
- A failed keyed run holds its key forever: it cannot be rerun from a step, and starting again
  returns the failed run. To allow another try, add a field to the key and raise it:
  `attempt: int = 1` with `key=("invoice_id", "attempt")`, then start with `"attempt": 2`. That is
  a new run from the first step; make its outside effects idempotent on a business id.
- Changing `key=` in the code changes the identity: runs started under the old key fields are
  no longer found by key (`NOT_FOUND`), only by run id.
- Invalid input on a keyed workflow is refused and no run is created:
  `FAILED_PRECONDITION: Invalid machine input/payload: <field>: <error type>`.
- The key applies to every way of starting: CLI, UI, REST, webhook, cron, `ava.call`, `ava.start_run`.
- A start without a key field fails: `missing run key field 'invoice_id'`.
- Key fields must be plain: no alias, no `default_factory`, no custom validators or serializers
  on the field or its model. Otherwise the workflow fails to build with `run key field '<name>': ...`.
- Keyed workflows need the operator or Cloud. An embedded `.run()` raises
  `RuntimeError: Keyed workflows require a run-capable scheduler host ...`; use `workflow.test()`.
- Without a key, the only identity is the run id. `POST /api/v1/runs` accepts your own `run_id`;
  reusing one is refused with `409 ALREADY_EXISTS`, it does not return the old run.

---

## Run statuses

| Run status | Meaning | Final |
|---|---|---|
| `queued` | Accepted, not started yet. | no |
| `running` | At least one step is executing. | no |
| `waiting` | Nothing is executing; the run is parked on a sleep, event, decision or child run. | no |
| `succeeded` | Every step ended as success or skipped. | yes |
| `failed` | A step failed for good, or the run could not be prepared. | yes |
| `cancelled` | Cancelled by a person or by its parent run. | yes |
| `unknown` | The host cannot tell. | no |

A `waiting` run carries what it waits on: `waiting on decision`, `waiting on event`,
`waiting on run`, `waiting on sleep`, with `+N` when there are several.

Step statuses inside a run: `pending`, `running`, `success`, `failed`, `skipped`, `reused`.
A step parked by `on_uncertain="hold"` stays `running` while the run is `waiting`.

```bash
ava run list [--flow FLOW] [--json]     # TYPE  RUN  DEFINITION  STATUS  NOTE
ava run show RUN_ID --json              # the run with every step
ava run cancel RUN_ID
ava result RUN_ID --wait --json         # waits for a final status, prints the output
```

Only `succeeded` runs have an output. All commands take `--connect HOST:PORT` (default
`localhost:7433`). Full command and route lists: [ava CLI](../reference/ava-cli.md),
[operator API](../reference/operator-api.md).

## See also

- [Control flow](control-flow.md): map item failures, skips, child run failures
- [Waits](waits.md) and [approvals](approvals.md): the `waiting` status
- [Triggers](../connect/triggers.md): every way a run starts
- [Debugging](../guides/debugging.md) and [errors](../reference/errors.md)
