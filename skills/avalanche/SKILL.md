---
name: avalanche
description: >-
  Write, run and debug Avalanche workflows: Python workflows whose steps are
  plain functions, AI agent steps and classifier steps, composed into a DAG
  with >> and &. Use when creating or changing an Avalanche workflow, when a
  user describes a process to automate with agents, or when looking up any
  Avalanche feature (map, retries, reruns, waits, approvals, webhooks,
  schedules, state machines, integrations, evaluations), the ava and delta
  CLIs, the Python API, the local operator or Avalanche Cloud.
compatibility: >-
  Requires Python 3.13 and the avalanche-ai package. Agent and classifier
  steps need package extras and model provider keys; see
  references/reference/project-layout.md.
---

# Avalanche

Avalanche runs workflows written in Python. A workflow is a graph of steps; a step is a
function. Some steps are plain Python, some call an AI agent, some ask a classifier a closed
question. An operator runs the workflow, records every run (inputs, outputs, errors, agent
traces) and can rerun it from any step (except workflows that declare a run key). The same code runs on the local operator and on
Avalanche Cloud.

This file holds the rules that always apply and the index of the reference files. Read the
file for a feature before you use it: each one has the full interface, a proven example and
the mistakes to avoid.

## The shape of a workflow

```python
from pydantic import BaseModel

import avalanche as ava


class Ticket(BaseModel):
    subject: str
    body: str


class Triage(BaseModel):
    category: str
    summary: str


class TriageTicket(ava.Signature):
    """Triage one support ticket: pick the best category and write a one-sentence summary."""

    ticket: Ticket = ava.InputField(desc="The ticket as the customer wrote it.")
    triage: Triage = ava.OutputField(desc="Category and one-sentence summary.")


@ava.source
def load_ticket() -> Ticket:
    """Load the ticket to triage."""
    return Ticket(subject="Charged twice", body="My card shows two charges for May.")


@ava.agent_step(TriageTicket, runtime=ava.runtimes.PredictRLM(lm="openai/gpt-5.5"))
async def triage_ticket(ticket: Ticket, *, agent: ava.Agent) -> Triage:
    """Ask the agent for the ticket's category and summary."""
    return (await agent(ticket=ticket)).outputs.triage


@ava.dest
def file_ticket(triage: Triage) -> str:
    """File the ticket in the queue of its category."""
    return f"{triage.category}: {triage.summary}"


@ava.workflow
def support_triage():
    """Triage a support ticket and file it."""
    return load_ticket() >> triage_ticket() >> file_ticket()
```

## Rules that always apply

Design:

1. **Build every workflow with the DAG API**: `@ava.workflow` returning steps composed with
   `>>` (then) and `&` (in parallel). Use a state machine only for a long-lived entity that
   reacts to outside events in an order you cannot know in advance.
2. **One job per step.** Plain Python (`@ava.step`) for anything a rule can decide, a
   classifier step when the answer is one of a known set, an agent step only where judgment
   or reading is needed. Never call a model outside an agent or classifier step.
3. **Type every input and output.** Pass Pydantic models between steps, not free text or
   bare dicts. Parameters are validated; return annotations are not, so return what you
   annotate.
4. **Every function has a one-line docstring.** A step's docstring is the description the
   user reads under the step in the UI. For an agent step the UI shows the first line of the
   signature's instructions, so write that line for the user too.
5. **Name steps verb_object** (`load_orders`, `score_lead`). The names are the graph.
6. **Side effects go in their own step, last, and must be safe to repeat.** A failed step is
   run again: up to 8 attempts by default, at once, for any exception except
   `ava.NonRetryableError`. Raise `ava.NonRetryableError` for errors that will not go away.
   Make outside calls idempotent with a key built from a business id (`deal-for-lead-<id>`)
   when the effect must happen once per thing; the run context's step key is the same across
   retries of one run only, and changes with every new run or rerun. Set `retries=0` on a
   step that must not repeat. A workflow with `key=` cannot be rerun, and a failed run keeps
   its key for good: add an `attempt` or date field to the key if it must be startable again.
7. **Secrets come from the environment or from integrations**, never from code or inputs.

