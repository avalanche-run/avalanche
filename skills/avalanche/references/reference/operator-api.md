# Local operator REST API reference

The HTTP API of the local operator (`ava dev`, `ava operator`, `ava web`): start runs, read their state and results, answer waits, control schedules and machines. It is a JSON layer over the operator's gRPC service.

Related: [`ava` CLI](ava-cli.md) (the same operations from a terminal), [running locally](../run/local.md), [triggers](../connect/triggers.md), [waits](../orchestration/waits.md), [approvals](../orchestration/approvals.md), [state machines](../building-blocks/state-machines.md).

## Address and auth

| Listener | Default address | Started by | Serves |
| --- | --- | --- | --- |
| Browser UI + REST | `http://127.0.0.1:7435`, REST under `/api/v1` | `ava dev`, `ava operator` (`--web-port`, off with `--no-web`), `ava web` (`--port`) | this API, the UI, a gRPC-Web proxy |
| Webhooks | `http://127.0.0.1:7434` | `ava dev`, `ava operator` (`--webhook-port`) | one `POST` route per workflow with `webhook=` |
| gRPC | `127.0.0.1:7433` | `ava dev`, `ava operator` (`--port`, `--host`) | `avalanche.operator.OperatorServiceV2`, used by the CLI and the Python client |
| Login callback | `127.0.0.1:7436` | `ava dev`, `ava operator` (`--login-port`) | where a browser login for an integration returns |

- **No authentication.** No route checks a token or a user. The REST and webhook listeners bind to loopback. `ava web --host` refuses a non-loopback host without `--trusted-proxy`; `ava operator --host` accepts one but logs a warning. Put an authenticating proxy in front before exposing either.
- The machine-readable description is at `GET /api/openapi.json`.
- `GET /api` and any unknown path under `/api` answer `404` with the error body below.

## Request rules

- `POST` needs `Content-Type: application/json`, else `415`. A route with a body also needs `Content-Length`, else `411`. `Transfer-Encoding` is refused (`400`). Bodies are limited to 4 MiB (`413`).
- Bodies are strict: an unknown field, a wrong type (a number where a string is expected), `NaN` or `Infinity` is a `400`.
- Query parameters: an unknown name, a repeated name, or more than 16 parameters is a `400`.
- Path parameters are percent-decoded once, so encode `/` and `:` inside a selector or run ID (`review_flow.py%3A%3Areview_order`). A bare workflow name needs no encoding.
- A workflow is named by its bare name or its full selector `file.py::name`; an ambiguous bare name is refused.
- Routes that change a wait or a machine take an `Idempotency-Key` header (required, non-empty; at most 256 characters for machine events). Sending the same request again with the same key does not apply it twice.

## Responses and errors

Success bodies are protobuf JSON: field names are snake_case, 64-bit integers are decimal strings (`"revision": "16"`), and several fields hold JSON encoded as a string (their names end in `_json`). Every response carries `Cache-Control: no-store`.

Errors always have this body:

```json
{"error": {"code": "NOT_FOUND", "message": "Unknown workflow: nope"}}
```

| HTTP | `code` | Typical cause |
| --- | --- | --- |
| 400 | `INVALID_ARGUMENT`, `OUT_OF_RANGE`, `BAD_REQUEST` | invalid body, query or input (`missing run key field 'order_id'`) |
| 401 / 403 | `UNAUTHENTICATED` / `PERMISSION_DENIED` | refused by the operator |
| 404 | `NOT_FOUND` | unknown route (`Unknown API route`), workflow, run (`Run X not found`), event key (`unknown_key`) |
| 405 | `METHOD_NOT_ALLOWED` | wrong method; `Allow` lists the right ones |
| 409 | `FAILED_PRECONDITION`, `ALREADY_EXISTS`, `ABORTED` | run not finished (`Run X is not terminal`), run failed (`Run X ended with status failed`), wait already answered (`already_resolved_or_withdrawn`), run closed (`run_closed`) |
| 411 / 413 / 415 | `LENGTH_REQUIRED` / `REQUEST_ENTITY_TOO_LARGE` / `UNSUPPORTED_MEDIA_TYPE` | request rules above |
| 422 | `UNPROCESSABLE_CONTENT` | payload does not fit the wait or machine event (`invalid_wait_submission`, `Invalid machine input/payload: note: missing`) |
| 429 / 503 / 504 / 501 | `RESOURCE_EXHAUSTED` / `UNAVAILABLE` / `DEADLINE_EXCEEDED` / `UNIMPLEMENTED` | operator busy, unreachable, slower than 30 s, or route not supported |

