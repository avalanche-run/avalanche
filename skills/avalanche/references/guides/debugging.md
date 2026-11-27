# Debugging

Symptom, cause, fix. Start with the [procedure](#investigate-a-failed-run-from-the-command-line)
when a run failed and you do not know why; use the tables when you already have an error text.

Three facts explain most surprises:

- The workflow function runs once, before any step, to build the graph. Step results are
  placeholders there.
- A step that raises is run again at once, up to 8 attempts by default. A code mistake shows up
  as `attempt: 8`, after 8 identical failures.
- Step outputs are saved as JSON on the operator. An in-process `workflow().run()` does not check
  that, and words some errors differently.

## Investigate a failed run from the command line

Commands talk to a local operator on `localhost:7433`; add `--connect HOST:PORT` for another.

```bash
# 1. Find the run and its one-line error
uv run ava run list --flow FLOW                  # TYPE  RUN  DEFINITION  STATUS  NOTE

# 2. Read the run: status, failed step, error text, attempts
uv run ava run show RUN_ID --json
```

Read these fields, in this order:

| Field | Tells you |
|---|---|
| `summary.status` | `failed`, `waiting`, `cancelled`, ... |
| `summary.error_code` | `node_failed` (a step failed), `input_invalid` (the run input), `coordinator_failed` (the run's return value), `unknown` (operator restart, or the run could not be prepared: read the log) |
| `summary.error_step`, `summary.error_message` | Node id of the failed step and `<ErrorType> in step <node id>` |
| `summary.waiting_on` | For a waiting run: counts per kind (`decision`, `event`, `run`, `sleep`) |
| `nodes[].status` | `success`, `failed`, `skipped`, `reused`, `pending`, `running` |
| `nodes[].error` | `<ErrorType>: <message>` of the failed step |
| `nodes[].attempt`, `nodes[].max_attempts` | `8` of `8`: retried to the end. Absent: failed once, not retried |
| `nodes[].skip_reason` | Why a step was skipped. A skipped step with no reason did not run because the run failed |
| `nodes[].step_io` | Previews of the step's inputs and output |
| `nodes[].trace.header` | Agent steps: model, turn count, usage, stop reason |

```bash
# 3. Output of a step that succeeded, by node id (not function name)
uv run ava result RUN_ID --node load_orders_1 --json

# 4. What a waiting run waits for
curl -s http://127.0.0.1:7435/api/v1/runs/RUN_ID/waits
```

5. The traceback, `print` and logger lines, and the per-attempt errors are in the run's log. No
   CLI command prints log text, and the REST activity route lists entries without their text.
   Read it with the client the CLI uses:

```python
# logs.py: uv run python logs.py 127.0.0.1:7433 RUN_ID [NODE_ID]
import sys

from runtime.operator.client import GrpcStateProvider

provider = GrpcStateProvider(sys.argv[1])
try:
    run = provider.get_run(sys.argv[2])
    print(run.status.value, run.error_step, run.error_message)
    for entry in run.logs:
        print(entry.level, entry.node_id, entry.message)
    if len(sys.argv) > 3:  # full trace of one agent step
        print(provider.hydrate_trace(sys.argv[2], sys.argv[3]).trace_body)
finally:
    provider.close()
```

A retried step logs one line per attempt: `attempt 1/3 failed: RuntimeError: boom 1; retrying`.

```bash
# 6. Fix the code (the operator reloads saved files), then rerun from the failed step.
#    --from takes the step's slug (function name), not the node id.
uv run ava run FLOW --rerun-of RUN_ID --from failed_step
uv run ava result NEW_RUN_ID --wait --json
```

Steps before the failed one show `reused`. If the rerun is refused, the message names the
earliest step you can start from. To reproduce one step in isolation, call `step.fn(...)` in a
test ([../run/testing.md](../run/testing.md)).

## Discovery and startup

| Symptom | Cause | Fix |
|---|---|---|
| Operator exits with code 1: `error: Avalanche operator discovery failed` then `<path>: import_error: <exception>` | A scanned file failed to import at startup. A broken file is not skipped. | Fix the import. Check with `uv run python path/to/file.py`. |
| Edited a file, the flow did not change, no error in the terminal | The reload failed; the previous catalog stays in use. | `curl -s http://127.0.0.1:7435/api/v1/flows` and read `diagnostics` (`kind`: `import_error`, `invalid_schedule`, ...). |
| Flow missing from `ava flow list` | Not defined in a scanned file, file or folder starts with `_` or `.`, name starts with `_`, or the builder raised. | Check the targets and `diagnostics`. A workflow is registered only in the file that defines it. |
| `No workflow targets configured. Pass FLOW [FLOW ...], or set [tool.avalanche].flow_targets in pyproject.toml.` | No targets on the command line or in the nearest `pyproject.toml`. | Pass a file or folder, or set `flow_targets`. |
| `Ambiguous workflow '<name>'; candidates: ...` | Two files define the same function name. | Use the full selector `file.py::name`. |
| `NOT_FOUND: Unknown workflow: <name>` | Wrong name, or the file is not scanned, or discovery failed. | `ava flow list`. |
| `Configured root basename '<name>' is ambiguous` | Two targets with the same folder name. | Give aliases: `a=path1 b=path2`. |
| `Webhook route collision for <path>` | Two workflows declare the same webhook path. | Change one `ava.Webhook(path=...)`. |
| `kind: invalid_schedule` diagnostic | Bad `cron=` expression. | Use a valid cron expression, for example `0 8 * * 1-5`. |
| `OSError: [Errno 98] Address already in use` at start | Another process holds a port. `ava dev` always uses webhook port 7434. | Pass other `--port`/`--web-port`/`--login-port`; for the webhook port use `ava operator --webhook-port`. |
| `Operator state database ... is already in use by another process` | Second operator on the same state file, or `ava state` while the operator runs. | One operator per `--state-db`; stop it before `ava state`. |
| `failed to connect to all addresses ... Connection refused` | No operator on that address. | Start one, or pass the right `--connect HOST:PORT`. |
| The `Ready.` line never appears in a redirected log | Output is buffered. | Start with `PYTHONUNBUFFERED=1`, or poll `ava flow list`. |
| A module-level side effect runs at odd times | Discovery imports every flow file and calls every builder. | Keep imports and the workflow function free of side effects. |

## Building the graph

These raise when the workflow function runs: at import in a test, or as a discovery diagnostic.

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: Node '<step>' invoked outside of workflow context` | A step (or `ava.sleep`, a wait) was called outside a workflow function, for example inside another step or in a test. | Call steps only in the workflow function; in tests call `step.fn(...)`. |
| `'function' object has no attribute 'test'` (or `run`) | `my_flow.test()` instead of `my_flow().test()`. | Call the builder first. A machine is the opposite: `my_machine.test(...)`. |
| `if`/`for` on a step result does nothing, or always takes one path | Results are placeholders at build time. | Branch with `ava.skip`, loop with `.map`: [../orchestration/control-flow.md](../orchestration/control-flow.md). |
| `ValueError: Workflow '<name>' contains a cycle involving: ...` | A loop in the graph. | Use a state machine for loops. |
| `TypeError: ava.start_run is workflow-only; use ava run or a normal run client` | `ava.call` / `ava.start_run` inside a step. | Call them in the workflow function. |
| `TypeError: ava.agent_step no longer accepts lm=; pass them to the runtime: ...` | Model or agent settings on the step. | `runtime=ava.runtimes.PredictRLM(lm=...)`. |
| `TypeError: ava.agent_step runtime must be an agent runtime such as ava.runtimes.PredictRLM(...), not str` | A name instead of an instance. | Pass `ava.runtimes.X(...)`. |
| `AgentStepError: Agent decorators cannot be declared inside a workflow body` | `@ava.agent_step` inside the workflow function. | Declare at module level, or use `ava.agent.step(Signature, inputs=...)`. |
| `AgentStepError: ... requires a keyword-only agent parameter` / `must be annotated ava.Agent` | Wrong `agent` parameter. | `async def f(x: X, *, agent: ava.Agent)`. |
| `ClassifierStepError: invalid classifier declaration: ValidationError` | A malformed question; the key is not named. | Check `type`, and `criteria`: dict for choice, list of 2+ for score, only `"true"`/`"false"` for noul; no extra keys. |
| `ClassifierStepError: ... requires a keyword-only classifier parameter` | Wrong `classifier` parameter. | `async def f(x: X, *, classifier: ava.Classifier)`. |
| `EvaluationError: Invalid metric question: ...` | Bad question in an `ava.Metric`, or one the provider cannot take. | Same shapes as classifier questions. |
| `TypeError: decision requires an ava.Event subclass` | The outcome model subclasses `BaseModel`. | Subclass `ava.Event`. |
| `TypeError` about a missing `timeout` on `ava.decision` / `ava.wait_for_event` | `timeout` has no default. | Pass it, `timeout=None` for no limit. |
| `ValueError: sleep requires exactly one of duration or until` / `until must be timezone-aware` | Wrong `ava.sleep` arguments. | One of them; an aware `datetime`. |
| `ValueError: Duplicate event consumer key in workflow` | Two `wait_for_event` with the same `key`. | One wait per key. |
| `ValueError: <step>: max_items must be between 0 and 64` | `max_items` above the hard cap. | Chunk the list, map over chunks. |
| `ValueError: Event-returning steps must declare emits` | Return annotation `ava.Event[...]` without `emits=`. | `@ava.step(emits=("name", ...))`. |
| `ValueError: machine requires exactly one initial state` / `each state must be assigned a unique builder-local name` / `final state <name> has outgoing transitions` | Machine definition errors. | [../building-blocks/state-machines.md](../building-blocks/state-machines.md). |
| `TypeError: workflow input type must inherit from ava.BaseInput` | `input=` is a plain `BaseModel`. | Subclass `ava.BaseInput`. |
| `run key field '<name>': ...` | A key field with an alias, `default_factory` or custom validator. | Use plain fields in `key=`. |
| `IntegrationError: ava.step integrations takes instances: write AcmeCrm(), not AcmeCrm` | A class in `integrations=[...]`. | Pass an instance. |
| `IntegrationError: ava.step cannot use MCP server '<name>': MCP tools are for agent steps only` | `ava.MCP` or `ava.catalog.*` on a plain step. | Move it to an agent step, or write a `Connector`. |
| `TypeError: ava.agent_step tools must be callable` | `tools=[fn()]` or a string. | Pass the function itself. |
| `SkillFormatError: ...` | A bad skill folder (`SKILL.md` missing, frontmatter, name differs from folder). | [../building-blocks/tools-and-skills.md](../building-blocks/tools-and-skills.md). |

## Passing data between steps

| Symptom | Cause | Fix |
|---|---|---|
| `TypeError: Cannot implicitly bind upstream result at position 1 for <step>: function signature has no parameter at that position` (after 8 attempts) | The upstream step returned a `list` or `tuple` and it went through `>>`: each element is bound to a separate parameter. | Pass it explicitly, `b(a())`, or return a model that holds the list. |
| `TypeError: missing a required argument: '<param>'` on the operator; `<step>() missing 1 required positional argument: '<param>'` in process | Nothing was bound to that parameter. Usual causes below. | |
| ... the parameter is named like an input field (`def greet(name: str)`) | The run input is not bound by name. | Type a parameter with the input model, or pass `ava.input.name` in the workflow function. |
| ... the upstream step returned an empty list through `>>` | An empty list binds nothing. | Pass the list explicitly. |
| ... `a() & b() >> c()` | `>>` binds tighter than `&`: this is `a() & (b() >> c())`, so `c` gets only `b`. | Parenthesise: `(a() & b()) >> c()`. |
| ... the step was called with a positional argument and also chained with `>>` | With any positional argument, `>>` only orders the steps. | Pass every data argument explicitly. |
| ... a parameter is typed with an input model but the workflow has no `input=` | Nothing to inject. | Add `input=Model`. |
| `TypeError: missing a required keyword-only argument` | `>>` fills positional parameters only. | Pass it by keyword: `b(value=a())`. |
| A step received fewer values than upstream produced, no error | The step has an injected parameter; surplus upstream values are dropped. | Match outputs to free parameters, or pass arguments explicitly. |
| A step received a placeholder object, or a list containing one | A future or `ava.input` was put inside a list or dict argument. | Pass futures and `ava.input` fields as direct arguments only. |
| Indexing a step result (`pair[0]`) fails at run time | The step has the default `num_returns=1`. | `@ava.step(num_returns=N)` and return a tuple of N. |
| `ValueError: Function <step> expected to return N values, but returned: ...` | `num_returns=N` and another number of items, or a list. | Return a tuple of exactly N. |
| `ValidationError` in a step before its code ran | The bound value does not fit the parameter's annotation. | Fix the upstream output type or the annotation. |
| `AttributeError: Node '<step>' references ava.input.<name> ..., but attribute '<name>' is missing on <Model>` | Typo in `ava.input.<name>`. | Use a field of the input model. |
| `ValueError: Node '<step>' references ava.input.x ..., but no run input is available` | `ava.input` in a workflow without `input=`. | Add `input=Model`. |
| `RuntimeError: file reference is not bound to a host path` | A file reached a parameter that is not annotated `ava.File` (or a model with a file field). | Annotate the parameter. |
| `File sha256 or size does not match bound content` | A file was changed after the step that returned it. | Write a new file instead of editing one already returned. |
| `RuntimeError: Workspace.path is available only during Avalanche node execution` | `.path` used at module level or in the workflow function. | Use it inside a step. |

## Step outputs and run results

| Symptom | Cause | Fix |
|---|---|---|
| `TypeError: Operator workflow results support JSON scalar/container values, Pydantic models, dataclasses, ava.File values, and ava.Workspace values` on a step (fails once, not retried) | The step returned a raw `datetime`, `bytes`, a custom class, a dataframe, or a dict with non-string keys. | Return a Pydantic model (its `datetime`, `Enum`, `UUID` fields are fine) or an `ava.File`. |
| Run `failed`, every step `success`, `error_code: coordinator_failed`, `Run coordinator failed; see run activity`; the log has the same `Operator workflow results support ...` text | The workflow function returned something that is not a result: usually a bare `a() & b()`. | Return a future, a `>>` chain, or a tuple of futures: `return a(), b()`. |
| Works with `workflow().run()`, fails on the operator | The in-process run does not save outputs, so it does not check them. | Test on the operator before calling it done. |
| `ValueError: Workflow result floats must be finite` | `nan` or `inf` in an output. | Replace with `None` or a number. |
| A step returned something other than its annotation says and nothing complained | The return annotation is not enforced when the step returns. | Make the code return the annotated type; downstream validation and reruns rely on it. |
| The result is `null` although the run succeeded | The last step was skipped, or the workflow function returns nothing. | Expected for a skip; otherwise add `return`. |
| Run `failed`, steps `success` or `skipped`, `error_code: coordinator_failed`; the log ends with `ValidationError: 1 validation error for <Model>` ... `input_value=None` | The workflow declares `output=Model` and the step it returns was skipped: the result is `None`, which is not a `Model`. In process: the same `ValidationError` from `.result()`. | Return a step that always runs (typed `X \| None`, building the model for the skipped case too), or drop `output=`. |
| A step received the wrong step's output, no error | A `>>` chain kept in a variable and passed as an argument: `x = a() >> b(); c(x)` gives `c` the output of `a`. | Bind the step's own call: `x = b(a()); c(x)`. |
| Output too large | Limits per value: 8 MiB of JSON, 100 levels, 100,000 items. | Write a file, return an `ava.File`. |

## Failures and retries

| Symptom | Cause | Fix |
|---|---|---|
| A step with an obvious bug shows `attempt: 8`, `max_attempts: 8` | Every exception is retried, code errors included. Default `retries=7`. | Fix the bug. While developing agent steps set `retries=0`. |
| A failing run takes long before it fails | 8 attempts, each a full execution (for an agent: new model calls). | Raise `ava.NonRetryableError` for failures no retry can fix; lower `retries`. |
| Need a pause between attempts | There is no backoff or delay option; a failed attempt is rerun at once. | Sleep inside the step before raising, or rerun later. |
| A side effect happened several times | The step was retried, rerun, or ran in a second run for the same thing. | Send an idempotency key built from a business id (`deal-for-lead-<id>`). `ctx.step_key` only covers retries inside one run: every run and rerun has a new one. Or `retries=0`; or `on_uncertain="hold"` with `ava.OutcomeUnknownError`. |
| `retries=20` still stops at 8 | Values above 7 act as 7. | Use a rerun for more. |
| Steps after the failure show `skipped` with no reason | They never ran because the run failed. | Rerun from the failed step (not possible with `key=`: see [Triggers and run keys](#triggers-and-run-keys)). |
| A skip did not happen: the step went on, or failed | `ava.skip()` raises `StepSkipped`; a broad `except Exception:` caught it. | Let it propagate. |
| Run `waiting`, a step stays `running`, a decision `Reconcile uncertain outcome` is open | `on_uncertain="hold"` parked the step. | Answer at the step's node id with `{"kind": "done", "output": ...}`, `{"kind": "retry"}` or `{"kind": "fail"}`. |
| Nothing resumes after a failure | A failed run is final. | `ava run FLOW --rerun-of RUN_ID --from SLUG`. A workflow with `key=`: start a run with a new key. |

## Reruns

| Symptom | Cause | Fix |
|---|---|---|
| `Rerun refused, nothing was started:` ... `unknown_start_step: the code that will run has no such step` | `--from` got a node id (`summarize_orders_1`) or a wrong name. | Use the slug: the function name, or the step's `slug=`. A second use of a step is `<slug>_2`. |
| ... `missing_from_source` | A step whose output is needed did not succeed in the source run. | Start from the step named after `Earliest step to rerun from:`. |
| ... `output_does_not_fit` | You changed an upstream return type. | Start the rerun from that upstream step. |
| ... `not_reusable` / `output_unavailable` | The output could not be saved, or was deleted. | Start earlier. |
| ... `source_run_active` | The source run is still going. | Wait or cancel it. |
| `--from and --only need --rerun-of` | Flags without a source run. | Add `--rerun-of RUN_ID`. |
| The rerun ran old behaviour | It did not: a rerun executes the current code. Reused steps keep their old outputs. | Start from the step whose code changed. |
| `Machine reruns are not supported; use a new key or retry an interrupted state entry` | Rerun of a state machine. | New key, or `ava run retry RUN_ID --entry`. |
| `--rerun-of` on a workflow with `key=`: `INVALID_ARGUMENT: missing run key field '<name>'`; or it prints the old run id and nothing starts; or `FAILED_PRECONDITION: keyed flow runs cannot be rerun from a step` | A keyed run cannot be rerun. The three answers are for: no key fields in `--input`, the key of an existing run, a new key. | Start a new run with a new key: keep an `attempt` (or date) field in `key=` and raise it. |

## Map, skip and branches

| Symptom | Cause | Fix |
|---|---|---|
| `ValueError: <step>: map_overflow input length 9 exceeds max_items 8 (raise with max_items=, hard cap 64)` | The list is longer than `max_items` (default 8). Nothing is truncated. | Set `max_items` to the largest expected length; above 64, map over chunks. |
| `ValueError: <step>: map_length_mismatch lengths [1, 2]` | Zipped lists of different lengths. | Same length, or pass one as a keyword to broadcast it. |
| `MapItemsFailed: <step>: MapItemsFailed [(1, <step>_1[1], ValueError: ...); ...]` | One or more items failed for good; a map has no partial result. | Catch inside the step and return an error field, or `ava.skip` the item. |
| The collected list is shorter than the input | Skipped items are left out; positions shift. | Carry an id in each item output. |
| A list passed by keyword to `.map` was not iterated | Keywords are broadcast unchanged to every item. | Pass lists to iterate positionally. |
| `ValueError: <step>: chained max_items may only lower inherited N` | A chained map tried to raise the bound. | Raise it on the first map. |
| A whole branch was skipped: `Upstream step '<slug>' was skipped` | A parameter that is not typed `X \| None` received a skipped output. | Expected for a branch; type the join step's parameters `X \| None` so it still runs. |
| The join step was skipped too | Its parameters do not accept `None`, or are not annotated. | Annotate them `X \| None`. |
| Both branches ran, or neither | The two skip conditions are not exact opposites. | Derive both from one value. |
| `ValueError: skip reason must not be blank` | `ava.skip("")`. | Give a reason or none. |

## Waits, events and approvals

| Symptom | Cause | Fix |
|---|---|---|
| A waiting run is `failed` after the operator restarted: `Operator restarted before the run finished; it was not resumed` (`error_code: unknown`) | The local operator does not resume flow runs that were running or waiting on a sleep, event or decision. | Rerun from the wait's slug (`--from sleep`, `--from wait_for_event`, or its `slug=`); a workflow with `key=` cannot be rerun and needs a new key value. For waits that must survive: a state machine, or Avalanche Cloud. |
| A wait started too early | Nothing links it to earlier steps; an unlinked wait starts with the run. | Chain it: `step >> wait`, or pass it a step result. |
| `NOT_FOUND: unknown_key` on `ava event send` | No `wait_for_event` with that key in the run's workflow. | Use the exact `key=`. |
| `INVALID_ARGUMENT: invalid_wait_submission` | The payload does not validate (strictly: `"5"` is not an `int`), or the address is not a decision of this run. | Fix the JSON; use the decision's `slug` or node id (`reviews[0]` for a mapped one). |
| `not_armed` (409) on a decision | The steps before it are still running. | Wait until the run is `waiting`. |
| `ALREADY_EXISTS: already_resolved_or_withdrawn` / `ALREADY_EXISTS: run_closed` (REST: 409) | Already answered or timed out; or the run ended (succeeded, failed or cancelled). The CLI prints the gRPC status name, not "conflict". | Nothing to do. |
| A decision or event timeout cannot be reached on the operator | There is no fast-forward for `ava.sleep`, `ava.wait_for_event` or `ava.decision` timeouts on an operator (`timers:fire-next` is for state machines). | Read the timeout from the run input or a constant you can set to seconds for a local check; or test in process with `advance_time`. |
| `idempotency_conflict` (409) | Same idempotency key, different payload. | New key for a new payload; same key only to resend. |
| `reason_required` (422) | Empty `--reason` on `ava run decide`. | Give a reason. |
| `unauthorized` (403) on a decision | The caller is not in `approvers`. On the local operator the caller is the OS user running the operator. | Fix `approvers`, or leave it `None` locally. |
| `DecisionTimeoutError: decision_timeout`, run failed, not retried | The decision timed out and has no `on_timeout`. | Pass `on_timeout=Outcome(...)` when silence has a safe answer. |
| The step after a wait got `None` | The event timeout passed. This is not an error. | Type the parameter `Model \| None` and handle both. |
| A rejected approval did not stop the run | A rejection is data. | The acting step reads the answer and returns, skips or raises. |
| `FAILED_PRECONDITION: Run <id> is not terminal` from `ava result` | The run is still running or waiting. | Add `--wait`, or answer what it waits for (`summary.waiting_on`). |
| A machine ignored an event | The current state neither accepts nor defers it. The receipt says `"status": "ignored"`; exit code is 0. | Read the receipt; check the state with `ava run show`. |
| `Invalid machine input/payload: <field>: missing` | Event data does not match the transition's `payload` model. | Send matching `--data`; `{}` when there is no payload. |
| `ava run history` prints `not_machine`; `ava run retry` says `is not a machine instance` | Those commands are for state machine runs. | For a flow use `ava run show` and a rerun. |
| `Timer fast-forward is available only in ava dev` | `timers:fire-next` under `ava operator`. | Run under `ava dev`, or use `machine.test(...).advance_time(...)`. |
| A state's `.after(...)` timer never fires while its work runs | No transition happens until the state's work ends. | Keep state work short. |

## Triggers and run keys

| Symptom | Cause | Fix |
|---|---|---|
| Started a keyed workflow again and got the old run id, even a `failed` one | One run per key value, forever, whatever its status. The new input is ignored, and a keyed run cannot be rerun from a step. | Put a field such as `attempt: int = 1` (or a date) in `key=` and start again with a new value. The new run starts from the first step. |
| `NOT_FOUND: 'Unknown machine key'` from `ava run show --flow ... --key` or `GET /runs:resolve`, for a run that exists | The key fields or values differ, or `key=` changed in the code since the run started. | Use the run id; resolve only runs started under the current `key=`. |
| `FAILED_PRECONDITION: Invalid machine input/payload: <field>: <error type>` when starting a workflow with `key=` | The input does not fit the input model. On a keyed workflow no run is created. | Fix the input. |
| `missing run key field '<name>'` | A start without a key field. | Supply it. |
| `RuntimeError: Keyed workflows require a run-capable scheduler host` | `workflow().run()` on a workflow with `key=`. | Use `workflow().test()` or the operator. |
| `RuntimeError: Declared RUN/MACHINE nodes require a scheduler host` | `workflow().run()` on a workflow with `ava.call`, `ava.start_run` or `machine.send`. | Use `workflow().test(targets=[...])` or the operator. |
| `INVALID_ARGUMENT: Input is missing required fields: a, b` | `ava run` or `POST /runs` without required input fields; no run is created. | Pass `--input`, or give the fields defaults. |
| Run `failed` at once, `error_code: input_invalid`, `error_message: Run failed`, no steps | On a workflow without `key=`: a wrong type, a broken constraint (`ge=`, `le=`) or an unknown field. `ava run` still printed a run id and exited 0. | The reason is only in the run log (procedure, step 5): `The run's input is invalid: <field>: <message> (<Model>)`. |
| Run `failed` before any step, `error_code: unknown`, `error_message: Run failed`, no steps; the log says `Workflow preparation failed: Workflow preparation exceeded 15.0s` | Each run starts in a new process that imports the flow file and builds the graph, and has 15 seconds to do it. Slow imports or work at module level, or a machine busy with many runs. The limit has no flag or setting. | Keep the flow file fast to import: import heavy libraries inside the steps that use them, do nothing at module level. Then start the run again. |
| Same, with `Workflow preparation failed: <ErrorType>: ...` and a traceback in the log | The flow file raised when the run's process imported it or built the graph (after it was discovered without error). | Fix the error the traceback names. |
| `--input must be a JSON object` / `Duplicate input field '<name>'` | Bad `--input`, or a field given twice across `--input`, `--file`, `--workspace`. | One JSON object; each field once. |
| Webhook answers `202` but nothing useful happened | Without `key=`, `202` is sent before the input is validated, even when a required field is missing; the run failed with `input_invalid`. | Check the run; model the sender's real payload. |
| Webhook answers `409 {"error": "workflow could not be started"}` | On a workflow with `key=`, any invalid body (missing key field, wrong type, broken constraint, unknown field). No run exists and the answer does not say why. | Send the same body with `ava run FLOW --input` to read the reason; fix the body or the input model. |
| Webhook `404 unknown webhook route` / `415` / `405` | Wrong path; content type is not `application/json`; not a `POST`. | `ava webhooks list` shows the URL. |
| `curl` to the webhook: connection refused | The listener starts only when a discovered workflow declares a webhook; it is on the webhook port (7434), not the web port. | `ava webhooks list`. |
| A `cron=` workflow never runs | A local schedule starts disabled. | `ava schedule enable SELECTOR [--input JSON]`. |
| A scheduled run used unexpected input | It uses the input stored at `schedule enable`, else the model's defaults; never a manual run's input. | `ava schedule list` shows the stored input. |
| Every scheduled run processes the same day or the same items | The stored input is replayed unchanged on every occurrence. | Keep dates and items out of the input: compute "today" and fetch the data in a source step. |
| Scheduled runs overlap, or were skipped during downtime | Cron does not wait for the earlier run and does not catch up. | Add `key=` on a period field; accept the gap or start a run by hand. |
| `child_run_failed: <id>` / `child_run_cancelled` / `child_run_timeout` on a parent | The child ended that way; the call node is not retried. `timeout` does not cancel the child. | Investigate the child run (`ava run links RUN_ID`); let children return a status instead of raising. |

## Agent and classifier steps

| Symptom | Cause | Fix |
|---|---|---|
| `ClassifierStepExecutionError: classifier step '<name>' failed: TYPESAFE_API_KEY is required to call a classifier` (after all attempts) | The provider's key is not in the environment of the operator process (nor in the nearest `.env`). | Set it where the operator runs, restart the operator. Same for `OPENAI_API_KEY`, `GEMINI_API_KEY`. |
| A model call fails for a missing key although the key is set in your shell | Keys are read by the process that runs the step: the operator, not the shell that ran `ava run`. | Export it before starting the operator. |
| `needs the Claude Agent SDK: install avalanche-ai[claude]` / `needs the OpenAI Agents SDK: install avalanche-ai[openai]` | Missing extra; found only when the step runs. | `uv add 'avalanche-ai[claude]'` (or `[openai]`, `[agents]`, `[typesafe]`, `[gemini]`). |
| `ImportError: Dataframe features require the optional dataframes dependency.` | Missing `dataframes` extra. | `uv add 'avalanche-ai[dataframes]'`. |
| `AgentStepCallError: agent step '...' called its agent more than once` | Two `await agent(...)` in one step. | Two agent steps. |
| Warning `finished without calling its agent`, no trace | The step body returned before awaiting the agent. | Fine for a guard (empty input). Otherwise call it once, or make it an `@ava.step`. |
| `AgentStepError: ... missing input fields ['x']; unexpected input fields ['y']` | The agent call's keywords do not match the signature's input fields. | Pass every input field by its exact name. |
| `AgentOutputError` (then retries) | The model's outputs do not match the signature types (strict). | Simplify the output types, describe fields with `desc`, check the trace. |
| `The JSPI backend does not support host directory mounts` | A PredictRLM step with `ava.File`/`ava.Workspace` fields on the default sandbox. | `interpreter=ava.agent.DirectPythonBackend()` or `sandbox_backend="sbx"`. |
| `runs on PredictRLM, which does not support ava.agent.Skill yet` (when the step runs) | `skills=[ava.agent.Skill]` on PredictRLM. | Use Claude or OpenAI, or `PredictRLM(skills=[...])`. |
| A misspelt `PredictRLM` option fails only at the first run | Unknown keywords are passed through. | Check option names in [../building-blocks/runtimes.md](../building-blocks/runtimes.md). |
| `AgentStepError: ... rename the signature's output or set narrate=False` | An output named `run_summary` on Claude/OpenAI. | Rename it. |
| `OpenAIAgent sends tool arguments and answers as OpenAI strict JSON schemas, which cannot express this step's types` | A tool or output type outside strict JSON schema. | Use plain models, lists, scalars. |
| The agent ignores the task and the UI shows generic instructions | The signature class has no docstring, so it inherits `ava.Signature`'s own. | Give every signature a docstring; give every field a `desc`. |
| Classifier call fails with only `ValidationError` | `state=` got a Pydantic model, or the questions are malformed. | `state=model.model_dump(mode="json")`. |
| `questions must be provided on the classifier call or as classifier_step defaults` | No questions anywhere. | Declare `questions=` or pass them in the call. |
| A `noul` used as a boolean, or a score used as an index | `noul` is a probability 0..1; a score is a float between levels. | Compare with thresholds in code. |
| No evaluation recorded | The run was in process (`workflow().run()`), or the agent call failed. | Evaluations run only on an operator, after a successful call. |
| Evaluation `failed`, run fine | Evaluations never fail the step; the record's `error` says why (often the missing provider key). | `GET /api/v1/runs/RUN_ID/evaluations`. |
| `AgentStepExecutionError: ... LM response cannot be serialized to a JSON object.` with `DummyLM` | `adapter=JSONAdapter()` missing on `DummyLM(...)` or `dspy.context(...)`. | Pass it to both. |
| `AgentStepExecutionError: ... Adapter JSONAdapter failed to parse the LM response` with `"answer": "No more responses"`, on a scripted (`DummyLM`) step | The script ran out. A scripted turn failed (its code raised, or the outputs did not validate), so the agent asked for another turn; or the step was retried and the retry got the replies that were left. This message hides the first error. | Print `lm.history[-1]["messages"][-1]["content"]`: the lines after `repl_history` show each turn's code and its `[Error] ...`. See [../run/testing.md](../run/testing.md#agent-and-classifier-steps). |
| Every turn of a PredictRLM step fails with `[Error] NameError: name '<Model>' is not defined` | `interpreter=ava.agent.DirectPythonBackend()` and a signature input field typed with a Pydantic model. | Type that field `dict` (or `list[dict]`) and pass `model.model_dump(mode="json")`, or use the default sandbox. |
| Items of a mapped PredictRLM agent step fail at start (sandbox health check, about 30 s) while one item alone works | Each item is its own agent call with its own sandbox process, and the items start together. | Map fewer items at once, or give one agent call the whole list. See [../orchestration/control-flow.md](../orchestration/control-flow.md#map). |

Reading what an agent did: `nodes[].trace.header` in `ava run show --json` (also in
`GET /api/v1/runs/RUN_ID`) has model, turns, usage and stop reason. The turns themselves are in
the web UI's Trace tab and in `provider.hydrate_trace(run_id, node_id)` (procedure, step 5).

## Integrations

| Symptom | Cause | Fix |
|---|---|---|
| `IntegrationNotConnectedError: step '<step>' uses integration '<name>' with no connected account. Connect with ...` (not retried) | No account stored for that connector. | `ava connect <name>`, then rerun from the failed step. |
| `IntegrationReconnectError` | An OAuth 2 login was revoked (`401`). | `ava connect <name>` again. |
| `ava connect NAME` refuses an unknown name | No discovered workflow declares it. | Declare it in a step first; check `ava connect`. |
| `IntegrationError: <Class>.client is only available inside a running step that declares the Connector` | A connector method called from a step that does not list it. | Add it to that step's `integrations=[...]`. |
| `IntegrationError: step '<name>' declares integrations [...], but nothing here stores accounts` | Run outside an operator. | Run on the operator, or wrap in `use_accounts(StaticAccounts({...}))`. |
| `IntegrationError` before a request is sent | An absolute URL to another host, or a redirect to one. | Use paths relative to `base_url`. |
| `MCPServerUnavailableError` (retried) | The MCP server did not answer within 30 seconds. | Check the URL or command. |
| `ProviderNotInstalledError`, or a `Connection*Error` | Built-in integrations need a provider and an assigned connection; not available on every account yet. | [../connect/integrations.md](../connect/integrations.md). |
| `IntegrationError: ... tools= and integration tools with the same names` | A step tool named like an integration tool. | Rename the step's tool. |

## Reading results with the CLI

| Symptom | Cause | Fix |
|---|---|---|
| `ava result RUN_ID --json` prints a Python traceback ending in `FAILED_PRECONDITION: Run <id> ended with status failed`, exit 1 | The run failed; only `succeeded` runs have a result. | Expected. Use `ava run show RUN_ID --json`. |
| `Provide --json or --output-dir PATH`, exit 2 | `ava result` without an output mode. | Add `--json`. |
| `FAILED_PRECONDITION: Requested step or state entry has no accepted result` | `--node` got a function name, or the step did not succeed. | Use the node id from `nodes[].node_id`: `<function>_1`. |
| The result is a tree of `{"kind": ..., "value": ...}` | `--json` prints a tagged value. | Read `value` recursively, or use `--output-dir DIR` for plain JSON and files (the folder must not exist). |
| `ava run FLOW` returned at once | It prints the run id when the run is accepted, not when it ends. | `ava result RUN_ID --wait --json` (default `--timeout` 300 s). |
| Statuses differ between tools | The CLI and REST say `succeeded`; the Python client's enum says `success`. Step statuses use `success`. | Compare accordingly. |
| `DEBUG` log lines are missing | Only `INFO` and above are captured. | Log at `INFO`. |
| `[activity truncated: worker log budget exhausted]` | The run passed 10,000 log lines or 4 MiB. | Log less; write details to a file in the workspace. |

Node id and slug are different names for different commands:

| | Example | Used by |
|---|---|---|
| Node id | `summarize_orders_1` | `ava result --node`, `nodes[].node_id`, `summary.error_step`, answering a held step |
| Slug | `summarize_orders` (or the step's `slug=`) | `ava run --rerun-of ... --from`, decision addresses, `Upstream step '<slug>' was skipped` |
| Selector | `orders.py::report_orders` | `ava run`, `ava flow show`, schedules, webhooks; the bare name works while it is unique |
| Flow slug (Cloud) | `report-orders` | Every `delta` command |

## Tests

| Symptom | Cause | Fix |
|---|---|---|
| `ExceptionGroup` containing `StepSkipped: <reason>` from `workflow().test(...)` | The test driver reports `ava.skip` as a failure. | Test skip-based branching with `workflow().run(...).result()`, or on the operator. |
| `ExceptionGroup` from `.test()`, `.send()`, `.decide()` or `.advance_time()` | A step failed on the test driver (after its retries). | `pytest.raises(ExceptionGroup)` and `group_contains(...)`. |
| A test that counts calls sees 8 | Steps retry in tests too. | `retries=0` on the step, or stub it. |
| `NonRetryableError: stub exhausted for step <name>` | Fewer stub values than calls. | One value per expected call. |
| `'Logger' object has no attribute 'info'` | `step.fn(...)` on a step with `log=ava.Logger()`. | Pass `log=logging.getLogger("test")`. |
| `RuntimeError: use machine.test_async/asend/aadvance_time inside an event loop` | `.test()` inside async code. | Use the async forms. |
| The driver's `.send(...)` or `.handle.send_event(...)` returns a receipt with `reason='ignored'` and the `ava.wait_for_event` keeps waiting | The test driver delivers state machine events only. | `workflow().run(events={...})` or `send_event` on a `run()` handle. With `key=` or `ava.call`: [../run/testing.md](../run/testing.md#workflows-that-run-cannot-start). |
| `KeyError: 'decision_1[0]'` from `driver.decide("reviews[0]", ...)`; or the step after a mapped decision gets one answer instead of a list | `workflow().test()` does not support `ava.decision.map`. | `workflow().run()` with `handle.decide("reviews[0]", ...)`, `decisions={"reviews[0]": ...}` or `advance_time`. |
| A step inside a child workflow returned a stub value | `stub=` replaces the step in every workflow the test runs, children included. | Stub only what should be replaced everywhere. |
| A sleep ended but a decision or event timeout did not | `fast_timers=True` skips sleeps only. | `advance_time(...)`. |
| A coroutine instead of a value from `step.fn(...)` | The step is `async def`. | `asyncio.run(step.fn(...))`. |

## Avalanche Cloud

| Symptom | Cause | Fix |
|---|---|---|
| 503 `durable_runs_org_unconfigured` | The organization is not enabled (private preview). | Nothing to fix in code. |
| `Dependencies require uv.lock. Run uv lock and retry.` | Missing or stale lock. | `uv lock`, then deploy. |
| `No Avalanche workflows were discovered for deployment.` | No flow found by the project's Python. | `uv sync`; check `[flows] paths` and `[tool.avalanche]`. |
| `Flow slug collision` | Two workflow names give the same slug. | Rename one. |
| `dependency_sdist_only:<name>` / `dependency_platform_unsupported:<name>` | A dependency without a locked binary wheel for the runtime. | Replace or pin it. |
| A step cannot read a variable, or fails with `env_unavailable` | The name is not declared in `[tool.delta.env]`, or declared but no value is set. | Declare it and `delta env set NAME --secret`. |
| A step fails writing next to its code | The deployed package is read-only. | Write in an `ava.Workspace`, or `/tmp` for scratch. |
| A step that worked locally cannot find a file from an earlier step | Each step runs in its own sandbox. | Pass an `ava.File` or `ava.Workspace`, never a path. |
| `step_timeout_exceeds_runtime_max`, or a step cut at 60 s | Step budgets: 60 s default, 3600 s maximum. | `[tool.delta.timeouts.<flow-slug>]`; split longer work. |
| `delta runs start price_orders` not found | Cloud addresses the flow slug. | `price-orders`. |
| 409 `template_trial_frozen` / `template_trial_exhausted` | A template trial project. | [../run/templates-and-sharing.md](../run/templates-and-sharing.md). |
| `delta login` hangs | It needs a person. | Ask the user to sign in, or to provide `DELTA_TOKEN`. |

## See also

- [../reference/errors.md](../reference/errors.md): every error type and code.
- [../run/local.md](../run/local.md): the commands used above.
- [../orchestration/retries.md](../orchestration/retries.md): retries, reruns, run keys, statuses.
- [../run/testing.md](../run/testing.md): reproducing a failure without an operator.
- [patterns.md](patterns.md), [choose-a-feature.md](choose-a-feature.md).
