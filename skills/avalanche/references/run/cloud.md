# Avalanche Cloud

Avalanche Cloud is the managed runtime for Avalanche workflows. You deploy a project with the
`delta` CLI; the cloud runs each step durably in its own sandbox, and a web console shows
flows, runs and traces. The workflow code is the same code that runs on the local operator.

Avalanche Cloud is in **private preview**: it serves only organizations that have been
enabled. For any other organization the API and CLI answer 503
`durable_runs_org_unconfigured` and the console shows "Avalanche Cloud is in private preview".

## When to use it

- Use it when runs must survive restarts, run on a schedule or a public webhook without a
  machine of your own, or be shared with a team in a console.
- Use the local operator to develop and to test: [local.md](local.md). Deploy only what
  already runs locally.
- To host everything yourself, use `ava operator`: [self-hosting.md](self-hosting.md).

## Interface

The CLI is the `delta` command of the `trampoline-delta` package. `ava init` adds it to the
project's `dev` dependency group, so `uv run delta ...` works in an initialized workspace.
Every command and flag is in [../reference/delta-cli.md](../reference/delta-cli.md).

### Sign in

| Command | Meaning |
| --- | --- |
| `delta login` | Opens a browser and waits for the callback on loopback port 8400. A person must complete it. |
| `delta login --no-browser` | Prints a sign-in URL and waits for a pasted response. Automatic over SSH or without a display. |
| `delta auth status`, `delta auth whoami` | Show the sign-in state and the account. |
| `delta logout` | Sign out. |
| `DELTA_TOKEN=...` or `--token` | A bearer token for automation, instead of a login. |

An agent cannot complete `delta login`. Ask the user to sign in, or to provide `DELTA_TOKEN`.

### Organization, project, deployment, flow

| Object | What it is |
| --- | --- |
| Organization | The account boundary: members, permissions, org-level variables. ID like `org_...`. |
| Project | One deployed codebase (one `pyproject.toml`). ID like `proj_...`. Bound to a folder by `delta.toml`. |
| Deployment | An immutable build of the project's source and locked dependencies. ID like `dpl_...`. |
| Flow | One discovered `@ava.workflow` or `@ava.machine` in a deployment, addressed by its flow slug. |

There are no separate environments (staging, production) inside a project. Use two projects.

The flow slug is the workflow function name, lowercased, with every run of characters
outside `a-z0-9` replaced by `-`: `price_orders` becomes `price-orders`. Two workflows that
map to the same slug fail the deploy with `Flow slug collision`.

`delta.toml`, written at the project root by the first successful `delta deploy` or by
`delta init`. Commit it.

```toml
org = "org_..."
project = "proj_..."
api_url = "https://..."

[flows]
paths = []
```

| Key | Meaning |
| --- | --- |
| `org`, `project`, `api_url` | The binding. Overridden by `--org` / `--project` / `--api-url`, then by `DELTA_ORG` / `DELTA_PROJECT` / `DELTA_API_URL`. |
| `python` | Project interpreter used to discover flows (default `.venv/bin/python`; also `DELTA_PROJECT_PYTHON`). |
| `execution_env` | Default execution environment name (also `--execution-env`, `DELTA_EXECUTION_ENV`). |
| `[flows] paths` | Project-relative folders to search for flows. |

### Deploy

```bash
uv lock                      # the deploy needs an up-to-date uv.lock
uv sync                      # and the project environment, to discover flows
uv run delta deploy          # from the project root
uv run delta deploy --json   # machine-readable result
```

What `delta deploy [PATH]` does:

1. Resolves the project: `delta.toml`, else `--project ID`, else an existing project named
   `--name` or `[project].name`. With no match it offers to create one, only in an
   interactive terminal; with `--json` or without a terminal it fails with guidance.