## Routes

All paths are under `/api/v1`. 22 routes.

| Method | Path | Does |
| --- | --- | --- |
| GET | `/flows` | list discovered flows and machines |
| POST | `/flows/{workflow_selector}/schedule` | enable or disable a cron schedule |
| GET | `/runs` | list run summaries |
| POST | `/runs` | start a run |
| GET | `/runs:resolve` | find a run by its key |
| GET | `/runs/{run_id}` | run snapshot |
| POST | `/runs/{run_id}/cancel` | cancel a run |
| GET | `/runs/{run_id}/links` | parent and child runs |
| GET | `/runs/{run_id}/waits` | open waits of a run |
| POST | `/runs/{run_id}/events/{key}` | deliver an event |
| POST | `/runs/{run_id}/decisions/{node}` | answer a decision |
| GET | `/runs/{run_id}/result` | result of a run, a step or a state entry |
| GET | `/runs/{run_id}/output` | raw stored output of a finished run |
| GET | `/runs/{run_id}/activity` | logs and events of a run |
| GET | `/runs/{run_id}/evaluations` | evaluation records of a run |
| GET | `/runs/{run_id}/machine` | machine run: state, accepted events |
| POST | `/runs/{run_id}/machine/events/{name}` | send a machine event |
| GET | `/runs/{run_id}/machine/history` | machine transitions |
| GET | `/runs/{run_id}/machine/entries/{entry}` | one state entry |
| GET | `/runs/{run_id}/machine/entries/{entry}/events` | events of one state entry |
| POST | `/runs/{run_id}/machine/entries:retry` | retry the current state entry |
| POST | `/runs/{run_id}/machine/timers:fire-next` | fire the next machine timer now (`ava dev` only) |

### Flows

#### `GET /flows`

| Query | Default | Meaning |
| --- | --- | --- |
| `page_size` | `100` | items per page, at least 1 |
| `continuation` | none | the `next_page` object of the previous response, JSON-encoded, unchanged |

Response `200`: `{cursor, flows: [...], next_page?}`. Each flow:

| Field | Meaning |
| --- | --- |
| `workflow_selector`, `workflow_id` | full selector, `file.py::name` |
| `display_name`, `file_path` | function name; file relative to its scan target |
| `version`, `manifest_digest` | hashes of the definition |
| `node_ids`, `node_slugs` | step node IDs (`load_order_1`) and their slugs (`load_order`) |
| `topology` | `node_ids`, `graph` (`{node: {children: [...]}}`), `node_types` (`source`, `step`, `dest`, `wait`, `run`, `machine`), `display_names`, `standard_step_docstring_lines` (first docstring line per step), `step_interface_json`, `input_declaration_json`, `agent_*`, `classifier_metadata_json`, `evaluation_metadata_json`, `map_specs` |
| `input_schema_json` | JSON Schema of the input model, as a string; empty without `input=` |
| `key_fields` | the `key=` field names |
| `cron`, `schedule_enabled`, `schedule_input_json`, `next_run_at`, `last_run_at` | schedule; times are epoch seconds, `0` when unset |
| `webhook_path`, `webhook_url`, `webhook_active` | webhook route; empty strings without `webhook=` |
| `node_policies_json` | per step `{"retries": 7, "on_uncertain": "retry"}` as a string |
| `node_waits_json`, `node_runs_json` | wait and run-start declarations per node, as strings |
| `machine_spec_json` | the machine definition as a string; empty for a flow |
| `agent_node_ids`, `agent_declarations_json`, `integration_declarations_json` | agent steps and declared integrations |

#### `POST /flows/{workflow_selector}/schedule`

