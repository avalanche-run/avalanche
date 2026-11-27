# Agent runtimes

A runtime is the agent engine behind an agent step: it holds the model and the agent's
settings, runs the call, and returns outputs plus a trace. There are three:
`ava.runtimes.PredictRLM`, `ava.runtimes.ClaudeAgent`, `ava.runtimes.OpenAIAgent`.

## When to use it

Pass `runtime=` to `@ava.agent_step` (or set one for the whole workflow) whenever you choose
a model or change agent settings. The signature, tools, skills and step function stay the same
across runtimes; see [agent-steps.md](agent-steps.md).

Default when no runtime is given anywhere: `ava.runtimes.PredictRLM()` with no model, which
uses DSPy's configured LM. In practice always name a runtime and a model.

```python
@ava.agent_step(Sig, runtime=ava.runtimes.ClaudeAgent(model="claude-haiku-5-5"))        # one step
@ava.workflow(agent_defaults={"runtime": ava.runtimes.PredictRLM(lm="openai/gpt-5.6-terra")})  # all steps
```

A step runtime of the same kind as the workflow default is merged over it (the step's set
fields win); a step runtime of another kind replaces the default entirely.

## Comparison

| | `PredictRLM` | `ClaudeAgent` | `OpenAIAgent` |
|---|---|---|---|
| Kind | `predict_rlm` | `claude_agent` | `openai_agent` |
| Extra | `avalanche-ai[agents]` | `avalanche-ai[claude]` | `avalanche-ai[openai]` |
| How it works | The model writes Python that runs in a sandbox, turn after turn, then submits the outputs | Claude Code CLI session through the Claude Agent SDK | OpenAI Agents SDK run on the Responses API |
| Models | Any DSPy LM: `"provider/model"` string or LM object; separate `sub_lm` | Claude models | OpenAI models |
| Model option | `lm=`, `sub_lm=` | `model=` | `model=` |
| Credentials | The provider's variable, e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`; or a `codex-lm` login | `claude` login, `CLAUDE_CODE_OAUTH_TOKEN`, or `ANTHROPIC_API_KEY` | `OPENAI_API_KEY` (and `OPENAI_BASE_URL`) |
| Also needed on the machine | Deno (default sandbox) | Claude Code CLI (ships with the SDK package) | — |
| Turn limit | `max_iterations=30` | `max_turns=None` (CLI decides) | `max_turns=None` → 10 |
| Step `tools=` | Called from the agent's Python code | Offered as tool calls | Offered as tool calls (strict JSON schema) |
| `skills=` (`ava.agent.Skill`) | No. Has its own skills: `PredictRLM(skills=[...])` | Yes | Yes |
| `integrations=` | Yes | Yes | Yes |
| Built-in tools | Python sandbox, `predict()` sub-model calls | Claude Code tools you list in `builtin_tools` (none by default) | `"filesystem"`, `"shell"` sandbox tools (none by default) |
| `ava.File` input | Yes, with a host-mount backend | Yes (path) | Yes (attached; also a path with sandbox tools) |
| `ava.File` output | Yes, with a host-mount backend | Yes | Yes (text files only without sandbox tools) |
| `ava.Workspace` | Input, read-only, with a host-mount backend | One input and/or output, edited in place | One input and/or output, needs `builtin_tools` |
| File field types | `ava.File`/`ava.Workspace` may be nested, e.g. `list[ava.File \| None]` | Exactly `ava.File`, `list[ava.File]`, `ava.Workspace` | Exactly `ava.File`, `list[ava.File]`, `ava.Workspace` |
| Cost in trace | Yes | Yes | No (tokens only) |
| Narration (`run_summary`) | — | `narrate=True` | `narrate=True` |

## How to choose

- Data-heavy work (many records, long documents, tables, computing over inputs): `PredictRLM`. Inputs are variables in a Python sandbox, not prompt text, so size is not limited by the context window.
- Work in a folder of files (code, reports) with read/edit/shell tools: `ClaudeAgent` with `builtin_tools` and an `ava.Workspace` input, or `OpenAIAgent` with `builtin_tools=["filesystem", "shell"]`.
- Agent Skills (`skills=[...]`): `ClaudeAgent` or `OpenAIAgent`.
- A plain tool-calling agent with a short prompt: `ClaudeAgent` or `OpenAIAgent`, picked by which provider the user has.
- A model that is neither Claude nor OpenAI, or two models (strong planner, cheap reader): `PredictRLM` with `lm` and `sub_lm`.
- A label, a yes/no or a score: not an agent at all. Use [classifier-steps.md](classifier-steps.md).

