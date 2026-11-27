# `delta` CLI reference

Every `delta` command, with every argument and default. `delta` deploys a workflow project to Avalanche Cloud and operates it there: runs, waits, schedules, webhooks, environment variables, integrations. It is installed by the `trampoline-delta` package, which `ava init` adds to the project's `dev` dependency group, so in a project run it as `uv run delta ...`.

Related: [Avalanche Cloud](../run/cloud.md), [templates and sharing](../run/templates-and-sharing.md), [`ava` CLI](ava-cli.md) for the local operator, [integrations](../connect/integrations.md), [triggers](../connect/triggers.md).

All 46 commands of the CLI are listed; none was left out as internal. `--execution-env`, `--s3-endpoint-url`, `--issuer-url`, `--client-id` and `--scopes` exist for non-default installations and are not needed on Avalanche Cloud.

## Conventions

- **Sign-in.** `delta login` opens the browser and stores credentials. For scripts, pass a token with `--token` or `DELTA_TOKEN` instead. A token is never read from a config file.
- **Project binding.** `delta init` (or the first `delta deploy`) writes `delta.toml` at the project root. Commands run inside that folder use its organization and project.
- **Where a setting comes from**, first match wins: the flag, the environment variable, `delta.toml`, the user config file `~/.config/trampoline-ai/delta/config.toml`, the default.

| Setting | Flag | Environment | `delta.toml` key | Default |
| --- | --- | --- | --- | --- |
| API URL | `--api-url` | `DELTA_API_URL` | `api_url` | `https://api.avalanche.run` |
| Organization | `--org` | `DELTA_ORG` | `org` | the signed-in organization |
| Project | `--project` | `DELTA_PROJECT` | `project` | none; required by most commands |
| Token | `--token` | `DELTA_TOKEN` | not allowed | the `delta login` credentials |
| Execution environment | `--execution-env` | `DELTA_EXECUTION_ENV` | `execution_env` | none |
| Project interpreter | `--python` (`delta tui`) | `DELTA_PROJECT_PYTHON` | `python` | `<project>/.venv/bin/python` |
| Flow search paths | `--flow-path` (`delta init`) | none | `[flows] paths` | set by `delta init` |

- **Common options.** Most commands accept `--org ORG` (organization ID), `--project PROJECT` (project ID `proj_...` or exact project name), `--api-url URL` and `--token TOKEN`. Each command below lists which of them it takes under "Common options". `delta env`, `delta connect` and `delta disconnect` spell them `--org-id` and `--project-id`, because there `--org` and `--project` choose a scope.
- **Global option.** `delta --no-retry COMMAND ...` turns off HTTP retries for that command. It goes before the command name.
- **JSON output.** Commands with `--json` print the API's model as JSON on stdout; run events print one JSON object per line. Retry identifiers (`sid`, `cmd_id`, `idempotency_key`) go to stderr so stdout stays parseable.
- **Paging.** List commands take `--limit` (default 100, maximum 500) and `--cursor`; the text output ends with `Continue with --cursor ...` when more remain.
- **Exit codes.** `0` success. `1` `Authentication error: ...` or `Delta CLI error: ...` on stderr. `2` `Configuration error: ...` or wrong usage.

## Environment variables

| Variable | Read by | Meaning |
| --- | --- | --- |
| `DELTA_TOKEN` | every command that calls the API | bearer token; replaces `delta login` |
| `DELTA_API_URL`, `DELTA_ORG`, `DELTA_PROJECT` | every command that calls the API | see the table above |
| `DELTA_EXECUTION_ENV` | `delta deploy` | execution environment override |
| `DELTA_S3_ENDPOINT_URL`, `AWS_ENDPOINT_URL_S3` | `delta deploy` | S3 endpoint for S3-compatible installations |
| `DELTA_PROJECT_PYTHON` | `delta deploy`, `delta tui` | interpreter that imports the project's flows |
| `DELTA_DISCOVERY_TIMEOUT` | `delta deploy` | seconds allowed for importing the flows; default `30` |
| `DELTA_HTTP_RETRY_BUDGET_S` | every command that calls the API | total seconds spent retrying a request; default `60` |
| `DELTA_CREATE_PROJECT`, `DELTA_PROJECT_NAME` | built-in integration connect | `1` allows creating a project on first connect; its name |
| `DELTA_AUTH_ISSUER_URL`, `DELTA_AUTH_CLIENT_ID`, `DELTA_AUTH_SCOPES`, `DELTA_AUTH_PUBLISHABLE_KEY` | `delta login`, `delta auth ...` | sign-in settings for non-default installations |