Body:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `enabled` | bool | yes | `true` lets the cron schedule start runs, `false` stops it |
| `input_json` | object | no | input of future cron runs, only when enabling; omitted keeps the stored input |

Response `200`: the flow record of `GET /flows`. Schedules start disabled; the choice and the input are stored in the state database and apply again after a restart.

```bash
curl -s -X POST http://127.0.0.1:7435/api/v1/flows/daily_report/schedule \
  -H 'Content-Type: application/json' -d '{"enabled": true, "input_json": {"region": "eu"}}'
```

### Runs

#### `POST /runs`

Body:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `workflow_selector` | string | required | flow or machine to run |
| `run_id` | string | `run_<32 hex>` | caller-chosen run ID, at most 256 bytes; a second start with an ID that exists is refused with `409` `ALREADY_EXISTS` |
| `input_json` | object | `{}` | run input, validated against the workflow's `input=` model |
| `context_json` | object | `{}` | run context, validated against `context=` |

Response `202`: `{"run_id": "...", "read_state": "retained", "key_json": "[\"A1\"]"}`. The run is started, not finished. For a keyed workflow, a start whose key already has a run returns that run's ID instead of starting another. Invalid `input_json`: a missing required field is `400` (`Input is missing required fields: ...`, `missing run key field '...'`); any other fault is `409` `Invalid machine input/payload: FIELD: ERROR_TYPE` on a keyed workflow, and on any other workflow still `202`, with a run that fails at once (`error_code: input_invalid`). Files and workspaces cannot be attached here: use `ava run --file/--workspace` or the Python client.

```bash
curl -s -X POST http://127.0.0.1:7435/api/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"workflow_selector": "review_order", "input_json": {"order_id": "A1"}}'
```

#### `GET /runs`

| Query | Default | Meaning |
| --- | --- | --- |
| `workflow_selector` | all | only runs of this flow or machine |
| `page_size` | `100` | items per page |
| `continuation` | none | `next_page` of the previous response, JSON-encoded |

Response `200`: `{cursor, runs: [...], scope_ref, next_page?}`. Each run summary:

| Field | Meaning |
| --- | --- |
| `run_id`, `workflow_selector`, `workflow_display_name` | identity |
| `kind` | `flow` or `machine` |
| `status` | `queued`, `running`, `waiting`, `succeeded`, `failed`, `cancelled` or `unknown` |
| `lifecycle` | the operator's raw state: `requesting`, `pending`, `running`, `waiting`, `success`, `failed`, `cancelled` |
| `waiting_on` | counts of open waits: `{operator, decision, event, run, sleep}` |
| `triggered_by`, `triggered_at` | `api` (`ava run`, REST, child runs), `manual` (web UI, terminal UI), `webhook` or `scheduled`; epoch seconds |
| `skips` | present when a step was skipped: `count`, `first_step` (node ID), `first_reason` |
| `started_at`, `ended_at` | operator clock readings, usable only for durations |
| `key_json`, `read_state` | the run key as a JSON string; `retained` or `expired` |
| `error_code`, `error_step`, `error_message` | on failure, for example `node_failed`, `summarize_order_1`, `TypeError in step summarize_order_1` |
| `revision`, `created_sequence` | change counters |

#### `GET /runs:resolve`

| Query | Required | Meaning |
| --- | --- | --- |
| `workflow_selector` | yes | keyed flow or machine |
| `key_fields` | yes | JSON object of the key fields, for example `{"order_id":"A1"}` |

Response `200`: `{run_id, read_state, key_json}` of the run that owns the key.

```bash
curl -s -G http://127.0.0.1:7435/api/v1/runs:resolve \
  --data-urlencode 'workflow_selector=review_order' --data-urlencode 'key_fields={"order_id":"A1"}'
```

#### `GET /runs/{run_id}`

Response `200`: the run snapshot.