## PredictRLM

`ava.runtimes.PredictRLM(...)` runs the step with the `predict-rlm` package (built on DSPy).

How it works:

1. The signature's input values become variables in a Python REPL sandbox.
2. Each iteration the main model (`lm`) writes a short plan and a block of Python; the code runs and its printed output (cut to `max_output_chars`) is shown to the model on the next iteration.
3. The code can call the step's tools and `await predict("in: type -> out: type", ...)`, which asks the sub-model (`sub_lm`) to read content and return typed fields.
4. The run ends when the code calls `SUBMIT(<output fields>)`. If `max_iterations` is reached first, a last model call extracts the outputs from what was done so far.

### Options

```python
ava.runtimes.PredictRLM(lm=None, sub_lm=None, max_iterations=30, max_llm_calls=50, ...)
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `lm` | `str` or DSPy LM | `None` | Main model: writes and runs the code. `None`: DSPy's current LM (`dspy.configure` / `dspy.context`). |
| `sub_lm` | `str` or DSPy LM | `None` | Model behind `predict()` calls. `None`: DSPy's current LM. |
| `max_iterations` | `int` | `30` | Most plan-and-code iterations in one call. |
| `max_llm_calls` | `int` | `50` | Most LM calls in one execution. |
| `max_output_chars` | `int` | `50_000` | Characters of printed output shown back to the model per iteration. |
| `verbose` | `bool` | `False` | Log compact per-iteration progress. |
| `debug` | `bool` | `False` | Interpreter debug output. |
| `skills` | sequence of PredictRLM skills | `None` | PredictRLM's own skills (below). |
| `allowed_domains` | sequence of `str` | `None` | Hosts the sandbox may reach. `None`: no network. |
| `sandbox_backend` | `str` | `None` | `"jspi"` (default: Pyodide under Deno) or `"sbx"` (Docker Sandboxes). |
| `interpreter` | interpreter object | `None` | A ready backend instead of a named one, e.g. `ava.agent.DirectPythonBackend()` (host Python, not isolated). |
| `sbx_config` | `predict_rlm.SbxConfig` | `None` | Settings of the `"sbx"` backend. |
| `sbx_pool` | `predict_rlm.SbxPool` | `None` | Prewarmed `"sbx"` sandboxes; requires `sandbox_backend="sbx"`. |
| `execution` | execution backend | `None` | Alternative to `interpreter`/`sandbox_backend`/`sbx_*`; not both. |
| `model_execution_timeout` | `bool` | `False` | Let the model set a per-iteration timeout for its own code. |
| `adapter` | `dspy.Adapter` | `None` | How requests are formatted and replies parsed. |
| `adapters` | sequence | `()` | Extra input/output adapters. |
| `modules` | sequence | `()` | Runtime contributions. |
| `events` | sequence of event sinks | `()` | Extra event sinks. |
| `runtime_hooks` | sequence | `None` | Runtime hooks; need the `"sbx"` backend. |
| `on_runtime_hook_event` | callable | `None` | Called for each hook event. |
| `submit_confirmation` | callable | `None` | Called after a valid `SUBMIT`; a returned message is fed back and the run continues. |
| `output_dir` | path | `None` | Not allowed on steps with file fields (`AgentStepError: File steps own output_dir`). |
| `telemetry_context` | `TelemetryContext` | `None` | Local telemetry context. |
| `trace_export_path` | path | `None` | Write trace snapshots to a file while running. |
| `run_export_root` | path | `None` | Write `trace.json` and `evidence.json` per run under this folder. |

Any other keyword is passed unchanged to `predict_rlm.PredictRLM`, which rejects an unknown one on the first run, not at declaration.
Types are strict: `max_iterations="3"` is a validation error.

### Models

- A string is a DSPy model string, `"<provider>/<model>"`: `"openai/gpt-5.6-terra"`, `"anthropic/claude-sonnet-5-5"`. The provider's key is read from the environment (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).
- An LM object works too, e.g. `dspy_codex_lm.CodexLM(model="gpt-5.6-terra")` after `codex-lm auth login NAME` (installed by the `agents` extra).
- Set both `lm` and `sub_lm`. The main model needs to be good at writing code; the sub-model reads content and can be cheaper.

### Files

`ava.File` and `ava.Workspace` fields need a backend that can mount host folders. The default
`"jspi"` sandbox cannot: the step fails with `The JSPI backend does not support host directory mounts`.
Use `interpreter=ava.agent.DirectPythonBackend()` (runs the code as host Python, no isolation) or `sandbox_backend="sbx"`.
With `DirectPythonBackend`, do not type a signature input field with a Pydantic model: every turn then
fails inside the agent's code with `NameError: name '<Model>' is not defined`. Type the field `dict`
(or `list[dict]`) and pass `model.model_dump(mode="json")`. The default sandbox takes model fields.
Workspace inputs are mounted read-only; file outputs are written to a folder the agent is given.

### PredictRLM's own skills

Not the Agent Skills format. A PredictRLM skill is always in the prompt and can add sandbox packages and tools.

```python
from avalanche.runtimes.predict_rlm import skills