Composition (the mistakes that cost the most time):

8. **The workflow function runs once, to build the graph.** A step call returns a
   placeholder, not a value: no `if`, `for` or arithmetic on it. Branch with `ava.skip`,
   loop with `.map`. Waits, decisions and child runs (`ava.sleep`, `ava.wait_for_event`,
   `ava.decision`, `ava.call`, `ava.start_run`) are called in the workflow function, never
   inside a step.
9. **Parenthesise every `&` group.** `>>` binds tighter than `&`: write `(a() & b()) >> c()`.
   `a() & b() >> c()` fails at run time, and so does returning a bare `a() & b()`.
10. **Never pass a `>>` chain as an argument.** `x = a() >> b(); c(x)` gives `c` the output
    of `a`, not `b`. Only returning a chain gives its last step's output. To reuse a step's
    output, bind that step's own call: `x = b(a()); c(x)`. Continuing a chain with `>>` is fine.
11. **`>>` spreads a returned list or tuple** across the next step's parameters. To pass a
   list as one value, pass it explicitly: `x = load_items(); count_items(x)`.
12. **Run input does not reach steps by parameter name.** Declare `@ava.workflow(input=Model)`
    and give the step a parameter typed with that model, or pass `ava.input.<field>`.
13. **To loop over items use `.map`**, not a Python loop. A map has a limit of 8 items by
    default and 64 at most, and going over it fails the run. A mapped agent step makes one
    agent call per item, all at once (on PredictRLM, one sandbox each): for more than a
    handful of items, give one agent call the whole list.
14. **A step output must be JSON-like, a Pydantic model, a dataclass, an `ava.File` or an
    `ava.Workspace`.** Put a `datetime` or a custom class inside a model. Only the operator
    checks this; an in-process run does not.
15. **With `output=Model` on the workflow, the returned step must never be skipped.** A
    skipped result is `None` and fails the run.

Working:

16. **Prove it before you say it works.** Start the operator, run the workflow, read the
    result, and on failure read the failed step's error. The loop is below.
17. **Do not invent API.** If a name, option or flag is not in these files, it does not
    exist. Check [python-api.md](references/reference/python-api.md) and
    [ava-cli.md](references/reference/ava-cli.md).
