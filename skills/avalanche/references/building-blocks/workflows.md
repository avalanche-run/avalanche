# Workflows

A workflow is a Python function decorated with `@ava.workflow` that calls steps and wires them into a
directed acyclic graph (DAG). The function body runs once, at build time, to record the graph; the
steps run later, when a run starts.

## When to use it

- Always. The DAG workflow is the default shape for every process: steps, then `>>` and `&`.
- Use a state machine only when a long-lived entity reacts to outside events in an order you cannot
  know in advance: see [state-machines.md](state-machines.md).
- Loops over a list, branches, child runs: see [../orchestration/control-flow.md](../orchestration/control-flow.md).
- Pauses, human decisions: see [../orchestration/waits.md](../orchestration/waits.md) and
  [../orchestration/approvals.md](../orchestration/approvals.md).

## Interface

```python
@ava.workflow                      # bare
@ava.workflow(
    cron=None, webhook=None, input=None, key=None, output=None, emits=(),
    context=None, ctx=None, agent_defaults=None, classifier_defaults=None,
)
def my_flow(): ...                 # takes no parameters
```

The decorated name is a builder: `my_flow()` returns an `ava.Workflow`. The operator calls it for you.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `cron` | `str \| None` | `None` | Cron expression for scheduled runs. See [../connect/triggers.md](../connect/triggers.md). |
| `webhook` | `ava.Webhook \| bool \| None` | `None` | `True` or `ava.Webhook(path="/x")` enables an HTTP trigger. See [../connect/triggers.md](../connect/triggers.md). |
| `input` | `type[ava.BaseInput] \| None` | `None` | Run input model, validated at run start. See [inputs-and-files.md](inputs-and-files.md). |
| `key` | `tuple[str, ...] \| None` | `None` | Input field names that form a permanent run identity: a second start with the same key returns the first run. Needs `input`. |
| `output` | `type[pydantic.BaseModel] \| None` | `None` | Model the returned value is validated into (a returned `dict` becomes this model). `None` is not accepted: the returned step must never be skipped. |
| `emits` | `tuple[str, ...]` | `()` | Event names the workflow can end with. Must equal the `emits` of the returned step. Used by state machines. |
| `context` | `type[ava.BaseContext] \| None` | `None` | Run context model. See [python-steps.md](python-steps.md). |
| `ctx` | same | `None` | Alias of `context`. Passing both with different types raises `ValueError`. |
| `agent_defaults` | `AgentDefaults \| Mapping \| None` | `None` | Runtime shared by agent steps. See [agent-steps.md](agent-steps.md). |
| `classifier_defaults` | `ClassifierDefaults \| dict \| None` | `None` | Defaults shared by classifier steps. See [classifier-steps.md](classifier-steps.md). |

Aliases kept for older code, identical objects: `ava.pipeline` is `ava.workflow`, `ava.Pipeline` is
`ava.Workflow`, `ava.transform` is `ava.step`. Write `workflow` and `step` in new code.

### The `ava.Workflow` object

| Member | Meaning |
|---|---|
| `.name` | The builder function's name. |
| `.nodes`, `.graph`, `.node_slugs` | Step calls by id, edges `{parent_id: [child_ids]}`, slug per id. |
| `.run(input=None, context=None, run_id=None, ...)` | Run in this process; returns a handle, `.result()` blocks. See [../run/local.md](../run/local.md). |
| `.test(input=None, *, stub=None, targets=())` | In-memory run with fake time and stubbed steps. See [../run/testing.md](../run/testing.md). |
| `.key(input)` | The run key tuple for an input (only with `key=`). |

### Composing steps

