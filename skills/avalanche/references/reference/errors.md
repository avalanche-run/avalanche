# Errors reference

Every exception a workflow author can meet: when it is raised, what the message looks like, whether the step is retried, and what to do.

Related: [retries](../orchestration/retries.md), [debugging](../guides/debugging.md), [Python API](python-api.md), [`ava` CLI](ava-cli.md), [operator REST API](operator-api.md).

## How a failing step is handled

| What the step raises | Result |
| --- | --- |
| `ava.StepSkipped` (from `ava.skip()`) | step ends `skipped`; never retried; not a failure |
| `ava.NonRetryableError` or a subclass | step fails at once; remaining retries are not used |
| `ava.OutcomeUnknownError` | by the step's `on_uncertain`: `"retry"` (default) retries, `"fail"` fails at once, `"hold"` parks the step for a person to reconcile |
| any other exception (`ValueError`, `TypeError`, `RuntimeError`, `httpx` errors, ...) | retried until it succeeds or the retries are used up, then the step fails |

- `retries` defaults to 7 and at most 7 are applied, so a step runs at most 8 times. Waits (`ava.sleep`, `ava.wait_for_event`, `ava.decision`) and `ava.start_run` are never retried.
- A failed step fails the run. The run summary shows `error_code` `node_failed`, `error_step` and a message without the exception text, for example `TypeError in step summarize_order_1`. The step itself carries the full text (`error`, `attempt`, `max_attempts`, `last_attempt_error`).
- "Retried: yes" below means the error class is not a `NonRetryableError`, so retries apply even when retrying cannot help. For a deterministic failure in your own step, raise `ava.NonRetryableError` to stop early.
- Errors marked "at declaration" are raised when the module is imported or the workflow is built, before any run: `ava dev` reports them as discovery diagnostics for that file.

## Core SDK