18. **Tell the user what you could not run.** Agent and classifier steps need provider keys;
    if none is set, say so, and run the graph on the operator with stand-in steps: a wiring
    function plus a key-free twin workflow, as in
    [testing.md](references/run/testing.md#key-free-on-the-operator). Do not leave scripting
    in step bodies.

## The working loop

No project yet? Either `uvx avalanche-ai@latest init` in an empty directory (a starter
project), or `uv add 'avalanche-ai[agents]'` in an existing one:
[getting-started.md](references/getting-started.md).

```bash
PYTHONUNBUFFERED=1 uv run ava dev                 # start the operator; keep it running
uv run ava flow list                              # what was discovered
RUN=$(uv run ava run support_triage)              # start a run; add --input '{"field": ...}'
uv run ava result "$RUN" --wait --json            # wait and read the result
uv run ava run show "$RUN" --json                 # on failure: status, failed step, error
```

`ava result --json` prints a Python traceback when the run failed; that is expected, read the
failure with `ava run show`. `ava run` prints a run id and exits 0 even when the input has a
wrong type or breaks a constraint, so always read the run. A failed run with no steps and
`error_code: unknown` failed while loading the file: read its log
([debugging.md](references/guides/debugging.md)). The operator reloads changed files, so edit and run again. Provider
keys must be in the environment of the operator, not of the shell that runs `ava run`. Full
detail: [local.md](references/run/local.md). Failures:
[debugging.md](references/guides/debugging.md).

## Where to read

Start here when the user describes a need, not a feature:

| The user | Read |
|---|---|
| has no Avalanche project yet, or wants it added to an existing project | [getting-started.md](references/getting-started.md) |
| describes a process to automate | [process-to-workflow.md](references/guides/process-to-workflow.md) |
| asks "can Avalanche do X" or you must pick between features | [choose-a-feature.md](references/guides/choose-a-feature.md) |
| wants a common shape quickly | [patterns.md](references/guides/patterns.md) |
| has a failing run or an error message | [debugging.md](references/guides/debugging.md) |

Building blocks:

| Topic | File |
|---|---|
| `@ava.workflow`, `>>` and `&`, how outputs reach the next step | [workflows.md](references/building-blocks/workflows.md) |
| `@ava.step`, `@ava.source`, `@ava.dest`, run context, logging | [python-steps.md](references/building-blocks/python-steps.md) |
| `@ava.agent_step`, signatures, outputs, uncertainty | [agent-steps.md](references/building-blocks/agent-steps.md) |
| PredictRLM, ClaudeAgent, OpenAIAgent: options, models, how to choose | [runtimes.md](references/building-blocks/runtimes.md) |
| Tools, skills and integrations for an agent | [tools-and-skills.md](references/building-blocks/tools-and-skills.md) |
| Evaluations and agent traces | [evaluations.md](references/building-blocks/evaluations.md) |
| `@ava.classifier_step`, question types, providers | [classifier-steps.md](references/building-blocks/classifier-steps.md) |
| `ava.machine`, states, events | [state-machines.md](references/building-blocks/state-machines.md) |
| Run input, `ava.File`, `ava.Workspace`, dataframes | [inputs-and-files.md](references/building-blocks/inputs-and-files.md) |

Orchestration:

| Topic | File |
|---|---|
| `.map`, `ava.skip`, branching, child workflows (`ava.call`, `ava.start_run`) | [control-flow.md](references/orchestration/control-flow.md) |
| Retries, non-retryable and unknown outcomes, reruns, run keys | [retries.md](references/orchestration/retries.md) |
| `ava.sleep`, `ava.wait_for_event` | [waits.md](references/orchestration/waits.md) |
| `ava.decision`: a person approves or chooses | [approvals.md](references/orchestration/approvals.md) |

Connect:

| Topic | File |
|---|---|
| What starts a run: CLI, UI, REST, webhooks, cron schedules, events | [triggers.md](references/connect/triggers.md) |
| Connectors, MCP servers, built-in integrations, connections | [integrations.md](references/connect/integrations.md) |

Run:

| Topic | File |
|---|---|
| The local operator and reading runs from the command line | [local.md](references/run/local.md) |
| Tests in pytest; key-free runs on the operator | [testing.md](references/run/testing.md) |
| Running the operator as a service | [self-hosting.md](references/run/self-hosting.md) |
| Avalanche Cloud: deploy, runs, secrets, limits | [cloud.md](references/run/cloud.md) |
| Templates and share pages | [templates-and-sharing.md](references/run/templates-and-sharing.md) |

Reference (complete lookup tables):

| Topic | File |
|---|---|
| Every public Python name with its signature | [python-api.md](references/reference/python-api.md) |
| Every error type, when it is raised, what to do | [errors.md](references/reference/errors.md) |
| Every `ava` command and flag | [ava-cli.md](references/reference/ava-cli.md) |
| Every `delta` command and flag (Avalanche Cloud) | [delta-cli.md](references/reference/delta-cli.md) |
| The local operator's REST API | [operator-api.md](references/reference/operator-api.md) |
| Project files, settings, environment variables, extras | [project-layout.md](references/reference/project-layout.md) |
| Terms | [glossary.md](references/reference/glossary.md) |

## What the user can and cannot expect

Say these plainly when they matter to the request:

- On the local operator, a workflow run that is waiting (sleep, event, decision) does not
  survive an operator restart; state machine instances do. If that run had a key, the key
  stays taken by the failed run.
- An operator cannot fast-forward a workflow's sleeps and timeouts. Make long timeouts
  configurable to check them locally.
- The local operator has no authentication; its UI, REST API and webhooks listen on
  127.0.0.1 only.
- Built-in integrations and named connections are not available on every account yet.
  Custom connectors and MCP servers work everywhere.
- Avalanche Cloud is in private preview.
- Tables and streams (`avalanche.iceberg`, `avalanche.lance`, `ava.Stream`) were removed and
  raise an error. Do not use them, and tell a user who asks.