Names for run variables must be uppercase. Names starting with `DELTA_`, and interpreter, loader, shell, proxy, CA-bundle and sandbox names (`PATH`, `HOME`, `PYTHONPATH`, `HTTP_PROXY`, `LD_PRELOAD`, ...) are reserved: `delta env set` and `delta deploy --env-file` refuse them.

## Command map

| Command | Does |
| --- | --- |
| `delta login` / `logout` / `auth status` / `auth whoami` | sign in and out |
| `delta init` | bind a folder to a cloud project |
| `delta deploy` | build and upload a deployment |
| `delta clone` | fetch a project's deployed source |
| `delta projects list` / `rename` / `delete` | manage projects |
| `delta flows list` / `get` | deployed flows |
| `delta deployments list` / `get` / `promote` / `rollback` | deployments |
| `delta runs start` / `list` / `get` / `events` / `history` | start and follow runs |
| `delta runs result` / `outputs` / `artifacts` / `download` | read results and files |
| `delta runs cancel` / `links` / `children` / `parent` | cancel, related runs |
| `delta runs send-event` / `decide` | answer waits |
| `delta machine send` | send an event to a machine run |
| `delta schedules list` / `get` / `disable` | cron schedules |
| `delta webhooks list` / `get` | webhooks |
| `delta env set` / `list` / `unset` | environment variables and secrets for runs |
| `delta connect` / `disconnect` | integration accounts |
| `delta tui` | terminal UI on the cloud project |
| `delta version` | CLI version |

## Sign in

### `delta login`

Sign in to Avalanche Cloud in the browser and store the credentials for later commands. Same as `delta auth login`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--issuer-url ISSUER_URL` | none | Auth issuer URL. Defaults to the Delta production issuer. |
| `--client-id CLIENT_ID` | none | OAuth application client ID. Defaults to the Delta public client. |
| `--scopes SCOPES` | none | OAuth scopes to request. Defaults to profile email offline_access. |
| `--no-browser` | off | Print the sign-in URL and wait for the pasted Console response instead of opening a browser. Automatic over SSH or without a display. |
| `--manual-callback` | off | Deprecated alias of `--no-browser`. |
| `--timeout TIMEOUT` | `300` | Seconds to wait for the loopback browser callback. |
| `--callback-port CALLBACK_PORT` | `8400` | Loopback callback port; `0` for a random port. |

```bash
delta login
delta login --no-browser
```

### `delta logout`

Sign out and remove the stored credentials. Same as `delta auth logout`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--issuer-url ISSUER_URL` | none | Auth issuer URL. Defaults to the Delta production issuer. |
| `--client-id CLIENT_ID` | none | OAuth application client ID. Defaults to the Delta public client. |
| `--scopes SCOPES` | none | OAuth scopes to request. Defaults to profile email offline_access. |

```bash
delta logout
```

### `delta auth login`

Sign in. Also spelled `delta auth signin` and `delta auth sign-in`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--issuer-url ISSUER_URL` | none | Auth issuer URL. Defaults to the Delta production issuer. |
| `--client-id CLIENT_ID` | none | OAuth application client ID. Defaults to the Delta public client. |
| `--scopes SCOPES` | none | OAuth scopes to request. Defaults to profile email offline_access. |
| `--no-browser` | off | Print the sign-in URL and wait for the pasted Console response instead of opening a browser. Automatic over SSH or without a display. |
| `--manual-callback` | off | Deprecated alias of `--no-browser`. |
| `--timeout TIMEOUT` | `300` | Seconds to wait for the loopback browser callback. |
| `--callback-port CALLBACK_PORT` | `8400` | Loopback callback port. Defaults to 8400; use 0 for a random port. |

```bash
delta auth login --callback-port 0
```

### `delta auth logout`

Sign out. Also spelled `delta auth signout` and `delta auth sign-out`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--issuer-url ISSUER_URL` | none | Auth issuer URL. Defaults to the Delta production issuer. |
| `--client-id CLIENT_ID` | none | OAuth application client ID. Defaults to the Delta public client. |
| `--scopes SCOPES` | none | OAuth scopes to request. Defaults to profile email offline_access. |

