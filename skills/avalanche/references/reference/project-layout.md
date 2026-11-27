# Project layout and settings reference

What an Avalanche project contains, every setting the tools read from files, every environment variable they read, and what each package extra installs.

Related: [`ava` CLI](ava-cli.md), [`delta` CLI](delta-cli.md), [running locally](../run/local.md), [runtimes](../building-blocks/runtimes.md), [classifier steps](../building-blocks/classifier-steps.md).

## Requirements

| Item | Value |
| --- | --- |
| Package | `avalanche-ai` (import name `avalanche`; convention `import avalanche as ava`) |
| Python | `>=3.13,<3.14` |
| Commands installed | `ava`, and `avalanche-ai` as a second name for it |
| Tools `ava init` needs | `git`, `uv` (it installs Python 3.13 through uv when missing) |

## What `ava init` creates

Run in an empty directory. Anything already there, hidden files included, stops it with `Current directory is not empty`.

```text
.
├── .agent/skills/avalanche/   # this skill, copied from the installed package
├── .git/                      # new repository, nothing committed yet
├── .gitignore                 # uv's Python ignores, plus .env
├── .venv/                     # created by `uv sync`
├── AGENTS.md                  # conventions for coding agents working in the project
├── README.md                  # how to run start_flow and choose a runtime and model
├── pyproject.toml             # dependencies and [tool.avalanche]
├── src/
│   └── start_flow/
│       └── flow.py            # starter flow: source, step, agent step, dest
└── uv.lock
```

| File | Role |
| --- | --- |
| `pyproject.toml` | one uv project for all workflows. Declares the dependencies and `[tool.avalanche].flow_targets = ["src"]` |
| `src/<workflow>/flow.py` | one folder per workflow, directly under `src/`. Do not add a wrapper package or a per-workflow `pyproject.toml` |
| `src/start_flow/flow.py` | `create_topic >> prepare_request >> write_greeting >> print_greeting`. Its agent step uses `ava.runtimes.PredictRLM(lm="openai/gpt-5.5", ...)`, so a run needs `OPENAI_API_KEY`; the flow can be viewed without a key |
| `.gitignore` | ignores `.venv`, build output and `.env` |
| `AGENTS.md`, `README.md` | project conventions; run and model instructions |
| `.agent/skills/avalanche/` | the authoring skill |

`ava init --editable-deps` also clones Avalanche and PredictRLM into `.trampoline-ai/` (ignored by git) and points `[tool.uv.sources]` at the clones as editable dependencies.

The generated `pyproject.toml`:

```toml
[project]
name = "avalanche-workspace"
version = "0.1.0"
requires-python = ">=3.13,<3.14"
dependencies = ["avalanche-ai[agents]>=0.17"]

[dependency-groups]
dev = ["trampoline-delta>=0.3.4"]

[tool.avalanche]
flow_targets = ["src"]

[tool.uv]
exclude-dependencies = ["litellm"]
```

`exclude-dependencies = ["litellm"]` keeps LiteLLM out of the install. Model routes served only through LiteLLM (for example `bedrock/`, `vertex_ai/`) need that line removed and `dspy[litellm]` added.

## Files the operator writes

`ava dev` and `ava operator` keep their state under the folder they are started in (change it with `--state-db`):

| Path | Holds |
| --- | --- |
| `.avalanche/operator/state.db` | SQLite: runs, steps, logs, events, traces, evaluations, schedules, integration accounts |
| `.avalanche/operator/results/` | stored run results |
| `.avalanche/operator/step-outputs/` | saved step outputs, reused by reruns |
| `.avalanche/operator/input-files/` | files attached to runs |
| `.avalanche/operator/schedule-inputs/` | files attached to schedule inputs |
| `.avalanche/operator/workspaces/` | workspaces the operator owns |

