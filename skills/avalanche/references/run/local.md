# Run locally

The local operator is one process that discovers workflows from Python files, runs them, and
stores every run in a SQLite file. The `ava` CLI starts it and talks to it. Use this loop to
check a workflow you wrote: start the operator, start a run, read the result and the errors.

## When to use it

- Use it for every change: it runs the real steps, with real retries, waits and state machines.
- For a check that needs no operator (one step, a stubbed workflow, fake time), write a test:
  [testing.md](testing.md).
- To keep an operator running as a service, read [self-hosting.md](self-hosting.md).
- Every command and flag is listed in [../reference/ava-cli.md](../reference/ava-cli.md).

## The loop

```bash
# 1. start the operator (terminal 1, or in the background)
PYTHONUNBUFFERED=1 uv run ava dev                 # scans [tool.avalanche].flow_targets
PYTHONUNBUFFERED=1 uv run ava dev flows/orders.py # or scan the files/folders you name

# 2. see what was discovered
uv run ava flow list
uv run ava flow show price_orders --json

# 3. start a run; the run ID is the only line on stdout
RUN=$(uv run ava run price_orders --input '{"order_id": "A1", "quantity": 4}')

# 4. wait for it and read the result as JSON
uv run ava result "$RUN" --wait --json

# 5. on failure, read the status, the failed step and its error
uv run ava run show "$RUN" --json
```

Verified output of step 4 for a workflow that returns the string `"A1: 10.00"`:

```json
{"run_id":"run_3a075652","entry":null,"node_id":null,"version":1,"value":{"kind":"scalar","value":"A1: 10.00"}}
```

## Interface

### Commands that start an operator

| Command | What it does |
| --- | --- |
| `ava init [--editable-deps]` | In an empty directory: creates a uv project (Python 3.13) with `src/start_flow/flow.py`, `[tool.avalanche] flow_targets = ["src"]`, a README, `AGENTS.md`, and this skill under `.agent/skills/avalanche`. Needs `git` and `uv`; runs `uv sync`. The starter flow has an agent step, so running it needs a model key. |
| `ava dev [FLOW ...]` | Starts the operator, the web UI and the REST API, opens a browser, and stops on Ctrl-C. Listens on loopback only. Allows timer fast-forward for state machines. |
| `ava operator [FLOW ...]` | Same operator without opening a browser; adds `--host`, `--webhook-port` and `--no-web`. Use it for a long-lived process. |
| `ava web` | Serves the web UI and REST API for an operator that is already running (`--connect`, `--host`, `--port`, `--trusted-proxy`). |
| `ava tui [FLOW]` | Terminal UI. With `--connect HOST:PORT` it shows a running operator; without it, mock data. Interactive: not usable by an agent without a terminal. |

Options shared by `ava dev` and `ava operator`:

| Option | Default | Meaning |
| --- | --- | --- |
| `FLOW ...` | `[tool.avalanche].flow_targets` | Python files or directories to scan. `alias=path` sets the root alias. |
| `--port` | `7433` | gRPC port; every other `ava` command connects to it. |
| `--web-port` | `7435` | Web UI and REST API (`http://127.0.0.1:7435/api/v1`). |
| `--login-port` | `7436` | Loopback port a browser login for an integration comes back to. |
| `--log-level` | `WARNING` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. `INFO` prints each reload. |
| `--discovery-timeout` | `60` | Maximum seconds for one scan of the workflow files. |
| `--schedules-enabled` | off | Start with every cron schedule on, for this start only. |
| `--state-db` | `.avalanche/operator/state.db` | SQLite file, relative to the current directory; `:memory:` keeps nothing. |

Only on `ava operator`: `--host` (default `127.0.0.1`), `--webhook-port` (default `7434`),
`--no-web`. `ava dev` always uses webhook port `7434`. The webhook listener starts only when
a discovered workflow declares a webhook.

### Commands that talk to a running operator

All take `--connect HOST:PORT` (default `localhost:7433`).