| Field | Meaning |
| --- | --- |
| `summary` | the run summary of `GET /runs` |
| `nodes` | one entry per step: `node_id`, `name`, `node_type`, `status` (`pending`, `running`, `success`, `failed`, `skipped`, `reused`), `started_at`, `ended_at`, `step_io` (`inputs` and `output` previews with `type_name`, `preview_json`, `truncated`), and on failure `error`, `attempt`, `max_attempts`, `last_attempt_error` |
| `topology` | as in `GET /flows` |
| `input` | `values_json` (the run input as a string) and `supplied_fields` |
| `machine_json` | machine state for a machine run |
| `terminal_seal` | final status and reason once the run ended |
| `expansions`, `log_continuation`, `latest_log_sequence`, `scope_ref`, `cursor`, `read_state` | map expansions and paging references |

#### `POST /runs/{run_id}/cancel`

No body (still send `Content-Type: application/json`). Response `200`: `{"run_id": "..."}`. Cancelling a finished run also answers `200`.

#### `GET /runs/{run_id}/links`

Response `200`: `{"parents": [...], "children": [...]}`: the runs that started this run, and the runs it started with `ava.start_run` or `ava.call`.

### Waits

#### `GET /runs/{run_id}/waits`

| Query | Default | Meaning |
| --- | --- | --- |
| `offset` | `0` | first wait to return |
| `limit` | `64` | 1 to 64 |

Response `200`: `{"waits_json": ["...", ...], "receipt_json": ""}`. Each `waits_json` item is a JSON string:

```json
{"node_id": "decision_1", "kind": "decision", "state": "waiting",
 "schema": {"properties": {"approved": {"type": "boolean"}}, "required": ["approved"], "type": "object"},
 "prompt": "Approve this order?", "show": {"order_id": "A1", "amount": 10.0},
 "key": null, "remaining_seconds": null, "audit": null}
```

`kind` is `decision`, `event` or another wait kind; an event wait has `key` and no `prompt`. `schema` is the JSON Schema the payload must fit. `remaining_seconds` is null without a timeout.

#### `POST /runs/{run_id}/events/{key}`

Delivers the event `ava.wait_for_event(Model, key="{key}")` waits for. Header `Idempotency-Key` required. Body: the event payload, a JSON object that fits the model.

Response `200`: `{"receipt_json": "...", "waits_json": [...]}`. The receipt, a JSON string, has `receipt_id`, `run_id`, `address` (`event:shipment`), `principal`, `accepted_at`, `reason`, `reviewed`, `schema_digest`, `payload_digest`. Errors: `404 unknown_key`, `422 invalid_wait_submission`, `409 run_closed`.

```bash
curl -s -X POST http://127.0.0.1:7435/api/v1/runs/$RUN_ID/events/shipment \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: ship-A1' -d '{"carrier": "UPS"}'
```

#### `POST /runs/{run_id}/decisions/{node}`

Answers an `ava.decision(...)`. `{node}` is the decision's slug (`decision`) or node ID (`decision_1`). Header `Idempotency-Key` required.

| Body field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `outcome` | object | yes | the fields of the decision's `ava.Event` subclass |
| `reason` | string | yes, non-empty | why; stored on the receipt |

Response `200`: as for events; the receipt's `address` is `decision:decision_1` and `reviewed` holds what the decision showed. Errors: `409 already_resolved_or_withdrawn`, `404 unknown_decision`, `422 invalid_wait_submission`.

```bash
curl -s -X POST http://127.0.0.1:7435/api/v1/runs/$RUN_ID/decisions/decision \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: approve-A1' \
  -d '{"outcome": {"approved": true}, "reason": "Checked the invoice"}'
```

### Results and activity

#### `GET /runs/{run_id}/result`

| Query | Default | Meaning |
| --- | --- | --- |
| `node_id` | none | read one step's output instead of the run's (`load_order_1`) |
| `entry` | none | for a machine run, a completed state entry, counted from 1 |

Response `200`: `{"run_id", "entry", "node_id", "version": 1, "value"}`. `value` is a tagged tree, not the bare value:

```json
{"run_id": "run_ea0b...", "entry": null, "node_id": null, "version": 1,
 "value": {"kind": "dict", "items": [["order_id", {"kind": "scalar", "value": "A2"}],
                                      ["approved", {"kind": "scalar", "value": true}]]}}
```

