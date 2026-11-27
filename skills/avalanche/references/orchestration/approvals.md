# Approvals

`ava.decision` pauses a run until a person answers a typed question. The answer is a value the
next steps receive, so the code decides what each answer does.

## When to use it

- A person must approve, reject, choose or fill in something before the run goes on: send this
  email, issue this refund, publish this draft, pick one of these options.
- Many items to review: `ava.decision.map`, one decision per item, not one decision showing the
  whole list. A single `show` value over 4096 bytes of JSON is replaced by a short preview, so
  the person could not read a long list anyway.
- Waiting for a system, not a person: `ava.wait_for_event`, see [waits](waits.md).
- A person resolving a step whose outcome is unknown: `on_uncertain="hold"`, see [retries](retries.md).
- Long back-and-forth with several rounds: a [state machine](../building-blocks/state-machines.md).

## Interface

```python
ava.decision(outcome, *, prompt, timeout, show=None, on_timeout=None,
             assignees=None, approvers=None, slug=None)

ava.decision.map(outcome, items, *, prompt, timeout, on_timeout=None, assignees=None,
                 approvers=None, max_items=8, slug=None, show=None)
```

Called in the workflow function. The result is the answer, an instance of `outcome`.

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `outcome` | subclass of `ava.Event` | required | The answer's type. Its fields are the form the person fills in. |
| `prompt` | `str` | required | The question shown to the person. |
| `timeout` | `timedelta`, seconds, or `None` | required (no default) | How long to wait for an answer. `None` waits without limit. |
| `show` | step result, Pydantic model, or JSON value | `None` | What the person reviews before answering. Usually the output of the step that prepared the proposal. |
| `on_timeout` | instance of `outcome` | `None` | The answer used when the timeout passes. Without it, a timeout fails the run with `DecisionTimeoutError: decision_timeout` (not retried). |
| `assignees` | list of `str` | `None` | Who is asked. Stored on the wait; the local operator does not notify or enforce it. |
| `approvers` | list of `str` | `None` | The only names allowed to answer. `None`: anyone who can reach the run. An answer from anyone else is refused with `unauthorized` (403). |
| `slug` | `str` | `"decision"` | Stable name. Use it: it is the address the answer is sent to. |

`ava.decision.map` only:

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `items` | `list` or step result holding a list | required | One decision is opened per item. |
| `max_items` | `int` 0..64 | `8` | Upper bound on the number of items; more fails the run with `map_overflow`. |
| `show` | function `item -> Pydantic model` | `None` | Builds what is shown for each item. Without it the item itself is shown. |

Its result is a `list` of answers in item order. Each item is answered at `<slug>[<index>]`.

### The outcome type

Subclass `ava.Event` and add fields. Use `Literal` for choices so the form offers exactly those
values, and plain typed fields for anything the person types.

```python
class RefundReview(ava.Event):
    kind: Literal["approve", "reject"]
    note: str = ""
```

### What the person sees and answers

A pending decision is a card with: the `prompt`, the `show` value (labelled as the reviewed
artifact), one input per field of `outcome`, a required free-text reason, and the time left.
Submitting needs every required field and a non-empty reason.

### Answering

| From | How |
|---|---|
| Web UI | Open the run, select the decision, fill the form, give a reason, submit. |
| CLI | `ava run decide RUN_ID ADDRESS 'JSON' --idempotency-key ID --reason TEXT [--connect HOST:PORT]` |
| REST | `POST /api/v1/runs/{run_id}/decisions/{address}` with header `Idempotency-Key: ID` and body `{"outcome": {...}, "reason": "..."}` |

`ADDRESS` is the decision's `slug`, or its node id (`decision_1`). For a mapped decision it is
`reviews[0]`, `reviews[1]`, ... The JSON is the `outcome` fields. `--reason` is required.

List what a run is waiting for, with each decision's schema, prompt and shown value:
`GET /api/v1/runs/{run_id}/waits`. The listing and the receipts name a decision by its node id
(`decision_1`, `decision_1[0]`), never by its slug. Both forms are accepted as the address:
`reviews[0]` and `decision_1[0]` answer the same item.

The codes below are the REST answers. The CLI prints the same code after a gRPC status name:
`422` as `INVALID_ARGUMENT: invalid_wait_submission`, `409` as
`ALREADY_EXISTS: already_resolved_or_withdrawn` or `ALREADY_EXISTS: run_closed`, `403` as
`PERMISSION_DENIED: unauthorized`.

| Refusal | Cause |
|---|---|
| `invalid_wait_submission` (422) | The answer does not validate against `outcome`, or the address is not a decision of this run. |
| `reason_required` (422) | Empty reason. |
| `unauthorized` (403) | The caller is not in `approvers`. |
| `not_armed` (409) | The decision has not started yet (the steps before it are still running). |
| `already_resolved_or_withdrawn` (409) | Already answered or timed out. |
| `run_closed` (409) | The run ended: succeeded, failed or cancelled. |
| `idempotency_conflict` (409) | Same idempotency id, different answer. |

### How the answer reaches the code

- The decision's result is the validated `outcome` instance. Pass it to the steps that act on it.
- A decision does not stop the run on "reject". Rejection is data: the next step reads
  `review.kind` and returns, skips, or raises.