| Exception | Base | Raised when | Message | Retried | What to do |
| --- | --- | --- | --- | --- | --- |
| `ava.NonRetryableError` | `Exception` | you raise it from a step | yours | no | use for bad input and other failures a retry cannot fix |
| `ava.OutcomeUnknownError` | `Exception` | you raise it when an outside call may have happened | yours | per `on_uncertain` | set `on_uncertain="hold"` or `"fail"` on steps that must not repeat a side effect |
| `ava.StepSkipped` | `Exception` | `ava.skip(reason)` | the reason; default `Skipped by the step` | no (skipped, not failed) | do not catch it; steps that need the skipped output are skipped with `Upstream step 'x' was skipped` |
| `ava.MapItemsFailed` | `RuntimeError` | a `.map` has items that failed for good | `name: MapItemsFailed [(index, child_id, Type: message); ...]`; `.failures` holds `MapItemFailure(index, child_id, error)` | each item used its own retries; the map itself is not rerun | fix the failing items; use `fail_fast=False` to let the other items finish first |
| `RunInputInvalidError` (`avalanche.run_input`) | `NonRetryableError` | the run input does not fit the workflow's `input=` model | `The run's input is invalid: field: message; ... (ModelName)`; code `input_invalid` | no | fix the input; field names and messages come from the model, values are not shown |
| `DecisionTimeoutError` (`avalanche.policies`) | `NonRetryableError` | an `ava.decision` timed out and has no `on_timeout` | `decision_timeout` | no | pass `on_timeout=Outcome(...)`, or `timeout=None` |
| `ChildRunError` (`avalanche.run_steps`) | `NonRetryableError` | the run an `ava.call` waits for failed, was cancelled or timed out, or would wait on itself | `<code>: <run_id>` with code `child_run_failed`, `child_run_cancelled`, `child_run_timeout`, `child_run_self_wait`, `child_run_wait_cycle` | no | inspect the child run; a timeout does not cancel the child |
| `TablesAndStreamsRemovedError` (`avalanche._removed`) | `ImportError` | any use of `ava.iceberg`, `ava.lance`, `ava.Stream`, `ava.ModelStream`, `ava.Cursor`, `ava.AppendResult` | `avalanche.iceberg, avalanche.lance and ava.Stream ... were removed.` | at declaration | remove the usage; there is no replacement in this version |
| `ImportError` (optional feature) | | a feature whose extra is not installed is used | ``Agent features require the optional agents dependency. Install it with `uv add 'avalanche-ai[agents]'` or `uv sync --extra agents`.`` | at declaration | install the extra: [project-layout.md](project-layout.md#package-extras) |
| `MachineDiscoveryWarning` (`avalanche.machine`) | `UserWarning` | a machine declares or defers an event no state accepts | `event 'x' is accepted by no state` | warning only | add a `state.on("x")` transition or remove the event |
| `UnsupportedModelFieldError` (`avalanche.model_frame`) | `ValueError` | a model with a field type that has no column form is converted to a dataframe | names the field | yes | annotate the field with `ava.Json` or change its type |

Errors of the authoring API raised as built-in exceptions, all at declaration:

| Exception and message | Cause |
| --- | --- |
| `RuntimeError: Node 'x' invoked outside of workflow context. Nodes can only be called within @workflow decorated functions.` | a decorated step was called like a function outside a workflow body |
| `RuntimeError: Cannot connect nodes from different workflow contexts using '>>'` (or `'&'`) | futures of two workflows were combined |
| `TypeError: Cannot chain NodeFuture with X. Expected NodeFuture or ParallelTasks.` | the right side of `>>` or `&` is not a step invocation (often a missing `()`) |
| `ValueError: Workflow 'x' contains a cycle involving: a_1, b_1` | the graph has a cycle |
| `ValueError: Duplicate node slug 'x' in workflow; use unique slug= values for rerun-addressable nodes` | two different steps share an explicit slug |
| `ValueError: Duplicate event consumer key in workflow` | two `ava.wait_for_event` use the same `key` |
| `ValueError: Event-returning steps must declare emits` | a step annotated `-> ava.Event[...]` has no `emits=` |
| `ValueError: emits must contain unique nonempty names` | bad `emits=` |
| `ValueError: workflow emits must match its returned node emits` | `@ava.workflow(emits=...)` differs from the returned step's `emits` |
| `ValueError: workflow key must contain at least one input field` / `workflow key fields must be declared input fields` / `duplicate workflow key fields` | bad `key=` |
| `TypeError: workflow input type must inherit from ava.BaseInput` / `workflow context type must inherit from ava.BaseContext` / `workflow output must be a Pydantic model` | bad `input=`, `context=`, `output=` |
| `ValueError: Use either context= or ctx=, not both` | both given with different types |
| `TypeError: webhook must be ava.Webhook, True, False, or None`; `ValueError: Webhook path must ...` | bad `webhook=` |
| `ValueError: x: map requires at least one mapped argument` / `x: max_items must be between 0 and 64` / `x: chained max_items may only lower inherited N` / `x: mapped sources have different fail_fast policies`; `TypeError: x: fail_fast must be bool` | bad `.map(...)` |
| `ValueError: sleep requires exactly one of duration or until` / `until must be timezone-aware` | bad `ava.sleep` |
| `TypeError: wait_for_event requires a Pydantic model class`; `ValueError: event key must be a nonempty string` | bad `ava.wait_for_event` |
| `TypeError: decision requires an ava.Event subclass` / `on_timeout must be an instance of the declared Event` / `decision.map show must be a callable receiving one item` | bad `ava.decision` |
| `TypeError: ava.start_run is workflow-only; use ava run or a normal run client` | `ava.start_run` or `ava.call` outside a workflow body |
| `RuntimeError: ava.state must be used inside an @ava.machine builder`; `ValueError: machine requires exactly one initial state` / `each state must be assigned a unique builder-local name` / `duplicate machine work name 'x'` / `internal event 'x' not declared in <state> work emits`; `TypeError: machine input must be a BaseInput model` / `state work must be an ava.step or ava.workflow` | bad machine |
| `TypeError: skip reason must be a string`; `ValueError: skip reason must not be blank` / `skip reason must be at most 1000 characters` | bad `ava.skip` argument |

Raised while a run executes:

| Exception and message | Cause | Retried |
| --- | --- | --- |
| `NonRetryableError: undeclared event 'x'; declares ('a', 'b')` | a step returned `ava.event("x", ...)` not listed in its `emits=` | no |
| `TypeError: ... multiple values for argument 'x'` | a step parameter annotated with an `ava.BaseInput` subclass also receives an upstream value; such parameters are filled with the run input | yes |
| `TypeError: hold steps require a return annotation` | `on_uncertain="hold"` on a step without a return annotation | fails the run |
| `RuntimeError: Keyed workflows require a run-capable scheduler host ...` / `Declared RUN/MACHINE nodes require a scheduler host ...` | `Workflow.run()` on a workflow with `key=`, `ava.start_run`, `ava.call` or a machine send | use `workflow.test()` or an operator |
| `RuntimeError: machine execution requires a machine-capable host; use machine.test locally` | `.run()` on a machine | use `machine.test()` or an operator |
| `RuntimeError: file reference is not bound to a host path; use a reference binder` | `File.path` or `File.open` on a file that was not given to this step by the host | read files inside steps, from step parameters |
| `ValueError: File.open accepts read modes only: 'r', 'rb', or 'rt'` | writing through an `ava.File` | write a new file and return `ava.File.from_path(...)` |
| `LoggerInstance.info() takes 2 positional arguments but N were given` (`TypeError`) | `logger.info("x %s", y)` on an `ava.Logger()` logger | pass one formatted string |

## Agent steps

| Exception | Base | Raised when | Message | Retried | What to do |
| --- | --- | --- | --- | --- | --- |
| `ava.agent.AgentStepError` | `RuntimeError` | at declaration: no `agent` parameter, not keyword-only, has a default, or not annotated `ava.Agent`; an agent decorator inside a workflow body; duplicate skill names; unexpected `inputs=` fields | `agent step 'f' requires a keyword-only agent parameter`; `... agent parameter must be annotated ava.Agent`; `Agent decorators cannot be declared inside a workflow body; use ava.agent.step(Signature, inputs=...) for an inline agent.` | at declaration | fix the function signature: `async def f(x: str, *, agent: ava.Agent) -> str` |
| `ava.agent.AgentStepError` | `RuntimeError` | in a run: the call's keyword arguments do not match the signature's input fields, or fail validation; `ava.agent.Skill` used with `PredictRLM` | `agent step 'f': missing input fields ['a']; unexpected input fields ['b'].`; `agent step 'f' runs on PredictRLM, which does not support ava.agent.Skill yet ...` | yes | call `agent(...)` with exactly the signature's input names; use `ClaudeAgent` or `OpenAIAgent` for skills |
| `ava.agent.AgentStepCallError` | `AgentStepError` | the step calls `agent(...)` a second time in one execution | `agent step 'f' called its agent more than once. An agent step may call its agent only once per execution: ...` | yes | one call per step; split the work into several agent steps |
| `ava.agent.AgentStepExecutionError` | `RuntimeError` | the runtime failed: model or SDK error, limits reached, or a missing extra | `agent step 'f' runs on ava.runtimes.ClaudeAgent, which needs the Claude Agent SDK: install avalanche-ai[claude]` | yes | read the step's trace; install the runtime's extra; check the model key |
| `ava.agent.AgentOutputError` | `AgentStepExecutionError` | the agent's answer does not fit the signature's output fields, or a file output is unusable | `agent step 'f' returned outputs that do not match signature S(a) -> (b): ...` | yes | simplify or describe the output fields better; the trace is still recorded |
| `ava.agent.SkillFormatError` | `ValueError` | `Skill.from_dir` on a folder that breaks the Agent Skills format | `<path>/SKILL.md: skill name 'x' must match its folder name 'y'`; `... must start with a '---' frontmatter line`; `... frontmatter has fields the Agent Skills specification does not define: [...]` | at declaration | fix the folder; allowed frontmatter: `name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools` |
| `TypeError` | | wrong option types on an agent step | `ava.agent_step requires a Signature as its first argument`; `ava.agent_step runtime must be an agent runtime such as ava.runtimes.PredictRLM(...), not str`; `ava.agent_step tools must be a sequence`; `ava.agent_step skills must be ava.agent.Skill objects, not X; ...`; `ava.agent_step evaluations must be an ava.Evaluations declaration`; `agent signature must be an ava.Signature subclass, ...`; `agent signature X declares no output fields` | at declaration | fix the argument |
| `TypeError` | | options that moved to the runtime, or unknown `agent_defaults` keys | `ava.agent_step no longer accepts lm=; pass them to the runtime: runtime=ava.runtimes.PredictRLM(lm=...)`; `ava.workflow(agent_defaults=...) got unexpected keys foo=; it accepts only 'runtime', and tools belong on each agent step` | at declaration | move the setting into `runtime=` |
| `pydantic.ValidationError` | `ValueError` | a runtime is built with a wrong type, an unknown option, or a refused one | `ClaudeAgent does not take 'cwd': give the step an ava.Workspace input ...`; `OpenAIAgent got unknown options ['x']: ...` | at declaration | fix the runtime options |

A step that finishes without calling its agent is not an error: a warning is logged (`agent step 'f' finished without calling its agent: no model ran ...`) and no trace or evaluation exists.

## Classifier steps

| Exception | Base | Raised when | Message | Retried | What to do |
| --- | --- | --- | --- | --- | --- |
| `ava.ClassifierStepError` | `RuntimeError` | at declaration: invalid questions or settings; no keyword-only `classifier: ava.Classifier` parameter; a question the provider cannot take | `invalid classifier declaration: ValidationError`; `classifier step 'f' requires a keyword-only classifier parameter`; `classifier parameter must be annotated ava.Classifier`; `question 'q' cannot be sent to <provider>: a choice takes 2 to 255 options` | at declaration | fix the questions or the signature |
| `ava.ClassifierStepExecutionError` | `RuntimeError` | a classifier call failed: missing key, missing extra, invalid `state`, no questions, provider error or timeout, invalid provider answer | `classifier step 'f' failed: TYPESAFE_API_KEY is required to call a classifier`; `... failed: <Provider> needs the 'openai' extra: install avalanche-ai[openai]`; `... failed: questions must be provided on the classifier call or as classifier_step defaults`; `... failed: ValidationError` | yes | set the provider's key; install the extra; pass text or finite JSON as `state` |
| `ava.ClassifierStepError` | `RuntimeError` | the classifier is used after its step returned | `classifier cannot be used after its step has finished` | yes | await every classifier call inside the step |

Messages never contain the judged state or the key: a validation problem is reported by its type name only.

## Evaluations

Evaluations run after the agent call, outside the step. A failed evaluation never fails or retries the step or the run; the error is recorded on the evaluation and shown with it.

| Exception | Base | Raised when | Message | What to do |
| --- | --- | --- | --- | --- |
| `ava.evaluations.EvaluationError` | `RuntimeError` | at declaration: a metric question is not one valid classifier question, a selector or composite is async | `Invalid metric question: ...; use one classifier Noul, Score, or Choice question`; `Metric state selector must be a synchronous callable` | fix the `ava.Metric` |
| `ava.evaluations.EvaluationError` | `RuntimeError` | while evaluating: a selector raised, returned non-JSON, the classifier call failed, or a composite raised or returned a number outside `[0, 1]` | `State selector for metric 'm' failed: AttributeError; ...`; `Evaluation for metrics 'm' failed: ...`; `Composite 'c' must return a finite number in [0, 1]; normalize an N-level Score with score / (N - 1)` | fix the selector or composite; set the classifier key |
| `pydantic.ValidationError` | `ValueError` | `ava.Evaluations(...)` with no metrics, an unknown field or a wrong type | pydantic's | fix the declaration |

## Integrations and connections

Built-in integrations and named connections are not available on every account yet.

| Exception | Base | Raised when | Message | Retried | What to do |
| --- | --- | --- | --- | --- | --- |
| `ava.integrations.IntegrationError` | `NonRetryableError`, `ValueError` | at declaration: invalid `ava.Connector`, `ava.MCP`, `ava.OAuth2`; classes passed instead of instances; MCP or built-in on a plain step; clashing tool names. In a run: a Connector's client used outside its step, or a request to another origin | `ava.step integrations takes instances: write AcmeCrm(), not AcmeCrm`; `ava.step cannot use MCP server 'x': MCP tools are for agent steps only; declare it on an ava.agent_step`; `ava.MCP 'x' takes exactly one of url= (a remote server) or command= (a local process)`; `request to https://other:443 refused: this Connector's client only calls its base_url, ...` | no | fix the declaration; pass paths relative to `base_url` |
| `ava.integrations.IntegrationNotConnectedError` | `NonRetryableError` | a step starts and one of its integrations has no account | ``step 'f' uses integration 'acme-crm' with no connected account. Connect with `ava connect acme-crm`, then start the run again.`` | no | run `ava connect NAME` (or `delta connect NAME`), then start a new run |
| `ava.integrations.IntegrationReconnectError` | `NonRetryableError` | a browser login expired or was revoked: at step start when it cannot be refreshed, or when the service answers 401 | ``step 'f': the login of integration 'x' is no longer valid (<reason>). Connect it again with `ava connect x`, then start the run again.`` | no | connect again |
| `ava.integrations.MCPServerUnavailableError` | `RuntimeError` | an MCP server could not be reached or did not answer in time when the step started | names the server | yes | check the URL or command; the cause may pass |
| `ava.integrations.MCPToolError` | `RuntimeError` | a call to an MCP tool failed or its arguments were refused | the tool's error | not raised to the step: the agent sees a failed tool call | none; the agent can react |
| `ava.integrations.AccountLoginExpiredError` | `Exception` | raised by an account store; surfaces as `IntegrationReconnectError` | `the login of integration 'x' is no longer valid: <reason>` | no | connect again |
| `ava.integrations.AccountUnavailableError` | `RuntimeError` | raised by an account store when a login could not be refreshed for a passing cause | `the login of integration 'x' could not be refreshed: <reason>` | yes | retry later |
| `ProviderNotInstalledError` (`avalanche.integrations.providers`) | `NonRetryableError`, `ValueError` | a built-in integration is used and no provider package is installed | ``Built-in integrations need a provider, for example Avalanche Cloud (Trampoline Delta): uv tool install trampoline-delta. Then sign in and use `ava connect <name>`.`` | no | install a provider and sign in |
| `ProviderNotSignedInError` | `NonRetryableError`, `ValueError` | the provider needs a sign-in | `The integration provider is not signed in. Follow its sign-in instructions, then connect again.` | no | sign in (`delta login`) |
| `ProviderSelectionError` | `NonRetryableError`, `ValueError` | several providers are installed and none is selected | `Several integration providers are installed: a, b. Set AVALANCHE_INTEGRATION_PROVIDER to an entry name.` | no | set the variable |
| `ProviderUnavailableError` | `MCPServerUnavailableError` | the provider cannot be reached | names the cause | yes | retry later |
| `ava.ConnectionNotResolvedError` | `NonRetryableError` | no connection of that integration is assigned to the flow, its project or the organization | `Flow 'f' uses slack, but no slack connection is assigned to the flow, its project or the organization. Assign one, then deploy again.`; code `connection_not_resolved` | no | assign a connection |
| `ava.ConnectionAmbiguousError` | `NonRetryableError` | several connections are assigned at the same level | `Flow 'f' uses slack, and two slack connections are assigned to <level>: 'a' and 'b'. Remove one, or name one with ava.catalog.slack(connection=...).`; code `connection_ambiguous` | no | name the connection in code |
| `ava.ConnectionNotAssignedError` | `NonRetryableError` | the named connection is not assigned to the flow, project or organization | `Flow 'f' names connection 'a', which is not assigned to the flow, its project or the organization.`; code `connection_not_assigned` | no | assign it |
| `ava.ConnectionNotFoundError` | `NonRetryableError` | the named connection does not exist or belongs to another integration | `Flow 'f' names connection 'a', which does not exist (or is not a slack connection).`; code `connection_not_found` | no | fix the name |
| `ava.ConnectionPerUserTriggeredError` | `NonRetryableError` | a flow with a schedule or webhook uses a per-user connection | `Flow 'f' has a schedule or webhook, and its slack connection 'a' is per user. A triggered run has no user. Assign a shared connection.`; code `connection_per_user_triggered` | no | use a shared connection |
| `ava.ConnectionUserNotConnectedError` | `NonRetryableError` | the person starting the run has not connected their account for a per-user connection | `Connect your slack account ('a') to run flow 'f', then start the run again.`; code `connection_user_not_connected` | no | connect, then start again |