A model or dict is `{"kind": "dict", "items": [[key, value], ...]}`, a plain value `{"kind": "scalar", "value": ...}`; files and workspaces appear as references. `409` while the run is not finished or when it failed. A step's output can be read with `node_id` even when the run failed later. For the decoded value, use `ava result RUN_ID --output-dir DIR` or `GrpcStateProvider.get_run_result`.

#### `GET /runs/{run_id}/output`

Response `200`: `{cursor, run_id, value: {value_json, sha256, size_bytes}, scope_ref, workspace_root, files}`. `value_json` is the same tagged tree as a string; `workspace_root` is the folder where the operator keeps workspaces.

#### `GET /runs/{run_id}/activity`

| Query | Default | Meaning |
| --- | --- | --- |
| `node_id` | all | only this step's activity |
| `order` | `forward` | `forward` (oldest first) or `newest_first` |
| `page_size` | `100` | items per page |
| `continuation` | none | `next_page` of the previous response, JSON-encoded |

Response `200`: `{cursor, run_id, activities: [...], next_page?}`. Each activity has `activity_id`, `run_sequence`, `kind` (`log`, `terminal_seal`, agent and classifier events), `timestamp`, `node_id`, `level`, `channel`, `attempt`, `error`, `size_bytes` and a `detail_ref` to its body. Bodies are not returned by this route.

#### `GET /runs/{run_id}/evaluations`

| Query | Default | Meaning |
| --- | --- | --- |
| `node_id` | all | only this agent step's evaluations |

Response `200`: `{"records": [...]}`, empty when the run has no evaluated agent step.

### Machines

These routes work on a run whose `kind` is `machine`. They return plain JSON (not protobuf JSON).

#### `GET /runs/{run_id}/machine`

Response `200`:

| Field | Meaning |
| --- | --- |
| `machine`, `key`, `run_id` | selector, key values, run |
| `status`, `state`, `entry` | run status, current state name, number of the current state entry (from 1) |
| `accepts` | event names the current state accepts |
| `due` | epoch seconds of the next timer, or null |
| `finished` | true once a final state's work is done |
| `machine_spec` | the definition: `initial`, `states` (`work`, `initial`, `final`, `defer`, `transitions`, `timers`), `work_graphs` |
| `pending_events`, `receipts`, `reason`, `read_state`, `deployment_id`, `version` | buffered events, accepted receipts, failure reason, bookkeeping |

#### `POST /runs/{run_id}/machine/events/{name}`

Sends the event `{name}` (as in `state.on("{name}")`). Header `Idempotency-Key` required, 1 to 256 characters. Body: the payload as JSON; it must fit the transition's `payload=` model.

Response `202`: `{"receipt_id", "key", "accepted_at", "duplicate", "status"}`; `status` is `accepted` or `ignored`, `duplicate` is true for a repeated key. `422` when the payload does not fit.

```bash
curl -s -X POST http://127.0.0.1:7435/api/v1/runs/$RUN_ID/machine/events/resolve \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: resolve-T1' -d '{"note": "fixed"}'
```

#### `GET /runs/{run_id}/machine/history`

| Query | Default | Meaning |
| --- | --- | --- |
| `limit` | `20` | 1 to 100 |
| `cursor` | none | `next_cursor` of the previous page |

Response `200`: `{"items": [{"from": "opened", "to": "closed", "event": "resolve", "source": "external", "entry": 1}], "next_cursor": null}`. `source` is `external`, `internal` or `timer`.

#### `GET /runs/{run_id}/machine/entries/{entry}`

Response `200`: `{entry, state, entered_at, left_at, disposition, transition, catalog_revision, snapshot}`. `disposition` is `current` for the entry in progress; `snapshot` is a run snapshot of that entry's steps, whose node IDs are prefixed with the entry (`e1.open_ticket_1`).

#### `GET /runs/{run_id}/machine/entries/{entry}/events`

Same paging as history. Response `200`: `{"items": [...], "next_cursor": null}`: the events received during that entry.

#### `POST /runs/{run_id}/machine/entries:retry`

No body. Runs the current state entry again. Response `200`: `null`. `422` with `Only an interrupted or waiting machine can be retried` otherwise.

#### `POST /runs/{run_id}/machine/timers:fire-next`

