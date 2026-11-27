# `ava` CLI reference

Every `ava` command, with every argument and default. `ava` is installed by the `avalanche-ai` package; `avalanche-ai` is a second name for the same program. In a project, run it as `uv run ava ...`.

Related: [running locally](../run/local.md), [self-hosting](../run/self-hosting.md), [operator REST API](operator-api.md), [project layout and settings](project-layout.md), [`delta` CLI](delta-cli.md) for Avalanche Cloud.

## Conventions

- **Operator address.** Every command except `init`, `dev`, `operator` and `state` talks to a running operator over gRPC. `--connect HOST:PORT` defaults to `localhost:7433` (`ava tui` has no default: without `--connect` it runs in mock mode).
- **Selectors.** A flow or machine is addressed by its bare name (`review_order`) or its full selector `file.py::name` (`review_flow.py::review_order`). A bare name that matches two definitions is refused as ambiguous; use the full selector. `ava webhooks get` takes the full selector only.
- **Flow targets.** `ava dev` and `ava operator` scan the files and directories given as `FLOW`. With none, they read `[tool.avalanche].flow_targets` from the nearest `pyproject.toml`; with neither they stop with `No workflow targets configured. Pass FLOW [FLOW ...], or set [tool.avalanche].flow_targets in pyproject.toml.` A target may be `alias=path`.
- **Ports.** gRPC 7433, webhooks 7434, browser UI and REST API 7435, browser-login callback 7436. `--port`, `--webhook-port` and `--web-port` must differ; `0` picks a free port.
- **State.** The operator keeps runs, results and schedules in `--state-db` (default `.avalanche/operator/state.db` under the current folder) and folders next to it. One operator per database.
- **JSON arguments.** `--input`, `--context` and payloads must be a JSON object; `NaN` and `Infinity` are refused. A `--key FIELD=JSON` value is JSON, so a string needs quotes: `--key 'order_id="A1"'`.
- **Exit codes.** `0` success. `1` the operator or the command refused (reason on stderr, usually `STATUS: detail` such as `NOT_FOUND: Unknown workflow: nope`). `2` wrong usage (argparse error, `ava` with no command, `ava result` without `--json` or `--output-dir`).
- **Environment.** `ava` itself reads only `AVALANCHE_TUI_GRPC_TOKEN`, `AVALANCHE_TUI_GRPC_TLS` and `AVALANCHE_TUI_GRPC_TLS_CA_CERT` (for `ava tui`). Steps read model keys from the environment of the operator process; see [project-layout.md](project-layout.md#environment-variables).

## Command map

| Command | Does |
| --- | --- |
| `ava init` | create a project in an empty directory |
| `ava dev` | operator + browser UI + REST API for development |
| `ava operator` | operator for long-running use |
| `ava web` | browser UI and REST API for a running operator |
| `ava tui` | terminal UI |
| `ava flow list` / `show` / `get` | inspect discovered flows and machines |
| `ava run FLOW` | start a run, or a rerun with `--rerun-of` |
| `ava run list` / `show` / `cancel` / `links` | inspect and cancel runs |
| `ava run history` / `retry` | machine run transitions; retry a state entry |
| `ava result` (also `ava run result`) | read or download a result |
| `ava event send` | deliver an event to a waiting run |
| `ava run decide` | answer a decision |
| `ava machine send` | send an event to a machine run |
| `ava schedule list` / `enable` / `disable` | cron schedules |
| `ava webhooks list` / `get` | webhook routes |
| `ava connect` / `ava disconnect` | integration accounts |
| `ava state info` / `delete` / `prune` / `clear` | stored runs and files |

`ava flow`, `ava event`, `ava schedule`, `ava webhooks` and `ava state` are groups: they need a subcommand. `ava run` is both a command (`ava run FLOW`) and a group: when the word after `run` is `list`, `show`, `history`, `retry`, `cancel`, `rerun`, `links`, `result` or `decide`, it is the subcommand, so a flow cannot be started by one of those bare names.

## Start and serve

### `ava init`

Create a workflow project in the current directory, which must be empty. Runs the bundled `init.sh`: needs `git` and `uv`, installs Python 3.13 through uv if missing, writes the project, runs `uv sync` and verifies it. See [project-layout.md](project-layout.md).

| Argument | Default | Meaning |
| --- | --- | --- |
| `--editable-deps` | off | Clone Avalanche and PredictRLM as editable dependencies. |

Output: Progress text. Exit code is the script's: 0 on success, 1 on any failed check.

```bash
mkdir my-workflows && cd my-workflows
uvx avalanche-ai init
```

### `ava dev [FLOW ...]`

Start a local operator for development, the browser UI and the REST API in one process, open the browser, and run until Ctrl-C. Watches the flow files and reloads them. gRPC and HTTP listen on 127.0.0.1 only; webhooks are served on port 7434 (no flag). Machine timers can be fast-forwarded only under `ava dev`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `FLOW` ... | optional | Workflow Python file or directory; uses workspace configuration when omitted. |
| `--port PORT` | `7433` | Operator gRPC port. |
| `--web-port WEB_PORT` | `7435` | Browser UI and REST API HTTP port. |
| `--login-port LOGIN_PORT` | `7436` | Loopback port a browser login comes back to (default: 7436). |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `WARNING` | Terminal log level (default: WARNING). |
| `--discovery-timeout SECONDS` | `60.0` | Maximum seconds for one discovery scan (default: 60). |
| `--schedules-enabled` | off | Start with every cron schedule enabled, for this start only (otherwise schedules start as last turned on or off). |
| `--state-db PATH` | `.avalanche/operator/state.db` | SQLite file that keeps operator state, or :memory: for a throwaway one (default: .avalanche/operator/state.db under the current folder). |

Output: Prints the scan targets, the state database, the discovered workflow count, `Operator ready: grpc://127.0.0.1:PORT`, `Web UI ready: http://127.0.0.1:PORT` and `REST API ready: .../api/v1`. Exit 0 after Ctrl-C, 1 on a discovery, state-database or startup failure, 2 on bad arguments.

```bash
uv run ava dev
uv run ava dev src/review_order/flow.py --port 7651 --web-port 7653
```

### `ava operator [FLOW ...]`

Start the local operator without opening a browser: gRPC, the webhook listener, and (unless `--no-web`) the browser UI and REST API. Use it for a long-running or self-hosted operator. Same discovery and state rules as `ava dev`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `FLOW` ... | optional | Workflow Python file or directory; uses workspace configuration when omitted. |
| `--port PORT` | `7433` | Operator gRPC port. |
| `--host HOST` | `127.0.0.1` | Listen host (default: loopback); non-loopback exposure requires an external trusted and authenticated boundary. |
| `--webhook-port WEBHOOK_PORT` | `7434` | Loopback webhook HTTP port. |
| `--web-port WEB_PORT` | `7435` | Loopback browser UI and REST API HTTP port. |
| `--no-web` | off | Serve gRPC without the HTTP listener. |
| `--login-port LOGIN_PORT` | `7436` | Loopback port a browser login comes back to (default: 7436). |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | `WARNING` | Terminal log level (default: WARNING). |
| `--discovery-timeout SECONDS` | `60.0` | Maximum seconds for one discovery scan (default: 60). |
| `--schedules-enabled` | off | Start with every cron schedule enabled, for this start only (otherwise schedules start as last turned on or off). |
| `--state-db PATH` | `.avalanche/operator/state.db` | SQLite file that keeps operator state, or :memory: for a throwaway one (default: .avalanche/operator/state.db under the current folder). |

Output: Same startup lines as `ava dev`, plus `Executor: Local`. Exit 0 after Ctrl-C, 1 on a discovery or state-database failure, 2 on bad arguments.

```bash
uv run ava operator src --host 127.0.0.1 --port 7433 --schedules-enabled
```

### `ava web`

Serve the browser UI and REST API for an operator that is already running (for example one started with `--no-web`), and open the browser.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--connect HOST:PORT` | `localhost:7433` | Operator address. |
| `--host HOST` | `127.0.0.1` | Browser UI listen host. |
| `--port PORT` | `7435` | Browser UI HTTP port. |
| `--trusted-proxy` | off | Confirm non-loopback browser traffic is protected by a trusted proxy. |

Output: Prints `Avalanche web UI: http://HOST:PORT`. A non-loopback `--host` without `--trusted-proxy` is refused.

```bash
uv run ava web --connect localhost:7433 --port 7435
```

### `ava tui [FLOW[/NODE]]`

Open the terminal UI. With `--connect` it shows a real operator; without it, it starts in mock mode, connected to nothing. `FLOW[/NODE]`, `--flow`, `--run` and `--node` open it on that item. The token, TLS switch and CA file can also come from `AVALANCHE_TUI_GRPC_TOKEN`, `AVALANCHE_TUI_GRPC_TLS` and `AVALANCHE_TUI_GRPC_TLS_CA_CERT`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `FLOW` | optional | Optional flow[/node] deep link. |
| `--flow FLOW_SELECTOR` | none | Canonical workflow selector. |
| `--run RUN` | none | Run ID to inspect. |
| `--node NODE` | none | Node ID to inspect. |
| `--read-only` | off | Disable all runtime mutations. |
| `--connect HOST:PORT` | none | Operator address. |
| `--token TOKEN` | none | Operator bearer token. |
| `--tls` | off | Use TLS for gRPC. |
| `--insecure` | off | Disable TLS for gRPC. |
| `--tls-ca-cert PATH` | none | TLS CA certificate. |

Output: Interactive. Positional `FLOW` and `--flow` together raise `ValueError`.

```bash
uv run ava tui --connect localhost:7433 --flow review_flow.py::review_order --read-only
```

## Definitions

### `ava flow list`

List every flow and machine the operator discovered.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--json` | off | Print JSON instead of the text table. |

Output: Text: a `TYPE<TAB>SELECTOR<TAB>NAME` table (`TYPE` is `flow` or `machine`). `--json`: an array of `{selector, name, kind, definition_file, key_fields, machine}`; `machine` is the machine spec or null.

```bash
uv run ava flow list --json
```

### `ava flow show SELECTOR`

Show one definition. `SELECTOR` is the full selector (`file.py::name`) or the bare name; it must match exactly one definition, else `Select one definition by its discovery selector` and exit 1.

| Argument | Default | Meaning |
| --- | --- | --- |
| `SELECTOR` | required | Full selector (`file.py::name`) or bare name. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--json` | off | Print one JSON record. |

Output: Text: `Kind`, `Definition`, `Key fields`, and `States` for a machine. `--json`: one record as in `ava flow list --json`.

```bash
uv run ava flow show review_order
```

### `ava flow get SELECTOR`

Same command as `ava flow show`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `SELECTOR` | required | Full selector (`file.py::name`) or bare name. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--json` | off | Print one JSON record. |

Output: Same as `ava flow show`.

```bash
uv run ava flow get review_flow.py::ticket --json
```

## Runs

### `ava run FLOW`

Start a run of a flow or machine on a running operator and print its run ID. It does not wait for the run. With `--rerun-of` it starts a rerun that reuses the saved outputs of the steps it does not run again.

| Argument | Default | Meaning |
| --- | --- | --- |
| `FLOW` | required | Flow name to run. |
| `--connect HOST:PORT` | `localhost:7433` | Operator address. |
| `--input INPUT_JSON` | none | JSON object for workflow input. |
| `--context CONTEXT_JSON` | none | JSON object for run context. |
| `--file FIELD=PATH` (repeatable) | none | Attach local file bytes as a top-level input field. |
| `--workspace FIELD=DIR` (repeatable) | none | Send a local directory to the operator, which keeps a copy, and pass that copy as a top-level Workspace input field. |
| `--rerun-of RUN_ID` | none | Rerun this finished, failed or cancelled run, reusing the saved outputs of the steps it skips and its input; --input and --file replace the fields they name. |
| `--from SLUG` (repeatable) | none | With --rerun-of, a step to run again along with everything after it; repeatable. |
| `--only` | off | With --rerun-of, run only the --from steps and nothing after them. |

Output: stdout: the run ID. Exit 1 with the reason on stderr for invalid JSON, a duplicate input field, an unknown flow (`NOT_FOUND: Unknown workflow: NAME`), a missing key field, or a refused rerun (`Rerun refused, nothing was started:` followed by one line per problem and `Earliest step to rerun from: ...`). A run of a keyed flow cannot be rerun from a step. With `--rerun-of` on a keyed flow: `INVALID_ARGUMENT: missing run key field 'NAME'` when `--input` lacks the key fields; the existing run's ID on stdout and exit 0, with nothing started, when the key already has a run; `FAILED_PRECONDITION: keyed flow runs cannot be rerun from a step` for a new key. Input that does not fit the model: on a keyed flow, exit 1 with `FAILED_PRECONDITION: Invalid machine input/payload: FIELD: ERROR_TYPE`; on any other flow only a missing required field is refused (`INVALID_ARGUMENT: Input is missing required fields: ...`), while a wrong type, a broken constraint or an unknown field still prints a run ID and exits 0, and that run fails with `error_code: input_invalid`.

```bash
uv run ava run review_order --input '{"order_id": "A1"}'
uv run ava run summarize_report --file report=./report.pdf --workspace repo=./checkout
uv run ava run summarize_report --rerun-of RUN_ID --from write_summary
```

### `ava run list`

List runs, newest first, for all definitions or one (`--flow`).

| Argument | Default | Meaning |
| --- | --- | --- |
| `--flow FLOW` | empty | Flow or machine selector to filter by. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--json` | off | Emit JSON. |

Output: Text: `TYPE<TAB>RUN<TAB>DEFINITION<TAB>STATUS<TAB>NOTE`; `NOTE` is the error summary, or `waiting on decision +1` style text for a waiting run. `--json`: `{"runs": [...]}` with the run-summary fields of the REST API, and `waiting_on` as `[operator, decision, event, run, sleep]` counts.

```bash
uv run ava run list --flow review_order --json
```

### `ava run show [RUN_ID]`

Print one run's snapshot: summary, steps with status, topology and input. Address the run by ID, or by `--flow` plus one `--key FIELD=JSON` per key field for a keyed flow or machine.

| Argument | Default | Meaning |
| --- | --- | --- |
| `RUN_ID` | optional | Run ID; omit when using `--flow` and `--key`. |
| `--flow FLOW` | empty | Flow or machine selector; with `--key`, addresses the run by its key instead of its ID. |
| `--key FIELD=JSON` (repeatable) | none | One key field as `FIELD=JSON` (a string value needs JSON quotes); repeat per key field. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--json` | off | Emit JSON. |

Output: The run snapshot as JSON (protobuf JSON, snake_case). Without `--json` a `Kind: flow|machine` line comes first.

```bash
uv run ava run show RUN_ID --json
uv run ava run show --flow review_order --key 'order_id="A1"'
```

### `ava run cancel [RUN_ID]`

Request cancellation of a run. Addressed like `ava run show`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `RUN_ID` | optional | Run ID; omit when using `--flow` and `--key`. |
| `--flow FLOW` | empty | Flow or machine selector; with `--key`, addresses the run by its key instead of its ID. |
| `--key FIELD=JSON` (repeatable) | none | One key field as `FIELD=JSON` (a string value needs JSON quotes); repeat per key field. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--json` | off | Emit JSON. |

Output: Prints `{}`.

```bash
uv run ava run cancel RUN_ID
```

### `ava run links [RUN_ID]`

Print the runs this run started or called (`children`) and the runs that started it (`parents`).

| Argument | Default | Meaning |
| --- | --- | --- |
| `RUN_ID` | optional | Run ID; omit when using `--flow` and `--key`. |
| `--flow FLOW` | empty | Flow or machine selector; with `--key`, addresses the run by its key instead of its ID. |
| `--key FIELD=JSON` (repeatable) | none | One key field as `FIELD=JSON` (a string value needs JSON quotes); repeat per key field. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--json` | off | Emit JSON. |

Output: JSON `{"parents": [...], "children": [...]}`.

```bash
uv run ava run links RUN_ID
```

### `ava run history [RUN_ID]`

Print a machine run's state transitions, oldest first, one page at a time.

| Argument | Default | Meaning |
| --- | --- | --- |
| `RUN_ID` | optional | Run ID; omit when using `--flow` and `--key`. |
| `--flow FLOW` | empty | Flow or machine selector; with `--key`, addresses the run by its key instead of its ID. |
| `--key FIELD=JSON` (repeatable) | none | One key field as `FIELD=JSON` (a string value needs JSON quotes); repeat per key field. |
| `--limit LIMIT` | `20` | Page size, 1 to 100. |
| `--cursor CURSOR` | none | `next_cursor` of the previous page. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--json` | off | Emit JSON. |

Output: JSON `{"items": [{"from", "to", "event", "source", "entry"}], "next_cursor"}`; pass `next_cursor` to `--cursor`.

```bash
uv run ava run history RUN_ID --limit 50
```

### `ava run retry [RUN_ID] --entry`

Run the current state entry of a machine run again. `--entry` is required. Only an interrupted or waiting machine can be retried, else exit 1 with `Only an interrupted or waiting machine can be retried`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `RUN_ID` | optional | Run ID; omit when using `--flow` and `--key`. |
| `--flow FLOW` | empty | Flow or machine selector; with `--key`, addresses the run by its key instead of its ID. |
| `--key FIELD=JSON` (repeatable) | none | One key field as `FIELD=JSON` (a string value needs JSON quotes); repeat per key field. |
| `--entry` | off | Required; retry the current state entry. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--json` | off | Emit JSON. |

Output: Prints `{}`.

```bash
uv run ava run retry RUN_ID --entry
```

### `ava run rerun [RUN_ID]`

Not implemented: always exits 1 with `Machine reruns are not supported; use a new key or retry an interrupted state entry`. To rerun a flow run, use `ava run FLOW --rerun-of RUN_ID --from SLUG`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `RUN_ID` | optional | Run ID; omit when using `--flow` and `--key`. |
| `--flow FLOW` | empty | Flow or machine selector; with `--key`, addresses the run by its key instead of its ID. |
| `--key FIELD=JSON` (repeatable) | none | One key field as `FIELD=JSON` (a string value needs JSON quotes); repeat per key field. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--json` | off | Emit JSON. |

Output: Exit 1.

```bash
uv run ava run flow_name --rerun-of RUN_ID --from step_slug
```

### `ava result RUN_ID`

Read the result of a successful run, of one step (`--node`), or of one completed machine state entry (`--entry`). Give `--json` or `--output-dir`; with neither it exits 2. `ava run result ...` is the same command.

| Argument | Default | Meaning |
| --- | --- | --- |
| `RUN_ID` | required | Run ID to retrieve. |
| `--connect HOST:PORT` | `localhost:7433` | Operator address. |
| `--output-dir PATH` | none | New destination directory for result metadata and downloaded files (must not already exist). |
| `--wait` | off | Wait for a nonterminal run before retrieving its result. |
| `--timeout TIMEOUT` | `300.0` | Maximum seconds to wait with --wait (default: 300). |
| `--entry ENTRY` | none | Completed machine state entry (1-based). |
| `--node NODE` | none | Step ID, local to --entry when supplied. |
| `--json` | off | Reference-only shared result JSON. |

Output: `--json`: one line `{run_id, entry, node_id, version, value}` where files and workspaces are references. `--output-dir PATH`: creates `PATH` (it must not exist), writes every file and workspace of the result into it plus `result-<hex>.json`, and prints `{run_id, result, files, metadata_path}` (plus `workspaces` when the result holds any); each file entry has `path`, `name`, `media_type`, `sha256`, `size`, each workspace entry `path`, `entries`, `sha256`. Exit 1 when the run is not finished (`Run RUN_ID is not terminal`), failed (`Run RUN_ID ended with status failed`) or the wait timed out; 2 for bad `--timeout` or missing output option.

```bash
uv run ava result RUN_ID --wait --timeout 600 --output-dir ./out
uv run ava result RUN_ID --node load_order_1 --json
```

## Waits and machine events

### `ava event send RUN_ID KEY JSON`

Deliver a JSON payload to a run waiting on `ava.wait_for_event(..., key=KEY)`. The payload must be a JSON object that fits the event's model. Reusing an `--idempotency-key` makes a repeat a no-op.

| Argument | Default | Meaning |
| --- | --- | --- |
| `RUN_ID` | required | Run that is waiting. |
| `ADDRESS` | required | Declared exact event key or decision invocation ID. |
| `JSON` | required | Typed JSON payload. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--idempotency-key IDEMPOTENCY_KEY` | required | Required; a repeat with the same key is not applied twice. |
| `--reason REASON` | empty | Optional note stored on the receipt. |

Output: stdout: the receipt as JSON (`receipt_id`, `run_id`, `address`, `principal`, `accepted_at`, `reason`, `reviewed`, `schema_digest`, `payload_digest`). Exit 1 with `STATUS: code`, for example `NOT_FOUND: unknown_key`, `INVALID_ARGUMENT: ...`, `ALREADY_EXISTS: run_closed`.

```bash
uv run ava event send RUN_ID shipment '{"carrier": "UPS"}' --idempotency-key ship-A1
```

### `ava run decide RUN_ID NODE JSON`

Answer a run waiting on `ava.decision(...)`. `NODE` is the decision's step slug or node ID (for example `decision` or `decision_1`). The payload is the outcome model's own fields. `--reason` is required and recorded with the answer.

| Argument | Default | Meaning |
| --- | --- | --- |
| `RUN_ID` | required | Run that is waiting. |
| `ADDRESS` | required | Declared exact event key or decision invocation ID. |
| `JSON` | required | Typed JSON payload. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--idempotency-key IDEMPOTENCY_KEY` | required | Required; a repeat with the same key is not applied twice. |
| `--reason REASON` | required | Required; why this answer was given, stored with it. |

Output: stdout: the receipt as JSON, as for `ava event send`. Exit 1 with `ALREADY_EXISTS: already_resolved_or_withdrawn` when the decision was already answered.

```bash
uv run ava run decide RUN_ID decision '{"approved": true}' --reason 'Checked the invoice' --idempotency-key approve-A1
```

### `ava machine send [MACHINE] EVENT`

Send an external event to a machine run. Address the run with `--run RUN_ID` (and no machine name), or with the machine name plus one `--key FIELD=JSON` per key field. `--data` is the event payload.

| Argument | Default | Meaning |
| --- | --- | --- |
| `MACHINE` | optional | Required with --key; omit with --run. |
| `--run RUN` | none | Target run ID; exclusive with `--key` and with a machine name. |
| `--key FIELD=JSON` (repeatable) | none | One key field as `FIELD=JSON` (a string value needs JSON quotes); repeat per key field; needs the machine name. |
| `EVENT` | required | Event name, as declared with `state.on("NAME")`. |
| `--data DATA` | `{}` | JSON value or @file. |
| `--idempotency-key IDEMPOTENCY_KEY` | none | Defaults to a new random value; reuse one when retrying. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--json` | off | Emit JSON. |

Output: stdout: the receipt `{receipt_id, key, accepted_at, duplicate, status}`; `status` is `accepted` or `ignored`. Exit 1 on an unknown key (`Unknown machine key`) or a payload that does not fit the event's model.

```bash
uv run ava machine send resolve --run RUN_ID --data '{"note": "fixed"}'
uv run ava machine send ticket resolve --key 'ticket_id="T1"' --data @resolution.json
```

## Triggers

### `ava schedule list`

List every workflow that declares `cron=`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--connect HOST:PORT` | `localhost:7433` | Operator address. |

Output: A JSON array of `{selector, name, cron, enabled, input, next_run_at, last_run_at}` (times are ISO 8601 UTC or null).

```bash
uv run ava schedule list
```

### `ava schedule enable SELECTOR`

Let a workflow's cron schedule start runs. Schedules start disabled; the on/off state and the input are stored in the state database and survive a restart.

| Argument | Default | Meaning |
| --- | --- | --- |
| `SELECTOR` | required | Canonical workflow selector. |
| `--input INPUT_JSON` | none | JSON object each cron run starts with; omitted keeps the current input. |
| `--connect HOST:PORT` | `localhost:7433` | Operator address. |

Output: The schedule record as JSON. Exit 1 on an unknown workflow, a workflow without `cron=`, or invalid `--input`.

```bash
uv run ava schedule enable daily_report --input '{"region": "eu"}'
```

### `ava schedule disable SELECTOR`

Stop a workflow's cron schedule from starting runs.

| Argument | Default | Meaning |
| --- | --- | --- |
| `SELECTOR` | required | Canonical workflow selector. |
| `--connect HOST:PORT` | `localhost:7433` | Operator address. |

Output: The schedule record as JSON.

```bash
uv run ava schedule disable daily_report
```

### `ava webhooks list`

List every workflow that declares `webhook=`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--connect HOST:PORT` | `localhost:7433` | Operator address. |

Output: A JSON array of `{selector, method, path, url, active}`; `method` is always `POST`.

```bash
uv run ava webhooks list
```

### `ava webhooks get SELECTOR`

Show one webhook route. `SELECTOR` must be the full selector (`file.py::name`).

| Argument | Default | Meaning |
| --- | --- | --- |
| `SELECTOR` | required | Canonical workflow selector. |
| `--connect HOST:PORT` | `localhost:7433` | Operator address. |

Output: One record as JSON; exit 1 with `Webhook not found: SELECTOR` otherwise.

```bash
uv run ava webhooks get review_flow.py::review_order
```

## Integrations

### `ava connect [NAME]`

Without `NAME`: list the integrations the discovered workflows declare and whether each has an account. With `NAME`: connect its account. A Connector with `ava.ApiKey` asks for the key; a Connector with `ava.OAuth2` asks for the client ID and secret, then opens the browser; a remote MCP server opens the browser, or takes a key with `--api-key`; a built-in (catalog) integration uses its provider's browser login. Keys and secrets are prompted for, or read from standard input when it is not a terminal; they are never arguments.

| Argument | Default | Meaning |
| --- | --- | --- |
| `NAME` | optional | Integration name, such as acme-crm. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--api-key` | off | Connect a remote MCP server with an API key instead of a browser login. |
| `--header HEADER` | none | Request header an MCP server expects its API key in (implies --api-key); a Connector declares its own and takes no --header. |
| `--client-secret` | off | Ask for the secret of the client an MCP server's declaration names with client_id=, for a server whose registered client has one. |
| `--no-browser` | off | Print the login address instead of opening a browser. |
| `--slug SLUG` | none | Immutable catalog connection slug (defaults to NAME). |
| `--per-user` | off | Connect the starting human's account. |
| `--json` | off | Emit JSON. |

Output: List: `NAME<TAB>KIND<TAB>ACCOUNT<TAB>USED BY` (`ACCOUNT` is `connected`, `not connected`, `reconnect needed`, `missing` or `no account`), or `No discovered workflow declares an integration.` `--json`: records with `name`, `kind`, `display_name`, `requires_account`, `login`, `connected`, `connected_at`, `account_state`, `icon_url`, `provider_error`, `connections`, `named_connections`, `used_by`. Connect: `Connected NAME.` Exit 1 with the reason for an unknown name or an option that does not fit the integration's kind.

```bash
uv run ava connect
printf '%s\n' "$ACME_KEY" | uv run ava connect acme-crm
uv run ava connect docs-mcp --api-key --header X-Api-Key
uv run ava connect slack --slug support-slack
```

### `ava disconnect NAME`

Remove the stored account of a declared integration.

| Argument | Default | Meaning |
| --- | --- | --- |
| `NAME` | required | Integration name. |
| `--slug SLUG` | none | Catalog connection to disconnect. |
| `--connect CONNECT` | `localhost:7433` | Operator address. |
| `--json` | off | Emit JSON. |

Output: `Disconnected NAME.`, or the status record with `--json`.

```bash
uv run ava disconnect acme-crm
```

## Stored state

### `ava state info`

Show what the state database holds: its path and size, runs by status, schedules, and the size of stored results, input files, schedule inputs, workspaces and step outputs. Like every `ava state` command it opens the database directly and refuses to run while an operator uses it.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--state-db PATH` | `.avalanche/operator/state.db` | SQLite file that keeps operator state, or :memory: for a throwaway one (default: .avalanche/operator/state.db under the current folder). |

Output: A labelled text table, then any leftovers. Exit 1 with `error: Operator state database PATH is already in use by another process, such as another operator. Stop it, or pass a different --state-db.`

```bash
uv run ava state info
```

### `ava state delete RUN_ID [RUN_ID ...]`

Delete the named runs with their logs, events, traces, evaluations, results, input files, workspaces and saved step outputs.

| Argument | Default | Meaning |
| --- | --- | --- |
| `RUN_ID` ... | required | Run to delete. |
| `--state-db PATH` | `.avalanche/operator/state.db` | SQLite file that keeps operator state, or :memory: for a throwaway one (default: .avalanche/operator/state.db under the current folder). |

Output: One `Deleted run RUN_ID (status): ...` line per run. Exit 1 with `error: No stored run RUN_ID in PATH; nothing was deleted.`

```bash
uv run ava state delete RUN_ID
```

### `ava state prune`

Delete old finished runs. Exactly one of `--before` and `--keep-last` is required. Unfinished runs and schedules are kept.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--before DATE` | none | Delete finished runs triggered before this ISO 8601 date or date-time (local time unless it names a time zone). |
| `--keep-last N` | none | Keep the N most recent finished runs and delete the older ones. |
| `--state-db PATH` | `.avalanche/operator/state.db` | SQLite file that keeps operator state, or :memory: for a throwaway one (default: .avalanche/operator/state.db under the current folder). |

Output: One line per deleted run, then `Pruned N finished runs; M unfinished runs left alone.`

```bash
uv run ava state prune --keep-last 50
uv run ava state prune --before 2026-11-01
```

### `ava state clear`

Delete every run, result and input file. Asks `Continue? [y/N]` unless `--yes`; schedules are kept unless `--include-schedules`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--yes` | off | Do not ask for confirmation. |
| `--include-schedules` | off | Also forget every schedule's on/off state, input and input files. |
| `--state-db PATH` | `.avalanche/operator/state.db` | SQLite file that keeps operator state, or :memory: for a throwaway one (default: .avalanche/operator/state.db under the current folder). |

Output: A summary of what was deleted. Exit 1 with `Nothing was cleared.` when the confirmation is declined.

```bash
uv run ava state clear --yes
```