The six `Connection*Error` classes share the base `ConnectionResolutionError` and carry `.integration`, `.connection_slug` and `.code`.

## Waits, events and decisions

Refusals of an event, a decision or a machine event. In Python (`RunHandle.send_event`, `RunHandle.decide`, the test drivers) they are `WaitRefusalError` (`avalanche.local_waits`, a `ValueError`) with `.code` and `.status`. Over REST the code is the error message; the CLI prints `STATUS: code` with the gRPC status name: 404 `NOT_FOUND`, 409 `ALREADY_EXISTS`, 422 `INVALID_ARGUMENT`, 403 `PERMISSION_DENIED`, 413 `RESOURCE_EXHAUSTED`, 503 `UNAVAILABLE` (for example `ALREADY_EXISTS: run_closed`).

| Code | HTTP | Meaning |
| --- | --- | --- |
| `unknown_run`, `unknown_key`, `unknown_decision`, `unknown_map_item` | 404 | no such run, event key, decision or mapped item |
| `already_resolved_or_withdrawn` | 409 | the decision was already answered or is no longer open |
| `run_closed` | 409 | the run has ended |
| `not_armed`, `state_missing`, `definition_unavailable`, `conflict` | 409 | the wait is not open yet, or the run's definition or state is not available |
| `idempotency_conflict` | 409 | the idempotency key was already used with a different payload |
| `inbox_full` | 409 | too many undelivered events are buffered for the run (more than 64 events or 1 MiB) |
| `invalid_wait_submission` | 422 | the payload does not fit the wait's schema, or the address is not a decision of the run |
| `event_payload_must_be_object`, `event_payload_must_be_empty` | 422 | the event payload must be a JSON object; an event declared without payload takes none |
| `not_a_decision`, `reason_required`, `idempotency_key_required`, `map_requires_item_identity` | 422 | wrong target or a missing field |
| `unauthorized` | 403 | the caller is not among the decision's `approvers` |
| `request_too_large`, `wait_card_too_large` | 413 | payload or shown data over the size limit |
| `coordinator_unavailable` | 503 | the run's process cannot be reached right now; retry |