```bash
delta auth logout
```

### `delta auth status`

Show whether you are signed in.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--issuer-url ISSUER_URL` | none | Auth issuer URL. Defaults to the Delta production issuer. |
| `--client-id CLIENT_ID` | none | OAuth application client ID. Defaults to the Delta public client. |
| `--scopes SCOPES` | none | OAuth scopes to request. Defaults to profile email offline_access. |

```bash
delta auth status
```

### `delta auth whoami`

Show the signed-in account.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--issuer-url ISSUER_URL` | none | Auth issuer URL. Defaults to the Delta production issuer. |
| `--client-id CLIENT_ID` | none | OAuth application client ID. Defaults to the Delta public client. |
| `--scopes SCOPES` | none | OAuth scopes to request. Defaults to profile email offline_access. |

```bash
delta auth whoami
```

### `delta version`

Print the CLI version. `delta --version` and `delta -v` do the same.

```bash
delta version
```

## Projects and deploys

### `delta init [LOCAL_PATH]`

Bind a local project folder to a cloud project by writing `delta.toml`. Reuses a project you can access with that name, or creates it. The name defaults to `[project].name` in `pyproject.toml`. Prints `Created PATH: NAME (proj_...)`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `LOCAL_PATH` | optional | Project folder. |
| `--name NAME` | none | Project name to reuse or create (default: pyproject.toml [project].name). |
| `--execution-env EXECUTION_ENV` | none | Default execution environment. |
| `--flow-path FLOW_PATH` (repeatable) | none | Project-root-relative flow search path. May be repeated. |
| `--force` | off | Overwrite config, preserving its ID. |

Common options: `--org`, `--project`, `--api-url`.

```bash
delta init --name invoice-review --flow-path src
```

### `delta deploy [LOCAL_PATH]`

Build the project in `LOCAL_PATH` and upload it as a new deployment, which becomes current for its flows. Without `delta.toml` it reuses the project named by `--name` or `pyproject.toml`, or offers to create it (default answer: no). Prints the deployment ID, the source commit, the bundle and the deployed flows, then any discovery diagnostics.

| Argument | Default | Meaning |
| --- | --- | --- |
| `LOCAL_PATH` | optional | Project folder. |
| `--name NAME` | none | Exact project name when delta.toml is missing; default: pyproject.toml [project].name. Reuses an existing project, or offers creation with interactive default-no confirmation. Never renames a project. |
| `--execution-env EXECUTION_ENV` | none | Execution environment override, such as local-ray. |
| `--s3-endpoint-url S3_ENDPOINT_URL` | none | S3 endpoint URL override for local and S3-compatible deployments. |
| `--env-file ENV_FILE` | none | Set each variable in a UTF-8 dotenv file as a mutable project-scoped secret before deploying; the file is excluded from source and build inputs. |
| `--json` | off | Print machine-readable JSON output. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta deploy
delta deploy --env-file .env --json
```

### `delta clone PROJECT [DIRECTORY]`

Fetch the source of a project's current deployment into a new folder: a git repository with one commit and a `delta.toml` that points at the project, ready for `delta deploy`. The folder must not exist or must be empty.

| Argument | Default | Meaning |
| --- | --- | --- |
| `PROJECT` | required | Delta project ID (proj_...) or exact project name in the selected organization. |
| `DIRECTORY` | optional | Folder to create (default: the project argument as given). |
| `--deployment DEPLOYMENT_ID` | none | Clone this finalized deployment (default: the one current for the project's flows). |

Common options: `--org`, `--api-url`, `--token`.

```bash
delta clone invoice-review ./invoice-review
```

### `delta projects list`

List the projects of an organization.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Print exact API model JSON. |
| `--limit LIMIT` | `100` | Page size (default 100, maximum 500). |
| `--cursor CURSOR` | none | Continue after the last returned item; refresh without a cursor to see newer items. |

Common options: `--org`, `--api-url`, `--token`.

```bash
delta projects list --json
```

### `delta projects rename NAME`

Set the display name of the project in `delta.toml` (or `--project`). The project ID does not change.

| Argument | Default | Meaning |
| --- | --- | --- |
| `NAME` | required | New project name (nonblank, at most 200 characters). |
| `--json` | off | Print exact API model JSON. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta projects rename 'Invoice review'
```