- Each answer is recorded with who answered, when, the reason and the value that was shown.

## Example

```python
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel

import avalanche as ava


class Refund(ava.BaseInput):
    order_id: str
    amount: float


class RefundReview(ava.Event):
    kind: Literal["approve", "reject"]
    note: str = ""


class RefundSummary(BaseModel):
    order_id: str
    amount: float
    reason: str


@ava.step
def summarize_refund(refund: Refund) -> RefundSummary:
    """Summarize the refund for the reviewer."""
    return RefundSummary(order_id=refund.order_id, amount=refund.amount, reason="damaged item")


@ava.step(retries=0)
def issue_refund(summary: RefundSummary, review: RefundReview) -> str:
    """Issue the refund when approved."""
    if review.kind != "approve":
        return f"refund {summary.order_id} rejected: {review.note}"
    return f"refunded {summary.amount} for {summary.order_id}"


@ava.workflow(input=Refund)
def approve_refund():
    """Ask a person to approve a refund, then act on the answer."""
    summary = summarize_refund(ava.input)
    review = ava.decision(
        RefundReview,
        prompt="Approve this refund?",
        show=summary,
        timeout=timedelta(hours=24),
        on_timeout=RefundReview(kind="reject", note="no answer in 24h"),
        slug="review_refund",
    )
    return issue_refund(summary, review)
```

```bash
ava run approve_refund --input '{"order_id": "A-17", "amount": 42.5}'   # run_3e458caf
ava run list --flow approve_refund         # waiting   waiting on decision
ava run decide run_3e458caf review_refund '{"kind": "approve", "note": "ok"}' \
  --idempotency-key review-1 --reason "checked the photos"
ava result run_3e458caf --wait --json      # "refunded 42.5 for A-17"
```

The same answer over REST:

```bash
curl -X POST http://127.0.0.1:7435/api/v1/runs/run_3e458caf/decisions/review_refund \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: review-1' \
  -d '{"outcome": {"kind": "reject", "note": "too old"}, "reason": "policy"}'
```

One decision per item, then a step that applies its own rule:

```python
@ava.step
def count_approved(reviews: list[RefundReview]) -> int:
    """Count the approved items."""
    return sum(review.kind == "approve" for review in reviews)


@ava.workflow
def review_each():
    """Ask for one answer per order."""
    reviews = ava.decision.map(
        RefundReview, ["A-1", "A-2"], prompt="Approve this order?", timeout=None, slug="reviews"
    )
    return count_approved(reviews)
```

## Rules

- Put the side effect in a step after the decision, never before it. Prepare and show a proposal,
  ask, then act on the answer.
- Show exactly what will be acted on: pass the same step result to `show=` and to the acting step.
- Keep `show` small and readable. A shown value over 4096 bytes of JSON is replaced by a short
  preview with a digest. For a list of items, use `ava.decision.map`.
- Always pass `timeout`. Choose `on_timeout` deliberately: a safe default answer (usually
  "reject"), or none so that silence fails the run.
- `on_timeout` must be an instance of the same `outcome` class
  (`TypeError: on_timeout must be an instance of the declared Event`).
- `outcome` must subclass `ava.Event`, not `BaseModel` (`TypeError: decision requires an ava.Event subclass`).
- Answers are validated strictly and unknown fields are refused.
- Give each decision a `slug`. Without one, addresses are `decision`, `decision_2`, ... and move
  when the workflow changes.
- A decision is answered once. A second, different answer is refused.
- On the local operator every answer is recorded under the OS user that runs the operator; an
  `approvers` list that does not contain that name refuses all answers there.
- A decision is a wait: it is never retried, it is skipped if a step it depends on was skipped,
  and on a local operator it does not survive an operator restart (see [waits](waits.md#operator-restarts)).
- An operator cannot fast-forward a decision's timeout. To check `on_timeout` on the operator,
  make the timeout a value you can set to a few seconds for the check; in process use
  `advance_time` (below).
- Set `retries=0` on the acting step when repeating it would repeat the side effect.

### Testing without a person

```python
run = approve_refund().run(
    input={"order_id": "A-1", "amount": 2.0},
    decisions={"review_refund": RefundReview(kind="approve")},
)
assert run.result(timeout=10) == "refunded 2.0 for A-1"
```

Mapped decisions are preloaded as `{"reviews[0]": ..., "reviews[1]": ...}`, or answered live with
`run.decide("reviews[0]", ...)`; `run.advance_time(...)` passes the timeout of the unanswered
ones. Use `.run()` for them: `workflow().test()` does not support `ava.decision.map`. See
[testing](../run/testing.md).

## In the UI

- The decision is a node in the graph, marked as needing attention while it waits. The run's
  status is `waiting` with the note `waiting on decision`.
- The person reads `prompt` and the `show` value, then a form built from the `outcome` fields:
  field names are the labels, so name them for the reader (`kind`, `note`, `refund_amount`).
- After the answer the node shows the recorded outcome and reason.

## See also

- [Waits](waits.md): sleeps and events, restarts
- [Control flow](control-flow.md#branching): branching on the answer
- [Retries and reruns](retries.md): `on_uncertain="hold"`, run statuses
- [Patterns](../guides/patterns.md): approve-then-act
- [Operator API](../reference/operator-api.md) and [ava CLI](../reference/ava-cli.md)
