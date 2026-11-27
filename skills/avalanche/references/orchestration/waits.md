# Waits

`ava.sleep` pauses a run for a time. `ava.wait_for_event` pauses it until something outside
sends it a typed event. A waiting run uses no worker.

## When to use it

- `ava.sleep`: a cool-down, a follow-up delay, "continue tomorrow at 9:00".
- `ava.wait_for_event`: the next step depends on something another system does later: a payment
  arrives, a document is signed, a job finishes.
- Waiting for a person's answer: use `ava.decision`, see [approvals](approvals.md).
- Waiting for another workflow: use `ava.call`, see [control flow](control-flow.md#child-workflows).
- Many events over days, in any order, with timers per state: use a
  [state machine](../building-blocks/state-machines.md).
- Do not use `time.sleep()` in a step for long pauses: it holds a worker and is lost with the step.

## Interface

```python
ava.sleep(duration=None, *, until=None, slug=None)
ava.wait_for_event(model, *, key, timeout, slug=None)
```

Both are called in the workflow function, like a step, and return a result you chain with `>>`
or pass to steps.

`ava.sleep`:

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `duration` | `timedelta` or seconds (`float`) | `None` | How long to pause, counted from when the wait starts. |
| `until` | timezone-aware `datetime` | `None` | Pause until this instant. A past instant ends at once. |
| `slug` | `str` | `"sleep"` | Stable name of the node. |

Give exactly one of `duration` and `until`. The sleep has no output: order it with `>>`.

`ava.wait_for_event`:

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `model` | Pydantic model class | required | The event's payload type. A sent payload must validate against it, strictly. |
| `key` | non-empty `str` | required | The event's name within the run, for example `"payment.received"`. Senders address it by this exact key. |
| `timeout` | `timedelta`, seconds, or `None` | required (no default) | How long to wait. `None` waits without limit. |
| `slug` | `str` | `"wait_for_event"` | Stable name of the node. |

Output: an instance of `model`, or `None` when the timeout passed first. Type the consuming
parameter `Model | None` and handle both.

### Sending an event

An event is declared by the `ava.wait_for_event(Model, key=...)` call itself; there is nothing
else to register. It is sent to one run, by run id and key.

```bash
ava event send RUN_ID KEY 'JSON' --idempotency-key ID [--connect HOST:PORT]
```

```text
POST /api/v1/runs/{run_id}/events/{key}
Content-Type: application/json
Idempotency-Key: ID

{"invoice_id": "inv-42", "amount": 99.5}
```

| Part | Meaning |
|---|---|
| `RUN_ID` | The run to deliver to. Keep the run id when you start the run, or find it by [run key](retries.md#run-keys). |
| `KEY` | The `key=` of the wait. |
| JSON body | One JSON object that validates against the wait's model. |
| `--idempotency-key` / `Idempotency-Key` | Required. Sending again with the same id and the same payload returns the first receipt and delivers nothing new. |

A successful send prints a receipt (receipt id, run id, `address: "event:<key>"`, time, digests).

| Refusal | Cause |
|---|---|
| `unknown_key` (404) | The run's workflow has no `wait_for_event` with that key. |
| `invalid_wait_submission` (422) | The payload does not validate against the model. |
| `idempotency_conflict` (409) | Same idempotency id, different payload. |
| `run_closed` (409) | The run already ended. |
| `inbox_full` (409) | More than 64 undelivered events, or more than 1 MiB of them, are queued for the run. |

The CLI prints refusals as a gRPC status name and the code, not the HTTP number: `404` is
`NOT_FOUND: unknown_key`, `409` is `ALREADY_EXISTS: run_closed`, `422` is
`INVALID_ARGUMENT: invalid_wait_submission`. An unknown run id is `NOT_FOUND: unknown_run`.

### Sending an event from another system

The outside system knows a business id, not the run id. Give the workflow a
[run key](retries.md#run-keys) on that id (below: a workflow `await_payment` declared with
`input=Order, key=("order_id",)`); the sender then makes two calls:

```bash
# 1. find the run of this key              -> {"run_id": "run_3296cea8", "read_state": "retained", "key_json": "[\"P1\"]"}
curl -s -G http://127.0.0.1:7435/api/v1/runs:resolve \
  --data-urlencode 'workflow_selector=await_payment' \
  --data-urlencode 'key_fields={"order_id":"P1"}'

# 2. send the event to that run            -> a receipt
curl -s -X POST http://127.0.0.1:7435/api/v1/runs/run_3296cea8/events/payment.received \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: pay-P1' \
  -d '{"amount": 5}'
```

What is not possible:

- No call sends an event by key, by workflow, or to several runs: an event goes to one run id.
- No run for that key yet: step 1 answers `404 NOT_FOUND`. The event cannot be left for a run
  that will start later; start the run first, or make the sender retry.
- A workflow without `key=` cannot be resolved (`400 Workflow does not declare a run key`): the
  sender must keep the run id it got when the run started.
- If `key=` changes in the code, runs started under the old key no longer resolve. They still
  take events by run id.
- The REST API has no authentication and listens on loopback. A system on another machine needs
  your own authenticated proxy in front of it.

Pending and resolved waits of a run: `GET /api/v1/runs/{run_id}/waits`. Each card has `node_id`,
`kind` (`sleep`, `event`, `decision`, `run`), `state` (`pending`, `waiting`, `resolved`,
`withdrawn`), `key`, `schema` and `remaining_seconds`.

## Example

```python
from datetime import timedelta

from pydantic import BaseModel

import avalanche as ava


class Payment(BaseModel):
    invoice_id: str
    amount: float


@ava.step
def send_invoice() -> str:
    """Send the invoice and return its id."""
    return "inv-42"


@ava.step
def close_invoice(invoice_id: str, payment: Payment | None) -> str:
    """Close the invoice as paid, or as overdue when no payment arrived."""
    if payment is None:
        return f"{invoice_id}: overdue"
    return f"{invoice_id}: paid {payment.amount}"


@ava.workflow
def collect_payment():
    """Send an invoice, wait for the payment event, then close it."""
    invoice = send_invoice()
    cooldown = ava.sleep(timedelta(seconds=2))
    payment = ava.wait_for_event(Payment, key="payment.received", timeout=timedelta(days=10))
    invoice >> cooldown >> payment
    return close_invoice(invoice, payment)
```

```bash
ava run collect_payment                       # prints run_1b1299f6
ava event send run_1b1299f6 payment.received \
  '{"invoice_id": "inv-42", "amount": 99.5}' --idempotency-key pay-1
ava result run_1b1299f6 --wait --json         # "inv-42: paid 99.5"
```

With no event before the timeout the output is `"inv-42: overdue"`.

## Rules

- Call waits in the workflow function only. In a step, or anywhere else, they raise
  `RuntimeError: Node 'sleep' invoked outside of workflow context`.
- A wait starts when the steps it depends on are done. Without `>>` or an argument linking it, it
  starts when the run starts. In the example, `invoice >> cooldown >> payment` sets the order.
- An event sent before its wait has started is kept and delivered when the wait starts. Senders
  need not time their send.
- Each `key` may be used by one `wait_for_event` per workflow:
  `ValueError: Duplicate event consumer key in workflow`. One wait takes one event.
- `timeout` must be passed, even as `None`. A timeout is not an error: the output is `None`.
- `ava.sleep()` with neither or both arguments raises
  `ValueError: sleep requires exactly one of duration or until`; a naive `until` raises
  `ValueError: until must be timezone-aware`.
- The event payload is strict: `"5"` is not accepted for an `int` field, and unknown keys follow
  the model's own config.
- Waits are never retried. If a step a wait depends on was skipped, the wait is skipped too.
- The sender needs the run id. Put it where the outside system can return it (an order record,
  the callback URL), or give the workflow a run key and resolve it.

### Operator restarts

- The local operator (`ava dev`, `ava operator`) does not resume workflow runs. A run that is
  `running`, or `waiting` on a sleep, an event or a decision, when the operator stops is marked
  `failed` at the next start, with the step error `Operator restarted before the run finished; it
  was not resumed`. A run parked on a child run it called with `ava.call` is kept.
- So on a local operator a wait lasts only as long as the operator process. Finished steps keep
  their saved outputs: after a restart, [rerun](retries.md#reruns) from the wait's slug. A
  workflow with `key=` cannot be rerun; it needs a key that allows a new start
  ([run keys](retries.md#run-keys)).
- For waits that must outlive restarts on a local operator, use a
  [state machine](../building-blocks/state-machines.md): its states, timers and received events
  are stored and picked up again at start.
- On Cloud, a waiting run is parked in the scheduler's store and holds no worker; see
  [cloud](../run/cloud.md).

### Testing without waiting

An operator cannot fast-forward a workflow's `ava.sleep`, event timeout or decision timeout:
they last their real time there (`timers:fire-next` under `ava dev` is for state machines
only). To check a timeout path on the operator, make the duration a value you can set to a few
seconds for the check (a module constant, or an environment variable read at import). In
process, time can be moved.

An embedded run can be given its events up front and skip its sleeps:

```python
run = collect_payment().run(
    fast_timers=True,
    events={"payment.received": Payment(invoice_id="inv-42", amount=1.0)},
)
assert run.result(timeout=10) == "inv-42: paid 1.0"
```

`run.send_event(key, payload, idempotency_key=...)`, `run.wait_until_waiting()` and
`run.advance_time(timedelta(...))` drive a live handle. See [testing](../run/testing.md).

## In the UI

- Waits are nodes in the graph, named `sleep` and `wait_for_event` (or after the child workflow
  for `ava.call`). They have no docstring; the `key` and the remaining time are shown on the wait.
- The run's status is `waiting`, with the note `waiting on sleep` or `waiting on event`.
- The event's model defines the fields shown for the event; name its fields clearly.

## See also

- [Approvals](approvals.md): `ava.decision`, the wait a person answers
- [Control flow](control-flow.md#child-workflows): `ava.call`, waiting for a child run
- [Retries and reruns](retries.md): statuses, rerunning after a restart
- [Triggers](../connect/triggers.md): starting runs from outside
- [State machines](../building-blocks/state-machines.md): long-lived, event-driven processes
- [Operator API](../reference/operator-api.md) and [ava CLI](../reference/ava-cli.md)
