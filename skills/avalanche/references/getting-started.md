# Getting started

How to set Avalanche up for a user. There are two ways: create a starter project in an
empty directory, or add Avalanche to a project that already exists. Both end with the same
check: an operator that lists the workflow, and a run with a result.

## Which one

| Situation | Do |
|---|---|
| Empty directory, the user wants to start with Avalanche | [Create a starter project](#create-a-starter-project) |
| A Python project already exists (it has a `pyproject.toml`) | [Add Avalanche to a project](#add-avalanche-to-a-project) |
| `import avalanche` already works in the project | Nothing to install. Go to [workflows.md](building-blocks/workflows.md) |

Requirements for both: [uv](https://docs.astral.sh/uv/), and Python 3.13 (the package
requires `>=3.13,<3.14`; uv installs it when missing). The starter also needs `git`.

## Create a starter project

Run in an **empty** directory. Anything already in it, hidden files included, stops the
command with `Current directory is not empty`.

```bash
mkdir my-workflows && cd my-workflows
uvx avalanche-ai@latest init
```

`avalanche-ai` is the package and `ava` is the command it installs; `uvx avalanche-ai@latest`
runs that command from the latest release without installing anything first. The command
ends with `Initialized and verified <path>`.

What it creates:

| Path | What it is |
|---|---|
| `pyproject.toml` | Depends on `avalanche-ai[agents]`; `[tool.avalanche] flow_targets = ["src"]` |
| `src/start_flow/flow.py` | A starter workflow: a source, a plain step, an agent step, a destination |
| `.agent/skills/avalanche/` | This skill |
| `AGENTS.md`, `README.md` | Project conventions for coding agents; how to run and choose a model |
| `.venv/`, `uv.lock`, `.git/`, `.gitignore` | Environment, lock file, a new repository with nothing committed |

It also adds the `delta` command (Avalanche Cloud) as a development dependency.

Run the starter:

```bash
export OPENAI_API_KEY=...          # the starter's agent step uses openai/gpt-5.5
PYTHONUNBUFFERED=1 uv run ava dev  # operator and UI; leave it running
```

In another terminal:

```bash
uv run ava flow list               # start_flow/flow.py::start_flow
RUN=$(uv run ava run start_flow)
uv run ava result "$RUN" --wait --json
```

Without a model key the operator starts and lists `start_flow`, but the run fails at the
agent step. To use another provider or runtime, change `lm` and `sub_lm` in
`src/start_flow/flow.py`: see [runtimes.md](building-blocks/runtimes.md).

New workflows go beside the starter, one directory each under `src/`
(`src/review_invoices/flow.py`). `ava dev` scans all of `src/`.

## Add Avalanche to a project

```bash
uv add avalanche-ai                 # plain Python steps only
uv add 'avalanche-ai[agents]'       # plus agent steps on PredictRLM (the usual choice)
```

Extras, combinable as `'avalanche-ai[agents,typesafe]'`:

| Extra | Adds |
|---|---|
| `agents` | Agent steps on the PredictRLM runtime |
| `claude` | The ClaudeAgent runtime (includes `agents`) |
| `openai` | The OpenAIAgent runtime and the OpenAI classifier (includes `agents`) |
| `typesafe` | Classifier steps on TypeSafe, the default classifier provider |
| `gemini` | The Gemini classifier |
| `integrations` | MCP servers and OAuth sign-in for integrations |
| `dataframes` | Dataframes of typed rows between steps |

Then tell Avalanche where the workflows are. Add to `pyproject.toml`:

```toml
[tool.avalanche]
flow_targets = ["flows"]
```

`flow_targets` lists the files or directories `ava dev` scans when it is given no path.
Without it, name the path each time: `uv run ava dev flows`.

Write a first workflow, `flows/hello.py`:

```python
import avalanche as ava


@ava.source
def load_name() -> str:
    """Load the name to greet."""
    return "Ada"


@ava.dest
def write_greeting(name: str) -> str:
    """Write the greeting."""
    return f"Hello {name}"


@ava.workflow
def say_hello():
    """Greet one person."""
    return load_name() >> write_greeting()
```

Check it (no model key needed for this one):

```bash
PYTHONUNBUFFERED=1 uv run ava dev  # leave it running
```

```bash
RUN=$(uv run ava run say_hello)
uv run ava result "$RUN" --wait --json
# {"run_id":"run_...","entry":null,"node_id":null,"version":1,"value":{"kind":"scalar","value":"Hello Ada"}}
```

## Rules

- Do not run `init` in a directory that has files; use the second way there.
- Use `uv run ava ...` inside a project so the project's environment is used. `uvx
  avalanche-ai@latest ...` is for `init` only.
- Install the extra before using its feature: a classifier step needs `typesafe` (or
  `openai` / `gemini`) even though TypeSafe is the default provider, and the starter
  project installs only `agents`.
- Provider keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `TYPESAFE_API_KEY`, ...) must be set
  in the environment of the operator (`ava dev`), not of the shell that runs `ava run`.
- `ava dev` uses fixed default ports (operator 7433, webhooks 7434, UI 7435). If another
  operator is running on the machine, pass `--port`, `--web-port` and `--login-port`, and
  `--connect localhost:<port>` to the other commands.
- The operator stores runs under `.avalanche/` in the project. `init` does not add it to
  `.gitignore`; add it.
- Tell the user which of the two ways you used and what you ran.

## See also

- [project-layout.md](reference/project-layout.md): every file, setting, environment variable and extra
- [local.md](run/local.md): the operator and reading runs from the command line
- [workflows.md](building-blocks/workflows.md): writing the first real workflow
- [cloud.md](run/cloud.md): deploying to Avalanche Cloud with `delta`
