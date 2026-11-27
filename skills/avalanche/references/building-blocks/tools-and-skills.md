# Tools and skills

Three ways to give an agent step more than its instructions: **tools** (Python functions it
can call), **Agent Skills** (instructions and files it loads when a task needs them), and
**integrations** (outside services whose tools it can call).

## When to use it

| Need | Use |
|---|---|
| The agent must look something up or do something in your code | A plain function in `tools=[...]` |
| Reusable know-how (style guide, procedure, reference files) that only some calls need | `ava.agent.Skill` in `skills=[...]` (Claude, OpenAI) |
| The agent must call an outside service with a stored account | `integrations=[...]`; see [../connect/integrations.md](../connect/integrations.md) |
| Sandbox packages or always-on guidance for PredictRLM | `ava.runtimes.PredictRLM(skills=[...])`; see [runtimes.md](runtimes.md) |
| The call is always needed and needs no judgment | Do it in a plain `@ava.step` before the agent and pass the result as an input; see [python-steps.md](python-steps.md) |

## Tools

### Interface

```python
@ava.agent_step(Sig, tools=[get_order, list_open_refunds])
```

A tool is any callable. No decorator is needed.

| Aspect | Rule |
|---|---|
| Name | The function's `__name__`. |
| Description | The function's docstring. The agent decides when to call from it. |
| Parameters | Type-annotate every parameter. Defaults make a parameter optional. |
| Sync / async | Both work; an awaitable result is awaited. |
| Return value | Returned to the agent as JSON (Claude, OpenAI) or as a Python value (PredictRLM). Return JSON-like data: `dict`, `list`, `str`, numbers, Pydantic models. |
| Errors | On Claude and OpenAI an exception is reported to the model as a failed call (`ExceptionType: message`) and it can try again; the trace records it. On PredictRLM it surfaces as an error in the agent's code. |
| Where it runs | In the process that runs the step, with that process's environment. |

`@ava.tool` is **not** for these functions. It marks a method of an `ava.Connector` subclass as an
integration tool ([../connect/integrations.md](../connect/integrations.md)).

### Per runtime

| | PredictRLM | ClaudeAgent | OpenAIAgent |
|---|---|---|---|
| How the agent calls it | From its Python code: `result = await get_order("A-1")` | Tool call `mcp__avalanche__get_order` | Tool call `get_order` |
| Annotations | Recommended | Required: `AgentStepError: tool 'x' parameter 'y' needs a type annotation` | Required, and must fit OpenAI strict JSON schema |
| Reserved names | `SUBMIT`, `print` | — | `load_skill`, `read_skill_file` (with skills); `apply_patch`, `view_image`, `exec_command`, `write_stdin` (with those `builtin_tools`) |
| Built-in tools | `predict()` | `builtin_tools=["Read", "Bash", ...]` | `builtin_tools=["filesystem", "shell"]` |

## Agent Skills

