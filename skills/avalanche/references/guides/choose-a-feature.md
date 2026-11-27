# Choose a feature

From what the user wants to the feature that does it, the reason in one line, and the file to
read. Nothing is explained here: read the linked file before using a feature. The last table
settles the choices that are easy to confuse.

Default for every process: a DAG workflow (`@ava.workflow`) of typed steps. Reach for anything
else only when a row below says so.

## The user wants ...

### Structure

| The user wants ... | Use | Why | Read |
|---|---|---|---|
| A process of several steps | `@ava.workflow` + steps, `>>` and `&` | Each step is retried, saved and shown on its own. | [workflows](../building-blocks/workflows.md) |
| Deterministic work: parse, compute, call an API | `@ava.step` (`@ava.source` in, `@ava.dest` out) | Plain Python, typed, no model. | [python-steps](../building-blocks/python-steps.md) |
| Steps one after another | `a() >> b()` or `b(a())` | The output becomes the next input. | [workflows](../building-blocks/workflows.md) |
| A few different steps at the same time | `(a() & b()) >> c()` | Independent branches, joined in order. | [workflows](../building-blocks/workflows.md) |
| A step with several separate outputs | `@ava.step(num_returns=N)` | Each output can be passed on by index. | [python-steps](../building-blocks/python-steps.md) |
| The same step for every item of a list | `step.map(items, max_items=N)` | Parallel, per-item retries, up to 64 items. | [control-flow](../orchestration/control-flow.md#map) |
| An agent over many items | One agent step that takes the whole list | A mapped agent step is one agent call and one sandbox per item, started together. | [control-flow](../orchestration/control-flow.md#map) |
| To do something only in some cases | `ava.skip(reason)` in the branch's first step | A skip is an outcome, not a failure. | [control-flow](../orchestration/control-flow.md#skip) |
| Either/or branches | Two branches that each skip unless it is their case, joined by `X \| None` parameters | The graph is fixed; steps choose at run time. | [control-flow](../orchestration/control-flow.md#branching) |
| A small either/or | An `if` inside one step | Simplest; no extra nodes. | [control-flow](../orchestration/control-flow.md#branching) |
| To reuse a whole workflow as a step | `ava.call(child, input)` | Waits for the child and returns its typed output. | [control-flow](../orchestration/control-flow.md#child-workflows) |
| To hand work off without waiting | `ava.start_run(child, input)` | Starts another run and continues. | [control-flow](../orchestration/control-flow.md#child-workflows) |
| A long-lived case that reacts to events and can go back a stage | `@ava.machine` with `ava.state` | Loops and event order cannot be drawn as a DAG. | [state-machines](../building-blocks/state-machines.md) |
| A workflow that sends an event to a case | `machine.send(key, event, payload)` | A step that delivers to the instance of that key. | [state-machines](../building-blocks/state-machines.md) |

### Data

| The user wants ... | Use | Why | Read |
|---|---|---|---|
| Values supplied when the run starts | `@ava.workflow(input=Model)`, an `ava.BaseInput` subclass | Validated once; builds the start form. | [inputs-and-files](../building-blocks/inputs-and-files.md) |
| One input field as a step argument | `ava.input.<field>` | Resolved when the step starts. | [inputs-and-files](../building-blocks/inputs-and-files.md) |
| The whole input inside a step | A parameter typed with the input model | Injected by type, never by name. | [python-steps](../building-blocks/python-steps.md) |
| Caller-supplied settings that are not business input (tenant, mode) | `@ava.workflow(context=Model)`, an `ava.BaseContext` subclass | Kept apart from the input; injected by type. | [python-steps](../building-blocks/python-steps.md) |
| The run id or step id in a step | A parameter typed `ava.RunContext` | Run metadata; `step_key` identifies the step within one run. | [python-steps](../building-blocks/python-steps.md) |
| Log lines per step | `print`, `logging`, or `log=ava.Logger()` | Captured per step on the operator. | [python-steps](../building-blocks/python-steps.md) |
| Structured data between steps | Pydantic models as outputs | Saved as JSON, reusable by reruns. | [python-steps](../building-blocks/python-steps.md) |
| A file in or out (PDF, CSV, report) | `ava.File` | Passed by reference; bytes never travel in outputs. | [inputs-and-files](../building-blocks/inputs-and-files.md) |
| A folder several steps or agents work in | `ava.Workspace` | One folder per run, kept with the run. | [inputs-and-files](../building-blocks/inputs-and-files.md) |
| Rows as Arrow or Polars tables | `avalanche.model_frame` (`dataframes` extra) | Converts lists of models; not a step output type. | [inputs-and-files](../building-blocks/inputs-and-files.md) |
| A typed, validated run result | `@ava.workflow(output=Model)` | Parents calling it get that type; the returned step must never be skipped. | [workflows](../building-blocks/workflows.md) |

### Models

| The user wants ... | Use | Why | Read |
|---|---|---|---|
| Reasoning, extraction, writing, tool use | `@ava.agent_step(Signature)` | An agent loop with a typed contract and a trace. | [agent-steps](../building-blocks/agent-steps.md) |
| A label, yes/no or score with a probability | `@ava.classifier_step(questions=...)` | One cheap request, no loop; thresholds in code. | [classifier-steps](../building-blocks/classifier-steps.md) |
| To pick the model or agent engine | `runtime=ava.runtimes.PredictRLM / ClaudeAgent / OpenAIAgent` | Model and settings live on the runtime. | [runtimes](../building-blocks/runtimes.md) |
| One model for all agent steps | `@ava.workflow(agent_defaults={"runtime": ...})` | A step runtime of the same kind is merged over it. | [agent-steps](../building-blocks/agent-steps.md) |
| One classifier provider for the workflow | `@ava.workflow(classifier_defaults={...})` | Also applies to evaluations. | [classifier-steps](../building-blocks/classifier-steps.md) |
| An agent that calls your Python functions | `tools=[fn]` | Name and docstring become the tool. | [tools-and-skills](../building-blocks/tools-and-skills.md) |
| Reusable know-how loaded when needed | `skills=[ava.agent.Skill...]` (Claude, OpenAI) | Agent Skills format; not always in the prompt. | [tools-and-skills](../building-blocks/tools-and-skills.md) |
| Sandbox packages or always-on guidance for PredictRLM | `PredictRLM(skills=[...])` | PredictRLM's own skill type. | [runtimes](../building-blocks/runtimes.md) |
| An agent without a function body | `ava.agent.step(Signature, inputs=...)` | Inline node in the workflow function. | [agent-steps](../building-blocks/agent-steps.md) |
| Quality scores on every agent call | `evaluations=ava.Evaluations(...)` | Observes; never changes or fails the step. | [evaluations](../building-blocks/evaluations.md) |
| To see what an agent did and what it cost | The agent trace | Turns, tool calls, tokens, cost. | [evaluations](../building-blocks/evaluations.md) |

### Outside services

| The user wants ... | Use | Why | Read |
|---|---|---|---|
| A step or agent to call an authenticated HTTP API | An `ava.Connector` subclass in `integrations=[...]` | The account is stored by the host, not in code. | [integrations](../connect/integrations.md) |
| An agent to use an existing MCP server | `ava.MCP(name, url=... \| command=[...])` | Agent steps only. | [integrations](../connect/integrations.md) |
| Slack, Notion, GitHub and others with no connector code | `ava.catalog.<name>` | Built-in; not available on every account yet. | [integrations](../connect/integrations.md) |
| Several accounts of one built-in | `ava.catalog.slack(connection="slug")` | Named connections; not available on every account yet. | [integrations](../connect/integrations.md) |
| A public API without auth, from one step | `httpx`/`requests` in an `@ava.step` | No account to store. | [python-steps](../building-blocks/python-steps.md) |

### Time, people and events

| The user wants ... | Use | Why | Read |
|---|---|---|---|
| A pause inside a run | `ava.sleep(duration)` / `ava.sleep(until=...)` | Uses no worker while it waits. | [waits](../orchestration/waits.md) |
| To wait for another system's signal | `ava.wait_for_event(Model, key=..., timeout=...)` | Typed event sent to the run; `None` on timeout. | [waits](../orchestration/waits.md) |
| Another system to signal a waiting run by a business id | `key=` on the workflow; the sender calls `GET /runs:resolve`, then `POST /runs/{id}/events/{key}` | An event goes to a run id; the key finds it. | [waits](../orchestration/waits.md#sending-an-event-from-another-system) |
| A person to approve or choose | `ava.decision(Outcome, prompt=..., timeout=...)` | The answer is a typed value the next steps read. | [approvals](../orchestration/approvals.md) |
| A person to review many items | `ava.decision.map(Outcome, items, ...)` | One decision per item, answers in item order; one `show` over 4096 bytes is cut to a preview. | [approvals](../orchestration/approvals.md) |
| A person to settle a step whose outcome is unknown | `on_uncertain="hold"` + `ava.OutcomeUnknownError` | Parks the step for a person. | [retries](../orchestration/retries.md) |
| A timer on a stage of a long-lived case | `state.after(timedelta) >> other` | Survives restarts with the machine. | [state-machines](../building-blocks/state-machines.md) |

### Starting runs

| The user wants ... | Use | Why | Read |
|---|---|---|---|
| To start by hand | `ava run FLOW --input JSON`, or the web UI form | Needs no trigger code. | [triggers](../connect/triggers.md) |
| To start from their own service | `POST /api/v1/runs` | Same input model. | [triggers](../connect/triggers.md) |
| Another system to start it by URL | `@ava.workflow(webhook=ava.Webhook(path=...))` | The POST body is the input. | [triggers](../connect/triggers.md) |
| To start on the clock | `@ava.workflow(cron="...")` | Enable the schedule once. | [triggers](../connect/triggers.md) |
| The same start twice to mean one run | `@ava.workflow(key=("field",))` | One run per key value, forever, even a failed one. | [retries](../orchestration/retries.md#run-keys) |
| A keyed run that can be started again after it failed | An `attempt` (or date) field in `key=` | A keyed run cannot be rerun; a new key value is a new run. | [retries](../orchestration/retries.md#run-keys) |

### Failures

| The user wants ... | Use | Why | Read |
|---|---|---|---|
| Passing failures to be tried again | Nothing: `retries=7` is the default (8 attempts, no delay) | Automatic, per step. | [retries](../orchestration/retries.md) |
| A side effect never repeated automatically | `retries=0` on that step | Then a person decides. | [retries](../orchestration/retries.md) |
| A failure that no retry can fix to stop at once | `raise ava.NonRetryableError(...)` | Skips the remaining attempts. | [retries](../orchestration/retries.md) |
| An outside effect that happens once per thing | An idempotency key built from a business id (`deal-for-lead-<id>`) | Holds across retries, reruns and second runs. | [retries](../orchestration/retries.md) |
| An outside effect that happens once per run | `ctx.step_key` as the idempotency key | Same on every attempt of one run; new in every run and rerun. | [retries](../orchestration/retries.md) |
| To continue a failed run after a fix | `ava run FLOW --rerun-of RUN --from SLUG` | Earlier outputs are reused. Not for workflows with `key=`. | [retries](../orchestration/retries.md#reruns) |
| Bad items not to fail the whole map | Catch inside the step and return an error field, or `ava.skip` | A map has no partial results. | [control-flow](../orchestration/control-flow.md#map) |

### Running, testing, shipping

| The user wants ... | Use | Why | Read |
|---|---|---|---|
| To try the workflow | `ava dev`, `ava run`, `ava result --wait --json` | Real steps, retries and waits. | [local](../run/local.md) |
| To test one step | `step.fn(...)` | The bare function. | [testing](../run/testing.md) |
| To test the graph in pytest | `workflow().run(...)` or `workflow().test(stub=...)` | In process; `.test` has fake time and stubs. | [testing](../run/testing.md) |
| To test an agent step without a key | Scripted `DummyLM` (PredictRLM), or a stub | No model call. | [testing](../run/testing.md) |
| To prove a workflow with model steps on the operator without keys | A twin workflow that passes stand-in steps to the same wiring function | No scripting left in step bodies. | [testing](../run/testing.md#key-free-on-the-operator) |
| To test a machine's transitions and timers | `machine.test(input)` | Fake time, `send`, `advance_time`. | [testing](../run/testing.md) |
| A long-lived operator on their own machine | `ava operator` | One process, one SQLite file. | [self-hosting](../run/self-hosting.md) |
| To free disk or delete old runs | `ava state info / prune / delete / clear` | Operator must be stopped. | [local](../run/local.md) |
| Managed hosting, durable runs, a team console | Avalanche Cloud: `delta deploy` | Each step in its own sandbox. | [cloud](../run/cloud.md) |
| Secrets and settings for deployed steps | `[tool.delta.env]` + `delta env set` | Only declared names reach a step. | [cloud](../run/cloud.md) |
| A longer time budget for a deployed step | `[tool.delta.timeouts.<flow>]` | 60 s default, 3600 s maximum. | [cloud](../run/cloud.md) |
| To start from a shared template | Import in the console, convert, `delta clone` | Publishing is restricted. | [templates-and-sharing](../run/templates-and-sharing.md) |

## Choices that are easy to confuse

| Between | Choose | Read |
|---|---|---|
| Plain step, classifier step, agent step | Plain `@ava.step` when a rule in code decides. Classifier when the answer is one of a few options, a yes/no or a level. Agent when the model must compose text or a rich object, use tools or files, or take several turns. To only measure quality without changing the flow: `ava.Evaluations`. | [python-steps](../building-blocks/python-steps.md), [classifier-steps](../building-blocks/classifier-steps.md), [agent-steps](../building-blocks/agent-steps.md), [evaluations](../building-blocks/evaluations.md) |
| DAG workflow, state machine | The DAG, always, unless the process follows one entity over time, takes events in an order you cannot fix, and can go back a stage. One approval or one wait is still a DAG. | [workflows](../building-blocks/workflows.md), [state-machines](../building-blocks/state-machines.md) |
| `step.map`, child workflows | `map` for one step per item. `ava.call` / `ava.start_run` for a whole workflow as its own run; they have no `.map`, so give the child the whole list. | [control-flow](../orchestration/control-flow.md) |
| `ava.call`, `ava.start_run` | `call` when the parent needs the child's output (a failed child fails the parent). `start_run` to kick work off and continue. | [control-flow](../orchestration/control-flow.md#child-workflows) |
| `ava.sleep`, `cron=` | `sleep` pauses a run that already started. `cron=` starts a new run at each occurrence. | [waits](../orchestration/waits.md), [triggers](../connect/triggers.md) |
| `ava.wait_for_event`, `ava.decision`, `webhook=` | A webhook starts a new run. An event resumes a waiting run on another system's signal. A decision resumes it with a person's typed answer. Many events for one entity over time: a state machine. | [waits](../orchestration/waits.md), [approvals](../orchestration/approvals.md), [triggers](../connect/triggers.md) |
| Retry, rerun | Retries are automatic, at once, inside the run: for passing faults. A rerun is a new run you start from chosen steps with the current code: after a fix or an operator restart; not for keyed workflows or machines. | [retries](../orchestration/retries.md) |
| `ava.Connector`, `ava.MCP`, `ava.catalog.<name>` | A connector for typed calls from plain steps and agents, or an API with no MCP server. `ava.MCP` when the service ships an MCP server and only agents use it. A built-in when the account has them (not on every account yet). A local function with no account: `tools=[fn]`. | [integrations](../connect/integrations.md), [tools-and-skills](../building-blocks/tools-and-skills.md) |
| `tools=`, `skills=`, `PredictRLM(skills=)` | Tools give the agent something to call (all runtimes). Agent Skills are instructions and files loaded when the task matches (Claude, OpenAI). PredictRLM skills are always-on guidance, sandbox packages and tools (PredictRLM only). | [tools-and-skills](../building-blocks/tools-and-skills.md), [runtimes](../building-blocks/runtimes.md) |
| `PredictRLM`, `ClaudeAgent`, `OpenAIAgent` | `PredictRLM` for large inputs and computing over data, for a model that is neither Claude nor OpenAI, for a strong model plus a cheap reader, and for a scripted key-free test. `ClaudeAgent` or `OpenAIAgent` for work in a folder with file and shell tools, for Agent Skills, and for a short tool-calling task. | [runtimes](../building-blocks/runtimes.md) |
| `input=`, `context=`, `ava.RunContext` | `input=` is the caller's business data (forms, triggers, keys). `context=` is the caller's settings around the run. `ava.RunContext` is filled by the host: `run_id`, `node_id`, `step_key`, `rerun`. | [inputs-and-files](../building-blocks/inputs-and-files.md), [python-steps](../building-blocks/python-steps.md) |
| Model field, `ava.File`, `ava.Workspace` | Model fields for small structured values. `ava.File` for one document, image or export. `ava.Workspace` for many files, or files several steps edit. Never bytes, base64 or long text in an output. | [inputs-and-files](../building-blocks/inputs-and-files.md) |
| `ava dev`, `ava operator`, Avalanche Cloud | `ava dev` to write and check workflows. `ava operator` for a long-lived service you host (no access control; flow runs that are running or waiting do not survive a restart). Cloud for managed, durable runs shared with a team (private preview). | [local](../run/local.md), [self-hosting](../run/self-hosting.md), [cloud](../run/cloud.md) |

## See also

- [patterns.md](patterns.md): complete workflows for the common shapes.
- [debugging.md](debugging.md): symptoms, causes, fixes.
- [../reference/python-api.md](../reference/python-api.md): every public name.