2. Discovers flows with the project's Python. No flow found: `No Avalanche workflows were
   discovered for deployment.`
3. Freezes the project folder (not the git index), builds a wheelhouse from `uv.lock` for
   the runtime's platform, uploads both, and finalizes the deployment.
4. Makes the new deployment current for its flows and turns on their code-declared schedules
   and webhooks.

It prints the deployment ID and one line per flow: `- <flow-slug>: <workflow name> (<file>)`.

| Related command | Meaning |
| --- | --- |
| `delta init [PATH] [--org] [--project] [--name] [--flow-path P]` | Create or bind a project and write `delta.toml` without deploying. Also adds `litellm` to `[tool.uv] exclude-dependencies`. |
| `delta projects list`, `rename NAME`, `delete [--yes] [--wait]` | List, rename, or permanently delete a project and all its data. |
| `delta flows list`, `delta flows get FLOW` | Flows and the deployment each one currently runs. |
| `delta deployments list`, `get DPL` | Finalized deployments. |
| `delta deployments promote DPL [--flow SLUG] [--yes]` | Make an older deployment current again. |
| `delta deployments rollback --flow SLUG [--yes]` | Point a flow at the newest deployment older than its current one. |
| `delta clone PROJECT [DIR] [--deployment DPL]` | Fetch a project's deployed source into a new folder bound to it (one commit, no history). |

### Variables and secrets

A deployed step sees only the variables its flow declares. Declare names in `pyproject.toml`,
then set values with the CLI.

```toml
[tool.delta.env]
price-orders = ["OPENAI_API_KEY", "MODEL_NAME"]   # flow slug = names it receives
```

```bash
uv run delta env set OPENAI_API_KEY --secret < key.txt        # project scope (default)
uv run delta env set MODEL_NAME --flow price-orders --value-file model.txt
uv run delta env set REGION --org < region.txt
uv run delta env list
uv run delta env unset MODEL_NAME --flow price-orders
uv run delta deploy --env-file .env.production                # set every name, then deploy
```

| Rule | Detail |
| --- | --- |
| Scopes | `--org`, `--project` (default), `--flow SLUG`. |
| Value source | stdin, or `--value-file`. Never a command-line argument. |
| `--secret` | Encrypts the value; `env list` never returns it. |
| Names | `[A-Z][A-Z0-9_]{0,127}`, not starting with `DELTA_`. At most 64 names per flow. |
| `--env-file` | A UTF-8 dotenv file: at most 128 variables, 128 KiB, 8 KiB per value, no `$` interpolation. Each name is stored as a project-scoped secret; names no flow declares are stored but not injected, with a warning. |
| `[tool.delta.env]` as a list | Allowed only when the project has exactly one flow. |

Accounts for integrations are separate: `delta connect` lists the integrations the flows
declare, `delta connect NAME` stores an account. See [integrations.md](../connect/integrations.md).

### Step timeouts

```toml
[tool.delta.timeouts.price-orders]     # flow slug
default_s = 300                        # every ordinary step of this flow
steps = { format_receipt_1 = 900 }     # one step, by node ID
```

| Step kind | Default budget |
| --- | --- |
| Ordinary step, `PredictRLM` agent step, classifier step | 60 seconds |
| `ClaudeAgent` or `OpenAIAgent` agent step | 600 seconds (not changed by `default_s`; name the node in `steps`) |
| Maximum for any step | 3600 seconds (`step_timeout_exceeds_runtime_max` above it) |

### Run and inspect

```bash
RUN=$(uv run delta runs start price-orders --input '{"order_id": "A1", "quantity": 4}')
uv run delta runs get "$RUN" --json        # status, each step's attempts and error code
uv run delta runs events "$RUN" --json     # event stream, one JSON object per line
uv run delta runs result "$RUN"            # the run's result
uv run delta runs result "$RUN" --node format_receipt_1
uv run delta runs download "$RUN" --out ./out
```

| Command | Meaning |
| --- | --- |
| `delta runs start FLOW --input JSON [--file FIELD=PATH] [--deployment DPL] [--sid ID]` | Start a run on the flow's current deployment; prints the run ID. `--input` is required (use `{}`), except with `--rerun-of`. `--sid` makes a retry of the same request return the same run. |
| `delta runs start FLOW --rerun-of RUN --from SLUG [--only] [--input JSON]` | Rerun from a step, reusing earlier results. |
| `delta runs list [--flow] [--status] [--deployment] [--after RUN]` | Runs of the project. |
| `delta runs get RUN`, `history RUN`, `events RUN` | Snapshot, machine transitions, event stream. |
| `delta runs result RUN [--node NODE_ID] [--entry N]`, `outputs RUN`, `artifacts RUN`, `download RUN` | Results and files. |
| `delta runs cancel RUN` | Cancel. |
| `delta runs send-event RUN KEY --data JSON\|@file`, `delta runs decide RUN INVOCATION --outcome JSON --reason TEXT` | Answer waits and approvals. |
| `delta runs links RUN`, `children RUN`, `parent RUN` | Child and parent runs. |
| `delta machine send [MACHINE] EVENT (--run RUN \| --key FIELD=VALUE) [--data JSON]` | Send an event to a state machine run. |
| `delta schedules list\|get\|disable`, `delta webhooks list\|get` | Inspect triggers declared in code. |
| `delta tui [--flow] [--run] [--read-only]` | The terminal UI against the cloud; interactive. |

All of these accept `--json`, `--org`, `--project`, `--api-url`, `--token`.

## Example

A project that already runs under `ava dev`, deployed and run once:

```bash
uv run delta login                       # done by a person, once
uv lock && uv sync
printf '%s' "$OPENAI_API_KEY" | uv run delta env set OPENAI_API_KEY --secret
uv run delta deploy                      # first time: creates delta.toml; commit it
uv run delta flows list
RUN=$(uv run delta runs start price-orders --input '{"order_id": "A1", "quantity": 4}')
uv run delta runs get "$RUN" --json
```

```toml
# pyproject.toml
[tool.avalanche]
flow_targets = ["flows"]

[tool.delta.env]
price-orders = ["OPENAI_API_KEY"]