No body. Fires the machine's next `state.after(...)` timer immediately. Only under `ava dev`; an `ava operator` answers `422` `Timer fast-forward is available only in ava dev`.

## Webhook listener

A workflow declared with `webhook=True` or `webhook=ava.Webhook(path)` gets one route on the webhook port. The default path is `/webhooks/<target alias>/<file path without .py>/<workflow name>`, for example `/webhooks/flows/review_flow/review_order`; `ava webhooks list` prints the exact URL.

| Request | Answer |
| --- | --- |
| `POST` with `Content-Type: application/json` and a JSON object body (at most 1 MiB) | `202` `{"run_id": "run_32726ae4"}`; the body is the run input |
| unknown path | `404` `{"error": "unknown webhook route"}` |
| other content type | `415` `{"error": "Content-Type must be application/json"}` |
| body not JSON, or not an object | `400` |
| body over 1 MiB | `413` |
| run could not be started (for example invalid input) | `409` `{"error": "workflow could not be started"}` |
| `GET`, `PUT`, `PATCH`, `DELETE` | `405` `{"error": "POST is required"}` |

No signature or token is checked. Two workflows that resolve to the same path stop discovery with `Webhook route collision`.

## gRPC-Web proxy

`POST /avalanche.operator.OperatorServiceV2/{Method}` on the browser listener forwards a gRPC-Web call (`Content-Type: application/grpc-web+proto`) to the operator. The browser UI uses it, including for streaming updates and staging input files (32 MiB per file). Prefer the REST routes or the Python client.

## Python client

`runtime.operator.client.GrpcStateProvider` is the gRPC client the CLI uses. It reaches what REST does not: decoded results, file and workspace inputs, integrations.

```python
from runtime.operator.client import GrpcStateProvider, OperatorCallError

provider = GrpcStateProvider("localhost:7433")
try:
    run_id = provider.start_run("review_order", input={"order_id": "A1"})
    print(run_id)
except OperatorCallError as exc:
    print(exc.status.name, exc.details)
finally:
    provider.close()
```

| Method | Returns | Does |
| --- | --- | --- |
| `GrpcStateProvider(address="localhost:7433", *, token=None, tls=False, ...)` | client | connect to an operator |
| `list_workflows()` | `list[WorkflowInfo]` | discovered flows and machines |
| `start_run(workflow_selector, *, run_id=None, input=None, context=None, files=None, rerun=None, ...)` | run ID | start a run; `files` maps an input field to an `ava.File` or bytes |
| `stage_input_workspace(workflow_selector, field_name, directory)` | `ava.Workspace` | copy a directory to the operator for a `Workspace` input field |
| `list_runs(workflow_selector)` | `list[RunState]` | run summaries |
| `get_run(run_id)` | `RunState` or `None` | one run with its steps |
| `get_run_result(run_id, *, entry=None, node_id=None)` | the decoded value | result of a successful run, step or state entry |
| `get_run_result_document(run_id, *, entry=None, node_id=None)` | `RunResult` | the tagged result tree, files as references |
| `cancel_run(run_id)` | `None` | request cancellation |
| `wait_control(run_id, WaitControlRequest(...))` | `WaitControlResponse` | list waits, send an event, answer a decision |
| `set_schedule_enabled(workflow_selector, enabled, run_input=None)` | `WorkflowInfo` | enable or disable a cron schedule |
| `list_integrations()` | `tuple[IntegrationStatus, ...]` | declared integrations and their accounts |
| `connect_integration(name, ApiKeyCredential(...))` | `IntegrationStatus` | store an API key |
| `start_integration_login(name, *, client_id=None, client_secret=None, slug=None, kind="shared")` | `IntegrationLogin` | start a browser login |
| `integration_login(login_id, *, wait_seconds=20.0)` | `IntegrationLogin` | poll a browser login |
| `disconnect_integration(name, *, slug=None)` | `IntegrationStatus` | remove an account |
| `close()` | `None` | close the channel |

A failed call raises `OperatorCallError` (`status`, `details`; message `STATUS: details`). A refused rerun raises `avalanche.reruns.RerunRefusedError`. See [errors.md](errors.md).