`ava.agent.Skill` follows the Agent Skills format (https://agentskills.io/specification): a
folder with a `SKILL.md` file plus any files its instructions refer to. The agent sees each
skill's name and description, and loads the instructions only when the task matches.
Skills hold no tools.

### Interface

```python
ava.agent.Skill.from_dir(path)                       # load a folder
ava.agent.Skill(name=..., description=..., instructions=..., files={...})   # inline
```

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | `str` | required | Lowercase letters, digits, single hyphens; 1 to 64 characters (`^[a-z0-9]+(-[a-z0-9]+)*$`). |
| `description` | `str` | required | 1 to 1024 characters. What the skill does and when to use it; the agent reads it before deciding to load. |
| `instructions` | `str` | required | Markdown body the agent reads when it uses the skill. |
| `files` | `Mapping[str, pathlib.Path]` | `{}` | Resource files: path inside the skill folder (`"references/style.md"`) to the host file. |
| `license` | `str` | `None` | Optional frontmatter field. |
| `compatibility` | `str` | `None` | Optional, 1 to 500 characters. |
| `metadata` | `Mapping[str, str]` | `{}` | Optional. |
| `allowed_tools` | `str` | `None` | Optional (`allowed-tools` in frontmatter). Carried in `SKILL.md`; Avalanche does not enforce it. |

`skill.skill_md()` returns the generated `SKILL.md` text.

### `SKILL.md` format (for `from_dir`)

```markdown
---
name: reply-style
description: House style for customer replies. Use when drafting any message a customer will read.
---

Write at most four sentences. Start with the answer, then the next step.
Read references/phrases.md for approved wording before you write.
```

- The file starts with a `---` line; frontmatter ends at the next `---` line; the rest is the instructions.
- Allowed frontmatter keys: `name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`. Values are read as strings.
- `name` must equal the folder's name.
- Every other file in the folder (recursively) becomes a resource file.

### Per runtime

| | PredictRLM | ClaudeAgent | OpenAIAgent |
|---|---|---|---|
| `skills=[ava.agent.Skill]` | Not supported; fails when the step runs | Yes | Yes |
| How it is offered | — | A temporary Claude Code plugin; the skill is `avalanche:<name>`, loaded with the `Skill` tool | Listed in the instructions; loaded with the `load_skill(name)` tool |
| Resource files | — | Read with `Read`, enabled for the skill folder only | Read with `read_skill_file(skill, path)` |
| Running a skill's scripts | — | Needs a shell tool the step enables (`builtin_tools=["Bash"]`) | Needs `builtin_tools=["shell"]`; otherwise the agent can read scripts but not run them |

Skills are written to a folder made for the call, never to the user's Claude configuration or to a workspace the step returns.

## Integrations

Pass integration **instances** to the step. Their tools reach the agent named
`<integration>__<tool>` (a `-` in the integration name becomes `_`).

```python
@ava.agent_step(Sig, integrations=[AcmeCrm(), ava.MCP("docs", url="https://example.com/mcp")])
```

| Kind | What the agent gets |
|---|---|
| `ava.Connector` subclass instance | Each method marked `@ava.tool`, e.g. `acme_crm__find_customer`. |
| `ava.MCP(name, url=... \| command=[...], allow=[...])` | The server's tools (only those in `allow` when given). Agent steps only. |
| `ava.catalog.<name>` (built-in, e.g. `ava.catalog.slack`) | The built-in's tools. Agent steps only. Not available on every account yet. |

All three runtimes receive integration tools the same way as step tools. Declaring, connecting
accounts and named connections are covered in [../connect/integrations.md](../connect/integrations.md).

## Example

Project layout:

```text
tools_and_skills.py
skills/reply-style/SKILL.md
skills/reply-style/references/phrases.md
```

```python
from pathlib import Path

import avalanche as ava

ORDERS = {"A-1": {"status": "shipped", "total": 42.5}}


def get_order(order_id: str) -> dict[str, str | float]:
    """Return the order's status and total, or an empty object when the id is unknown."""
    return ORDERS.get(order_id, {})


async def list_open_refunds(customer_email: str, limit: int = 5) -> list[str]:
    """List the ids of the customer's refunds that are still open, newest first."""
    return []


reply_style = ava.agent.Skill.from_dir(Path(__file__).parent / "skills" / "reply-style")

escalation = ava.agent.Skill(
    name="escalation-rules",
    description="When and how to escalate an order problem. Use when the order is lost or late.",
    instructions="Escalate when the order is more than 10 days late. Say who will follow up.",
)


class DraftOrderReply(ava.Signature):
    """Draft a reply to a customer's question about one order.

    Look the order up before answering. Never promise a refund that is not open.
    """

    order_id: str = ava.InputField(desc="The order the question is about.")
    customer_email: str = ava.InputField(desc="The customer's email address.")
    question: str = ava.InputField(desc="What the customer asked.")
    reply: str = ava.OutputField(desc="The message to send, ready for review.")


class OrderQuestion(ava.BaseInput):
    order_id: str
    customer_email: str
    question: str


@ava.agent_step(
    DraftOrderReply,
    runtime=ava.runtimes.ClaudeAgent(model="claude-haiku-5-5", max_turns=8),
    tools=[get_order, list_open_refunds],
    skills=[reply_style, escalation],
)
async def draft_order_reply(payload: OrderQuestion, *, agent: ava.Agent) -> str:
    """Draft the reply with the order tools and the house style."""
    result = await agent(
        order_id=payload.order_id,
        customer_email=payload.customer_email,
        question=payload.question,
    )
    return result.outputs.reply


@ava.workflow(input=OrderQuestion)
def order_reply():
    """Draft a reply to an order question."""
    return draft_order_reply()
```

## Rules

Tools:

- Pass the function itself: `tools=[get_order]`, not `tools=[get_order()]` and not a string. Otherwise `TypeError: ava.agent_step tools must be callable` / `tools must be a sequence`.
- Write the docstring for the agent: what the tool returns, when to use it, what an empty result means.
- Give every tool a distinct `__name__`; do not pass lambdas.
- Tools are per step. `agent_defaults={"tools": [...]}` raises `TypeError: ... it accepts only 'runtime', and tools belong on each agent step`.
- A tool with a side effect (send, pay, write) can run again when the step is retried. Make it idempotent or do the side effect in a later plain step.
- A step tool must not share a name with an integration tool: `IntegrationError: ava.agent_step has tools= and integration tools with the same names [...]; rename the step's own tools`.

Skills:

- `skills=` takes `ava.agent.Skill` objects: `TypeError: ava.agent_step skills must be ava.agent.Skill objects, not str; PredictRLM's own skills go in runtime=ava.runtimes.PredictRLM(skills=[...])`.
- On PredictRLM: `AgentStepError: agent step '...' runs on PredictRLM, which does not support ava.agent.Skill yet (skills ['...'])`. It is raised when the step runs, so check the runtime when you add skills.
- Two skills with one name: `AgentStepError: ava.agent_step has several skills named ['...']`.
- `files` values must be `pathlib.Path` objects of existing files; a `str` path is a validation error. Keys are relative, use `/`, and contain no `.` or `..`.
- `SKILL.md` cannot be listed in `files`: it is generated from name, description and instructions.
- Resolve skill folders from the module, not the working directory: `Path(__file__).parent / "skills" / "name"`.

`ava.agent.Skill.from_dir` raises `ava.agent.SkillFormatError` (a `ValueError`):

| Message | Cause |
|---|---|
| `skill folder <dir> has no SKILL.md file` | Wrong path or missing file. |
| `must start with a '---' frontmatter line` | No frontmatter. |
| `frontmatter has no closing '---' line` | Unterminated frontmatter. |
| `frontmatter is not valid YAML` / `must be a YAML mapping` | Malformed frontmatter. |
| `frontmatter has fields the Agent Skills specification does not define: ['version']` | Unknown key. |
| `skill name 'x' must match its folder name 'y'` | `name` differs from the folder. |
| `folders [...] are symlinks; a skill folder may link files but not folders` | Symlinked sub-folder. |
| `resource files [...] are symlinks to files outside the skill folder` | A file symlink leaves the folder. |

Integrations:

- Pass instances: `IntegrationError: ava.agent_step integrations takes instances: write AcmeCrm(), not AcmeCrm`.
- `ava.MCP` and `ava.catalog.*` work on agent steps only; on a plain step: `... MCP tools are for agent steps only; declare it on an ava.agent_step`.
- Two integrations whose names give the same tool prefix are refused: `... give their tools the same names (acme_crm__...); rename one`.

## In the UI

- "Agent definition" lists each step tool with its name, docstring and **source code**, so keep tool functions short and readable, with no secrets in the source.
- Each skill is listed with its name, description, instructions and resource file paths.
- The step's tool list holds only `tools=`; integration tools are listed with their integration.
- In a run's trace, every tool call appears on its turn with arguments, result or error, and duration. Large values are replaced by a marker.

## See also

- [agent-steps.md](agent-steps.md): the step that takes `tools=`, `skills=`, `integrations=`.
- [runtimes.md](runtimes.md): built-in tools and PredictRLM's own skills.
- [../connect/integrations.md](../connect/integrations.md): `ava.Connector`, `ava.MCP`, built-ins, accounts.
- [evaluations.md](evaluations.md): reading tool calls in a trace.
