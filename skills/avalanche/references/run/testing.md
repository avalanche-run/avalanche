# Testing

How to check steps, workflows, waits, state machines and agent steps with pytest, in one
process, without an operator and without a model key; and how to run a workflow that has model
steps on the operator when no key is set.

Tests do not replace the proof on the operator: a workflow is done when a run of it succeeded
there ([local.md](local.md)). In-process runs skip checks the operator makes (saved outputs,
discovery, input handling). Use tests for logic and paths, the operator for the proof.

## When to use it

| You want to check | Use | Section |
| --- | --- | --- |
| One step's logic | `step.fn(...)` | [Call a step function](#call-a-step-function) |
| The whole graph with real steps | `workflow().run(...).result()` | [Run a workflow in process](#run-a-workflow-in-process) |
| The graph with some steps replaced, or with waits | `workflow().test(...)` | [The test driver](#the-test-driver) |
| A state machine's transitions and timers | `machine.test(...)` | [Test a state machine](#test-a-state-machine) |
| An agent step without a model | a scripted model, or a stub | [Agent and classifier steps](#agent-and-classifier-steps) |

Do not use these to check discovery, the CLI, reruns or persisted state: start a local
operator instead ([local.md](local.md)). `workflow().test()` does not start threads or
processes and uses fake time; `workflow().run()` uses real threads and real time.

The package ships no pytest plugin and no fixtures. `avalanche._testing` is internal and
empty of helpers; do not import it.

### Where tests live and how they run

Keep tests outside the scanned folders (`tests/` next to `src/`), so the operator does not
import them. `ava init` does not install pytest: `uv add --dev pytest`. A flow file is
`src/<workflow>/flow.py` with no package around it; put `src` on the import path and import by
folder name:

```toml
# pyproject.toml
[tool.pytest.ini_options]
pythonpath = ["src"]
```

```python
# tests/test_triage.py
from triage.flow import Ticket, triage_ticket
```

```bash
uv run pytest tests -q
```

## Call a step function

A decorated step is a node object. Calling it outside a workflow raises. The original
function is on `.fn`.

```python
import asyncio

import pytest
from pydantic import BaseModel

import avalanche as ava


class OrderInput(ava.BaseInput):
    """One order to price."""

    order_id: str
    quantity: int
    unit_price: float = 2.5


class OrderTotal(BaseModel):
    """Priced order."""

    order_id: str
    total: float


@ava.step
def price_order(order: OrderInput) -> OrderTotal:
    """Compute the order total from quantity and unit price."""
    return OrderTotal(order_id=order.order_id, total=order.quantity * order.unit_price)


@ava.step(retries=0)
async def format_receipt(order: OrderTotal) -> str:
    """Render the priced order as a one-line receipt."""
    if order.total > 1000:
        raise ValueError("total exceeds the 1000 limit")
    return f"{order.order_id}: {order.total:.2f}"


def test_step_function_directly():
    total = price_order.fn(OrderInput(order_id="A1", quantity=4))
    assert total == OrderTotal(order_id="A1", total=10.0)
    assert asyncio.run(format_receipt.fn(total)) == "A1: 10.00"
    with pytest.raises(RuntimeError, match="outside of workflow context"):
        price_order(OrderInput(order_id="A1", quantity=4))
```

`.fn` runs the bare function: no retries, no input validation, nothing injected. Pass every
argument yourself, including a logger for a `log=ava.Logger()` parameter:

```python
import logging


@ava.step
def log_order(order_id: str, log=ava.Logger()) -> str:
    """Log the order ID and return it."""
    log.info(f"order {order_id}")
    return order_id


def test_step_with_logger():
    assert log_order.fn("A1", log=logging.getLogger("test")) == "A1"
```

## Run a workflow in process

```python
@ava.workflow(input=OrderInput)
def price_orders():
    """Price an order and format its receipt."""
    return price_order() >> format_receipt()


def test_workflow_in_process():
    handle = price_orders().run(
        executor=ava.LocalExecutor(), input={"order_id": "A1", "quantity": 4}
    )
    assert handle.result(timeout=30) == "A1: 10.00"


def test_workflow_failure_in_process():
    handle = price_orders().run(input={"order_id": "A1", "quantity": 1000})
    with pytest.raises(ValueError, match="1000 limit"):
        handle.result(timeout=30)
```

`Workflow.run(...)` returns a `RunHandle` at once; the run executes in a background thread.

| `run()` argument | Default | Meaning |
| --- | --- | --- |
| `executor` | `LocalExecutor` | Where steps execute. |
| `input` | `None` | A dict or an instance of the workflow's input model. |
| `context` | `None` | A dict or an instance of the workflow's context model. |
| `run_id` | generated | Your own run ID. |
| `fast_timers` | `False` | `ava.sleep` ends at once. Decision and event timeouts are not shortened. |
| `events` | `None` | `{event key: payload}` delivered before the run asks for them. |
| `decisions` | `None` | `{decision slug: payload}` answered when the decision is reached. |
| `principal` | `LocalPrincipal()` | Who answers preloaded decisions; name `"local"`, may send events and decide. |

| `RunHandle` method | Meaning |
| --- | --- |
| `result(timeout=None)` | The workflow's return value; re-raises the step's exception on failure. |
| `exception(timeout=None)` | The failure, or `None`. |
| `done()`, `running()`, `cancelled()` | State checks. |
| `cancel()` | Request cancellation; the active step finishes first. |
| `wait_until_waiting(timeout=5.0)` | `True` once the run is parked on a wait. |
| `send_event(key, payload, *, idempotency_key, principal=...)` | Deliver an event to `ava.wait_for_event`. |
| `decide(node, payload, *, idempotency_key, reason, principal=...)` | Answer an `ava.decision` by its slug. |
| `advance_time(timedelta)` | Move wait deadlines forward. |

A handle is also awaitable: `await handle` in async code.

## The test driver

`Workflow.test(input=None, *, stub=None, targets=())` executes the graph on a deterministic
driver: fake time, no threads, child runs and machines hosted in memory. It returns after
the run finished or parked on a wait. `await workflow().test_async(...)` is the async form.

| Argument | Meaning |
| --- | --- |
| `input` | A dict or an instance of the workflow's input model. |
| `stub` | `{step: [value, value, ...]}`. Each call of that step returns the next value instead of running. |
| `targets` | Workflows or machines that `ava.start_run` / `ava.call` may address by name. |

| On the returned driver | Meaning |
| --- | --- |
| `.handle` | A `RunHandle`: `.handle.result(timeout=1)`, `.handle.done()`. |
| `.send(name, payload=None)` | Send a state machine event. It does not answer `ava.wait_for_event` in a flow, and neither does `.handle.send_event(...)` here: both return a receipt with `reason='ignored'`. |
| `.decide(node, payload, *, reason)` | Answer a decision by its slug. Not a mapped one (`ava.decision.map`): use `run()`. |
| `.advance_time(timedelta)` | Move fake time forward; sleeps and timeouts that are due fire. |
| `.host` | The in-memory host: `.host.flow_runs`, `.host.instances` (started child runs and machines). |

Async forms: `asend`, `adecide`, `aadvance_time`.

```python
def test_workflow_with_stub():
    driver = price_orders().test(
        OrderInput(order_id="A1", quantity=4),
        stub={price_order: [OrderTotal(order_id="A1", total=99.0)]},
    )
    assert driver.handle.result(timeout=1) == "A1: 99.00"
```

Waits: nothing happens until you move time or answer.

```python
from datetime import timedelta
from typing import Literal


class Approval(ava.Event):
    """A reviewer's answer."""

    kind: Literal["approve", "reject"]


@ava.step
def draft_refund(order: OrderInput) -> str:
    """Write the refund draft a reviewer approves."""
    return f"refund {order.order_id}"


@ava.step
def send_refund(draft: str, approval: Approval) -> str:
    """Send the refund when it was approved."""
    return f"{draft}: {'sent' if approval.kind == 'approve' else 'dropped'}"


@ava.workflow(input=OrderInput)
def refund_order():
    """Draft a refund, wait a day, ask a reviewer, then send it."""
    draft = draft_refund()
    review = ava.decision(
        Approval, prompt="Approve refund?", show=draft, timeout=None, slug="review"
    )
    draft >> ava.sleep(timedelta(days=1)) >> review
    return send_refund(draft, review)


def test_waits_with_fake_time():
    driver = refund_order().test({"order_id": "A1", "quantity": 1})
    assert not driver.handle.done()
    driver.advance_time(timedelta(days=1))
    assert not driver.handle.done()
    driver.decide("review", Approval(kind="approve"), reason="checked")
    assert driver.handle.result(timeout=1) == "refund A1: sent"


def test_waits_in_process():
    run = refund_order().run(input={"order_id": "A1", "quantity": 1}, fast_timers=True)
    assert run.wait_until_waiting(timeout=5)
    run.decide("review", {"kind": "reject"}, idempotency_key="d1", reason="no")
    assert run.result(timeout=5) == "refund A1: dropped"
```

An `ava.wait_for_event` in a flow is answered on a `run()` handle, live or preloaded:

```python
class Signed(BaseModel):
    """A signature notification."""

    envelope_id: str


@ava.step
def record_signature(signed: Signed) -> str:
    """Return the signed envelope's ID."""
    return signed.envelope_id


@ava.workflow
def wait_signature():
    """Wait for a signature event."""
    signed = ava.wait_for_event(Signed, key="signed", timeout=None)
    return record_signature(signed)


def test_event_in_process():
    run = wait_signature().run()
    assert run.wait_until_waiting(timeout=5)
    run.send_event("signed", {"envelope_id": "e1"}, idempotency_key="evt-1")
    assert run.result(timeout=5) == "e1"


def test_preloaded_event():
    run = wait_signature().run(events={"signed": {"envelope_id": "e1"}})
    assert run.result(timeout=5) == "e1"
```

### Workflows that `run()` cannot start

`run()` refuses a workflow with `key=`, `ava.call` or `ava.start_run`; `test()` accepts them
but cannot deliver an event to `ava.wait_for_event`. To test the event path of such a workflow,
build its graph in a plain function that takes the parts `run()` cannot host as arguments:

```python
# src/fulfil/flow.py
from datetime import timedelta

from pydantic import BaseModel

import avalanche as ava


class Order(ava.BaseInput):
    order_id: str


class Payment(BaseModel):
    amount: float


class Shipment(BaseModel):
    order_id: str
    carrier: str


@ava.step
def book_carrier(order: Order) -> Shipment:
    """Book a carrier for the order."""
    return Shipment(order_id=order.order_id, carrier="post")


@ava.workflow(input=Order, output=Shipment)
def ship_order():
    """Ship one order."""
    return book_carrier(ava.input)


@ava.step
def close_order(shipment: Shipment, payment: Payment | None) -> str:
    """Close the order as paid or unpaid."""
    paid = "unpaid" if payment is None else f"paid {payment.amount}"
    return f"{shipment.order_id} via {shipment.carrier}: {paid}"


def wire_fulfilment(order, ship):
    """Build the graph; `ship` makes the node that ships the order."""
    payment = ava.wait_for_event(Payment, key="payment.received", timeout=timedelta(days=10))
    return close_order(ship(order), payment)


@ava.workflow(input=Order, key=("order_id",))
def fulfil_order():
    """Ship an order through a child workflow and wait for its payment."""
    return wire_fulfilment(ava.input, lambda order: ava.call(ship_order, order))
```

The test builds a twin with the same wiring, no key, and a stand-in step for the child, and
drives the event on a `run()` handle. The child call and the timeout are tested on the real
workflow with `test()`:

```python
# tests/test_fulfil.py
from datetime import timedelta

from fulfil.flow import Order, Shipment, fulfil_order, ship_order, wire_fulfilment

import avalanche as ava


@ava.step
def ship_stand_in(order: Order) -> Shipment:
    """Stand in for the child workflow."""
    return Shipment(order_id=order.order_id, carrier="test")


@ava.workflow(input=Order)
def fulfil_order_twin():
    """The same wiring without key= and without ava.call, so run() accepts it."""
    return wire_fulfilment(ava.input, ship_stand_in)


def test_event_arrives():
    run = fulfil_order_twin().run(input={"order_id": "A1"})
    assert run.wait_until_waiting(timeout=5)
    run.send_event("payment.received", {"amount": 5.0}, idempotency_key="p1")
    assert run.result(timeout=5) == "A1 via test: paid 5.0"


def test_child_call_and_timeout():
    driver = fulfil_order().test({"order_id": "A1"}, targets=[ship_order])
    assert not driver.handle.done()
    driver.advance_time(timedelta(days=10))
    assert driver.handle.result(timeout=1) == "A1 via post: unpaid"
```

No in-process run takes an event on the keyed workflow itself; that path is proven on the
operator ([waits](../orchestration/waits.md#sending-an-event-from-another-system)).

A step that fails on the test driver raises an `ExceptionGroup` from `test()`:

```python
def test_failure_on_driver():
    with pytest.raises(ExceptionGroup) as raised:
        price_orders().test({"order_id": "A1", "quantity": 1000})
    assert raised.group_contains(ValueError, match="1000 limit")
```

## Test a state machine

`machine.test(input, *, stub=None)` starts one instance on the same driver and runs it until
it finishes or waits for an event or a timer. `await machine.test_async(...)` is the async form.

| On the returned driver | Meaning |
| --- | --- |
| `.state` | Name of the current state. |
| `.history` | Transitions so far: items with `.state`, `.event`, `.source` (`"internal"`, `"external"`, `"timer"`; `None` on the final entry). |
| `.send(name, payload=None)` | Send an external event. |
| `.advance_time(timedelta)` | Fire state timers (`state.after(...)`) that are due. |
| `.handle` | `.handle.done()` is `True` once a final state finished. |

```python
class Ticket(ava.BaseInput):
    """One support ticket."""

    ticket_id: str


@ava.step(emits=("resolved", "escalated"))
def triage_ticket(ticket: Ticket) -> ava.Event[str]:
    """Decide whether the ticket is resolved or escalated."""
    return ava.event("resolved", "answered from the FAQ")


@ava.step
def notify_owner(ticket: Ticket) -> None:
    """Tell the on-call owner about the escalation."""


@ava.step
def close_ticket(ticket: Ticket) -> None:
    """Close the ticket."""


@ava.workflow(input=Ticket, emits=("resolved", "escalated"))
def triage():
    """Triage one ticket."""
    return triage_ticket()


@ava.machine(input=Ticket, key=("ticket_id",))
def ticket_lifecycle():
    """Follow a ticket from triage to closure."""
    open_ = ava.state(triage, initial=True)
    escalated = ava.state(notify_owner)
    closed = ava.state(close_ticket, final=True)

    open_.on("resolved") >> closed
    open_.on("escalated") >> escalated
    escalated.on("fixed") >> closed
    escalated.after(timedelta(days=7)) >> closed


def test_machine_transitions():
    m = ticket_lifecycle.test(
        Ticket(ticket_id="T1"),
        stub={triage_ticket: [ava.event("escalated", "needs an engineer")]},
    )
    assert m.state == "escalated"
    m.send("fixed")
    assert m.state == "closed"
    assert m.handle.done()
    assert [(h.state, h.event, h.source) for h in m.history] == [
        ("open_", "escalated", "internal"),
        ("escalated", "fixed", "external"),
        ("closed", None, None),
    ]


def test_machine_timer():
    m = ticket_lifecycle.test(
        Ticket(ticket_id="T2"),
        stub={triage_ticket: [ava.event("escalated", "needs an engineer")]},
    )
    m.advance_time(timedelta(days=7))
    assert m.state == "closed"
    assert m.history[-2].source == "timer"
```

A state's name is the Python variable it is assigned to in the machine function (`open_` above).

## Agent and classifier steps

Two ways to run an agent step without a model key.

**Stub the step** when the test is about the graph around it:

```python
class SumSignature(ava.Signature):
    """Compute a sum by executing Python before submitting it."""

    task: str = ava.InputField()
    answer: int = ava.OutputField()


@ava.agent_step(SumSignature, runtime=ava.runtimes.PredictRLM(max_iterations=3))
async def sum_numbers(task: str, *, agent: ava.Agent) -> int:
    """Ask the agent for the sum."""
    return (await agent(task=task)).outputs.answer


@ava.step
def write_task() -> str:
    """Write the task the agent receives."""
    return "Sum 2, 3 and 5."


@ava.workflow
def sum_workflow():
    """Have an agent compute a sum."""
    return write_task() >> sum_numbers()


def test_agent_step_stubbed():
    driver = sum_workflow().test(stub={sum_numbers: [42]})
    assert driver.handle.result(timeout=1) == 42
```

**Script the model** when the test is about the agent step itself. With the `PredictRLM`
runtime, leave `lm` unset on the runtime and put a DSPy `DummyLM` in the DSPy context. The
agent loop, the sandbox and the signature validation all run for real.

```python
import dspy
from dspy import JSONAdapter
from dspy.utils import DummyLM


def test_agent_step_with_scripted_model():
    lm = DummyLM(
        [
            {"plan": "Compute the sum", "code": "total = sum([2, 3, 5])\nprint(total)"},
            {"plan": "Submit", "code": "SUBMIT(answer=total)"},
        ],
        adapter=JSONAdapter(),
    )
    with dspy.context(lm=lm, adapter=JSONAdapter()):
        assert sum_workflow().run(executor=ava.LocalExecutor()).result(timeout=60) == 10
    assert len(lm.history) == 2
```

Each scripted reply is one agent iteration with the keys `plan` and `code`; the last one
calls `SUBMIT(<output field>=...)`. Variables persist between iterations.

When a scripted step fails with `Adapter JSONAdapter failed to parse the LM response` and
`"answer": "No more responses"`, the script ran out; that is never the first error:

- A turn whose code raises, or whose `SUBMIT` does not fit the output types, is not the end:
  the agent is shown the error and asks for another turn, which uses the next reply.
- Each reply is used once. A step retry starts the agent again at turn one with only the
  replies that are left, so retries cannot rescue a script that is too short.
- The first error is in the last request the model received:
  `print(lm.history[-1]["messages"][-1]["content"])`, after `repl_history` (each turn's code and
  its `[Error] ...` line).

A step whose runtime names a model (`PredictRLM(lm="...")`) always calls that model. To script a
real step, name the model once on the workflow, `agent_defaults={"runtime":
ava.runtimes.PredictRLM(lm="...")}`, and leave `lm` off the step's runtime; a twin workflow
without `agent_defaults` (next section) then runs the same step on the scripted model.

**Classifier steps** have no fake provider in the package. Stub the step with an
`ava.ClassificationResult` (fields `model`, `answers`, `usage`):

```python
QUESTIONS = {
    "urgent": {"type": "noul", "instructions": "Does the ticket require action today?"},
}


@ava.step
def load_ticket() -> str:
    """Load one support ticket."""
    return "We were charged twice."


@ava.classifier_step(questions=QUESTIONS)
async def classify_ticket(
    ticket: str, *, classifier: ava.Classifier
) -> ava.ClassificationResult:
    """Answer the questions about the ticket."""
    return await classifier(state=ticket)


@ava.step
def route_by_urgency(result: ava.ClassificationResult) -> str:
    """Pick the queue from the urgency answer."""
    return "today" if result.answers["urgent"].noul > 0.5 else "backlog"


@ava.workflow
def route_ticket():
    """Classify a ticket and pick its queue."""
    return load_ticket() >> classify_ticket() >> route_by_urgency()


def test_classifier_step_stubbed():
    fake = ava.ClassificationResult.model_validate(
        {
            "model": "fake",
            "answers": {"urgent": {"type": "noul", "noul": 0.9}},
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )
    driver = route_ticket().test(stub={classify_ticket: [fake]})
    assert driver.handle.result(timeout=1) == "today"
```

## Key-free on the operator

To prove a workflow with model steps on the operator when no key is set, do not put scripting
or `if TESTING` branches in step bodies. Build the graph in a plain function that takes the
model steps as arguments, and keep a twin workflow that passes plain stand-in steps:

```python
# src/triage/flow.py
from pydantic import BaseModel

import avalanche as ava


class Ticket(ava.BaseInput):
    ticket_id: str
    text: str


class Reply(BaseModel):
    ticket_id: str
    queue: str
    text: str


QUESTIONS = {
    "urgent": {"type": "noul", "instructions": "Does the ticket require action today?"},
}


class DraftReply(ava.Signature):
    """Draft a short, polite reply to a support ticket."""

    ticket: str = ava.InputField(desc="The ticket text.")
    queue: str = ava.InputField(desc="The queue the ticket goes to.")
    reply: str = ava.OutputField(desc="The reply, at most three sentences.")


@ava.classifier_step(questions=QUESTIONS)
async def classify_ticket(ticket: Ticket, *, classifier: ava.Classifier) -> ava.ClassificationResult:
    """Ask whether the ticket is urgent."""
    return await classifier(state=ticket.text)


@ava.step
def pick_queue(result: ava.ClassificationResult) -> str:
    """Pick the queue from the urgency answer."""
    return "today" if result.answers["urgent"].noul > 0.5 else "backlog"


@ava.agent_step(DraftReply, runtime=ava.runtimes.PredictRLM(max_iterations=5), retries=2)
async def draft_reply(ticket: Ticket, queue: str, *, agent: ava.Agent) -> str:
    """Ask the agent for the reply text."""
    return (await agent(ticket=ticket.text, queue=queue)).outputs.reply


@ava.dest
def build_reply(ticket: Ticket, queue: str, text: str) -> Reply:
    """Assemble the reply to send."""
    return Reply(ticket_id=ticket.ticket_id, queue=queue, text=text)


def wire_triage(classify, draft):
    """Build the graph; the two model steps are passed in."""
    ticket = ava.input
    queue = pick_queue(classify(ticket))
    return build_reply(ticket, queue, draft(ticket, queue))


@ava.workflow(
    input=Ticket,
    agent_defaults={"runtime": ava.runtimes.PredictRLM(lm="openai/gpt-5.5")},  # the model, named once
)
def triage_ticket():
    """Classify a ticket, draft a reply and assemble it."""
    return wire_triage(classify_ticket, draft_reply)
```

```python
# src/triage/flow_keyfree.py: next to flow.py; delete it, or keep it as the smoke check
import avalanche as ava
from flow import Ticket, wire_triage


@ava.step
def classify_stand_in(ticket: Ticket) -> ava.ClassificationResult:
    """Stand in for the classifier: urgent when the text says so."""
    urgent = 0.9 if "urgent" in ticket.text.lower() else 0.1
    return ava.ClassificationResult.model_validate(
        {
            "model": "stand-in",
            "answers": {"urgent": {"type": "noul", "noul": urgent}},
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    )


@ava.step
def draft_stand_in(ticket: Ticket, queue: str) -> str:
    """Stand in for the agent: a fixed reply."""
    return f"[stand-in reply for {ticket.ticket_id}, queue {queue}]"


@ava.workflow(input=Ticket)
def triage_ticket_keyfree():
    """The triage graph with stand-ins for its two model steps."""
    return wire_triage(classify_stand_in, draft_stand_in)
```

```bash
RUN=$(uv run ava run triage_ticket_keyfree --input '{"ticket_id": "T-1", "text": "Urgent: charged twice"}')
uv run ava result "$RUN" --wait --json    # queue "today", text "[stand-in reply for T-1, queue today]"
```

- This proves the graph, the plain steps, the input and the types on the operator. It does not
  run the model steps: tell the user that `triage_ticket` itself was not run, and why.
- A stand-in has the parameters and the return type of the step it replaces. For a classifier
  step it is a plain step returning an `ava.ClassificationResult` (or whatever the step returns).
- `from flow import ...` is how one scanned file imports its sibling on the operator. A test
  imports the same file as `from triage.flow import ...` and cannot import `flow_keyfree.py`;
  a test that needs a twin defines it in the test file (as in
  [Workflows that `run()` cannot start](#workflows-that-run-cannot-start)).
- In pytest, check the agent step itself with a scripted model: a twin
  `wire_triage(classify_stand_in, draft_reply)` without `agent_defaults`, run inside
  `dspy.context(lm=DummyLM([...]), ...)` as shown above.

## Rules

- Call `step.fn(...)`, never `step(...)`, in a test. `step(...)` outside a workflow raises
  `RuntimeError: Node '<name>' invoked outside of workflow context.`
- Call the workflow function to build the graph, then run it: `price_orders().test(...)`.
  `price_orders.test()` fails with `'function' object has no attribute 'test'`. A machine is
  the opposite: `ticket_lifecycle.test(...)`, no call.
- Async step functions return a coroutine from `.fn`: use `asyncio.run(step.fn(...))` or an
  async test.
- A stub value is validated against the step's return annotation. A step that returns
  `ava.Event[T]` is stubbed with `ava.event("name", value)`; the name must be in the step's `emits`.
- A stub list is consumed in order, one value per call. Running out raises
  `NonRetryableError: stub exhausted for step <name>`.
- `test()` inside a running event loop raises
  `RuntimeError: use machine.test_async/asend/aadvance_time inside an event loop`. Use the
  async forms there.
- On the test driver, a failing step raises from `test()`, `send()`, `decide()` or
  `advance_time()`, after its retries, wrapped in an `ExceptionGroup`. Use
  `pytest.raises(ExceptionGroup)` and `group_contains(...)`. A `run()` handle re-raises the
  step's own exception from `result()`.
- `step.fn(...)` on a step with `log=ava.Logger()` and no `log` argument fails with
  `'Logger' object has no attribute 'info'`. Pass `log=logging.getLogger(...)`.
- The test driver's `send()` reaches state machine events only. For `ava.wait_for_event` in a
  flow, use `run()` with `events=` or `handle.send_event(...)`.
- Steps retry in tests as in production (up to 8 attempts by default). Set `retries=0` on a
  step, or stub it, when a test counts calls.
- `workflow().test()` treats `ava.skip` as a failure: it raises an `ExceptionGroup` holding
  `StepSkipped`. Test a path that skips with `workflow().run()` or on the operator.
- `step.fn(...)` on a step that calls `ava.skip` raises `ava.StepSkipped`:
  `pytest.raises(ava.StepSkipped, match="reason")`.
- `workflow().test()` does not support `ava.decision.map`: `driver.decide("reviews[0]", ...)`
  raises `KeyError: 'decision_1[0]'`, and a timeout gives the next step one answer instead of a
  list. Use `run()` with `decisions={"reviews[0]": ...}`, `handle.decide("reviews[0]", ...)` and
  `handle.advance_time(...)`.
- `stub=` replaces the step wherever it runs, including inside child workflows started through
  `ava.call` / `ava.start_run`. A child given as a function needs no `targets=`; a child
  addressed by name does.
- `fast_timers=True` skips sleeps only. To pass a decision or event timeout, call
  `advance_time`.
- `ava.decision(...)` requires `timeout=` (use `None` for no timeout) and an outcome model
  that subclasses `ava.Event`; otherwise the graph does not build
  (`TypeError: decision requires an ava.Event subclass`).
- A workflow with `key=` or with `ava.start_run` / `ava.call` cannot use `.run()` directly
  (`RuntimeError: Keyed workflows require a run-capable scheduler host ...`). Use `.test()`.
- With `DummyLM`, pass `adapter=JSONAdapter()` to both `DummyLM(...)` and `dspy.context(...)`.
  Without it the step fails with `AgentStepExecutionError: ... LM response cannot be
  serialized to a JSON object.`
- The scripted model works for `ava.runtimes.PredictRLM` only. For `ClaudeAgent` and
  `OpenAIAgent` steps, stub the step.
- Keep model keys out of tests: a test that needs `OPENAI_API_KEY` is an integration check,
  run it through the operator.

## In the UI

Tests show nothing in the UI: no run is recorded. To see a run with its steps and traces,
start it through an operator ([local.md](local.md)).

## See also

- [local.md](local.md): run through the operator and read results from the CLI.
- [../building-blocks/python-steps.md](../building-blocks/python-steps.md),
  [../building-blocks/workflows.md](../building-blocks/workflows.md),
  [../building-blocks/agent-steps.md](../building-blocks/agent-steps.md),
  [../building-blocks/classifier-steps.md](../building-blocks/classifier-steps.md),
  [../building-blocks/state-machines.md](../building-blocks/state-machines.md).
- [../orchestration/waits.md](../orchestration/waits.md), [../orchestration/approvals.md](../orchestration/approvals.md),
  [../orchestration/retries.md](../orchestration/retries.md).
- [../guides/debugging.md](../guides/debugging.md).
