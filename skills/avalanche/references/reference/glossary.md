# Glossary

One line per term, as the code uses it. Where two words name one thing, the first table says which to write.

## Which word to use

| Write | Also seen in code and tools | Rule |
| --- | --- | --- |
| **workflow** | flow | Same thing. Python says workflow (`@ava.workflow`, `ava.Workflow`). The CLIs, the REST API and the UI often say flow (`ava flow list`, `FLOW`, `/flows`, `delta flows`). Write "workflow" in code and prose; type "flow" where a command or route is spelled that way. |
| workflow | pipeline | `ava.pipeline` and `ava.Pipeline` are legacy aliases of `ava.workflow` and `ava.Workflow`. Do not write them. |
| **step** | node | A step is what the author declares. Node is the graph's word for one invocation of a step: node ID (`load_order_1`), node type, `--node`, `node_id`. Write "step"; use "node ID" only for that identifier. |
| step | transform | `ava.transform` is a legacy alias of `ava.step`. Do not write it. |
| **state machine** | machine | Same thing; `@ava.machine`, `ava machine send`. |
| **decision** | approval, human review | The code's word is decision (`ava.decision`, `ava run decide`). An approval is a decision whose outcome has an approve/reject field. |
| **integration** | connector, MCP server, built-in, catalog | Integration is the general word for an outside service a step declares. Connector, MCP server and built-in (catalog) integration are its three kinds. |
| **selector** | workflow ID, flow name | The full name of a discovered workflow: `file.py::name`. A bare name works where it is unambiguous. |
| **slug** | step name | The stable name of a step in a workflow (`load_order`); on Avalanche Cloud also the name of a deployed flow (flow slug). |
| **operator** | local operator, server | The local process that runs workflows. |
| **Avalanche Cloud** | Delta, `delta` | The hosted service; its CLI is `delta` and its package `trampoline-delta`. Write "Avalanche Cloud" in prose and `delta` for the command. |
| **run** | execution | One execution of a workflow or machine. |

## Terms