| Command | Output |
| --- | --- |
| `ava flow list [--json]` | Discovered flows and machines: `selector`, `name`, `kind`, `definition_file`, `key_fields`. |
| `ava flow show SELECTOR [--json]` (alias `get`) | One definition; for a machine, its full spec (states, transitions). |
| `ava run FLOW [--input JSON] [--context JSON] [--file FIELD=PATH] [--workspace FIELD=DIR]` | Starts a run, prints the run ID. |
| `ava run FLOW --rerun-of RUN_ID --from SLUG [--from SLUG] [--only] [--input JSON]` | Reruns part of a finished run, prints the new run ID. |
| `ava result RUN_ID --json [--wait] [--timeout S] [--node NODE_ID] [--entry N]` | The run's result, or one step's output, as one JSON line. |
| `ava result RUN_ID --output-dir PATH [--wait]` | Writes the result and its files into a new directory; prints metadata as JSON. |
| `ava run list [--flow SELECTOR] [--json]` | All runs: ID, definition, status, error note. |
| `ava run show RUN_ID --json` | Run snapshot: `summary`, `nodes[]` (status, error, attempts, inputs and output previews), `topology`. |
| `ava run cancel RUN_ID` | Cancels a run; prints `{}`. |
| `ava run history RUN_ID`, `ava run retry RUN_ID --entry` | State machine runs only: transitions, retry of an interrupted state entry. |
| `ava run links RUN_ID` | Child and parent runs of any run (flow or machine). |
| `ava machine send [MACHINE] EVENT (--run RUN_ID \| --key FIELD=JSON) [--data JSON\|@file]` | Sends an event to a machine run. See [state-machines.md](../building-blocks/state-machines.md). |
| `ava event send RUN_ID KEY JSON --idempotency-key K`, `ava run decide RUN_ID NODE JSON --idempotency-key K --reason TEXT` | Answer a wait. See [waits.md](../orchestration/waits.md) and [approvals.md](../orchestration/approvals.md). |
| `ava schedule list\|enable\|disable`, `ava webhooks list\|get` | Print JSON. See [triggers.md](../connect/triggers.md). |
| `ava connect`, `ava disconnect` | Accounts for integrations. See [integrations.md](../connect/integrations.md). |

`FLOW` and `SELECTOR` accept the workflow function name (`price_orders`) or the full selector
(`orders.py::price_orders`). Use the full selector when two files define the same name.

### Commands that need the operator stopped

`ava state` opens the database directly and fails while an operator holds it.

| Command | What it does |
| --- | --- |
| `ava state info` | Database path and size, runs by status, size of stored results, input files, workspaces and step outputs. |
| `ava state delete RUN_ID ...` | Deletes those runs with their logs, traces, results and files. |
| `ava state prune --before DATE` or `--keep-last N` | Deletes old finished runs; unfinished runs and schedules are kept. |
| `ava state clear [--yes] [--include-schedules]` | Deletes every run; asks for confirmation without `--yes`. |

All four accept `--state-db PATH`.

## Where state is stored

Everything is under the directory you started the operator from:

```text
.avalanche/
├── operator/
│   ├── state.db          runs, steps, logs, agent traces, evaluations, schedules, machines
│   ├── results/          one bundle per run result (values and files)
│   ├── step-outputs/     saved step outputs, used by reruns
│   ├── input-files/      files sent with --file
│   ├── schedule-inputs/  files of a schedule's input
│   └── workspaces/       each run's workspace folder
└── cache/operator/       discovery cache
```

Runs survive a restart. Start the operator from the same directory (or pass the same
`--state-db`) to see them again. Add `.avalanche/` to `.gitignore`.

## Discovery and reload

- Without `FLOW` arguments, the operator reads `flow_targets` from `[tool.avalanche]` in the
  nearest `pyproject.toml` (current directory, then its parents). Paths are relative to that file.
- A target is a `.py` file or a directory. Directories are scanned recursively, skipping
  directories that start with `.` and `__pycache__`, `build`, `dist`, `node_modules`, `venv`.
- Each `@ava.workflow` and `@ava.machine` in a scanned file becomes one definition with the
  selector `<file relative to the target>::<function name>`.
- Both `ava dev` and `ava operator` watch the source folders. Saving a file rediscovers the
  workflows within a few seconds; the next `ava run` uses the new code. No restart is needed.
- If a file fails to import at startup, the operator exits with code 1 and prints
  `<path>: import_error: <exception>`. If it breaks while the operator runs, the previous
  catalog stays and `GET /api/v1/flows` lists the problem under `diagnostics`.

