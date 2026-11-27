# Python API reference

Every public name of the `avalanche` package (version 0.20.39) a workflow author uses: kind, signature with defaults, meaning, and the file that explains it. Convention: `import avalanche as ava`.

How to read it: `ava.x` is importable from the top-level package. Submodules (`ava.agent`, `ava.runtimes`, `ava.classifier`, `ava.classifiers`, `ava.catalog`, `ava.integrations`, `ava.evaluations`) load on first attribute access, so `ava.runtimes.PredictRLM` works after `import avalanche as ava` alone.

| Topic | Section | Explained in |
| --- | --- | --- |
| Workflows, inputs, context | [Workflows](#workflows) | [workflows.md](../building-blocks/workflows.md), [inputs-and-files.md](../building-blocks/inputs-and-files.md) |
| Steps, composition, map | [Steps](#steps) | [python-steps.md](../building-blocks/python-steps.md), [control-flow.md](../orchestration/control-flow.md) |
| Retries, skip | [Retries and skips](#retries-and-skips) | [retries.md](../orchestration/retries.md) |
| Sleep, events, decisions | [Waits](#waits) | [waits.md](../orchestration/waits.md), [approvals.md](../orchestration/approvals.md) |
| Starting other runs, run handle, reruns | [Runs](#runs) | [control-flow.md](../orchestration/control-flow.md), [testing.md](../run/testing.md) |
| State machines | [State machines](#state-machines) | [state-machines.md](../building-blocks/state-machines.md) |
| Files and workspaces | [Files and workspaces](#files-and-workspaces) | [inputs-and-files.md](../building-blocks/inputs-and-files.md) |
| Agent steps | [Agent steps](#agent-steps) | [agent-steps.md](../building-blocks/agent-steps.md), [tools-and-skills.md](../building-blocks/tools-and-skills.md) |
| Runtimes | [Runtimes](#runtimes) | [runtimes.md](../building-blocks/runtimes.md) |
| Classifier steps | [Classifier steps](#classifier-steps) | [classifier-steps.md](../building-blocks/classifier-steps.md) |
| Evaluations | [Evaluations](#evaluations) | [evaluations.md](../building-blocks/evaluations.md) |
| Integrations | [Integrations](#integrations) | [integrations.md](../connect/integrations.md) |
| Triggers | `cron=`, `webhook=`, `ava.Webhook` under [Workflows](#workflows) | [triggers.md](../connect/triggers.md) |
| Exceptions | every section; all in one place in [errors.md](errors.md) | [errors.md](errors.md) |

A complete example that uses the core names and runs without a model key:

```python
from datetime import timedelta

import avalanche as ava


class ReportInput(ava.BaseInput):
    names: list[str]
    greeting: str = "Hello"


class Approval(ava.Event):
    approved: bool


@ava.source
def list_names(report: ReportInput) -> list[str]:
    """List the names from the run input."""
    return report.names


@ava.step
def greet_person(name: str, greeting: str, *, logger=ava.Logger(), run: ava.RunContext) -> str:
    """Greet one person."""
    logger.info(f"greeting {name} in {run.step_key}")
    return f"{greeting} {name}"


@ava.step
def count_names(names: list[str]) -> int:
    """Count the names."""
    return len(names)


@ava.dest
def join_report(greetings: list[str], count: int, approval: Approval) -> str:
    """Join the greetings into one report."""
    return f"{count}: {', '.join(greetings)} approved={approval.approved}"


@ava.workflow(input=ReportInput)
def build_report():
    """Greet every name in parallel, count them, ask for approval, join."""
    names = list_names()
    greetings = greet_person.map(names, greeting=ava.input.greeting, max_items=4)
    approval = ava.decision(Approval, prompt="Send it?", timeout=timedelta(hours=1), show=names)
    return (greetings & count_names(names) & approval) >> join_report()


test = build_report().test({"names": ["Ada", "Lin"]})
test.decide("decision", {"approved": True}, reason="ok")
print(test.handle.result())  # 2: Hello Ada, Hello Lin approved=True
```

## Workflows

```python
ava.workflow(fn=None, *, cron: str | None = None, webhook: ava.Webhook | bool | None = None,
             input: type | None = None, key: tuple[str, ...] | None = None,
             output: type[BaseModel] | None = None, emits: tuple[str, ...] = (),
             context: type | None = None, ctx: type | None = None,
             agent_defaults: AgentDefaults | Mapping[str, object] | None = None,
             classifier_defaults: dict | None = None)
ava.Webhook(path: str | None = None)
ava.input            # InputRef: ava.input.<field>[.<field>...]
class ava.BaseInput(pydantic.BaseModel)    # extra="forbid"
class ava.BaseContext(pydantic.BaseModel)  # extra="forbid"
```

| Name | Kind | Meaning |
| --- | --- | --- |
| `ava.workflow` | decorator, bare or with options | Turns a function that composes steps into a workflow. Calling the decorated function builds and returns a `Workflow`; it does not run it. The function name is the workflow name; the value it returns is the workflow's output. |
| `ava.pipeline` | alias | The same object as `ava.workflow`. Legacy name; write `ava.workflow`. |
| `ava.Workflow` | class | The built graph. Authors get it from `my_workflow()`; they do not construct it. See [Runs](#runs) for its methods. |
| `ava.Pipeline` | alias | The same class as `ava.Workflow`. Legacy name. |
| `ava.Webhook` | frozen dataclass | `webhook=ava.Webhook("/orders/new")` gives the workflow's `POST` webhook a fixed path. `path` must be an absolute URL path without query, fragment, empty, `.` or `..` segments, at most 1024 characters, else `ValueError`. |
| `ava.input` | value | A reference to the run input, usable as a step argument inside a workflow body: `ava.input.order_id`. Resolved when the step runs. Immutable. |
| `ava.BaseInput` | base class | Base of a workflow's input model. Unknown fields are refused. |
| `ava.BaseContext` | base class | Base of a workflow's context model (`context=`). Unknown fields are refused. |
| `ava.RunContext` | class (`BaseContext`) | Run metadata a step receives by annotating a parameter with it. See [Steps](#steps). |
| `ava.BaseModel` | re-export | `pydantic.BaseModel`, for step input and output models. |
| `ava.Json` | marker class | Marks a pydantic field to be stored as a JSON string column when models are converted to dataframes (`dataframes` extra). |

`ava.workflow` options:

| Option | Type | Default | Meaning |
| --- | --- | --- | --- |
| `cron` | `str` | `None` | cron expression; the operator starts a run on that schedule once the schedule is enabled |
| `webhook` | `ava.Webhook`, `True`, `False`, `None` | `None` | `True` enables a `POST` webhook at the default path; `ava.Webhook(path)` at `path`. Any other value: `TypeError: webhook must be ava.Webhook, True, False, or None` |
| `input` | `ava.BaseInput` subclass | `None` | model the run input is validated against at start. Other type: `TypeError: workflow input type must inherit from ava.BaseInput` |
| `key` | tuple of input field names | `None` | makes run starts idempotent: one run per distinct value of these fields. Empty tuple: `ValueError: workflow key must contain at least one input field`. Unknown field: `ValueError: workflow key fields must be declared input fields` |
| `output` | pydantic model | `None` | model the workflow's returned value is validated against |
| `emits` | tuple of event names | `()` | named events the workflow's returned step may emit; must equal that step's `emits`, else `ValueError: workflow emits must match its returned node emits` |
| `context` | `ava.BaseContext` subclass | `None` | model the run context is validated against |
| `ctx` | same | `None` | alias of `context`; giving two different types is a `ValueError` |
| `agent_defaults` | `{"runtime": <runtime>}` or `ava.agent.AgentDefaults` | `None` | runtime shared by the workflow's agent steps. Only the key `runtime` is accepted |
| `classifier_defaults` | dict with `classifier`, `model`, `timeout` | `None` | defaults for classifier steps and evaluations; a value of `None` is refused, omit the key instead |

Build-time errors of a workflow: a cycle (`ValueError: Workflow 'name' contains a cycle involving: ...`), two `wait_for_event` with the same key (`ValueError: Duplicate event consumer key in workflow`), two different steps with the same explicit `slug=` (`ValueError: Duplicate node slug ...`).

## Steps

```python
ava.source(fn=None, *, num_returns: int = 1, slug: str | None = None,
           retries: int = 7, on_uncertain: Literal["retry", "hold", "fail"] = "retry")
ava.step(fn=None, *, num_returns: int = 1, slug: str | None = None,
         retries: int = 7, on_uncertain: Literal["retry", "hold", "fail"] = "retry",
         emits: tuple[str, ...] = (), integrations: Sequence[Integration] = ())
ava.dest(fn=None, *, num_returns: int = 1, slug: str | None = None,
         retries: int = 7, on_uncertain: Literal["retry", "hold", "fail"] = "retry")

node(*args, **kwargs) -> NodeFuture                       # only inside a workflow body
node.map(*mapped, max_items: int | None = None, fail_fast: bool = True, **broadcast) -> MapFuture
future_a >> future_b      # b after a
future_a & future_b       # a and b in parallel
future[i]                 # element i of a num_returns > 1 step
ava.Logger(name: str | None = None, level: int | None = None)
```

| Name | Kind | Meaning |
| --- | --- | --- |
| `ava.source` | decorator | A step that brings data in. Shown as a source in the graph. Takes no `emits` or `integrations`. |
| `ava.step` | decorator | A plain Python step. Sync or `async def`. |
| `ava.transform` | alias | The same object as `ava.step`. Legacy name; write `ava.step`. |
| `ava.dest` | decorator | A step that sends data out. Shown as a destination in the graph. Takes no `emits` or `integrations`. |
| node call | `Node.__call__` | Inside a workflow body, calling a decorated step declares one invocation and returns a `NodeFuture`. A `NodeFuture` passed as an argument makes that step run first and passes its output. Outside a workflow body: `RuntimeError: Node 'x' invoked outside of workflow context.` |
| `.map` | `Node.map` | Run the step once per item of the positional lists, in parallel, and gather the outputs as one list in order. Keyword arguments are passed unchanged to every item. Returns `ava.MapFuture`. |
| `>>` | operator | `a >> b`: `b` runs after `a`. When `b` was called without arguments it receives `a`'s output as its first positional input. Returns a future for the chain; a workflow that returns a chain returns the last step's output. |
| `&` | operator | `a & b`: independent branches. `(a & b) >> c` passes both outputs to `c` positionally, in branch order. |
| `future[i]` | indexing | Selects element `i` of a step declared with `num_returns > 1`. |
| `ava.MapFuture` | class | The future `.map` returns. Passing it to another `.map` chains the maps item by item. |
| `ava.Logger` | parameter marker | `def my_step(x, *, logger=ava.Logger())` injects a logger whose records carry the step and run. Methods `debug`, `info`, `warning`, `error`, `critical`, `exception` take one message string (no `%s` arguments). |
| `ava.RunContext` | injected by annotation | `def my_step(x, run: ava.RunContext)`. Fields: `run_id`, `workflow_name`, `executor_type`, `rerun`, `node_id`, `node_name`, `node_slug`, `metadata`. Property `step_key`: `"<run_id>:<node_id>"`, stable across retries; use it as an idempotency key for outside calls. |

Step decorator options:

| Option | Default | Meaning |
| --- | --- | --- |
| `num_returns` | `1` | number of values the step returns as a tuple, each addressable with `future[i]` |
| `slug` | function name | stable name of the step for reruns (`--from SLUG`), decisions and the UI. A second invocation of the same step gets `<slug>_2` |
| `retries` | `7` | extra attempts after a failure. At most 7 are applied, so a step runs at most 8 times. `0` disables retries |
| `on_uncertain` | `"retry"` | what to do when the step raises `ava.OutcomeUnknownError`: `"retry"` retries like any error, `"fail"` fails at once, `"hold"` parks the step until a person reconciles it (the step needs a return annotation) |
| `emits` | `()` | (`ava.step` only) names of the events the step may return with `ava.event(name, output)`. Unique, non-empty. A step annotated `-> ava.Event[...]` without `emits`: `ValueError: Event-returning steps must declare emits` |
| `integrations` | `()` | (`ava.step` only) `ava.Connector` instances whose tools the step calls as methods. MCP servers and built-ins are for agent steps only |

How a step's parameters are filled:

| Parameter | Filled with |
| --- | --- |
| passed in the call (`step(a, b=c)`) | the value, or the output of the `NodeFuture` |
| left open and fed by `>>` | upstream outputs, positionally, in order |
| annotated with the workflow's `ava.BaseInput` subclass and left open | the run input |
| annotated with `ava.RunContext` or the workflow's `ava.BaseContext` subclass and left open | the run metadata or the run context |
| default `ava.Logger()` | a logger |

Do not annotate a data parameter with an `ava.BaseInput` subclass: such a parameter is reserved for the run input, and passing an upstream value to it fails with `TypeError: multiple values for argument`.

`.map` rules:

| Rule | Detail |
| --- | --- |
| mapped arguments | positional; each a list, a `NodeFuture` that yields a list, or an `ava.input` reference; lists are zipped and must have equal lengths |
| `max_items` | most items allowed, default `ava.DEFAULT_MAX_ITEMS` (8), from 0 to `ava.HARD_CAP` (64); more items fail the map with `map_overflow`. Out of range: `ValueError: x: max_items must be between 0 and 64` |
| `fail_fast` | `True` (default): the first item that fails for good fails the map. `False`: the other items finish, then the map raises `ava.MapItemsFailed` |
| chained maps | a map fed by a `MapFuture` inherits its `fail_fast`; its `max_items` may only be lower |
| no mapped argument | `ValueError: x: map requires at least one mapped argument` |

| Name | Kind | Meaning |
| --- | --- | --- |
| `ava.DEFAULT_MAX_ITEMS` | `int` = `8` | default `max_items` of a map |
| `ava.HARD_CAP` | `int` = `64` | highest `max_items` |
| `ava.MapItemFailure` | frozen dataclass `(index: int, child_id: str, error: Exception)` | one failed item |
| `ava.MapItemsFailed` | `RuntimeError`, attribute `failures: tuple[MapItemFailure, ...]` | raised by a map with failed items; message `name: MapItemsFailed [(index, child_id, Type: message); ...]` |

## Retries and skips

```python
class ava.NonRetryableError(Exception)
class ava.OutcomeUnknownError(Exception)
ava.skip(reason: str | None = None) -> NoReturn
class ava.StepSkipped(Exception)      # attribute: reason
```

| Name | Kind | Meaning |
| --- | --- | --- |
| `ava.NonRetryableError` | exception | Raise it (or a subclass) from a step to fail the step at once, without the remaining retries. |
| `ava.OutcomeUnknownError` | exception | Raise it when an outside call may or may not have happened. The step's `on_uncertain` decides: retry, fail, or hold. |
| `ava.skip` | function | Ends the current step as skipped, with an optional reason (at most 1000 characters, not blank). Not a failure and never retried. |
| `ava.StepSkipped` | exception | What `ava.skip` raises. Do not catch it: `except Exception` around `ava.skip()` cancels the skip. |

A step that depends on a skipped step is skipped too, unless the parameter that would receive the skipped output is annotated to accept `None` (`x: Report | None`); then it runs and receives `None`.

## Waits

```python
ava.sleep(duration: timedelta | float | None = None, *, until: datetime | None = None,
          slug: str | None = None) -> NodeFuture
ava.wait_for_event(model: type[BaseModel], *, key: str, timeout: timedelta | float | None,
                   slug: str | None = None) -> NodeFuture
ava.decision(outcome: type[ava.Event], *, prompt: str, timeout: timedelta | float | None,
             show: NodeFuture | JsonValue | BaseModel = None, on_timeout: ava.Event | None = None,
             assignees: list[str] | tuple[str, ...] | None = None,
             approvers: list[str] | tuple[str, ...] | None = None,
             slug: str | None = None) -> NodeFuture
ava.decision.map(outcome: type[ava.Event], items: NodeFuture | list, *, prompt: str,
                 timeout: timedelta | float | None, on_timeout: ava.Event | None = None,
                 assignees=None, approvers=None, max_items: int = 8, slug: str | None = None,
                 show: Callable[[Item], BaseModel] | None = None) -> NodeFuture
class ava.Event[T = JsonValue](BaseModel)   # fields: name: str | None = None, output: T = None
ava.event(name: str, output: T) -> ava.Event[T]
```

Waits are declared in the workflow body, like steps. No worker is held while a run waits.

| Name | Kind | Meaning |
| --- | --- | --- |
| `ava.sleep` | function | Pause for `duration` (seconds or `timedelta`) or until `until` (a timezone-aware `datetime`). Exactly one of the two, else `ValueError: sleep requires exactly one of duration or until`. |
| `ava.wait_for_event` | function | Pause until an event with `key` is delivered to the run. Its output is an instance of `model`, or `None` on timeout. `timeout` is required; pass `None` to wait without limit. Event keys are unique per workflow. |
| `ava.decision` | callable | Pause until a person answers. `outcome` is an `ava.Event` subclass whose own fields are the form; the step's output is an instance of it. `prompt` is the question. `show` is the data shown to the reviewer: a step output, a model or JSON. `timeout` is required (`None` waits without limit). On timeout the output is `on_timeout` (an instance of `outcome`); without `on_timeout` the step fails with `decision_timeout`. `assignees` names who is asked; `approvers`, when given, is the list of names allowed to answer (anyone else is refused as `unauthorized`). |
| `ava.decision.map` | method | One decision per item of `items`, at most `max_items`. `show` is a function from one item to a pydantic model shown for that item. Output: the list of answers. |
| `ava.Event` | generic pydantic model, frozen | Two uses. Subclass it with your own fields for a decision's outcome. Or return `ava.event(name, output)` from a step to emit a named event that drives a state machine. |
| `ava.event` | function | Builds a named event: `ava.event("approved", order)`. The step must list the name in `emits=`; an undeclared name fails with `NonRetryableError: undeclared event 'x'; declares (...)`. |

## Runs

```python
ava.start_run(target, input: BaseInput | dict | NodeFuture | None = None, *,
              when: bool | NodeFuture = True) -> NodeFuture          # output: ava.RunStart | None
ava.call(target, input: BaseInput | dict | NodeFuture | None = None, *,
         timeout: timedelta | float | None = None, when: bool | NodeFuture = True) -> NodeFuture
class ava.RunStart(BaseModel)   # run_id: str, key: list | None = None, created: bool,
                                # read_state: Literal["retained", "expired"] = "retained"

workflow.run(executor=None, hooks=None, input=None, context=None, run_id: str | None = None,
             rerun=None, execution_services=None, *, planned_rerun=None, fast_timers: bool = False,
             events: dict | None = None, decisions: dict | None = None,
             principal=LocalPrincipal()) -> ava.RunHandle
workflow.test(input=None, *, stub: Mapping[Node, list] | None = None, targets: tuple = ()) -> WorkflowTest
await workflow.test_async(input=None, *, stub=None, targets=())
workflow.key(input) -> tuple
class ava.Rerun(BaseModel)      # run_id: str, start: tuple[str, ...],
                                # mode: Literal["autorun", "lazy"] = "autorun",
                                # deployment_id: str | None = None, row_sources: dict[str, str] = {}
```

`target` is a workflow or machine (the decorated function), its name as a string, or a `NodeFuture` that yields the name.

| Name | Kind | Meaning |
| --- | --- | --- |
| `ava.start_run` | function, workflow body only | Start another flow or machine run and continue without waiting. Output: `ava.RunStart`. With `when=False` nothing is started and the output is `None` (the step shows as skipped with reason `when=False`, and later steps still run). Outside a workflow body: `TypeError: ava.start_run is workflow-only; use ava run or a normal run client`. |
| `ava.call` | function, workflow body only | Start another run and wait for its output, which becomes this step's output, validated against the child's output type. For a keyed child whose key already has a run, attaches to that run. `timeout` stops the wait, not the child. A child that fails, is cancelled or times out fails the step with `child_run_failed`, `child_run_cancelled` or `child_run_timeout`. |
| `ava.RunStart` | model | `run_id` of the started run, its `key`, `created` (false when a keyed run already existed), `read_state`. |
| `Workflow.run` | method | Run the workflow in this process and return a `RunHandle` at once. For scripts and tests of simple flows. Refused for a workflow with `key=`, `ava.start_run`, `ava.call` or machine sends (`RuntimeError`); use `.test()` or an operator. `input` and `context` take a dict or a model instance. `events` and `decisions` preload answers by event key and decision slug. `fast_timers=True` makes `ava.sleep` steps elapse immediately (timeouts of events and decisions are not shortened). |
| `Workflow.test` / `test_async` | method | Run the workflow in memory with fake time, stubs and an in-memory host. Returns a driver with `handle`, `send`, `decide`, `advance_time` (see [State machines](#state-machines)). `stub` maps a step to the list of values its successive executions return instead of running. `targets` registers the flows and machines that `ava.start_run`/`ava.call` name. |
| `Workflow.key` | method | The run key derived from an input, for a workflow with `key=`. |
| `ava.RunHandle` | class | Handle of a `Workflow.run`. Awaitable. |
| `ava.Rerun` | model | Asks for a rerun of part of a previous run: `run_id` of that run, `start` step slugs, `mode` `"autorun"` (those steps and everything after) or `"lazy"` (only those steps). Passed as `rerun=` to a run start; the CLI builds it from `--rerun-of`, `--from`, `--only`. |

`ava.RunHandle` methods:

| Method | Meaning |
| --- | --- |
| `result(timeout: float \| None = None)` | wait for and return the output; raises the run's failure |
| `exception(timeout=None)` | the failure, or `None` |
| `await handle` | same as `result()` from async code |
| `running()`, `done()`, `cancelled()`, `cancel_requested()` | state |
| `cancel() -> bool` | request cooperative cancellation |
| `send_event(key, payload, *, idempotency_key, principal=...)` | deliver an event to a `wait_for_event` |
| `decide(node, payload, *, idempotency_key, reason, principal=...)` | answer a decision; `node` is its slug or node ID |
| `advance_time(duration: timedelta)` | test helper: move wait deadlines forward |
| `wait_until_waiting(timeout: float = 5.0) -> bool` | block until the run is parked on a wait |
| `canonical_node(node)`, `execution_receipts(timeout=None)` | node ID for a slug; host bookkeeping |

## State machines

```python
ava.machine(*, input: type[ava.BaseInput], key: tuple[str, ...], cron: str | None = None,
            webhook: ava.Webhook | bool | None = None)
ava.state(work, *, initial: bool = False, final: bool = False,
          defer: list[str] | tuple[str, ...] = ()) -> State
state.on(name: str, *, payload: type[BaseModel] | None = None) >> other_state
state.after(delay: timedelta) >> other_state

machine.test(input, *, stub: Mapping[Node, list] | None = None) -> MachineTest
await machine.test_async(input, *, stub=None)
machine.send(key, event, payload=None, idempotency_key=None, *, host=None, when=True)
machine.key(input) -> tuple
machine.spec
```

| Name | Kind | Meaning |
| --- | --- | --- |
| `ava.machine` | decorator, options required | Turns a builder function into an `ava.Machine`: a long-lived run that moves between states. `input` (an `ava.BaseInput` subclass) and `key` (input field names; one machine run per key) are required. `cron` and `webhook` as for workflows. |
| `ava.state` | function, machine builder only | Declares a state. `work` is an `ava.step` or an `ava.workflow` that runs each time the state is entered. Exactly one state has `initial=True`; a `final=True` state ends the run when its work is done. `defer` lists event names to keep for a later state instead of dropping. Each state must be assigned to its own local variable; the variable name is the state name. Outside a builder: `RuntimeError: ava.state must be used inside an @ava.machine builder`. |
| `state.on(name, payload=Model) >> target` | transition | On event `name`, go to `target`. `payload` is the model the event's data must fit. The event comes from outside (`ava machine send`, `machine.send`) or from the state's own work returning `ava.event(name, output)`. |
| `state.after(delay) >> target` | timer | Go to `target` when `delay` has passed in this state. |
| `ava.Machine` | class | What `@ava.machine` returns. |
| `Machine.test` / `test_async` | method | Run the machine in memory. Returns a `MachineTest`. |
| `Machine.send` | method | Inside a workflow body: declares a step that sends `event` to the machine run with `key`; `when=False` skips it. Outside a workflow it needs `host=` and an `idempotency_key` (1 to 256 printable characters), and returns a coroutine. |
| `Machine.key`, `Machine.spec` | method, property | The key for an input; the machine's declared states and transitions. |
| `ava.MachineHost` | protocol | What `host=` must implement: `async send(machine, key, event, payload, idempotency_key) -> MachineEventReceipt`. |
| `ava.MachineEventReceipt` | model | `receipt_id`, `key`, `accepted_at`, `duplicate`, `status` (`"accepted"` or `"ignored"`). |
| `ava.MachineInstance` | model | A machine run as a host reports it: `machine`, `key`, `run_id`, `deployment_id`, `status`, `state`, `entry`, `version`, `accepts`, `due`, `finished`. |

Builder errors (all `ValueError`): `machine requires exactly one initial state`, `each state must be assigned a unique builder-local name`, `duplicate machine work name 'x'`, `unknown target state...`. An event that no state accepts gives a `MachineDiscoveryWarning`.

Test driver (`MachineTest`, and `WorkflowTest` from `Workflow.test`):

| Member | Meaning |
| --- | --- |
| `state` | current state name (machines) |
| `history` | list of `(state, event, source)` transitions (machines) |
| `handle` | an `ava.RunHandle`; `handle.done()`, `handle.result()` |
| `send(name, payload=None)` / `await asend(...)` | deliver an event, then run until the next wait |
| `decide(node, payload, *, reason)` / `await adecide(...)` | answer a decision |
| `advance_time(duration)` / `await aadvance_time(...)` | move fake time forward; fires sleeps, timeouts and timers |

Inside a running event loop use the `test_async`, `asend`, `adecide`, `aadvance_time` forms; the sync forms raise `RuntimeError`.

## Files and workspaces

```python
class ava.File(BaseModel)       # frozen; fields: ref, sha256, size, name, content_type
ava.File.from_path(path: str | Path, *, content_type: str | None = None,
                   workspace: ava.Workspace | None = None) -> ava.File
file.path -> Path
file.open(mode: str = "rb", **kwargs)
file.read_bytes() -> bytes

class ava.Workspace(BaseModel)  # field: reference: str | None = None
ava.Workspace.from_path(path: str | Path) -> ava.Workspace
ava.Workspace.from_directory(path: str | Path, *, reference: str) -> ava.Workspace
workspace.path -> Path
```

| Name | Kind | Meaning |
| --- | --- | --- |
| `ava.File` | model | A reference to one file. Use it as an input field type, a step parameter type and a step return type; the bytes never travel in the run's data. |
| `File.from_path` | classmethod | Make a `File` from an existing regular file, without reading it. Return it from a step to pass a file on. |
| `File.path` | property | Local path of the file inside a step. `RuntimeError: file reference is not bound to a host path` when used outside a step. |
| `File.open`, `File.read_bytes` | methods | Read the file. `open` accepts `"r"`, `"rb"`, `"rt"` only. |
| `File.name`, `size`, `content_type`, `sha256` | fields | file name, bytes, media type, digest |
| `File.bind`, `File.snapshot` | methods | used by hosts; not needed in steps |
| `File(content=...)`, `File.content` | deprecated | use `File.from_path()` and `read_bytes()` |
| `ava.Workspace` | model | A reference to a directory the host keeps. Use it as an input field or step parameter type to give a step, or an agent, a folder to read and write. |
| `Workspace.path` | property | The directory, inside a step. |
| `Workspace.from_path` | classmethod | Copy a directory once into a host-owned folder and refer to the copy. |
| `Workspace.from_directory` | classmethod | Refer to an existing host directory without copying it. |
| `Workspace.is_reference`, `manifest()`, `from_manifest()`, `snapshot()` | members | wire form; used by hosts |

## Agent steps

```python
ava.agent_step(signature, *, runtime=None, tools: Sequence[Callable] = (),
               skills: Sequence[ava.agent.Skill] = (), integrations: Sequence[Integration] = (),
               evaluations: ava.Evaluations | None = None, retries: int = 7,
               on_uncertain: Literal["retry", "hold", "fail"] = "retry")
ava.agent.step(signature, *, inputs: Mapping[str, Any] | None = None, slug: str | None = None,
               runtime=None, tools=(), skills=(), integrations=(), evaluations=None,
               retries: int = 7, on_uncertain="retry") -> NodeFuture
ava.agent.step.map(signature, *mapped, max_items: int | None = None, fail_fast: bool = True,
                   slug=None, runtime=None, tools=(), skills=(), integrations=(),
                   evaluations=None, retries: int = 7, on_uncertain="retry", **broadcast) -> MapFuture
class ava.Signature                       # subclass it, or call ava.Signature("a: str -> b: str", "Instructions.")
ava.InputField(**kwargs); ava.OutputField(**kwargs)
await agent(**inputs) -> ava.agent.AgentResult      # agent: ava.Agent
class ava.agent.Skill(BaseModel)          # name, description, instructions, files={}, license=None,
                                          # compatibility=None, metadata={}, allowed_tools=None
ava.agent.Skill.from_dir(path: Path | str) -> Skill
```

| Name | Kind | Meaning |
| --- | --- | --- |
| `ava.agent_step` | decorator, module level | A step with a body that receives an agent. The function must have a keyword-only parameter `agent: ava.Agent` without default, and calls `await agent(field=value, ...)` once with the signature's input fields. The first argument, the signature, is required. Cannot be used inside a workflow body. |
| `ava.agent.step` | function | Inside a workflow body: declares an agent step without a body. `inputs` maps signature input fields to values or futures; the output is the signature's output field (a tuple when it has several). Outside a workflow body it behaves as `ava.agent_step`. |
| `ava.agent.step.map` | method | One agent run per item: the positional `mapped` lists fill the signature's input fields in order, keywords are passed to every item. Same `max_items` and `fail_fast` as `.map`. |
| `ava.Signature` | class | The agent's contract: instructions, typed inputs and outputs. Class form: subclass with a docstring (the instructions) and `InputField()` / `OutputField()` attributes. String form: `ava.Signature("request: str -> greeting: str", "Instructions.")`. At least one output field is required. Needs the `agents` extra. |
| `ava.InputField`, `ava.OutputField` | functions | Field declarations for a class-form signature; `desc="..."` describes the field to the model. |
| `ava.Agent` | class | The injected agent. `await agent(**inputs)` runs the model and returns an `AgentResult`. One call per step execution; a second raises `AgentStepCallError`. Property `called`. |
| `ava.agent.AgentResult` | model | `outputs`: a model with one attribute per signature output field. `trace`: the `AgentTrace`. |
| `ava.agent.AgentTrace` | model | `header` (`status`, `stop_reason`, `model`, `duration_ms`, `usage`, `turn_count`, `tool_call_count`, ...) and `turns` (each with `index`, `message`, `reasoning`, `tool_calls`, `duration_ms`, `usage`). |
| `ava.agent.Skill` | model | An Agent Skill: `name` (lowercase letters, digits, single hyphens, at most 64), `description` (at most 1024), `instructions` (Markdown), `files` (path inside the skill -> local file). Passed in `skills=`. |
| `Skill.from_dir` | classmethod | Load a folder holding `SKILL.md`; the frontmatter `name` must equal the folder name. Method `skill_md()` renders the file text. |
| `ava.agent.AgentDefaults` | frozen dataclass `(runtime=None)` | The typed form of `agent_defaults=`. |
| `ava.agent.capture_agent_evidence` | context manager `(listener, *, errors="ignore")` | Calls `listener` with each live event and the final trace of agent calls made inside the block. `errors="raise"` lets a listener error propagate. Event types: `AgentEvidenceEvent`, `AgentTraceFinishedEvent`, `AgentTraceUnavailableEvent`; aliases `AgentEvidenceListener`, `AgentEvidenceObserverEvent`, `AgentInvocationId`, `ListenerErrorPolicy`. |
| `ava.agent.DirectPythonBackend` | re-export | PredictRLM's unsandboxed Python backend, for `PredictRLM(interpreter=...)`. |
| `ava.agent.File` | deprecated | use `ava.File` |

Agent step options:

| Option | Default | Meaning |
| --- | --- | --- |
| `signature` | required | `ava.Signature` subclass or string-form signature |
| `runtime` | workflow's `agent_defaults` runtime, else `ava.runtimes.PredictRLM()` | which agent runtime and model runs the step. A step runtime of the same kind as the workflow default is merged over it field by field; another kind replaces it |
| `tools` | `()` | Python callables the agent may call; name and docstring are shown to the model |
| `skills` | `()` | `ava.agent.Skill` objects; names must be unique. Not supported by `PredictRLM` |
| `integrations` | `()` | `ava.Connector`, `ava.MCP` and `ava.catalog.*` instances; their tools reach the agent as `<integration>__<tool>` |
| `evaluations` | `None` | an `ava.Evaluations` run on each successful agent call |
| `retries`, `on_uncertain` | `7`, `"retry"` | as for plain steps |
| `inputs`, `slug` | `None` | (`ava.agent.step` only) field bindings; stable step name |

Exceptions: `ava.agent.AgentStepError`, `AgentStepCallError`, `AgentStepExecutionError`, `AgentOutputError`, `SkillFormatError`. See [errors.md](errors.md#agent-steps).

## Runtimes

```python
ava.runtimes.PredictRLM(*, lm=None, sub_lm=None, max_iterations: int = 30, max_llm_calls: int = 50,
                        max_output_chars: int = 50_000, verbose: bool = False, interpreter=None,
                        sandbox_backend: str | None = None, sbx_config=None, sbx_pool=None,
                        allowed_domains: Sequence[str] | None = None, skills=None, debug: bool = False,
                        output_dir=None, telemetry_context=None, submit_confirmation=None,
                        trace_export_path=None, runtime_hooks=None, on_runtime_hook_event=None,
                        model_execution_timeout: bool = False, adapter=None, adapters=(),
                        execution=None, modules=(), events=(), run_export_root=None, **other)
ava.runtimes.ClaudeAgent(*, model: str | None = None, max_turns: int | None = None,
                         builtin_tools: Sequence[str] = (),
                         permission_mode: Literal["default", "acceptEdits", "plan",
                                                  "bypassPermissions", "dontAsk"] = "default",
                         system_prompt_append: str | None = None,
                         system_prompt_preset: Literal["claude_code"] | None = None,
                         max_budget_usd: float | None = None, narrate: bool = True, **sdk_options)
ava.runtimes.OpenAIAgent(*, model: str | None = None, max_turns: int | None = None,
                         builtin_tools: Sequence[Literal["filesystem", "shell"]] = (),
                         system_prompt_append: str | None = None, narrate: bool = True,
                         tracing: bool = False, **model_settings)
```

| Name | Extra | Meaning |
| --- | --- | --- |
| `ava.runtimes.PredictRLM` | `agents` | The default runtime. The agent writes and runs Python in a sandbox. `lm` and `sub_lm` are `provider/model` strings (as in `"openai/gpt-5.5"`) or DSPy LM objects; `None` uses DSPy's configured LM. `max_iterations` and `max_llm_calls` bound one call. `skills` takes PredictRLM's own skills (`ava.runtimes.predict_rlm.skills.*`), not `ava.agent.Skill`. Other keywords are passed to PredictRLM unchanged. |
| `ava.runtimes.ClaudeAgent` | `claude` | Claude through the Claude Agent SDK. `model` such as `"claude-haiku-5-5"`; `None` lets the Claude CLI choose. `builtin_tools` names Claude Code tools allowed without asking (`"Read"`, `"Bash"`, ...); none by default. `max_turns` and `max_budget_usd` bound one call. `narrate` asks Claude to note what it does before each tool call. Other keywords are `ClaudeAgentOptions` fields; `system_prompt`, `output_format` and `cwd` are refused. |
| `ava.runtimes.OpenAIAgent` | `openai` | OpenAI models through the OpenAI Agents SDK. `model` such as `"gpt-5.4-mini"`, without provider prefix. `max_turns` defaults to the SDK's 10. `builtin_tools`: `"filesystem"` (edit files, view images) and `"shell"` (run commands), in a local sandbox rooted at the step's `ava.Workspace` input. `tracing` sends the SDK's trace to OpenAI. Other keywords are `agents.ModelSettings` fields (`temperature`, `reasoning`, `max_tokens`, ...). |
| `ava.runtimes.predict_rlm`, `claude_agent`, `openai_agent` | | the runtime modules; `ava.runtimes.predict_rlm.skills` holds PredictRLM's built-in skills |

All three are frozen pydantic models with strict types: `max_turns="5"` is an error. An unknown keyword fails when the runtime is built if its SDK is installed, otherwise when the step runs. Keys come from the environment: [project-layout.md](project-layout.md#read-by-the-agent-runtimes).

## Classifier steps

```python
ava.classifier_step(*, questions: dict | None = None, input_model: type[BaseModel] | None = None,
                    classifier=None, model: str | None = None, timeout: float | None = None,
                    slug: str | None = None, retries: int = 7,
                    on_uncertain: Literal["retry", "hold", "fail"] = "retry")
await classifier(*, state, questions: dict | None = None) -> ava.ClassificationResult   # classifier: ava.Classifier
ava.classifiers.TypeSafe()          # default model "jev-latest",        key TYPESAFE_API_KEY
ava.classifiers.OpenAIDecisions()   # default model "gpt-6-luna",        key OPENAI_API_KEY, extra openai
ava.classifiers.GeminiDecision()    # default model "gemini-decision-1", key GEMINI_API_KEY, extra gemini
```

| Name | Kind | Meaning |
| --- | --- | --- |
| `ava.classifier_step` | decorator | A step whose body receives a classifier: a small decision model that answers fixed questions with probabilities. The function must have a keyword-only parameter `classifier: ava.Classifier` without default. |
| `ava.Classifier` | class | The injected classifier. `await classifier(state=..., questions=...)`: `state` is the text or JSON to judge; `questions` overrides the decorator's. May be called several times in one step. |
| `ava.ClassificationResult` | model | `model` (the model that answered), `answers` (question name -> answer), `usage` (`input_tokens`, `output_tokens`). Properties `choices`, `nouls`, `scores`: the answers of each type by name. |
| `ava.classifiers.TypeSafe` / `OpenAIDecisions` / `GeminiDecision` | provider values | Passed as `classifier=`. Default provider: `TypeSafe()`. |
| `ava.classifier.ChoiceQuestion` / `NoulQuestion` / `ScoreQuestion` | models | The validated forms of a question dict. |
| `ava.classifier.ChoiceAnswer` | model | `choice`, `probabilities` (option -> 0..1), `confidence`. |
| `ava.classifier.NoulAnswer` | model | `noul`: probability from 0 to 1 that the answer is yes. |
| `ava.classifier.ScoreAnswer` | model | `score` (0 to levels - 1), `legend`, `probabilities`, `confidence`. |
| `ava.classifier.ClassificationUsage`, `ClassifierDeclaration`, `ClassifierRuntime`, `ClassifierInvocation` | models | token usage; a step's declared questions and resolved settings; one recorded call |
| `ava.classifier.capture_classifier_evidence` | context manager `(listener)` | Calls `listener` with a `ClassifierInvocation` at the start and end of each classifier call in the block. Alias `ClassifierEvidenceListener`. |

Options:

| Option | Default | Meaning |
| --- | --- | --- |
| `questions` | `None` | default questions: `{name: question}`. Without them every call must pass `questions=` |
| `input_model` | `None` | pydantic model the `state` is validated against |
| `classifier` | workflow's `classifier_defaults`, else `TypeSafe()` | provider |
| `model` | the provider's default | model ID; a model set at workflow level for another provider is not applied |
| `timeout` | `10.0` | seconds per call, greater than 0 |
| `slug`, `retries`, `on_uncertain` | function name, `7`, `"retry"` | as for plain steps |

Question dicts (`instructions` is optional text or JSON, except where a provider requires it):

| `type` | `criteria` | Answer |
| --- | --- | --- |
| `"choice"` | dict: option name -> description (or `None`); at least 1 option | `ChoiceAnswer` |
| `"noul"` | optional dict with keys `"true"` and/or `"false"` -> description | `NoulAnswer` |
| `"score"` | list of level descriptions, lowest first; at least 2 | `ScoreAnswer` |

Provider limits checked when the step is declared: `OpenAIDecisions` takes 2 to 255 options with text criteria and at most 10 score levels; `GeminiDecision` requires text `instructions`, takes 2 to 255 options, at most 255 score levels, and text criteria only. A violation raises `ClassifierStepError: question 'x' cannot be sent to <provider>: <limit>`.

Exceptions: `ava.ClassifierStepError`, `ava.ClassifierStepExecutionError`. See [errors.md](errors.md#classifier-steps).

## Evaluations

```python
ava.Evaluations(*, metrics: Mapping[str, ava.Metric], composites: Mapping[str, Callable] = {},
                classifier=None, model: str | None = None, timeout: float | None = None)
ava.Metric(*, state: Callable[[ava.EvalContext], str | dict | list], question: dict)
class ava.EvalContext    # frozen dataclass: inputs, output, trace
```

| Name | Kind | Meaning |
| --- | --- | --- |
| `ava.Evaluations` | model | Grades each successful call of an agent step with classifier questions. Passed as `evaluations=` of the agent step. `metrics` needs at least one entry. `composites` maps a name to a sync function from the `ClassificationResult` to a number in `[0, 1]`. `classifier`, `model`, `timeout` as for classifier steps. |
| `ava.Metric` | class | One metric: `state` is a sync function that picks what to judge from the `EvalContext`; `question` is one classifier question dict. |
| `ava.EvalContext` | frozen dataclass | `inputs` and `output`: the agent call's validated inputs and outputs (read fields as attributes); `trace`: its `AgentTrace`. |
| `ava.evaluations.EvaluationResult` | model | `classifier`, `classification` (a `ClassificationResult`), `composites` (name -> float). |
| `ava.evaluations.EvaluationDeclaration` | model | What is shown about an evaluation: questions, composite names, resolved settings. |
| `ava.evaluations.EvaluationError` | `RuntimeError` | A selector, question, classifier call or composite failed. See [errors.md](errors.md#evaluations). |
| `Evaluations.evaluate(context, *, runtime_defaults=None)` | async method | Run the evaluation directly, for tests. |

## Integrations

Built-in integrations and named connections are not available on every account yet.

```python
class ava.Connector                       # subclass: name, base_url, auth = ava.ApiKey(...) | ava.OAuth2(...) | None
MyConnector(name: str | None = None, *, display_name: str | None = None, icon: str | None = None)
@ava.tool                                 # on a Connector method
connector.client -> ava.Client            # httpx.Client bound to base_url and the account
ava.ApiKey(*, header: str, prefix: str = "")
ava.OAuth2(*, authorize_url: str, token_url: str, scopes: Sequence[str] = (),
           authorize_params: Mapping[str, str] | None = None,
           client_auth: Literal["basic", "post"] = "basic")
ava.MCP(name: str, *, url: str | None = None, command: Sequence[str] | None = None,
        allow: Sequence[str] | None = None, client_id: str | None = None,
        display_name: str | None = None, icon: str | None = None)
ava.Catalog(name: str, *, allow: Sequence[str] | None = None, connection: str | None = None)
ava.catalog.slack(*, connection: str | None = None, allow: Sequence[str] | None = None)
```

| Name | Kind | Meaning |
| --- | --- | --- |
| `ava.Connector` | base class | An outside HTTP service. A subclass sets `name`, `base_url`, optionally `auth`, and marks methods with `@ava.tool`. An instance is one account, named by its `name`; `MyConnector("other-name")` is a second account. Declared in `integrations=` of `ava.step` (call its tools as methods) or of an agent step (the agent calls them). |
| `ava.tool` | decorator | Marks a `Connector` method as a tool. On anything else: `IntegrationError: @ava.tool decorates a method of an ava.Connector subclass`. |
| `ava.Client` | class (`httpx.Client`) | What `self.client` returns inside a tool: requests go to `base_url` only (paths are relative), with the account's key or token attached. Another origin or a redirect to one raises `IntegrationError`. |
| `ava.ApiKey` | model | Login with a key sent in `header`, after `prefix` (`ava.ApiKey(header="Authorization", prefix="Bearer ")`). The key is given with `ava connect`, never in code. |
| `ava.OAuth2` | model | Browser login with the OAuth 2 authorization code flow. `ava connect` asks for the client ID and secret. `authorize_params` adds query parameters to the consent page; `client_auth` says where the client secret goes in a token request. |
| `ava.MCP` | class | An MCP server for agent steps: exactly one of `url` (remote, http or https) and `command` (local process, as an argument list). `allow` limits the tools offered. `client_id` is for a remote server that does not let clients register themselves. |
| `ava.Catalog` | class | A built-in integration by name, for agent steps only. `allow` limits its tools; `connection` names which connection to use. Calling an instance returns a copy with those options. |
| `ava.catalog.slack`, `notion`, `hubspot`, `linear`, `github`, `jira`, `asana`, `gmail`, `google_drive` | `Catalog` instances | The built-ins. Use bare (`ava.catalog.slack`) or configured (`ava.catalog.slack(connection="support", allow=[...])`). |
| `ava.integrations.Integration` | base class | Common base: `name`, `display_name`, `icon`, `tool_prefix` (the name with `-` as `_`). |
| `ava.integrations.StaticAccounts(accounts, *, connect_command="ava connect")` | class | An in-memory account store for tests and embedding: maps integration name -> credential. |
| `ava.integrations.use_accounts(source)` | context manager | Runs made inside the block read accounts from `source`. `install_account_source(source)` sets it for the current context without a block. |
| `ava.integrations.ApiKeyCredential(key, header=None)`, `AccessTokenCredential(token, expires_at)`, `OAuthCredential(...)` | models | Credentials a store holds. |
| `ava.integrations.Connection`, `ConnectionInstance`, `OrgAssignment`, `ProjectAssignment`, `FlowAssignment`, `resolve` | dataclasses, function | How a host picks a named connection for a flow; not used in workflow code. |
| `ava.integrations.ConnectorDeclaration`, `MCPDeclaration`, `CatalogDeclaration`, `ApiKeyDeclaration`, `OAuth2Declaration`, `IntegrationToolDeclaration`, `IntegrationDeclaration`, `StepIntegrations`, `step_integration_declarations`, `AccountSource`, `Credential`, `StoredCredential` | models, protocol, aliases | Static descriptions of declared integrations and the account-store interface; for hosts. |

Tool names an agent sees: `<integration name with - as _>__<tool>`. Two integrations with the same prefix, or an integration tool named like one of the step's `tools=`, is an `IntegrationError` when the step is declared.

Exceptions exported at top level: `ava.ConnectionNotResolvedError`, `ava.ConnectionAmbiguousError`, `ava.ConnectionNotAssignedError`, `ava.ConnectionNotFoundError`, `ava.ConnectionPerUserTriggeredError`, `ava.ConnectionUserNotConnectedError`. In `ava.integrations`: `IntegrationError`, `IntegrationNotConnectedError`, `IntegrationReconnectError`, `MCPServerUnavailableError`, `MCPToolError`, `AccountLoginExpiredError`, `AccountUnavailableError`. See [errors.md](errors.md#integrations-and-connections).

## Not documented here

| Names | Why |
| --- | --- |
| `ava.Executor`, `ava.LocalExecutor`, `ava.get_default_executor` | execution engine internals; `Workflow.run` picks the local executor itself |
| `ava.ExecutionServices`, `ExecutionServicesSpec`, `ExecutionServiceReceipt`, `ExecutionTaskSpec`, `EXECUTION_SERVICES_V1` | host-side worker services |
| `ava.PROTOCOL_VERSION`, `ava.ProtocolVersion` | host and SDK protocol version (`3.2`) |
| `ava.ReferenceBinder`, `LocalReferenceBinder`, `rebind_arguments`, `file_reference_context` | how hosts bind files and workspaces |
| `ava.iceberg`, `ava.lance`, `ava.Stream`, `ava.ModelStream`, `ava.Cursor`, `ava.AppendResult` | removed; any use raises `TablesAndStreamsRemovedError` |