[tool.uv]
exclude-dependencies = ["litellm"]
```

## What differs from local

| Topic | Local operator | Avalanche Cloud |
| --- | --- | --- |
| Where steps run | Processes on your machine, your files and logins available | Each step in its own sandbox; nothing from your machine |
| Files between steps | Any local path works | Only an `ava.Workspace` passed to the steps, or `ava.File` values. See [inputs-and-files.md](../building-blocks/inputs-and-files.md). |
| Environment | The operator's environment | Only names declared in `[tool.delta.env]` and set with `delta env set` |
| Code in use | Reloaded when a file is saved | Fixed per deployment; `delta deploy` again to change it |
| Restart | In-flight flow runs fail | Runs are durable |
| Step time | No limit | Budget per step, 3600 seconds at most |
| Flow addressing | Function name or `file.py::name` | Flow slug (`price-orders`) |
| Schedules | Start disabled; `ava schedule enable` | On as soon as the flow is deployed. Turn one off in the console, or with `delta schedules disable SCHEDULE_ID --expected-revision N` (ID and revision from `delta schedules list --json`); the choice survives later deploys |
| Webhooks | `http://127.0.0.1:7434/webhooks/...` | A public URL per flow: `delta webhooks get FLOW` |
| Dependencies | Whatever the environment has | Hash-locked binary wheels from `uv.lock` |
| Access | No authentication | Sign-in, organizations, permissions |

## Limits

| Limit | Value | Error |
| --- | --- | --- |
| Step budget | 60 s default, 3600 s maximum | `step_timeout_exceeds_runtime_max` |
| Steps in one flow | 192 | refused at deploy |
| One uploaded input file (`--file`) | 64 MiB | upload refused |
| Project source at deploy | 20,000 files, 25 MiB per file, 128 MiB in total | named in the deploy error |
| Variables | 64 names per flow; dotenv file 128 variables, 8 KiB per value | `env_declaration_invalid`, `environment_*` |
| Dependencies | Must have a locked binary wheel for the runtime; no local or editable paths, no source-only packages | `dependency_sdist_only:<name>`, `dependency_platform_unsupported:<name>` |
| Pending file uploads | Capped per organization, by count and bytes | `file_upload_pending_count_limit`, `file_upload_pending_bytes_limit` |
| Trial runs of an imported template | 3 per user | `template_trial_exhausted` ([templates-and-sharing.md](templates-and-sharing.md)) |

## Rules

- Make the flow pass locally first; a deploy does not run your code, it only discovers it.
- Commit `pyproject.toml`, `uv.lock` and `delta.toml`. `Dependencies require uv.lock. Run uv
  lock and retry.` means the lock is missing or stale.
- The deploy freezes the working folder as it is, committed or not. It leaves out `.git`,
  `.venv`, `venv`, caches, root-level `build`/`dist`, every `.env*` file, and paths matched
  by the project's `.gitignore` files.
- Never put a secret in code, in `delta.toml` or on a command line. Use `delta env set NAME
  --secret` with stdin.
- Declare every variable a flow reads in `[tool.delta.env]`. A value that is set but not
  declared never reaches the step.
- Write nothing next to your code at run time: the deployed package is read-only. Share
  files between steps through an `ava.Workspace` parameter; use `/tmp` for scratch.
- Pass values and relative names between steps, never absolute paths.
- Split work that needs more than an hour into several steps.
- Do not rely on local logins (`claude`, `gh`, cloud CLIs). Use API keys as declared secrets.
- `delta deploy` never renames a project and never creates one silently. In automation pass
  `--project ID` (find it with `delta projects list`).
- A run addresses the flow slug, not the Python name: `delta runs start price-orders`.
- Do not run `delta login` yourself; it needs a person. Without a session and without
  `DELTA_TOKEN`, stop and ask the user.
- A project imported from a template is frozen (no deploy, no clone) until converted:
  409 `template_trial_frozen`. See [templates-and-sharing.md](templates-and-sharing.md).
- Built-in integrations and named connections are not available on every account yet. See
  [integrations.md](../connect/integrations.md).

## In the UI

- The console shows the same flow and run views as the local web UI: graph, step cards with
  the first docstring line, inputs and outputs, logs, agent traces.
- Projects, deployments, variables and integrations have console pages; everything on this
  page is also reachable with the CLI, except what
  [templates-and-sharing.md](templates-and-sharing.md) lists as console-only.

## See also

- [local.md](local.md), [self-hosting.md](self-hosting.md), [templates-and-sharing.md](templates-and-sharing.md).
- [../reference/delta-cli.md](../reference/delta-cli.md), [../reference/project-layout.md](../reference/project-layout.md).
- [../building-blocks/inputs-and-files.md](../building-blocks/inputs-and-files.md),
  [../connect/triggers.md](../connect/triggers.md), [../connect/integrations.md](../connect/integrations.md).
- [../guides/debugging.md](../guides/debugging.md).