### `delta projects delete`

Permanently delete the project and everything stored for it: deployments, runs and their files, schedules, webhooks, env vars, integration grants. There is no restore. Organization admins only; refused while a run is unfinished. Asks you to type the project ID unless `--yes`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--yes` | off | Do not ask to type the project ID to confirm. |
| `--wait` | off | Wait until the stored data is gone, not only hidden. |
| `--json` | off | Print exact API model JSON. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta projects delete --project proj_demo --wait
```

### `delta flows list`

List the deployed flows of the project.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Print exact API model JSON. |
| `--limit LIMIT` | `100` | Page size (default 100, maximum 500). |
| `--cursor CURSOR` | none | Continue after the last returned item; refresh without a cursor to see newer items. |
| `--deployment-id DEPLOYMENT_ID` | none | Read flows from this finalized deployment (default: each flow's current deployment). |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta flows list
```

### `delta flows get FLOW`

Show one deployed flow by its slug, including its current deployment.

| Argument | Default | Meaning |
| --- | --- | --- |
| `FLOW` | required | Deployed flow slug. |
| `--json` | off | Print exact API model JSON. |
| `--deployment-id DEPLOYMENT_ID` | none | Read the flow from this finalized deployment (default: the flow's current deployment). |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta flows get review_order --json
```

### `delta deployments list`

List finalized deployments and the flows each one is current for.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Print the API deployment model JSON plus current_for: the flow slugs whose current deployment this is. |
| `--limit LIMIT` | `100` | Page size (default 100, maximum 500). |
| `--cursor CURSOR` | none | Continue after the last returned item; refresh without a cursor to see newer items. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta deployments list
```

### `delta deployments get DPL`

Show one finalized deployment and the flows it is current for.

| Argument | Default | Meaning |
| --- | --- | --- |
| `DPL` | required | Deployment ID. |
| `--json` | off | Print the API deployment model JSON plus current_for: the flow slugs whose current deployment this is. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta deployments get DEPLOYMENT_ID
```

### `delta deployments promote DPL`

Make a finalized deployment current for its flows, or for one flow with `--flow`. The next `delta deploy` takes over again. Asks for confirmation unless `--yes`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `DPL` | required | Deployment ID. |
| `--flow FLOW_SLUG` | none | Only this flow (default: every flow in DPL). |
| `--yes` | off | Skip the confirmation prompt. |
| `--json` | off | Print exact API model JSON. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta deployments promote DEPLOYMENT_ID --flow review_order --yes
```

### `delta deployments rollback`

Point a flow at the newest finalized deployment older than its current one. Asks for confirmation unless `--yes`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--flow FLOW_SLUG` | required | Flow slug. |
| `--yes` | off | Skip the confirmation prompt. |
| `--json` | off | Print exact API model JSON. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta deployments rollback --flow review_order --yes
```

## Runs

### `delta runs start [FLOW]`

Start a run of a deployed flow and print its run ID. Name the flow as `FLOW` or `--flow`. The retry-stable `sid` is printed on stderr; pass it back with `--sid` to retry a start without creating a second run. With `--rerun-of`, rerun a finished, failed or cancelled run from the `--from` steps.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `FLOW` | optional | Deployed flow slug. |
| `--flow FLOW` | none | Deployed flow slug (same as FLOW). |
| `--input INPUT` | none | Input as a JSON object (use {} for file-only input). With --rerun-of it is optional and replaces only the fields it names. |
| `--rerun-of RUN_ID` | none | Rerun this finished, failed or cancelled run. |
| `--from SLUG` (repeatable) | none | Step to rerun from; repeatable. Earlier steps reuse their saved results. |
| `--only` | off | Rerun only the --from steps, not the steps after them. |
| `--file FIELD=PATH` (repeatable) | none | Upload and bind a local file to an input field; repeatable. |
| `--deployment DEPLOYMENT` | none | Deployment ID; default: the flow's current deployment (see `delta flows get`). |
| `--sid SID` | none | Retry-stable ID; default: a fresh cli-<hex> value. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs start review_order --input '{"order_id": "A1"}'
delta runs start summarize_report --input '{}' --file report=./report.pdf
delta runs start summarize_report --rerun-of RUN_ID --from write_summary
```

### `delta runs list`

List runs, newest first. Text output is `TYPE<TAB>RUN<TAB>FLOW<TAB>STATUS<TAB>NOTE`, then `Continue with --cursor ...` when there is another page. `--json` prints `{runs, cursor, complete}`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `--flow FLOW` | none | Filter by deployed flow slug. |
| `--status {queued,running,waiting,succeeded,failed,cancelled,unknown}` | none | Filter by human run status. |
| `--deployment DEPLOYMENT` | none | Filter by deployment ID. |
| `--after AFTER` | none | List runs newer than this run ID; exclusive with --cursor. |
| `--limit LIMIT` | `100` | Page size (default 100, maximum 500). |
| `--cursor CURSOR` | none | Continue after the last returned item; refresh without a cursor to see newer items. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs list --flow review_order --status waiting
```

### `delta runs get RUN_ID`

Show one run: kind, status, steps and attempts.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs get RUN_ID --json
```

### `delta runs events RUN_ID`

Follow a flow run's events as they happen. With `--json`, one JSON object per line. Stops with an error when the stream reports `stream_error`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |
| `--last-event-id LAST_EVENT_ID` | none | Resume after this event ID. |
| `--max-reconnects MAX_RECONNECTS` | `3` | How many times to reconnect a dropped stream. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs events RUN_ID --json
```

### `delta runs history RUN_ID`

Show a machine run's state transitions, one page at a time. For a flow run, use `delta runs events`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |
| `--limit LIMIT` | `20` | Page size, 1 to 100. |
| `--cursor CURSOR` | none | `next_cursor` of the previous page. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs history RUN_ID --limit 50
```

### `delta runs result RUN_ID`

Read the result of a run, of one step (`--node`), or of one completed machine state entry (`--entry`). Files are returned as references; fetch them with `delta runs download`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |
| `--entry ENTRY` | none | Completed machine state entry (1-based). |
| `--node NODE` | none | Step ID, local to --entry when supplied. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs result RUN_ID --json
```

### `delta runs outputs RUN_ID`

Show the step outputs accepted so far. This is a progress view, not the final result.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs outputs RUN_ID
```

### `delta runs artifacts RUN_ID`

List the files (artifacts) the run's steps produced.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs artifacts RUN_ID --json
```

### `delta runs download RUN_ID [BINDING] [PATH]`

Download a run's artifacts into a directory (default `./RUN_ID`), or only one with `--path`. The positional `BINDING PATH` form with `--dest` is the legacy single-file form; do not combine it with `--out` or `--path`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |
| `BINDING` | optional | Legacy single-artifact binding. |
| `PATH` | optional | Legacy single-artifact path. |
| `--dest DEST` | none | Legacy single-file destination. |
| `--path PATH_FILTER` | none | Download only this canonical artifact path. |
| `--out OUT` | none | Destination directory (default: ./RUN). |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs download RUN_ID --out ./artifacts
```

### `delta runs cancel RUN_ID`

Ask for a run to be cancelled. The command is recorded; runs it started are not cancelled with it. The `cmd_id` is printed on stderr; reuse it with `--cmd-id` when retrying.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |
| `--cmd-id CMD_ID` | none | Retry-stable ID of this cancel command; default: a new UUID. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs cancel RUN_ID
```

### `delta runs links RUN_ID`

Print the runs that started this run (`parents`) and the runs it started (`children`) as JSON.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs links RUN_ID
```

### `delta runs children RUN_ID`

Print only the `children` list of `delta runs links`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs children RUN_ID
```

### `delta runs parent RUN_ID`

Print only the `parents` list of `delta runs links`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs parent RUN_ID
```

## Waits and machine events

### `delta runs send-event RUN_ID KEY`

Deliver an event to a run waiting on `ava.wait_for_event(..., key=KEY)`. `--data` is the payload, as JSON or `@file`. The idempotency key (a new one unless given) is printed on stderr.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |
| `KEY` | required | Event key, as in `ava.wait_for_event(..., key=...)`. |
| `--data DATA` | required | JSON or @file. |
| `--idempotency-key IDEMPOTENCY_KEY` | none | Retry-stable ID; default: a new UUID. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs send-event RUN_ID shipment --data '{"carrier": "UPS"}'
```

### `delta runs decide RUN_ID INVOCATION`

Answer a run waiting on `ava.decision(...)`. `INVOCATION` is the decision's node ID; `--index` picks one item of a mapped decision. `--outcome` and `--reason` are required.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Exact generated model JSON (events: JSON lines). |
| `RUN_ID` | required | Run ID. |
| `INVOCATION` | required | Decision node ID. |
| `--index INDEX` | none | Item index of a mapped decision. |
| `--outcome OUTCOME` | required | JSON or @file. |
| `--reason REASON` | required | Why this answer was given; stored with it. |
| `--idempotency-key IDEMPOTENCY_KEY` | none | Retry-stable ID; default: a new UUID. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta runs decide RUN_ID decision_1 --outcome '{"approved": true}' --reason 'Checked the invoice'
```

### `delta machine send [MACHINE] EVENT`

Send an external event to a machine run, addressed by `--run RUN_ID` (no machine name) or by machine name plus one `--key FIELD=VALUE` per key field.

| Argument | Default | Meaning |
| --- | --- | --- |
| `MACHINE` | optional | Machine name; required with --key, omitted with --run. |
| `EVENT` | required | External event name. |
| `--run RUN_ID` | none | Target run ID; no discovery required. |
| `--key FIELD=VALUE` (repeatable) | none | One key field; repeat per key field; needs the machine name. |
| `--json` | off | Print JSON. |
| `--data DATA` | none | Typed payload JSON or @file; default empty body. |
| `--idempotency-key IDEMPOTENCY_KEY` | none | Reuse this value when retrying. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta machine send ticket resolve --key ticket_id=T1 --data '{"note": "fixed"}'
delta machine send resolve --run RUN_ID --data @resolution.json
```

## Triggers

### `delta schedules list`

List the project's cron schedules. Schedules are declared in code with `@ava.workflow(cron=...)` and created by a deploy. Prints `No schedules.` when there are none.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Print machine-readable JSON. |
| `--limit LIMIT` | `100` | Page size (default 100, maximum 500). |
| `--cursor CURSOR` | none | Continue after the last returned item; refresh without a cursor to see newer items. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta schedules list
```

### `delta schedules get SCHEDULE_ID`

Show one schedule.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Print machine-readable JSON. |
| `SCHEDULE_ID` | required | Schedule ID. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta schedules get SCHEDULE_ID --json
```

### `delta schedules disable SCHEDULE_ID`

Stop a schedule from starting runs. `--expected-revision` is required: pass the revision shown by `delta schedules get`, so a schedule changed in the meantime is not disabled by mistake.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--json` | off | Print machine-readable JSON. |
| `SCHEDULE_ID` | required | Schedule ID. |
| `--expected-revision EXPECTED_REVISION` | required | Revision the schedule must still have. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta schedules disable SCHEDULE_ID --expected-revision 3
```

### `delta webhooks list`

List the webhooks declared by the project's deployed flows (`@ava.workflow(webhook=...)`).

| Argument | Default | Meaning |
| --- | --- | --- |
| `--limit LIMIT` | `100` | Page size (default 100, maximum 500). |
| `--cursor CURSOR` | none | Continue after the last returned item; refresh without a cursor to see newer items. |
| `--json` | off | Print machine-readable JSON output. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta webhooks list
```

### `delta webhooks get FLOW_SLUG`

Show the webhook of one flow.

| Argument | Default | Meaning |
| --- | --- | --- |
| `FLOW_SLUG` | required | Deployed flow slug. |
| `--json` | off | Print machine-readable JSON output. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta webhooks get review_order --json
```

## Environment variables and integrations

### `delta env set NAME`

Set an environment variable that runs see, at organization, project (default) or flow scope. The value is read from `--value-file`, else from standard input verbatim; it is never an argument. With `--secret` the value is encrypted and never returned. Prints `{"name": NAME, "status": "set"}`.

| Argument | Default | Meaning |
| --- | --- | --- |
| `NAME` | required | Variable name. |
| `--org` | off | Organization scope. |
| `--project` | off | Project scope (default). |
| `--flow SLUG` | none | Flow scope in the current project. |
| `--org-id ORG_ID` | none | Organization ID (otherwise current configuration). |
| `--project-id PROJECT_ID` | none | Delta project ID (proj_...) or exact project name in the selected organization. Defaults to delta.toml/environment. |
| `--json` | off | Print JSON. |
| `--secret` | off | Encrypt this value; omit it from list responses. |
| `--value-file VALUE_FILE` | none | UTF-8 value file; otherwise read stdin verbatim. |

Common options: `--api-url`.

```bash
printf '%s' "$OPENAI_API_KEY" | delta env set OPENAI_API_KEY --secret
delta env set REGION --flow review_order --value-file region.txt
```

### `delta env list`

List the variables of one scope. Secret values are not shown.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--org` | off | Organization scope. |
| `--project` | off | Project scope (default). |
| `--flow SLUG` | none | Flow scope in the current project. |
| `--org-id ORG_ID` | none | Organization ID (otherwise current configuration). |
| `--project-id PROJECT_ID` | none | Delta project ID (proj_...) or exact project name in the selected organization. Defaults to delta.toml/environment. |
| `--json` | off | Print JSON. |

Common options: `--api-url`.

```bash
delta env list --org
```

### `delta env unset NAME`

Remove a variable from one scope.

| Argument | Default | Meaning |
| --- | --- | --- |
| `NAME` | required | Variable name. |
| `--org` | off | Organization scope. |
| `--project` | off | Project scope (default). |
| `--flow SLUG` | none | Flow scope in the current project. |
| `--org-id ORG_ID` | none | Organization ID (otherwise current configuration). |
| `--project-id PROJECT_ID` | none | Delta project ID (proj_...) or exact project name in the selected organization. Defaults to delta.toml/environment. |
| `--json` | off | Print JSON. |

Common options: `--api-url`.

```bash
delta env unset REGION --flow review_order
```

### `delta connect [NAME]`

Without `NAME`, list the integrations the project's current flows declare and whether each has an account. With `NAME`, connect its account: an API-key Connector asks for the key, a browser-login Connector asks for the client ID and secret then opens the browser, a remote MCP server opens the browser or takes a key with `--api-key`. Keys and secrets are prompted for or read from standard input, never passed as arguments.

| Argument | Default | Meaning |
| --- | --- | --- |
| `NAME` | optional | Integration name, such as acme-crm. |
| `--api-key` | off | Connect a remote MCP server with an API key instead of a browser login. |
| `--header HEADER` | none | Request header an MCP server expects its API key in (implies --api-key); a Connector declares its own and takes no --header. |
| `--client-secret` | off | Ask for the secret of the client an MCP server's declaration names with client_id=, for a server whose registered client has one. |
| `--no-browser` | off | Print the login address instead of opening a browser. |
| `--create-project` | off | Explicitly create/link a project on first built-in connect (requires --project-name or pyproject name). |
| `--project-name PROJECT_NAME` | none | Name for an explicitly created project; never inferred from the folder. |
| `--org-id ORG_ID` | none | Organization ID (otherwise current configuration). |
| `--project-id PROJECT_ID` | none | Delta project ID (proj_...) or exact project name in the selected organization. Defaults to delta.toml/environment. |
| `--json` | off | Print JSON. |

Common options: `--api-url`.

```bash
delta connect
delta connect acme-crm
delta connect docs-mcp --api-key --header X-Api-Key
```

### `delta disconnect NAME`

Remove the account stored for an integration.

| Argument | Default | Meaning |
| --- | --- | --- |
| `NAME` | required | Integration name, such as acme-crm. |
| `--org-id ORG_ID` | none | Organization ID (otherwise current configuration). |
| `--project-id PROJECT_ID` | none | Delta project ID (proj_...) or exact project name in the selected organization. Defaults to delta.toml/environment. |
| `--json` | off | Print JSON. |

Common options: `--api-url`.

```bash
delta disconnect acme-crm
```

## Terminal UI

### `delta tui`

Open the terminal UI on the cloud project. It runs with the project's own Python interpreter (default `<project>/.venv/bin/python`), which must have Avalanche installed.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--python PYTHON` | none | Project interpreter (default: <project>/.venv/bin/python). |
| `--flow FLOW` | none | Deployed flow slug. |
| `--run RUN` | none | Run ID to inspect. |
| `--read-only` | off | Disable all backend mutations. |

Common options: `--org`, `--project`, `--api-url`, `--token`.

```bash
delta tui --flow review_order --read-only
```