```toml
# pyproject.toml
[tool.avalanche]
flow_targets = ["flows"]
```

## Start a run with inputs

`--input` is a JSON object validated against the workflow's input model
(`@ava.workflow(input=...)`, see [inputs-and-files.md](../building-blocks/inputs-and-files.md)).

```bash
uv run ava run price_orders --input '{"order_id": "A1", "quantity": 4}'
uv run ava run review_document --input '{"reviewer": "ada"}' --file document=./report.pdf
uv run ava run index_repository --workspace repo=./checkout
```

- `--file FIELD=PATH` sends a local file as the top-level input field `FIELD`; repeatable.
- `--workspace FIELD=DIR` sends a local directory; the operator keeps a copy and passes that copy.
- `--context JSON` sets the run context object.
- A field may appear once: in `--input`, `--file` or `--workspace`.

## Read a run from the command line

| Need | Command |
| --- | --- |
| Wait until the run ends, then read its result | `ava result RUN --wait --timeout 600 --json` |
| One step's output | `ava result RUN --node price_order_1 --json` |
| Status, failed step, error text, attempts | `ava run show RUN --json` |
| Result files on disk | `ava result RUN --output-dir ./out` (the directory must not exist) |
| An agent step's model, turns, usage | `nodes[].trace.header` in `ava run show RUN --json` |
| Log text and full agent traces | Python client, below |

`ava result --json` prints `{"run_id", "entry", "node_id", "version", "value"}`. `value` is a
tagged tree: `{"kind": "scalar", "value": ...}`, `{"kind": "dict", "items": [[key, value], ...]}`,
and so on. `--output-dir` writes plain JSON instead (`"result": "A1: 10.00"`), plus each file.

`--node` takes the node ID, not the function name: `price_order_1`, `format_receipt_1`. Node IDs
are in `nodes[].node_id` of `ava run show --json`.

Fields of `ava run show RUN --json` to read first:

```text
summary.status          queued | running | waiting | succeeded | failed | cancelled | unknown
summary.waiting_on      for a waiting run: counts per kind (decision, event, run, sleep)
summary.error_code      node_failed, input_invalid, coordinator_failed, unknown: see debugging.md
summary.error_step      node ID of the step that failed
summary.error_message   "ValueError in step format_receipt_1"
summary.triggered_by    api (ava run, REST) | manual (web UI, terminal UI) | webhook | scheduled
summary.skips           only when a step was skipped: count, first_step (node ID), first_reason
nodes[].status          pending | running | success | failed | skipped | reused
nodes[].error           "ValueError: total exceeds the 1000 limit"
nodes[].attempt / max_attempts
nodes[].step_io         previews of the step's inputs and output
```

Log lines (including `ava.Logger()` output and the failure traceback) and the turns of an agent
trace are not printed by a CLI command. Read them with the Python client that the CLI uses:

```python
import sys

from runtime.operator.client import GrpcStateProvider

provider = GrpcStateProvider(sys.argv[1])  # "127.0.0.1:7433"
try:
    run = provider.get_run(sys.argv[2])
    print(run.status.value, run.error_step, run.error_message)
    for node in run.nodes.values():
        print(node.node_id, node.status.value, node.error)
    for entry in run.logs:
        print(entry.level, entry.node_id, entry.message)
    # An agent step's trace, by node ID:
    # trace = provider.hydrate_trace(sys.argv[2], "write_greeting_1")
finally:
    provider.close()
```

Here `run.status.value` is `pending`, `running`, `waiting`, `success`, `failed` or `cancelled`
(the CLI prints `success` as `succeeded`).

The REST API under `http://127.0.0.1:7435/api/v1` returns the same data as JSON:
`GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/result?node_id=...`,
`GET /runs/{id}/activity?node_id=...` (an index of log and agent events, without their text),
`POST /runs`. No REST route returns log text or trace turns.
All routes are in [../reference/operator-api.md](../reference/operator-api.md).

## Rerun

A rerun starts a new run that reuses the saved outputs of the steps it skips.