## Reruns

| Exception | Base | Raised when | Message | What to do |
| --- | --- | --- | --- | --- |
| `RerunRefusedError` (`avalanche.reruns`) | `Exception` | a rerun request fails its checks; nothing is started | `Rerun refused: <step or 'source run'>: <detail>; ...`; `.refusal.problems` lists `(step, reason, detail)`, `.refusal.earliest_start` the nearest steps that would pass | rerun from `earliest_start` |

`reason` values: `unknown_start_step` (no step with that slug in the current code), `missing_from_source` (the source run has no saved output for a step that would be reused), `not_reusable` (that output cannot be reused), `output_unavailable` (a saved output or one of its files is gone), `output_does_not_fit` (the saved output no longer fits the step that reads it), `source_run_active` (the source run is still going).

Operator refusals: a keyed flow run cannot be rerun. A rerun request on one answers `INVALID_ARGUMENT: missing run key field '<name>'` without the key fields in the input, returns the existing run's ID and starts nothing when the key already has a run, and answers `FAILED_PRECONDITION: keyed flow runs cannot be rerun from a step` for a new key. `ava run rerun` always answers `Machine reruns are not supported; use a new key or retry an interrupted state entry`.

## Operator, CLI and client

| Exception | Where | Raised when | Message | What to do |
| --- | --- | --- | --- | --- |
| `OperatorCallError` (`runtime.operator.client`) | Python client, CLI | any refused operator call; `.status` (gRPC status), `.details` | `STATUS: details`, for example `NOT_FOUND: Unknown workflow: nope`, `INVALID_ARGUMENT: missing run key field 'order_id'`, `ALREADY_EXISTS: Run X already exists`, `UNAVAILABLE: ...` when no operator listens at the address | fix the request; start the operator; check `--connect` |
| `WorkflowDiscoveryError` (`runtime.operator.discovery`) | `ava dev`, `ava operator` | no usable set of workflows could be loaded | `error: Avalanche dev failed during discovery`, then one line per file: `path: kind: message` with kind `import_error`, `build_error`, `invalid_schedule`, `invalid_catalog`, `warning` or `skipped` | fix the file named; `WorkflowDiscoveryTimeoutError` means the scan exceeded `--discovery-timeout` |
| `StateStoreLockedError` (`runtime.operator.state`) | operator start, `ava state` | another process uses the state database | `Operator state database PATH is already in use by another process, such as another operator. Stop it, or pass a different --state-db.` | stop the other operator or use another `--state-db` |
| `StateSchemaTooNewError` | operator start | the database was written by a newer Avalanche | `Operator state database PATH has schema version N, but this Avalanche version supports up to M. Upgrade Avalanche, or pass a different --state-db.` | upgrade, or use another database |
| `StateSchemaMismatchError`, `StateRowError`, `StateMaintenanceError` | operator start, `ava state` | the database does not match this code, or a maintenance command cannot proceed (`No stored run X in PATH; nothing was deleted.`) | as shown | use another `--state-db`, or `ava state clear` |
| `ValueError` (flow targets) | `ava dev`, `ava operator` | no targets, or a bad one; exit code 2 | `No workflow targets configured. Pass FLOW [FLOW ...], or set [tool.avalanche].flow_targets in pyproject.toml.`; `Workflow target does not exist: PATH`; `Workflow file target must end in .py: PATH`; `--port and --web-port must differ` | fix the arguments or `pyproject.toml` |
| unknown or ambiguous workflow | every run start | the selector matches nothing, or a bare name matches several | `Unknown workflow: NAME`; `'NAME' is ambiguous:` followed by the full selectors | use the full selector `file.py::name` |
| result not available | `ava result`, `GET /runs/{id}/result` | the run has not ended, or did not succeed | `Run X is not terminal`; `Run X ended with status failed` | wait (`ava result --wait`), or read a step's output with `--node` |
| webhook start refused | webhook listener | the run could not be started: on a workflow with `key=`, any body that does not fit the input model (without `key=` the answer is `202` and the run fails) | HTTP 409 `{"error": "workflow could not be started"}`, without the reason | check the body against the input model; `ava run FLOW --input` with the same body prints the reason |
| keyed start refused | `ava run`, `POST /runs` on a workflow with `key=` | the input does not fit the input model; no run is created | `FAILED_PRECONDITION: Invalid machine input/payload: <field>: <error type>` (HTTP 409); `INVALID_ARGUMENT` when a key field has the wrong type | fix the input |
| run preparation failed | every run start | the run's process did not import the flow file and build the graph within 15 seconds (no flag or setting changes the limit), or that import raised | run `failed` with no steps, `error_code: unknown`, `error_message: Run failed`; log: `Workflow preparation failed: Workflow preparation exceeded 15.0s`, or `Workflow preparation failed: <ErrorType>: ...` with a traceback | keep flow files fast to import (heavy imports inside steps, nothing at module level); start the run again |
| operator restart | run list | a run was unfinished when its operator stopped | run fails with `Operator restarted before the run finished; it was not resumed` | start the run again, or rerun it from a step |

Run failures that are not a step's exception appear as `error_code` on the run: `input_invalid` (the input did not fit the model; `Run failed`), `node_timeout` (`Step X timed out`), `worker_lost`, `wait_expired`, `run_rejected` (`Rejected at admission`), `coordinator_failed` (the workflow's returned value could not be stored or validated, for example a skipped final step with `output=Model`), `coordinator_exited`, `result_store_failed`, `unknown`. Their detail is in the run's log.

`delta` reports its own failures as `Authentication error: ...` (exit 1), `Delta CLI error: ...` (exit 1) and `Configuration error: ...` (exit 2): [delta-cli.md](delta-cli.md#conventions).