| Term | Meaning | More |
| --- | --- | --- |
| Agent | The object injected into an agent step (`agent: ava.Agent`); calling it once runs the model. | [agent-steps.md](../building-blocks/agent-steps.md) |
| Agent step | A step whose work is done by a model following a signature, on a runtime, optionally with tools, skills and integrations. Declared with `@ava.agent_step` or `ava.agent.step`. | [agent-steps.md](../building-blocks/agent-steps.md) |
| Answer | A classifier's reply to one question: a choice, a noul or a score, with probabilities. | [classifier-steps.md](../building-blocks/classifier-steps.md) |
| Artifact | On Avalanche Cloud, a file a run's step produced, listed and downloaded with `delta runs artifacts` and `delta runs download`. | [cloud.md](../run/cloud.md) |
| Attempt | One execution of a step. A step has at most 8: the first and up to 7 retries. | [retries.md](../orchestration/retries.md) |
| Built-in integration | A provider-hosted integration picked by name from `ava.catalog` (`slack`, `notion`, `github`, ...), for agent steps. Not available on every account yet. | [integrations.md](../connect/integrations.md) |
| Call | `ava.call(target, input)`: start another run and wait for its output. | [control-flow.md](../orchestration/control-flow.md) |
| Catalog | The set of built-in integrations (`ava.catalog`). Also, in the operator, the set of discovered workflows. | [integrations.md](../connect/integrations.md) |
| Choice | A classifier question type: pick one of several named options. | [classifier-steps.md](../building-blocks/classifier-steps.md) |
| Classifier | The object injected into a classifier step (`classifier: ava.Classifier`); a decision model that answers fixed questions with probabilities. | [classifier-steps.md](../building-blocks/classifier-steps.md) |
| Classifier provider | The service that answers classifier questions: `ava.classifiers.TypeSafe()` (default), `OpenAIDecisions()`, `GeminiDecision()`. | [classifier-steps.md](../building-blocks/classifier-steps.md) |
| Classifier step | A step declared with `@ava.classifier_step` that asks a classifier choice, noul or score questions about some state. | [classifier-steps.md](../building-blocks/classifier-steps.md) |
| Composite | In an evaluation, a number in `[0, 1]` computed by your function from the metrics' answers. | [evaluations.md](../building-blocks/evaluations.md) |
| Connection | A connected account of an integration. On Avalanche Cloud a named connection has a slug, is shared or per user, and is assigned to an organization, project or flow. Not available on every account yet. | [integrations.md](../connect/integrations.md) |
| Connector | An outside HTTP service described in code by subclassing `ava.Connector`, with `@ava.tool` methods and an optional login (`ava.ApiKey`, `ava.OAuth2`). | [integrations.md](../connect/integrations.md) |
| Context | Optional run-level data, validated against the workflow's `context=` model and injected into steps that annotate a parameter with it. | [inputs-and-files.md](../building-blocks/inputs-and-files.md) |
| Cron | The schedule expression of `@ava.workflow(cron=...)`. | [triggers.md](../connect/triggers.md) |
| Decision | A wait for a person's typed answer, declared with `ava.decision`. The answer is an instance of an `ava.Event` subclass. | [approvals.md](../orchestration/approvals.md) |
| Deployment | On Avalanche Cloud, one uploaded build of a project (`delta deploy`). Each flow has a current deployment; `promote` and `rollback` change it. | [cloud.md](../run/cloud.md) |
| Destination (dest) | A step declared with `@ava.dest`: sends data out. Shown as the end of a flow. | [python-steps.md](../building-blocks/python-steps.md) |
| Discovery | How the operator finds workflows: it imports the scanned files and collects every `@ava.workflow` and `@ava.machine`. | [local.md](../run/local.md) |
| Emits | The event names a step may return, declared with `emits=`. | [state-machines.md](../building-blocks/state-machines.md) |
| Entry (state entry) | One stay of a machine run in a state, numbered from 1; the state's work runs once per entry. | [state-machines.md](../building-blocks/state-machines.md) |
| Evaluation | Automatic grading of an agent step's calls with classifier questions (`ava.Evaluations`). | [evaluations.md](../building-blocks/evaluations.md) |
| Event | Three related uses of `ava.Event`: data delivered to a waiting run (`ava.wait_for_event`); a named event that moves a state machine (`ava.event`, `state.on`); the base class of a decision's outcome. | [waits.md](../orchestration/waits.md) |
| Extra | An optional dependency group of `avalanche-ai`: `agents`, `claude`, `openai`, `gemini`, `typesafe`, `integrations`, `dataframes`. | [project-layout.md](project-layout.md#package-extras) |
| File | `ava.File`: a reference to one file, passed between steps and into runs without its bytes. | [inputs-and-files.md](../building-blocks/inputs-and-files.md) |
| Flow | See workflow. | |
| Flow target | A file or directory the operator scans for workflows; set by `FLOW` arguments or `[tool.avalanche].flow_targets`. | [project-layout.md](project-layout.md) |
| Hold | What happens to a step with `on_uncertain="hold"` that raises `OutcomeUnknownError`: it is parked until a person reconciles it. | [retries.md](../orchestration/retries.md) |
| Idempotency key | A caller-chosen string sent with an event, decision or machine event so a repeated request is applied once. | [operator-api.md](operator-api.md) |
| Input | The data a run starts with, validated against the workflow's `input=` model (an `ava.BaseInput` subclass). | [inputs-and-files.md](../building-blocks/inputs-and-files.md) |
| Integration | An outside service a step declares in `integrations=`: a Connector, an MCP server or a built-in. Its account is connected with `ava connect`, not in code. | [integrations.md](../connect/integrations.md) |
| Key (run key) | Input fields named in `key=`. One run exists per distinct key value; a second start with the same key returns the first run. Required for machines. | [workflows.md](../building-blocks/workflows.md) |
| Map | Running a step once per item of a list, in parallel, and gathering the outputs: `step.map(items)`. Default limit 8 items, at most 64. | [control-flow.md](../orchestration/control-flow.md) |
| MCP server | A Model Context Protocol server whose tools an agent step may call, declared with `ava.MCP` (remote `url=` or local `command=`). | [integrations.md](../connect/integrations.md) |
| Metric | In an evaluation, one classifier question asked about one part of an agent call (`ava.Metric`). | [evaluations.md](../building-blocks/evaluations.md) |
| Node | One invocation of a step in a workflow's graph. See step. | |
| Node ID | The identifier of a node: function name plus a counter (`load_order_1`); inside a machine entry, prefixed with the entry (`e1.open_ticket_1`). | [operator-api.md](operator-api.md) |
| Noul | A classifier question type whose answer is a number from 0 to 1: how likely the statement holds. | [classifier-steps.md](../building-blocks/classifier-steps.md) |
| Operator | The local process started by `ava dev` or `ava operator`: discovers workflows, runs them, stores state, serves gRPC, the browser UI, the REST API and webhooks. | [local.md](../run/local.md) |
| Organization | On Avalanche Cloud, the account that owns projects. | [cloud.md](../run/cloud.md) |
| Outcome | The typed answer of a decision. Also, an "unknown outcome": an outside call that may or may not have happened (`ava.OutcomeUnknownError`). | [approvals.md](../orchestration/approvals.md) |
| Project | Locally, the folder with `pyproject.toml` and the workflows. On Avalanche Cloud, the unit a folder deploys to (`delta.toml` binds the two). | [project-layout.md](project-layout.md) |
| Receipt | The record returned when an event, decision or machine event is accepted. | [operator-api.md](operator-api.md) |
| Rerun | A new run that reuses the saved outputs of some steps of an earlier run and executes the rest again (`ava run FLOW --rerun-of RUN_ID --from SLUG`). | [debugging.md](../guides/debugging.md) |
| Retry | A further attempt of a failed step. Default 7 retries; `ava.NonRetryableError` stops them. | [retries.md](../orchestration/retries.md) |
| Run | One execution of a workflow or machine, with an ID, an input, a status (`queued`, `running`, `waiting`, `succeeded`, `failed`, `cancelled`) and a result. | [local.md](../run/local.md) |
| Run context | `ava.RunContext`: the run ID, workflow name and current step, injected into a step that annotates a parameter with it. | [python-steps.md](../building-blocks/python-steps.md) |
| Runtime | What executes an agent step: `ava.runtimes.PredictRLM` (default), `ClaudeAgent` or `OpenAIAgent`, with its model and limits. | [runtimes.md](../building-blocks/runtimes.md) |
| Schedule | A workflow's cron trigger. Schedules start disabled and are enabled per workflow. | [triggers.md](../connect/triggers.md) |
| Score | A classifier question type: place the state on an ordered list of levels. | [classifier-steps.md](../building-blocks/classifier-steps.md) |
| Selector | See "Which word to use". | [ava-cli.md](ava-cli.md) |
| Share page | On Avalanche Cloud, the public page of a published template, viewable without signing in. | [templates-and-sharing.md](../run/templates-and-sharing.md) |
| Signature | The contract of an agent step: instructions plus typed input and output fields (`ava.Signature`). | [agent-steps.md](../building-blocks/agent-steps.md) |
| Skill | For agents: an Agent Skill, a folder with `SKILL.md` and files the agent loads when a task needs it (`ava.agent.Skill`). PredictRLM has its own separate skills (`ava.runtimes.predict_rlm.skills`). | [tools-and-skills.md](../building-blocks/tools-and-skills.md) |
| Skip | A step outcome that is neither success nor failure: `ava.skip(reason)`. Steps that need its output are skipped too. | [control-flow.md](../orchestration/control-flow.md) |
| Sleep | A wait for a duration or until a time: `ava.sleep`. | [waits.md](../orchestration/waits.md) |
| Slug | See "Which word to use". | |
| Source | A step declared with `@ava.source`: brings data in. Shown as the start of a flow. | [python-steps.md](../building-blocks/python-steps.md) |
| State | In a state machine, a named stage whose work runs when the machine enters it (`ava.state`). In a classifier call, the text or JSON being judged (`state=`). In the operator, what it stores (`ava state`, the state database). | [state-machines.md](../building-blocks/state-machines.md) |
| State machine | A long-lived, keyed run that moves between states on events and timers. Declared with `@ava.machine`. | [state-machines.md](../building-blocks/state-machines.md) |
| Step | A unit of work in a workflow: a decorated Python function (`@ava.source`, `@ava.step`, `@ava.dest`), an agent step, a classifier step, or a wait. Its docstring's first line is shown in the UI. | [python-steps.md](../building-blocks/python-steps.md) |
| Template | On Avalanche Cloud, a published workflow that other people can try and copy. | [templates-and-sharing.md](../run/templates-and-sharing.md) |
| Template trial | A project created by trying a template. It cannot be cloned with `delta clone` until it is converted. | [templates-and-sharing.md](../run/templates-and-sharing.md) |
| Timer | A machine transition that fires after a delay in a state: `state.after(timedelta(...)) >> target`. | [state-machines.md](../building-blocks/state-machines.md) |
| Tool | A function an agent may call: a Python callable in `tools=`, a Connector method marked `@ava.tool`, or a tool of an MCP server or built-in integration. Integration tools are named `<integration>__<tool>`. | [tools-and-skills.md](../building-blocks/tools-and-skills.md) |
| Trace | The record of one agent call: model, turns, tool calls, token usage (`ava.agent.AgentTrace`). Stored with the step. | [agent-steps.md](../building-blocks/agent-steps.md) |
| Transition | A machine edge from one state to another on an event: `state.on("name") >> target`. | [state-machines.md](../building-blocks/state-machines.md) |
| Trigger | What starts a run: a person or API call, a cron schedule, or a webhook. | [triggers.md](../connect/triggers.md) |
| Turn | One model response inside an agent call, with its tool calls. | [agent-steps.md](../building-blocks/agent-steps.md) |
| Wait | A step that pauses a run without using a worker: a sleep, an event wait, a decision, or a call waiting for another run. | [waits.md](../orchestration/waits.md) |
| Webhook | An HTTP `POST` route that starts a run of a workflow declared with `webhook=`; the JSON body is the run input. | [triggers.md](../connect/triggers.md) |
| Work | In a state machine, the step or workflow a state runs on entry. | [state-machines.md](../building-blocks/state-machines.md) |
| Workflow | A graph of steps built by a function decorated with `@ava.workflow`, composed with `>>` (then) and `&` (in parallel). | [workflows.md](../building-blocks/workflows.md) |
| Workspace | `ava.Workspace`: a reference to a directory the host keeps, passed to a step or an agent to read and write. Not the project folder. | [inputs-and-files.md](../building-blocks/inputs-and-files.md) |

Words that do not exist in this version: belt (not used anywhere in the code); tables, streams, `ava.Stream`, `avalanche.iceberg`, `avalanche.lance` (removed).