ava.runtimes.PredictRLM(lm="openai/gpt-5.6-terra", skills=[skills.pdf, skills.spreadsheet, skills.docx])

skills.Skill(name="pdf-extraction", instructions="Use pdfplumber for tables.",
             packages=["pdfplumber"], modules={}, tools={})
```

`pdf`, `spreadsheet`, `docx` are built in. `Skill` fields: `name`, `instructions` (default `""`),
`packages` (PyPI names installed in the sandbox), `modules` (`{import_name: host .py path}`), `tools` (`{name: callable}`).

### Writing signatures and instructions for PredictRLM

- Put data in input fields, not in the instructions. The model inspects inputs with code, so large inputs are fine.
- Say what to compute and what to check before submitting ("verify every cited id exists in the input"). The model can test its answer in code.
- Type outputs precisely (Pydantic models, `Literal`, `list[Model]`). `SUBMIT` uses the output field names verbatim, so name them clearly.
- State limits and non-goals ("use only the packet", "do not invent counts").
- Give tools clear docstrings and typed parameters: the model reads them to decide how to call.
- Budget with `max_iterations`: set it to what the task needs (for example 10) instead of leaving the default 30. Each iteration is a model call.
- Do not ask for chat-style behaviour (asking the user questions, streaming text). One call, one submit.

## ClaudeAgent

`ava.runtimes.ClaudeAgent(...)` runs the step as a Claude Code session through the Claude Agent SDK.

How it works: the signature's instructions become the system prompt; the inputs go in the
first message as JSON; the output fields are requested as structured output. The session
starts with no tools, no user or project Claude settings, and no MCP servers of the user's account.

### Options

```python
ava.runtimes.ClaudeAgent(model=None, max_turns=None, builtin_tools=(), permission_mode="default",
                         system_prompt_append=None, system_prompt_preset=None,
                         max_budget_usd=None, narrate=True, **claude_agent_options)
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `model` | `str` | `None` | Claude model id, e.g. `"claude-haiku-5-5"`. `None`: the Claude CLI chooses. |
| `max_turns` | `int > 0` | `None` | Most turns in the session. |
| `builtin_tools` | sequence of `str` | `()` | Claude Code tools the agent may use without asking, e.g. `"Read"`, `"Bash"`. |
| `permission_mode` | `"default"`, `"acceptEdits"`, `"plan"`, `"bypassPermissions"`, `"dontAsk"` | `"default"` | Claude Code permission mode. |
| `system_prompt_append` | `str` | `None` | Text added after the instructions in the system prompt. |
| `system_prompt_preset` | `"claude_code"` | `None` | Put Claude Code's own system prompt first, the step's after it. |
| `max_budget_usd` | `float > 0` | `None` | Spending cap for the session. |
| `narrate` | `bool` | `True` | Ask Claude to note what it does before each tool call and to return a `run_summary`, shown in the trace. |
| other keywords | — | — | Fields of `claude_agent_sdk.ClaudeAgentOptions`, e.g. `effort="low"`, `mcp_servers={...}`. |