Add `.avalanche/` to `.gitignore`; `ava init` does not. One operator may use a database at a time; a second one stops with `StateStoreLockedError`. Runs that were unfinished when an operator stopped are marked failed at the next start and are not resumed. `--state-db :memory:` keeps nothing. Inspect and clear with `ava state` ([ava-cli.md](ava-cli.md#stored-state)).

## `[tool.avalanche]` in `pyproject.toml`

The code reads one key. Other keys are ignored.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `flow_targets` | list of strings | none | files and directories `ava dev` and `ava operator` scan when started without `FLOW` arguments |

- The nearest `pyproject.toml`, searching from the current directory upward, is used.
- A relative path is relative to that `pyproject.toml`; `~` is expanded.
- A target is a `.py` file or a directory (scanned for workflow files). A missing target stops startup with `Workflow target does not exist: PATH`.
- A target may be written `alias=path`. The alias replaces the target's own name in webhook paths.
- A non-list value or a non-string item stops startup with `Invalid [tool.avalanche] configuration in PATH: ...`.
- Arguments on the command line replace `flow_targets` entirely; they are not merged.

```toml
[tool.avalanche]
flow_targets = ["src", "shared=../shared-flows/flows.py"]
```

A workflow is found when a scanned module defines a function decorated with `@ava.workflow` (or an `@ava.machine`). Its selector is `<file path relative to its target>::<function name>`, for example `review_order/flow.py::review_order`.

## `delta.toml`

Written by `delta init` and `delta deploy`; read by every `delta` command run in the project. See [delta-cli.md](delta-cli.md#conventions) for precedence.

```toml
org = "org_..."
project = "proj_..."

[flows]
paths = ["src"]
```

| Key | Meaning |
| --- | --- |
| `org` | organization ID |
| `project` | project ID |
| `api_url` | API URL, when not the default |
| `execution_env` | default execution environment |
| `python` | interpreter that imports the flows for a deploy; default `<project>/.venv/bin/python` |
| `[flows] paths` | project-root-relative paths searched for flows |

## Environment variables

### Read by Avalanche

| Variable | Read by | Meaning |
| --- | --- | --- |
| `TYPESAFE_API_KEY` | classifier steps and evaluations with `ava.classifiers.TypeSafe()` (the default) | provider key |
| `OPENAI_API_KEY` | classifier steps and evaluations with `ava.classifiers.OpenAIDecisions()` | provider key |
| `GEMINI_API_KEY` | classifier steps and evaluations with `ava.classifiers.GeminiDecision()` | provider key |
| `PYTHON_DOTENV_DISABLED` | evaluations | `1`, `true`, `t`, `yes` or `y` stops the `.env` lookup for a missing classifier key |
| `AVALANCHE_INTEGRATION_PROVIDER` | built-in integrations (`ava.catalog.*`) | name of the integration provider to use when several are installed |
| `AVALANCHE_LOCAL_WORKSPACE_ROOT` | `ava.Workspace`, `ava.File` | folder where the host keeps workspaces. The operator sets it to `.avalanche/operator/workspaces`; without it, workspaces live in the system temporary folder |
| `AVALANCHE_TUI_GRPC_TOKEN` | `ava tui` | bearer token for the operator, same as `--token` |
| `AVALANCHE_TUI_GRPC_TLS` | `ava tui` | `1`, `true` or `yes` turns TLS on, same as `--tls` |
| `AVALANCHE_TUI_GRPC_TLS_CA_CERT` | `ava tui` | CA certificate file, same as `--tls-ca-cert` |

A classifier key missing from the environment is looked up once in the nearest `.env` file, searching upward from the current directory; values already in the environment win. A classifier call without a key fails with `TYPESAFE_API_KEY is required to call a classifier` (or the provider's variable).

The CLI and the operator take their ports, hosts and paths from flags only; no environment variable sets them.

### Read by the agent runtimes

Avalanche does not read these; the model library a runtime uses does, in the process that runs the step (the operator's environment).

| Runtime | Variables |
| --- | --- |
| `ava.runtimes.PredictRLM` | the key of the provider named by the model prefix: `OPENAI_API_KEY` (`openai/`), `ANTHROPIC_API_KEY` (`anthropic/`), `GEMINI_API_KEY` or `GOOGLE_API_KEY` (`gemini/`), `MOONSHOT_API_KEY` (`moonshot/`), `XAI_API_KEY` (`xai/`), `GROQ_API_KEY` (`groq/`), `DEEPSEEK_API_KEY` (`deepseek/`), `OPENROUTER_API_KEY` (`openrouter/`); none for `ollama/` |
| `ava.runtimes.ClaudeAgent` | a `claude` login, or `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`), or `ANTHROPIC_API_KEY` |
| `ava.runtimes.OpenAIAgent` | `OPENAI_API_KEY`; `OPENAI_BASE_URL` when set; `OPENAI_DEFAULT_MODEL` when the runtime names no model |

Rules:

- Agent steps do not read `.env`. Export the key, or start the operator with `uv run --env-file .env ava dev`.
- A local MCP server (`ava.MCP(name, command=[...])`) is started with the operator's whole environment; that is how it gets its credentials.
- An integration's key or login is never an environment variable: connect it with `ava connect` ([integrations](../connect/integrations.md)).
- On Avalanche Cloud, set variables for runs with `delta env set` or `delta deploy --env-file` ([delta-cli.md](delta-cli.md#environment-variables-and-integrations)).

### Read by `delta`

`DELTA_TOKEN`, `DELTA_API_URL`, `DELTA_ORG`, `DELTA_PROJECT` and the others are listed in [delta-cli.md](delta-cli.md#environment-variables).

## Package extras

`avalanche-ai` without extras installs the SDK, the `ava` CLI, the local operator, the browser UI and the terminal UI. Extras add optional features:

| Extra | Installs | Needed for |
| --- | --- | --- |
| `agents` | `predict-rlm[codex-lm]` (with DSPy) | agent steps: `ava.Signature`, `ava.agent_step`, `ava.agent.step`, `ava.runtimes.PredictRLM` |
| `claude` | `avalanche-ai[agents]`, `claude-agent-sdk` (ships the Claude Code CLI) | `ava.runtimes.ClaudeAgent` |
| `openai` | `avalanche-ai[agents]`, `openai-agents`, `openai` | `ava.runtimes.OpenAIAgent`; `ava.classifiers.OpenAIDecisions` |
| `gemini` | `google-genai` | `ava.classifiers.GeminiDecision` |
| `typesafe` | `typesafe-sdk` | `ava.classifiers.TypeSafe`, the default classifier provider |
| `integrations` | `mcp`, `authlib` | `ava.MCP` servers and browser (OAuth 2) logins for integrations |
| `dataframes` | `polars`, `dataframely`, `pyarrow` | dataframe values and `ava.Json` columns |

```bash
uv add 'avalanche-ai[agents,claude,typesafe]'
```

What happens without the extra:

| Missing | Error |
| --- | --- |
| `agents` | `ImportError` on first use of `ava.Signature`, `ava.Agent` or `ava.agent_step`: `Agent features require the optional agents dependency.` followed by the `uv add 'avalanche-ai[agents]'` command |
| `claude` | the step fails with `AgentStepExecutionError`: `... runs on ava.runtimes.ClaudeAgent, which needs the Claude Agent SDK: install avalanche-ai[claude]` |
| `openai` (runtime) | `AgentStepExecutionError`: `... runs on ava.runtimes.OpenAIAgent, which needs the OpenAI Agents SDK: install avalanche-ai[openai]` |
| `openai`, `gemini` (classifier) | `ClassifierStepExecutionError`: `... needs the 'openai' extra: install avalanche-ai[openai]` |
| `dataframes` | `ImportError`: `Dataframe features require the optional dataframes dependency. ...` |

`import avalanche` itself never needs an extra: agent, classifier, runtime and integration modules load on first use. Declaring a `ClaudeAgent` or `OpenAIAgent` runtime does not need its extra either; only running the step does.

Built-in integrations (`ava.catalog.slack` and the others) need an integration provider package; `trampoline-delta` provides one. Without it a step fails with `ProviderNotInstalledError`. Built-in integrations are not available on every account yet.

## Removed

`avalanche.iceberg`, `avalanche.lance`, `ava.Stream`, `ava.ModelStream`, `ava.Cursor` and `ava.AppendResult` were removed. Any use raises `TablesAndStreamsRemovedError` (an `ImportError`). Do not use them.
