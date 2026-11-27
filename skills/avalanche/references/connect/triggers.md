# Triggers

Every way a run starts. A workflow needs no trigger code to be started by hand or by API; a
webhook and a cron schedule are each one argument of `@ava.workflow`.

## When to use it

| The run starts when... | Use | Declared in code |
|---|---|---|
| A person starts it from a terminal | `ava run` | nothing |
| A person starts it from the browser | The operator's web UI | nothing |
| Your own service or script starts it | REST `POST /api/v1/runs` | nothing |
| Another system calls a URL | Webhook | `@ava.workflow(webhook=...)` |
| The clock says so | Cron schedule | `@ava.workflow(cron="...")` |
| Another workflow needs it | `ava.call` / `ava.start_run` | in the parent workflow |
| Something happens to a run that is already going | Not a trigger: `ava.wait_for_event`, see [waits](../orchestration/waits.md) | in the workflow |

All of these start the same workflow with the same input model. Addresses below are the local
operator's defaults (`ava dev` or `ava operator`): gRPC `localhost:7433`, webhooks
`http://127.0.0.1:7434`, web UI and REST `http://127.0.0.1:7435`. For hosted projects see
[cloud](../run/cloud.md).

## Interface

Trigger arguments of `@ava.workflow`:

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `input` | `ava.BaseInput` subclass | `None` | The run's input model. Every trigger's payload is validated against it. |
| `webhook` | `ava.Webhook`, `True`, `False`, `None` | `None` | Gives the workflow a POST URL. `True` is `ava.Webhook()`. |
| `cron` | `str` | `None` | Cron expression for scheduled runs. |
| `key` | `tuple[str, ...]` | none | Input fields that make starts idempotent. See [run keys](../orchestration/retries.md#run-keys). |

`ava.Webhook(path=None)`:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `path` | `str` or `None` | `None` | URL path of the webhook. `None` uses `/webhooks/<root>/<file path without .py>/<workflow name>`, where `<root>` is the name of the folder the operator scans. |

A workflow is addressed by its selector, `<file>::<name>` (`flows.py::triage_ticket`), or by its
bare name when only one workflow has that name.

### Inputs per trigger

| Trigger | Where the input comes from |
|---|---|
| `ava run` | `--input 'JSON'`, plus `--file FIELD=PATH` and `--workspace FIELD=DIR` for file fields |
| Web UI | A form built from the input model |
| REST | `input_json` in the body |
| Webhook | The POST body, which must be one JSON object |
| Cron | The input stored when the schedule was enabled, else the model's defaults. The same stored input is replayed on every occurrence; enabling is refused while a required field has no value |
| `ava.call` / `ava.start_run` | The `input` argument; with invalid input the child run fails |

What invalid input does depends on the fault and on `key=`. Only the first row and the keyed
column refuse the start; elsewhere a run is created and fails.

| Fault | `ava run` / REST, no `key=` | `ava run` / REST, with `key=` | Webhook, no `key=` | Webhook, with `key=` |
|---|---|---|---|---|
| A required field is missing | No run: `INVALID_ARGUMENT: Input is missing required fields: a, b` (REST `400`) | No run: `INVALID_ARGUMENT: missing run key field '<name>'` for a key field (REST `400`); `FAILED_PRECONDITION: Invalid machine input/payload: <field>: missing` for another field (REST `409`) | `202` and a run id; the run fails | `409`, no run |
| Wrong type, broken constraint (`ge=`, `le=`), unknown field | A run id is printed, exit 0 (REST `202`); the run fails at once | No run: `FAILED_PRECONDITION: Invalid machine input/payload: <field>: <error type>` (REST `409`) | `202` and a run id; the run fails | `409`, no run |

A run that fails on its input has `error_code: input_invalid`, `error_message: Run failed` and no
steps. The reason (`The run's input is invalid: <field>: <message> (<Model>)`) is only in the run
log: see [debugging](../guides/debugging.md#investigate-a-failed-run-from-the-command-line). The
webhook `409` body is always `{"error": "workflow could not be started"}`, without the reason.

File and folder inputs are covered in [inputs and files](../building-blocks/inputs-and-files.md).

### `ava run`

```bash
ava run FLOW [--input JSON] [--context JSON] [--file FIELD=PATH]... [--workspace FIELD=DIR]...
             [--rerun-of RUN_ID --from SLUG... [--only]] [--connect HOST:PORT]
```

Prints the new run id and returns at once. Follow with:

```bash
ava result RUN_ID --wait --json            # wait, then print the output as JSON
ava result RUN_ID --wait --output-dir out  # wait, then write the output and its files to a new folder
ava run list --flow FLOW                   # statuses
```

`--rerun-of` starts a [rerun](../orchestration/retries.md#reruns).

### Web UI

`ava dev` opens the UI; with `ava operator` open the web address yourself. Pick the workflow,
fill the input form, start. The UI also answers decisions and starts reruns. `ava tui` is the
terminal UI.

### REST API

```text
POST /api/v1/runs
Content-Type: application/json

{"workflow_selector": "triage_ticket", "input_json": {"subject": "down", "priority": 1}}
```

| Body field | Type | Default | Meaning |
|---|---|---|---|
| `workflow_selector` | `str` | required | Selector or unique name. |
| `input_json` | object | `{}` | The run input. |
| `context_json` | object | `{}` | The run context, when the workflow declares one. |
| `run_id` | `str` | generated | Your own run id. A used id is refused with `409 ALREADY_EXISTS`. |

Answer: `202` with `{"run_id": "...", "read_state": "retained", "key_json": ""}`. Unknown body
fields are refused. The body limit is 4 MiB. Other useful routes: `GET /api/v1/flows`,
`GET /api/v1/runs/{run_id}`, `GET /api/v1/runs/{run_id}/result`,
`POST /api/v1/runs/{run_id}/cancel`. Full list: [operator API](../reference/operator-api.md).

### Webhook

```bash
ava webhooks list [--connect HOST:PORT]        # every webhook: selector, method, path, url, active
ava webhooks get SELECTOR [--connect HOST:PORT]
```

| Request | Answer |
|---|---|
| `POST <url>` with `Content-Type: application/json` and a JSON object body up to 1 MiB | `202 {"run_id": "run_..."}`; the body is the run input |
| Unknown path | `404 {"error": "unknown webhook route"}` |
| Another content type | `415 {"error": "Content-Type must be application/json"}` |
| Body is not JSON, or not an object | `400` |
| Body over 1 MiB | `413` |
| `GET`, `PUT`, `PATCH`, `DELETE` | `405 {"error": "POST is required"}` |
| The run could not be started | `409 {"error": "workflow could not be started"}` |

The webhook server listens on `127.0.0.1` only, on the operator's webhook port (`7434`;
`ava operator --webhook-port N` changes it). Request headers and query strings are not passed to
the workflow and nothing is verified: there is no signature or token check.

### Cron schedule

```bash
ava schedule list [--connect HOST:PORT]                     # JSON: selector, cron, enabled, input, next_run_at, last_run_at
ava schedule enable SELECTOR [--input JSON] [--connect HOST:PORT]
ava schedule disable SELECTOR [--connect HOST:PORT]
```

```text
POST /api/v1/flows/{workflow_selector}/schedule
{"enabled": true, "input_json": {"region": "eu"}}
```

- A schedule starts disabled. Declaring `cron=` alone starts nothing until it is enabled.
- `--input` is the input every scheduled run starts with. Omitted, the stored input is kept;
  with none stored, the input model's defaults are used.
- A scheduled run replays that stored input unchanged. A job that processes "today's items" must
  compute the date and fetch its data in a source step, not expect them in the input.
- Whether a schedule is on, its input and its last run are stored in the operator's state
  database and apply again after a restart.
- `ava dev --schedules-enabled` and `ava operator --schedules-enabled` start with every schedule
  on, for that process only.
- The first run is the next cron occurrence after the minute in which it was enabled.

### Events for a run that already exists

A callback from another system to a run that is waiting is an event, not a trigger. The sender
makes two calls: find the run by its [run key](../orchestration/retries.md#run-keys), then send
the event. Full interface: [waits](../orchestration/waits.md#sending-an-event-from-another-system).

### From another workflow

`ava.call(child, input)` starts a child run and waits for its output; `ava.start_run(child, input)`
starts it and continues. See [control flow](../orchestration/control-flow.md#child-workflows).

## Example

```python
import avalanche as ava


class Ticket(ava.BaseInput):
    ticket_id: str
    subject: str
    priority: int = 3


@ava.step
def route_ticket(ticket: Ticket) -> str:
    """Pick the queue for a ticket."""
    return "urgent" if ticket.priority <= 1 else "normal"


@ava.workflow(input=Ticket, webhook=ava.Webhook(path="/hooks/tickets"), key=("ticket_id",))
def triage_ticket():
    """Route an incoming ticket, once per ticket id."""
    return route_ticket(ava.input)


class Region(ava.BaseInput):
    region: str = "eu"


@ava.step
def count_open_tickets(region: Region) -> int:
    """Count the open tickets of a region."""
    return 0


@ava.workflow(input=Region, cron="0 8 * * 1-5")
def report_open_tickets():
    """Report open tickets every weekday at 08:00."""
    return count_open_tickets(ava.input)
```

```bash
ava dev flows.py

# by hand
ava run triage_ticket --input '{"ticket_id": "T-1", "subject": "down", "priority": 1}'

# by webhook
ava webhooks list
curl -X POST http://127.0.0.1:7434/hooks/tickets \
  -H 'Content-Type: application/json' \
  -d '{"ticket_id": "T-2", "subject": "slow", "priority": 2}'      # {"run_id": "run_f58b9dab"}

# by REST
curl -X POST http://127.0.0.1:7435/api/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"workflow_selector": "triage_ticket", "input_json": {"ticket_id": "T-3", "subject": "hi"}}'

# by schedule
ava schedule enable flows.py::report_open_tickets --input '{"region": "us"}'
```

## Rules

- Declare `input=` with a typed model on every triggered workflow. It is the contract of every trigger.
- Webhook senders retry. Give a webhook workflow a `key=` on the sender's event id so a redelivery
  returns the first run instead of starting a second one.
- A webhook on a workflow without `key=` answers `202` before the input is validated. Do not
  treat `202` as "accepted and valid"; check the run. With `key=`, an invalid body gets `409` and
  no run.
- The webhook body must be a JSON object that matches the input model. If the sender's payload
  has another shape, model that shape (nested models, optional fields) rather than expecting
  top-level fields that are not sent.
- To expose a webhook beyond the machine, put your own authenticated proxy or tunnel in front of
  the webhook port and verify the sender there.
- Two workflows with the same webhook path make discovery fail with
  `Webhook route collision for <path>: ...`. A path must start with `/`, with no query, fragment,
  empty, `.` or `..` segment (`ValueError: Webhook path must ...`).
- The default webhook path contains the scanned folder's name and the file path; it changes when
  files move. Set `path=` for a URL you hand to someone.
- A cron expression is checked at discovery; an invalid one is a discovery error. It is matched
  once per minute against the operator machine's local time.
- Occurrences that pass while the operator is stopped are not caught up.
- Cron does not check for an earlier run still going: a slow workflow on a tight schedule
  overlaps itself. Add a `key=` on a date or period field if a period must run once.
- A webhook or scheduled run has no starting user: a built-in integration connected per user
  cannot be used there (see [integrations](integrations.md)).
- `ava webhooks get` needs the full selector as printed by `ava webhooks list`.

## In the UI

- Each workflow shows its triggers: the cron expression with on/off state and next run time, and
  the webhook URL.
- The start form is built from the input model: name and type its fields for the person who
  fills it in, and give defaults where a value is usual.
- Each run records what started it, as `summary.triggered_by` in `ava run show --json`: `manual`
  (started from the web UI or the terminal UI), `api` (`ava run`, REST, a child started by
  `ava.call` / `ava.start_run`), `webhook` or `scheduled`.

## See also

- [Workflows](../building-blocks/workflows.md) and [inputs and files](../building-blocks/inputs-and-files.md)
- [Retries, reruns and run keys](../orchestration/retries.md)
- [Waits](../orchestration/waits.md): sending an event to a running run
- [Local](../run/local.md), [self-hosting](../run/self-hosting.md), [cloud](../run/cloud.md)
- [ava CLI](../reference/ava-cli.md) and [operator API](../reference/operator-api.md)