Extra SDK options: `mcp_servers`, `hooks`, `plugins`, `allowed_tools`, `add_dirs`, `tools`, `skills`
are combined with what Avalanche sets; any other replaces Avalanche's value.
`system_prompt`, `output_format` and `cwd` are rejected. These options are never published or recorded.

### Specifics

- Authentication is the CLI's: a `claude` login, `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`), or `ANTHROPIC_API_KEY`.
- The Claude Code CLI bundled with the SDK package is used; it must be installed wherever the step runs.
- Step tools are served as an in-process MCP server named `avalanche` (`mcp__avalanche__<tool>`); they are always allowed.
- Working folder: the step's `ava.Workspace` input, edited in place; otherwise a temporary folder. At most one workspace input.
- File inputs are given as paths and read in place; file outputs are written to a fresh output folder.
- Building a `ClaudeAgent` needs neither the SDK nor the CLI; a workflow that declares one imports and publishes without the extra.

## OpenAIAgent

`ava.runtimes.OpenAIAgent(...)` runs the step with the OpenAI Agents SDK.

How it works: the instructions become the agent's instructions; the inputs go in one user
message as JSON, with input files attached; the outputs are requested as a strict JSON schema.

### Options

```python
ava.runtimes.OpenAIAgent(model=None, max_turns=None, builtin_tools=(), system_prompt_append=None,
                         narrate=True, tracing=False, **model_settings)
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `model` | `str` | `None` | OpenAI model id, e.g. `"gpt-5.4-mini"`. `None`: the SDK chooses (`OPENAI_DEFAULT_MODEL`, else its default). |
| `max_turns` | `int > 0` | `None` | Most model responses in one call; 10 when unset. |
| `builtin_tools` | sequence of `"filesystem"`, `"shell"` | `()` | Sandbox tools: `"filesystem"` edits files with `apply_patch` and views images; `"shell"` runs commands. |
| `system_prompt_append` | `str` | `None` | Text added after the instructions. |
| `narrate` | `bool` | `True` | Ask the model to note what it does before each tool call and to return a `run_summary`, shown in the trace. |
| `tracing` | `bool` | `False` | Also send the SDK's own trace of the call to OpenAI. |
| other keywords | — | — | Fields of `agents.ModelSettings`, e.g. `temperature=0.2`, `reasoning=...`, `max_tokens=...`. Never published or recorded. |

### Specifics

- Reads `OPENAI_API_KEY` (and `OPENAI_BASE_URL`) from the environment of the process that runs the step.
- With `builtin_tools`, the agent works in a local sandbox rooted at the step's `ava.Workspace` input (edited in place), else a temporary folder. On Linux its commands are not confined.
- An `ava.Workspace` input or output requires `builtin_tools`: `AgentStepError: OpenAIAgent works in an ava.Workspace only through its sandbox tools; set builtin_tools to ['filesystem'], ['shell'] or both`.
- File inputs are attached to the message (images as images, others as files). File outputs: with sandbox tools, files the model wrote; without, text files returned as name plus content.
- Tool parameters and outputs must fit OpenAI strict JSON schemas, else `AgentStepError: OpenAIAgent sends tool arguments and answers as OpenAI strict JSON schemas, which cannot express this step's types`.
- Reserved tool names: `apply_patch`, `view_image` (filesystem), `exec_command`, `write_stdin` (shell), `load_skill`, `read_skill_file` (when the step has skills).

## Example

One signature and step body, three runtimes:

