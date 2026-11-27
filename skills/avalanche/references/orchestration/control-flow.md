# Control flow

How a workflow fans out, branches, skips work and starts other workflows. The graph is
fixed when the workflow function runs; everything here is how a fixed graph still adapts at run time.

| Need | Use | Section |
|---|---|---|
| Same step once per item of a list | `step.map(...)` | [Map](#map) |
| A step decides its work is not needed | `ava.skip(reason)` | [Skip](#skip) |
| Two or more paths | `&`, skips, nullable inputs | [Branching](#branching) |
| Run another workflow and use its output | `ava.call(...)` | [Child workflows](#child-workflows) |
| Start another workflow and move on | `ava.start_run(...)` | [Child workflows](#child-workflows) |

Graph operators (see [workflows](../building-blocks/workflows.md)): `a >> b` runs `b` after `a` and
passes `a`'s output as `b`'s first inputs when `b()` is called with no arguments; `a & b` runs both
in parallel; `(a & b) >> c` passes both outputs to `c` in branch order. Passing a step result as an
argument, `b(a())`, creates the same dependency and is the clearest form.

---

## Map

Runs one step once per item of a list known only at run time, in parallel, and collects the
outputs into a list in input order.

### When to use it

- Use it for up to 64 independent items: documents, tickets, rows, URLs.
- More than 64 items: split the list into at most 64 chunks in a step, then map over the chunks.
- One approval per item: use `ava.decision.map`, see [approvals](approvals.md).
- A fixed, small number of different steps: use `&`, not a map.

### Interface

```python
step.map(*mapped, max_items=None, fail_fast=True, **broadcast) -> ava.MapFuture
ava.agent.step.map(Signature, *mapped, max_items=None, fail_fast=True, slug=None, runtime=None,
                   tools=(), skills=(), integrations=(), evaluations=None,
                   retries=7, on_uncertain="retry", **broadcast) -> ava.MapFuture
```

`.map` exists on every step: `@ava.step`, `@ava.agent_step`, `@ava.classifier_step`.

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `*mapped` | `list`, step result, `ava.input` field, or another map's result | required, at least one | The lists to iterate. They fill the step's first parameters in order. Several lists are zipped and must have the same length. |
| `max_items` | `int` 0..64 | `8` (`ava.DEFAULT_MAX_ITEMS`) | Upper bound on the list length. A longer list fails the run; it is never truncated. Hard cap `ava.HARD_CAP` = 64. |
| `fail_fast` | `bool` | `True` | `True`: the first item that fails for good fails the map and the run at once. `False`: every item runs to its end, then the map fails listing all failed items. |
| `**broadcast` | any step argument | none | Keyword arguments given unchanged to every item. May be step results. |

Result: a `MapFuture`. A downstream step receives one `list` of the item outputs, in input order.
An empty input list gives `[]`.

There is no concurrency option. Items run in parallel on the run's worker pool; `max_items` is the
only bound. Each item is retried on its own with the step's `retries` (see [retries](retries.md)).

Mapping an agent step multiplies what one agent call costs. Each item is a separate agent call, and
with `PredictRLM` each call starts its own sandbox process; the items start together. On a busy
machine sandboxes can then fail to start in time and the items fail. For more than a handful of
items, prefer one agent call that receives the whole list (PredictRLM reads large inputs as
variables), or map over a few chunks.

Failure types: `ava.MapItemsFailed` (a `RuntimeError`) has `.failures`, a tuple of
`ava.MapItemFailure(index, child_id, error)` sorted by index.

### Example

```python
from pydantic import BaseModel

import avalanche as ava


class Order(BaseModel):
    order_id: str
    amount: float


class Priced(BaseModel):
    order_id: str
    total: float


@ava.step
def list_orders() -> list[Order]:
    """List the orders to price."""
    return [Order(order_id=f"o{i}", amount=10.0 * i) for i in range(1, 4)]


@ava.step
def price_order(order: Order, tax_rate: float) -> Priced:
    """Price one order."""
    return Priced(order_id=order.order_id, total=round(order.amount * (1 + tax_rate), 2))


@ava.step
def sum_totals(priced: list[Priced]) -> float:
    """Sum the priced orders."""
    return sum(item.total for item in priced)


@ava.workflow
def price_orders():
    """Price every order, then total them."""
    priced = price_order.map(list_orders(), tax_rate=0.2, max_items=10)
    return sum_totals(priced)
```

`ava run price_orders` returns `72.0`.

Chained maps run as a per-item pipeline: item `i` of the second map starts as soon as item `i`
of the first is done, without waiting for the whole first map.

```python
@ava.workflow
def clean_then_score():
    """Clean each record, then score each cleaned record."""
    return score_record.map(clean_record.map(load_records()))
```

Mapping an agent step is the same call:

```python
drafts = write_summary.map(tickets, tone="short")          # @ava.agent_step function
drafts = ava.agent.step.map(Summarize, tickets, tone="short")  # inline agent from a Signature
```

For the inline form the positional lists fill the Signature's input fields in order and the
keywords are its other input fields. See [agent steps](../building-blocks/agent-steps.md).

### Rules

- Set `max_items` to the largest list you expect. Over the bound the run fails with
  `ValueError: <step>: map_overflow input length 9 exceeds max_items 8 (raise with max_items=, hard cap 64)`.
- `max_items` above 64 fails when the workflow is built: `ValueError: <step>: max_items must be between 0 and 64`.
- Zipped lists of different lengths fail with `ValueError: <step>: map_length_mismatch lengths [1, 2]`.
- A map never gives partial results. With any failed item the map fails with
  `MapItemsFailed: <step>: MapItemsFailed [(1, <step>_1[1], ValueError: ...); ...]` and steps after it do not run.
- To keep going past bad items, handle the error inside the step: return an output model with an
  error field, or call `ava.skip(reason)` for that item.
- Skipped items are left out of the collected list, so the list can be shorter than the input
  and positions shift. Carry an id in the item output if you need to match items back.
- A chained map inherits `max_items` and `fail_fast` from its source map. It may only lower
  `max_items` (`ValueError: <step>: chained max_items may only lower inherited 4`).
- Mapped arguments are positional only. A keyword argument is always broadcast, even if it is a list.
- If the positional list comes from a skipped step, the whole map is skipped.
- Do not write a Python `for` loop over a step result in the workflow function: the result is a
  placeholder there, not a list.

### In the UI

The map is one node named after the step. Each item appears under it as `<step>[0]`, `<step>[1]`,
... with its own status, inputs, output, error and attempts. Item ids are `<node_id>[i]`
(`price_order_1[2]`).

---

## Skip

`ava.skip(reason)` ends the current step at once with status `skipped`. A skip is an outcome,
not a failure: the run goes on and can succeed.

### When to use it

- A step finds at run time that its work does not apply (not qualified, nothing to send, already done).
- It is the way to branch on data: see [Branching](#branching).
- Do not use it for errors. Raise an exception so retries and the failed status apply, see [retries](retries.md).

### Interface

```python
ava.skip(reason: str | None = None) -> NoReturn   # raises ava.StepSkipped
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `reason` | `str` or `None` | `"Skipped by the step"` | Shown on the step. Must not be blank; at most 1000 characters. |

What steps after a skipped step receive:

| Downstream | Result |
|---|---|
| Step parameter typed to accept `None` (`int \| None`) | The step runs and gets `None`. |
| Any other step parameter | The step is skipped too, reason `Upstream step '<slug>' was skipped`. |
| `ava.sleep`, `ava.wait_for_event`, `ava.decision`, `ava.call`, `ava.start_run` | Skipped too. |
| `.map` whose positional list is the skipped output | The whole map is skipped. |
| `.map` broadcast keyword | Each item gets `None` if the parameter accepts it, else each item is skipped. |
| The workflow's return value | `None` in that position. With `output=Model` on the workflow the run fails (see Rules). |

### Example

```python
import avalanche as ava


class Lead(ava.BaseInput):
    name: str
    employees: int


@ava.step
def score_lead(lead: Lead) -> int:
    """Score a lead, or skip a lead that is too small."""
    if lead.employees < 10:
        ava.skip(f"{lead.name} has fewer than 10 employees")
    return min(lead.employees, 100)


@ava.step
def draft_offer(score: int) -> str:
    """Draft an offer for a scored lead."""
    return f"offer for score {score}"


@ava.step
def write_summary(lead: Lead, offer: str | None) -> str:
    """Summarize the outcome, with or without an offer."""
    return f"{lead.name}: {offer or 'no offer'}"


@ava.workflow(input=Lead)
def qualify_lead():
    """Score a lead, draft an offer, and always write a summary."""
    lead = ava.input
    offer = draft_offer(score_lead(lead))
    return write_summary(lead, offer)
```

With `{"name": "Acme", "employees": 3}`: `score_lead` is skipped with its reason, `draft_offer`
is skipped (`Upstream step 'score_lead' was skipped`), `write_summary` runs and returns
`"Acme: no offer"`. The run succeeds.

### Rules

- Let `StepSkipped` propagate. A broad `except Exception:` around `ava.skip()` catches it and
  cancels the skip.
- A skip is never retried.
- Type every parameter. `X | None` means "run me even if that input was skipped"; anything else
  means "skip me too". An unannotated parameter counts as not nullable.
- Give every step a return annotation. With `>>` and no arguments, a skipped step stands in
  for one input only when its return type is not a tuple or list.
- `ava.skip` does nothing but raise `ava.StepSkipped`; the host turns that into a skipped step.
  Called through `step.fn(...)` in a unit test it raises too: `pytest.raises(ava.StepSkipped)`.
  A blank reason raises `ValueError: skip reason must not be blank`.
- A workflow with `output=Model` must not return a step that can be skipped: the result would be
  `None` and the run fails with `error_code: coordinator_failed` (a `ValidationError` for the
  model in the log). Return a step that always runs and takes `X | None`.

### In the UI

A skipped step shows status `skipped` and its reason. Steps that never ran because the run
failed or was cancelled are also shown as `skipped`, without a reason.

---

## Branching

The workflow function runs once, before any step, to build the graph. Step results are
placeholders there, so `if result:` and `for x in result:` in the workflow function do not branch
on data. Use these patterns.

| Pattern | How |
|---|---|
| Parallel branches | `a(x) & b(x)`, or just call both with the same input. Join with a step that takes both. |
| Optional branch | The branch's first step calls `ava.skip()`; the rest of the branch is skipped with it. |
| Either/or | Two branches from one decision value; each first step skips itself unless it is its case. |
| Join after optional branches | A step with `X \| None` parameters; it runs with `None` for skipped branches. |
| Optional child workflow | `ava.call(child, input, when=flag_step())`. |
| Choice by a model | A classifier step returns the label; branches skip on it. See [classifier steps](../building-blocks/classifier-steps.md). |
| Choice by a person | `ava.decision` returns the answer; branches skip on it. See [approvals](approvals.md). |
| Simple choice | An `if` inside one step. Prefer this when the branches are small. |
| Loops, revisit a state, long-lived cases | Not possible in a workflow graph (a cycle fails with `Workflow '<name>' contains a cycle`). Use a [state machine](../building-blocks/state-machines.md). |

```python
import avalanche as ava


class Ticket(ava.BaseInput):
    text: str
    is_refund: bool


@ava.step
def handle_refund(ticket: Ticket) -> str:
    """Handle a refund ticket."""
    if not ticket.is_refund:
        ava.skip("not a refund")
    return "refund started"


@ava.step
def handle_question(ticket: Ticket) -> str:
    """Handle any other ticket."""
    if ticket.is_refund:
        ava.skip("a refund")
    return "answer sent"


@ava.step
def close_ticket(refund: str | None, answer: str | None) -> str:
    """Close the ticket with whichever branch ran."""
    return refund or answer or "nothing done"


@ava.workflow(input=Ticket)
def route_ticket():
    """Send a ticket down one of two branches, then close it."""
    ticket = ava.input
    return close_ticket(handle_refund(ticket), handle_question(ticket))
```

---

## Child workflows

A workflow can start another workflow (or a state machine) as its own run. `ava.call` starts it
and waits for its output. `ava.start_run` starts it and continues at once.

### When to use it

- `ava.call`: reuse a whole workflow as one step and use its typed output.
- `ava.start_run`: hand work off (notify, archive, a long case) without waiting.
- Not for sharing a few steps: import and call the same step functions in both workflows.
- Not available in an embedded `workflow().run()`; it needs the operator or Cloud. See [testing](../run/testing.md) for `workflow.test(targets=...)`.

### Interface

```python
ava.call(target, input=None, *, timeout=None, when=True)   # -> the child's output
ava.start_run(target, input=None, *, when=True)             # -> ava.RunStart | None
```

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `target` | workflow function, `Workflow`, machine, name `str`, or a step result holding a name | required | What to start. Pass the function when you can: the output is then validated to the child's type. |
| `input` | `ava.BaseInput`, `dict`, step result, or `None` | `None` | The child's run input. |
| `timeout` (`call` only) | `timedelta`, seconds, or `None` | `None` | How long to wait for the child. `None` waits without limit. On timeout the parent step fails; the child keeps running. |
| `when` | `bool` or step result | `True` | `False` at run time: nothing starts, the result is `None`, the node shows as skipped with reason `when=False`, and steps after it still run. |

`ava.RunStart` fields:

| Field | Type | Meaning |
|---|---|---|
| `run_id` | `str` | The child's run id. |
| `key` | `list` or `None` | The child's run key when its workflow declares `key=` (see [retries](retries.md#run-keys)). |
| `created` | `bool` | `False` when a run with that key already existed and was attached to instead. |
| `read_state` | `"retained"` or `"expired"` | Whether the child's data can still be read. |

What `ava.call` returns: the child's output, validated to the child's `output=` model, else to the
return annotation of its last step. With a `str` target it is the plain decoded value. Calling a
state machine waits until the machine closes.

How a child ends, as seen by the parent's call node (not retried):

| Child | Parent |
|---|---|
| Succeeds | The call gives its output. |
| Fails | `ValueError: child_run_failed: <child run id>` |
| Is cancelled | `ValueError: child_run_cancelled: <child run id>` |
| Still running at `timeout` | `ValueError: child_run_timeout: <child run id>` |

### Example

```python
from datetime import timedelta

from pydantic import BaseModel

import avalanche as ava


class Doc(ava.BaseInput):
    doc_id: str


class DocSummary(BaseModel):
    doc_id: str
    words: int


@ava.step
def count_words(doc: Doc) -> DocSummary:
    """Count the words of one document."""
    return DocSummary(doc_id=doc.doc_id, words=len(doc.doc_id) * 100)


@ava.workflow(input=Doc, output=DocSummary)
def summarize_doc():
    """Summarize one document."""
    return count_words(ava.input)


@ava.step
def pick_doc() -> Doc:
    """Pick the document to process."""
    return Doc(doc_id="contract-7")


@ava.step
def report_words(summary: DocSummary) -> str:
    """Report the child's result."""
    return f"{summary.doc_id} has {summary.words} words"


@ava.step
def note_start(start: ava.RunStart | None) -> str:
    """Record the id of the run that was started."""
    return "not started" if start is None else f"started {start.run_id}"


@ava.workflow
def process_doc():
    """Call a child workflow and use its output; also start one without waiting."""
    doc = pick_doc()
    summary = ava.call(summarize_doc, doc, timeout=timedelta(minutes=5))
    started = ava.start_run(summarize_doc, doc)
    return report_words(summary), note_start(started)
```

`ava run process_doc` returns `("contract-7 has 1000 words", "started run_...")` and creates two
`summarize_doc` runs.

### Rules

- Both are workflow-function calls, like `ava.sleep`. Inside a step they raise
  `TypeError: ava.start_run is workflow-only; use ava run or a normal run client`.
- The child is a normal run: own id, own status, own page. Cancelling the parent cancels a
  child it created.
- Each call node starts its child once per parent run. The child's run id is derived from the
  parent run and node, so a resumed parent attaches to the same child.
- If the child workflow declares `key=`, an existing run with the same key is reused
  (`created=False`); `ava.call` then waits for that run.
- `timeout` does not cancel the child. Cancel it yourself (`ava run cancel <id>`) if needed.
- A failed child fails the parent. To tolerate it, let the child return an output model with a
  status field instead of raising.
- A run cannot wait on itself or on a run that waits on it: `child_run_self_wait`, `child_run_wait_cycle`.
- A wait on a child uses no worker while it waits.
- `ava.call` and `ava.start_run` have no `.map`. To fan out, map a step, or give the child the
  whole list.
- If a step the start depends on was skipped, the start is skipped too.

### In the UI

`ava.call` shows as one node named after the child workflow, with a link to the child run. The
run list shows the parent as `waiting` with the note `waiting on run`. Links are also available
with `ava run links RUN_ID` and `GET /api/v1/runs/{run_id}/links`.

## See also

- [Workflows](../building-blocks/workflows.md): `>>`, `&`, inputs and outputs
- [Retries and reruns](retries.md): what happens when an item or a child fails
- [Waits](waits.md) and [approvals](approvals.md): pausing a run
- [State machines](../building-blocks/state-machines.md): loops and long-lived cases
- [Patterns](../guides/patterns.md) and [choose a feature](../guides/choose-a-feature.md)