```bash
# run format_receipt again, and everything after it; earlier steps are reused
uv run ava run price_orders --rerun-of "$RUN" --from format_receipt

# rerun from the first step with one input field changed; other fields are kept
uv run ava run price_orders --rerun-of "$RUN" --from price_order --input '{"quantity": 1000}'

# run only the named step, nothing after it
uv run ava run price_orders --rerun-of "$RUN" --from price_order --only
```

- `--from` takes the step's slug: the function name unless the step sets `slug=`. Repeatable.
- The source run must be finished, failed or cancelled.
- Not for a workflow with `key=`: a keyed run cannot be rerun
  ([retries.md](../orchestration/retries.md#run-keys)).
- Reused steps show `"status": "reused"` and `reused_from_run_id` in `ava run show --json`.
- Edit the code first, then rerun: the rerun executes the current code.
- A refused rerun starts nothing and explains why on stderr, for example
  `nope: unknown_start_step: the code that will run has no such step`.

## Rules

- Run `ava` through the project environment: `uv run ava ...`.
- Set `PYTHONUNBUFFERED=1` when you redirect the operator's output to a file; otherwise the
  `Ready.` line appears only when the process exits. Or poll `ava flow list` until it answers.
- In a headless shell, `ava dev` prints `Could not open a browser automatically` on stderr and
  keeps running. Prefer `ava operator` for a background process.
- One operator per state database. A second one fails with `Operator state database ... is
  already in use by another process`. Use another `--state-db` and other ports for a second one.
- `--port`, `--webhook-port` and `--web-port` must differ.
- `--input` must be a JSON object. Errors: `--input must be a JSON object`,
  `INVALID_ARGUMENT: Input is missing required fields: quantity`, `NOT_FOUND: Unknown workflow: nope`.
- Only a missing required field is refused at the start. A wrong type, a broken constraint or an
  unknown field still prints a run id and exits 0; that run fails at once with
  `error_code: input_invalid`, `error_message: Run failed`, no steps, and the reason only in the
  run log. Always read the run after starting it. A workflow with `key=` refuses instead: see
  [triggers.md](../connect/triggers.md#inputs-per-trigger).
- Workflow inputs do not bind to step parameters by name. A step reads the input through a
  parameter typed with the workflow's input model, or through `ava.input.<field>`.
- `ava run` prints only the run ID on stdout and exits 0 once the run is accepted, not when
  it ends. Use `ava result --wait` to block. Default `--timeout` is 300 seconds.
- `ava result` needs `--json` or `--output-dir`; without either it exits 2.
- `ava result --json` on a failed run prints a Python traceback ending in
  `FAILED_PRECONDITION: Run <id> ended with status failed` and exits 1. Read failures with
  `ava run show <id> --json`.
- `ava result --node` with a function name instead of a node ID fails with
  `Requested step or state entry has no accepted result`.
- A step that raises is retried: by default up to 8 attempts. A wrong input shape therefore
  shows `attempt: 8`. See [retries.md](../orchestration/retries.md).
- Stop the operator with Ctrl-C or `SIGINT`/`SIGTERM` to the `ava` process. Runs in flight
  when it stops are marked failed at the next start; state machine runs continue.
- `uv run ava operator` is two processes: `uv` and, as its child, the Python operator. `SIGINT`
  or `SIGTERM` sent to the `uv` process is passed on and stops both (`Stopped.` is printed).
  `SIGKILL` on the `uv` process leaves the operator running, holding its ports and state
  database: then signal the child (`pgrep -P <uv pid>` before killing, or find the process
  listening on the port).
- `ava run rerun` does not exist for flows; use `ava run FLOW --rerun-of`.

## In the UI

- The web UI at `http://127.0.0.1:7435` lists the same selectors and names as `ava flow list`.
- Each step card shows the first line of the step's docstring; write one for every step.
- A run page shows each step's inputs and output previews, logs, and agent traces: the same
  data as `ava run show --json` and the Python client above.

## See also

- [testing.md](testing.md): checks in pytest, and key-free runs on the operator.
- [self-hosting.md](self-hosting.md): the operator as a service.
- [cloud.md](cloud.md): deploy the same project to Avalanche Cloud.
- [../reference/ava-cli.md](../reference/ava-cli.md), [../reference/operator-api.md](../reference/operator-api.md),
  [../reference/project-layout.md](../reference/project-layout.md).
- [../guides/debugging.md](../guides/debugging.md).