```python
import avalanche as ava


class SummarizeText(ava.Signature):
    """Summarize the text in at most two sentences, using only what it says."""

    text: str = ava.InputField(desc="The text to summarize.")
    summary: str = ava.OutputField(desc="At most two sentences.")


@ava.agent_step(
    SummarizeText,
    runtime=ava.runtimes.PredictRLM(
        lm="openai/gpt-5.6-terra", sub_lm="openai/gpt-5.6-terra", max_iterations=8
    ),
)
async def summarize_with_predict_rlm(text: str, *, agent: ava.Agent) -> str:
    """Summarize with PredictRLM."""
    return (await agent(text=text)).outputs.summary


@ava.agent_step(
    SummarizeText,
    runtime=ava.runtimes.ClaudeAgent(model="claude-haiku-5-5", max_turns=4, effort="low"),
)
async def summarize_with_claude(text: str, *, agent: ava.Agent) -> str:
    """Summarize with Claude."""
    return (await agent(text=text)).outputs.summary


@ava.agent_step(
    SummarizeText,
    runtime=ava.runtimes.OpenAIAgent(model="gpt-5.4-mini", max_turns=4, temperature=0.2),
)
async def summarize_with_openai(text: str, *, agent: ava.Agent) -> str:
    """Summarize with an OpenAI model."""
    return (await agent(text=text)).outputs.summary


@ava.source
def load_text() -> str:
    """Load a sample text."""
    return "Avalanche runs workflows made of steps. Some steps call agents."


@ava.workflow
def summarize_three_ways():
    """Summarize one text with each runtime in parallel."""
    text = load_text()
    return text >> (summarize_with_predict_rlm() & summarize_with_claude() & summarize_with_openai())
```

## Rules

- Settings go on the runtime, never on the step: `ava.agent_step(Sig, max_iterations=5)` raises `TypeError: ava.agent_step no longer accepts max_iterations=; pass them to the runtime: runtime=ava.runtimes.PredictRLM(max_iterations=...)`.
- `runtime=` takes an instance, not a name: `TypeError: ava.agent_step runtime must be an agent runtime such as ava.runtimes.PredictRLM(...), not str`.
- Use only model ids the user gave you or that their provider documents. Model strings differ: PredictRLM takes `"provider/model"`, Claude and OpenAI take the bare id.
- A misspelt `ClaudeAgent`/`OpenAIAgent` option fails at declaration when the SDK is installed (`ClaudeAgent got unknown options ['effortt']`), and only when the step runs otherwise. A misspelt `PredictRLM` option always fails only at the first run.
- `ClaudeAgent(system_prompt=...)` is rejected: `ClaudeAgent does not take 'system_prompt': the step's instructions are the system prompt; add to it with system_prompt_append, ...`. Same for `output_format` and `cwd` (use an `ava.Workspace` input).
- `ava.agent.Skill` on PredictRLM fails when the step runs: `agent step '...' runs on PredictRLM, which does not support ava.agent.Skill yet`.
- Missing extra fails when the step runs: `needs the Claude Agent SDK: install avalanche-ai[claude]` / `needs the OpenAI Agents SDK: install avalanche-ai[openai]`.
- Keys must be in the environment of the process that runs the step (the operator or worker), not only in the shell that submits the run.
- Runtimes are frozen descriptors: building one starts nothing, and one instance can be shared by several steps.
- Every `PredictRLM` agent call starts its own sandbox (by default a Deno process running Python), and a mapped agent step makes one call per item, all started together. Many items on a busy machine can make sandboxes miss their start check (about 30 seconds) and fail. For more than a handful of items, give one agent call the whole list instead of mapping; there is no concurrency option. See [../orchestration/control-flow.md](../orchestration/control-flow.md#map).
- Enable built-in tools deliberately. `"Bash"` on Claude and `"shell"` on OpenAI run commands on the host that runs the step.

## In the UI

- The agent's "Agent definition" tab shows the runtime kind, the model and the settings above. For PredictRLM it also shows where each model came from: `step override`, `workflow default` or `PredictRLM default`.
- Extra SDK options (`ClaudeAgentOptions`, `ModelSettings` fields) and anything that looks like a credential are not shown.
- The trace view is specific to the runtime: PredictRLM shows the generated Python and its output per turn; Claude and OpenAI show messages, reasoning and tool calls.

## See also

- [agent-steps.md](agent-steps.md): the step, the signature, retries.
- [tools-and-skills.md](tools-and-skills.md): what each runtime does with tools and skills.
- [evaluations.md](evaluations.md): traces, tokens and cost.
- [inputs-and-files.md](inputs-and-files.md): `ava.File`, `ava.Workspace`.
- [../run/local.md](../run/local.md): where steps run and which environment they see.
- [../guides/choose-a-feature.md](../guides/choose-a-feature.md)