Calling a step inside the workflow function does not run it. It returns a future (a placeholder for the
step's output) and adds one node to the graph. Combine futures in three ways:

| Form | Graph | Data |
|---|---|---|
| `b(a())` or `x = a(); b(x)` | `a → b` | `a`'s output is the argument you passed. Works for keyword arguments too. |
| `a() >> b()` | `a → b` | `a`'s output fills `b`'s first free parameter (rules below). |
| `a() & b()` | no edge | `a` and `b` run in parallel. Use it as the left or right side of `>>`. |
| `(a() & b()) >> c()` | `a → c`, `b → c` | Fan-in: `c` gets `a`'s output, then `b`'s, in the order written. |
| `a() >> (b() & c())` | `a → b`, `a → c` | Fan-out: `b` and `c` each get `a`'s output. |
| `(a() & b()) >> (c() & d())` | every left to every right | `c` and `d` each get both outputs. |

`>>` returns the whole chain, so `a() >> b() >> c()` reads left to right. A chain gives its **last**
step's output in two places only: when the workflow returns it, and when it is continued with `>>`.
Passed as an argument, a chain resolves to its **first** step's output: after `x = a() >> b()`, `c(x)`
gives `c` the output of `a`. To reuse `b`'s output, bind `b`'s own call: `x = b(a()); c(x)`.

### How `>>` maps outputs to parameters

1. Upstream outputs form an ordered list of values: one per upstream step, left to right.
2. A step declared with `num_returns=N` contributes `N` values. `x[i]` contributes value `i` only.
3. A step with the default `num_returns=1` that returns a `tuple` or `list` contributes **one value per
   element**. A returned `dict`, model or scalar is one value.
4. The values fill the next step's positional parameters in order, skipping parameters that are
   injected (typed `ava.BaseInput` / `ava.BaseContext` subclasses, `ava.Logger()` defaults) and
   parameters you passed by keyword. `*args` takes all remaining values.
5. If the next step was called with any positional argument, nothing is bound from `>>`: the edge only
   orders the two steps. A step with no parameters is also only ordered.
6. A step whose future you already passed as an argument is not bound a second time.

### What the workflow function returns

| Return | Run result |
|---|---|
| a future or a `>>` chain | Output of the last step of the chain. |
| a chain ending in `(b() & c())` | Tuple of the branch outputs. |
| a tuple of futures | Tuple of their outputs. |
| nothing | `None`. All steps still run. |
| a plain value | That value. |

## Example

```python
import avalanche as ava
from pydantic import BaseModel


class Order(BaseModel):
    order_id: str
    amount: float


class Report(BaseModel):
    count: int
    total: float


@ava.source
def load_orders() -> list[Order]:
    """Load the orders to process."""
    return [Order(order_id="a1", amount=10.0), Order(order_id="b2", amount=32.5)]


@ava.step
def count_orders(orders: list[Order]) -> int:
    """Count the orders."""
    return len(orders)


@ava.step
def sum_amounts(orders: list[Order]) -> float:
    """Add up the order amounts."""
    return sum(order.amount for order in orders)


@ava.step
def build_report(count: int, total: float) -> Report:
    """Combine the count and the total into one report."""
    return Report(count=count, total=total)


@ava.dest
def publish_report(report: Report) -> Report:
    """Publish the report."""
    return report


@ava.workflow
def order_report():
    """Summarise the orders."""
    orders = load_orders()
    return (count_orders(orders) & sum_amounts(orders)) >> build_report() >> publish_report()
```

`orders` is a list, so it is passed as an explicit argument (rule 3). The two parallel outputs then
fill `count` and `total` in the order written.

```bash
ava dev flows/                       # or: ava operator flows/
ava run order_report                 # prints the run id
ava result RUN_ID --wait --json      # the Report, as tagged JSON: count 2, total 42.5
```

## Rules

- Parenthesise every `&` group. Python binds `>>` tighter than `&`: `a() & b() >> c()` means
  `a() & (b() >> c())`.
- Never send a `list` or `tuple` output through `>>` to a one-parameter step. It is spread over the
  parameters and fails with `TypeError: Cannot implicitly bind upstream result at position 1 for
  <step>: function signature has no parameter at that position`. An empty list binds nothing and fails
  with `missing 1 required positional argument` (on the operator: `missing a required argument:
  '<name>'`). Pass it explicitly (`b(a())`) or return a model.
- When the next step has an injected parameter (context, input, logger), surplus upstream values are
  dropped without an error. Match the number of outputs to the number of free parameters.
- `>>` fills positional parameters only. A keyword-only data parameter stays empty (`TypeError:
  missing a required keyword-only argument`); pass its future by keyword: `b(value=a())`.
- To index an output (`pair[0]`), declare `num_returns=N` on the step. Indexing a default step fails at
  run time.
- Pass futures as direct arguments only. A future inside a list or dict argument is not resolved and
  adds no edge. The same holds for the return value: return a future or a tuple of futures.
- Never pass a `>>` chain as an argument, and never keep one in a variable to pass it on: the argument
  is the chain's first step's output, with no error. Write `x = b(a())`, then `c(x)`.
- Do not return a bare `a() & b()`. Return `(a(), b())`, or end a chain with the group.
- With `output=Model`, return a step that always produces a value. If that step is skipped (by
  `ava.skip` or by a skipped input) the result is `None` and the run fails after its steps:
  `error_code: coordinator_failed`, with `ValidationError: 1 validation error for <Model>` in the
  log. End with a step whose inputs are typed `X | None`, or leave `output=` out.
- Do not call a step outside a workflow function: `RuntimeError: Node '<step>' invoked outside of
  workflow context`. In a unit test call the plain function with `<step>.fn(...)`.
- No Python `if`/`for` on step outputs in the workflow function: outputs do not exist at build time.
  Use the features in [../orchestration/control-flow.md](../orchestration/control-flow.md).
- A cycle raises `ValueError: Workflow '<name>' contains a cycle involving: ...`.
- Calling the same step twice is fine. Ids are `<name>_1`, `<name>_2`; slugs are `<name>`, `<name>_2`.
- `key=` needs a host that keeps runs (the operator). `Workflow.run()` raises `RuntimeError: Keyed
  workflows require a run-capable scheduler host`; use `.test()` in unit tests.
- Keep module import and the workflow function free of side effects: discovery imports every flow file
  and calls every builder.

## How flows are discovered

The operator finds workflows by scanning flow targets. A flow target is a `.py` file or a directory,
optionally prefixed with an alias: `alias=path`.

```bash
ava dev flows/ other/report.py       # explicit targets
ava dev                              # targets from pyproject.toml
```

```toml
# pyproject.toml, nearest one at or above the current directory
[tool.avalanche]
flow_targets = ["flows"]             # relative to this file's folder
```

- A directory is scanned recursively for `*.py` files. Files starting with `_` are skipped, as are
  folders starting with `.` and folders named `__pycache__`, `build`, `dist`, `node_modules`, `venv`.
- In each file, every public module-level name decorated with `@ava.workflow` or `@ava.machine` and
  defined in that file is registered. A workflow imported from another file is registered once, in the
  file that defines it. Names starting with `_` are skipped.
- A flow file can import sibling modules; a file inside a package (`__init__.py`) is imported by its
  package name.
- The selector of a flow is `<file path relative to its target>::<function name>`, for example
  `orders.py::order_report`. With several targets it is `<alias>/<file>::<function>`.
- The bare function name works wherever a selector is asked, while it is unique. Otherwise:
  `Ambiguous workflow '<name>'; candidates: ...`.
- Neither explicit targets nor `flow_targets`: `No workflow targets configured. Pass FLOW [FLOW ...],
  or set [tool.avalanche].flow_targets in pyproject.toml.`
- Two targets with the same folder name need aliases: `Configured root basename '<name>' is ambiguous;
  provide explicit stable aliases`.
- A file that fails to import, a builder that raises, or an invalid cron expression is a discovery
  error. At startup the operator prints it and exits with code 1. While the operator runs, the
  previous catalog stays and the error is listed under `diagnostics` of `GET /api/v1/flows`.
  `ava flow list` shows what is registered.
- The running operator watches the targets and picks up edits and new files.

## In the UI

- Flow name: the workflow function's name. Its docstring is not read by discovery; keep one anyway for
  human readers.
- Step name: the step function's name (the `_1` suffix of the node id is removed).
- Step description: the first non-empty line of the step's docstring, for Python steps.
- Step detail: the step's source code and the type names and JSON schemas of its parameters and return
  annotation. Unannotated values show as `Unspecified`.
- Each node is typed `source`, `step` or `dest` from its decorator.
- The graph shows exactly the edges you created with `>>` and with futures passed as arguments.

## See also

- [python-steps.md](python-steps.md): the step decorators.
- [inputs-and-files.md](inputs-and-files.md): run input, `ava.input`, files.
- [state-machines.md](state-machines.md): event-driven lifecycles.
- [../orchestration/control-flow.md](../orchestration/control-flow.md): map, skip, child runs.
- [../run/local.md](../run/local.md), [../run/testing.md](../run/testing.md): running and testing.
- [../reference/project-layout.md](../reference/project-layout.md): where flow files live.
