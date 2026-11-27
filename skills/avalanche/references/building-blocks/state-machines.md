# State machines

A machine is a long-lived run that sits in one named state at a time and moves when an event arrives,
a timer expires, or its own work emits an event. Each state runs a step or a workflow on entry.

## When to use it

The DAG workflow ([workflows.md](workflows.md)) is always the default. Choose a machine only when all
of these hold:

- The process follows one entity (an order, a pull request, an article) identified by a key.
- Events come from outside, in an order and number you cannot fix in advance.
- The process can go back to an earlier stage, which a DAG cannot express.

Use something simpler when you can:

| Need | Use |
|---|---|
| One pause for a person | `ava.decision` in a DAG: [../orchestration/approvals.md](../orchestration/approvals.md) |
| One pause for an outside signal or a delay | `ava.wait_for_event`, `ava.sleep`: [../orchestration/waits.md](../orchestration/waits.md) |
| Repeat over a list, branch, call another flow | [../orchestration/control-flow.md](../orchestration/control-flow.md) |
| Retry a failing step | [../orchestration/retries.md](../orchestration/retries.md) |

## Interface

```python
@ava.machine(*, input: type[ava.BaseInput], key: tuple[str, ...], cron=None, webhook=None)
def my_machine():                                   # takes no parameters, returns nothing
    a = ava.state(work, *, initial=False, final=False, defer=())
    a.on(name, *, payload=None) >> b                # event transition
    a.after(delay) >> b                             # timer transition
```

### `ava.machine`

| Option | Type | Default | Meaning |
|---|---|---|---|
| `input` | `type[ava.BaseInput]` | required | Input model of an instance. Injected into the steps of every state. |
| `key` | `tuple[str, ...]` | required | Input field names that identify an instance. At most one instance exists per key value, forever. |
| `cron` | `str \| None` | `None` | Scheduled start. See [../connect/triggers.md](../connect/triggers.md). |
| `webhook` | `ava.Webhook \| bool \| None` | `None` | HTTP start. See [../connect/triggers.md](../connect/triggers.md). |

The decorated name is an `ava.Machine`. It is discovered like a workflow (see [workflows.md](workflows.md)).

### `ava.state`

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `work` | a step, or an `@ava.workflow` function | required | Runs each time the state is entered. |
| `initial` | `bool` | `False` | The state an instance starts in. Exactly one. |
| `final` | `bool` | `False` | Entering it runs its work, then closes the instance. Its work's output is the run result. |
| `defer` | `list[str] \| tuple[str, ...]` | `()` | Event names to keep in the inbox while in this state, for a later state that accepts them. |

The state's name is the local variable it is assigned to.

### Transitions

| Call | Meaning |
|---|---|
| `s.on(name, payload=Model) >> target` | When event `name` arrives in `s`, go to `target`. `payload` is a Pydantic model the event data must match; without it the data must be empty. |
| `s.after(timedelta) >> target` | Go to `target` when `s` has been entered for that long. The timer is dropped when `s` is left. |

An event is **external** (sent by the CLI, the API or another workflow) or **internal** (returned by the
state's own work). For an internal event, the last step of the work declares `emits=(...)` and returns
`ava.event(name, output)`; its return annotation is `ava.Event[OutputModel]`.

### What happens in a state

1. The state's work runs to the end. Nothing interrupts it; events that arrive meanwhile wait in the inbox.
2. If the state is final, the instance closes.
3. Otherwise the first match wins: an expired timer, then the internal event the work returned, then the
   oldest inbox event this state accepts.
4. If nothing matches, the instance waits (status `waiting`).

Data carried into the next state: the event's payload, or the internal event's `output`. A timer or an
event without payload keeps the previous value. The first free data parameter of the next state's first
step receives it; the machine input is injected by annotation, as in any step.

### `ava.Machine`

| Member | Meaning |
|---|---|
| `.test(input, *, stub=None)` | Run an instance in memory with fake time. Returns the test driver below. `.test_async` in async code. |
| `.send(key, event, payload=None, idempotency_key=None, *, when=True)` | Inside a workflow function: a step that sends an event to an instance. Output: `ava.MachineEventReceipt`, or `None` when `when` is false. |
| `.key(input)` | The key tuple for an input. |
| `.spec` | The validated definition (states, transitions, work graphs). |

In `.send`, `key` is the tuple of key values in `key=` order, or the instance's run id. Arguments may be
step outputs. `idempotency_key` defaults to the sending step's `ctx.step_key`.

### Starting and calling from a workflow

| Call | Output |
|---|---|
| `ava.start_run(machine, input, *, when=True)` | `ava.RunStart`: `run_id`, `key`, `created` (`False` when the key already had an instance), `read_state`. |
| `ava.call(machine, input, *, timeout=None, when=True)` | Waits until the instance closes; output is the final state's result. See [../orchestration/control-flow.md](../orchestration/control-flow.md). |

### Result models

| Model | Fields |
|---|---|
| `ava.MachineEventReceipt` | `receipt_id: str`, `key: str`, `accepted_at: float`, `duplicate: bool`, `status: "accepted" \| "ignored"` |
| `ava.MachineInstance` | `machine`, `key`, `run_id`, `deployment_id`, `status`, `state`, `entry`, `version`, `accepts: list[str]`, `due: float \| None`, `finished: bool` |
| `ava.RunStart` | `run_id: str`, `key: list \| None`, `created: bool`, `read_state: "retained" \| "expired"` |

### Test driver (`machine.test(...)`)

| Member | Meaning |
|---|---|
| `.state` | Name of the current state. |
| `.history` | List of `(state, event, source)` records; `source` is `"internal"`, `"external"`, `"timer"` or `None` for the closing entry. |
| `.send(name, payload=None)` | Send an external event, then run until the machine waits or closes. |
| `.advance_time(timedelta)` | Move fake time forward; fires due timers. |
| `.decide(node, payload, *, reason)` | Answer an `ava.decision` wait inside the current state's work. |
| `.handle.result()` / `.handle.done()` | Result of the closed instance / whether it closed. |
| `.asend`, `.aadvance_time`, `.adecide` | Async variants, for use inside an event loop. |

`stub={step: [value, ...]}` replaces a step: each call takes the next value instead of running the
function. Values are validated against the step's return annotation.

## Example

`flows/article_machine.py`:

```python
from datetime import timedelta

import avalanche as ava
from pydantic import BaseModel


class Article(ava.BaseInput):
    site: str
    slug: str
    title: str


class Submission(BaseModel):
    body: str


class Review(BaseModel):
    approved: bool
    comment: str


@ava.step
def open_draft(article: Article) -> str:
    """Record that the draft is open."""
    return f"draft open: {article.title}"


@ava.step(emits=("approved", "changes_requested"))
def review_submission(article: Article, submission: Submission) -> ava.Event[Review]:
    """Review the submitted body and emit the outcome."""
    approved = len(submission.body.split()) >= 3
    review = Review(approved=approved, comment="ok" if approved else "too short")
    return ava.event("approved" if approved else "changes_requested", review)


@ava.step
def publish_article(article: Article, review: Review) -> str:
    """Publish the approved article."""
    return f"published {article.site}/{article.slug} ({review.comment})"


@ava.step
def archive_draft(article: Article) -> str:
    """Archive a draft nobody submitted."""
    return f"archived {article.slug}"


@ava.machine(input=Article, key=("site", "slug"))
def article_lifecycle():
    """Follow one article from draft to publication."""
    drafting = ava.state(open_draft, initial=True)
    reviewing = ava.state(review_submission)
    published = ava.state(publish_article, final=True)
    archived = ava.state(archive_draft, final=True)

    drafting.on("submitted", payload=Submission) >> reviewing
    drafting.after(timedelta(days=7)) >> archived
    reviewing.on("changes_requested") >> drafting
    reviewing.on("approved") >> published


def test_article_is_published() -> None:
    m = article_lifecycle.test(Article(site="blog", slug="hello", title="Hello"))
    assert m.state == "drafting"
    m.send("submitted", Submission(body="too short"))
    assert m.state == "drafting"
    m.send("submitted", Submission(body="long enough body now"))
    assert m.state == "published"
    assert m.handle.result() == "published blog/hello (ok)"
    assert [(h.state, h.event, h.source) for h in m.history] == [
        ("drafting", "submitted", "external"),
        ("reviewing", "changes_requested", "internal"),
        ("drafting", "submitted", "external"),
        ("reviewing", "approved", "internal"),
        ("published", None, None),
    ]


def test_article_is_archived_after_a_week() -> None:
    m = article_lifecycle.test({"site": "blog", "slug": "idle", "title": "Idle"})
    m.advance_time(timedelta(days=7))
    assert m.state == "archived"
    assert m.handle.result() == "archived idle"
```

A workflow that feeds the machine, `flows/article_router.py`:

```python
import avalanche as ava

from article_machine import Article, Submission, article_lifecycle


class Delivery(ava.BaseInput):
    site: str
    slug: str
    title: str = ""
    body: str = ""
    delivery_id: str


@ava.step
def build_article(delivery: Delivery) -> Article:
    """Build the machine input from the delivery."""
    return Article(site=delivery.site, slug=delivery.slug, title=delivery.title)


@ava.step
def has_body(delivery: Delivery) -> bool:
    """Tell whether the delivery carries a submission."""
    return bool(delivery.body)


@ava.step
def build_key(delivery: Delivery) -> tuple[str, str]:
    """Build the machine key in key-field order."""
    return delivery.site, delivery.slug


@ava.step
def build_submission(delivery: Delivery) -> Submission:
    """Build the event payload."""
    return Submission(body=delivery.body)


@ava.workflow(input=Delivery)
def route_delivery():
    """Open the article's machine if needed, then forward a submission to it."""
    started = ava.start_run(article_lifecycle, build_article())
    sent = article_lifecycle.send(
        build_key(),
        "submitted",
        build_submission(),
        idempotency_key=ava.input.delivery_id,
        when=has_body(),
    )
    started >> sent
    return started, sent
```

## Running it locally

```bash
ava dev flows/                                   # in another terminal
ava flow show article_lifecycle                  # Kind, key fields, states

# Start an instance. The same key again prints the same run id.
ava run article_lifecycle --input '{"site": "blog", "slug": "hello", "title": "Hello"}'

# Send an event, by key (values are JSON, so strings carry quotes) or by run id.
ava machine send article_lifecycle submitted --key site='"blog"' --key slug='"hello"' \
    --data '{"body": "long enough body now"}'
ava machine send submitted --run RUN_ID --data '{"body": "long enough body now"}'

ava run list --flow article_lifecycle            # TYPE RUN DEFINITION STATUS NOTE
ava run history RUN_ID                           # transitions: from, to, event, source, entry
ava result RUN_ID --json                         # result of the closed instance
ava result RUN_ID --json --entry 1               # result of one completed state entry
ava run cancel RUN_ID
```

| Command | Options |
|---|---|
| `ava machine send MACHINE EVENT --key FIELD=JSON ...` | `--data JSON\|@file` (default `{}`), `--idempotency-key KEY` (default: random), `--connect HOST:PORT` (default `localhost:7433`) |
| `ava machine send EVENT --run RUN_ID` | Same options. Do not give the machine name with `--run`. |
| `ava run show RUN_ID` or `ava run show --flow MACHINE --key FIELD=JSON ...` | `--json`, `--connect` |
| `ava run history RUN_ID` | `--limit 1..100` (default 20), `--cursor`, `--connect` |
| `ava run retry RUN_ID --entry` | Retry an interrupted state entry. |
| `ava run links RUN_ID` | Parent and child runs. |

`ava machine send` prints the receipt as JSON. Exit code 1 with a one-line reason on refusal.

To test a timer without waiting, run under `ava dev` and fire the next timer of an instance:

```bash
curl -X POST -H 'content-type: application/json' -d '{}' \
    http://127.0.0.1:7435/api/v1/runs/RUN_ID/machine/timers:fire-next
```

Under `ava operator` this route answers `Timer fast-forward is available only in ava dev`.

## Rules

- `input` and `key` are required and keyword-only. `key` fields must be plain input fields (`str`, `int`,
  `bool`, dates, enums, plain nested models): no aliases, default factories or custom validators.
- Assign every `ava.state(...)` to its own local variable: `ValueError: each state must be assigned a
  unique builder-local name`. Pick names a reader understands; they are the state names everywhere.
- Exactly one `initial=True`: `ValueError: machine requires exactly one initial state`.
- A final state has no outgoing transition: `final state <name> has outgoing transitions`.
- One transition per event name per state: `duplicate event transition in state <name>`.
- `ava.state` outside a machine function: `RuntimeError: ava.state must be used inside an @ava.machine
  builder`.
- A step that returns `ava.event(...)` must list the name in `emits=`. A workflow used as state work
  must repeat it: `@ava.workflow(emits=...)` equal to the `emits` of the step it returns.
- An internal event only triggers a transition declared on the same state: `internal event '<name>'
  not declared in <state> work emits`. An emitted or deferred event no state accepts is a discovery
  warning: `event '<name>' is accepted by no state`.
- An event the current state neither accepts nor defers is not an error: the receipt says
  `"status": "ignored"` and nothing happens. Check the receipt.
- On entering a state, inbox events it neither accepts nor defers are dropped.
- Event data must be a JSON object matching the transition's `payload` model: `Invalid machine
  input/payload: <field>: missing`. With no `payload`, send `{}`.
- Sending to a closed instance fails with `run_closed`. A full inbox (64 events or 1 MiB) fails with
  `inbox_full`. The same idempotency key with different data fails with `idempotency_conflict`.
- Reuse the idempotency key when you resend the same event (a webhook delivery id). The duplicate is
  accepted once and reported with `"duplicate": true`.
- One state's work holds at most 64 steps, counting mapped items.
- A state's work may contain `ava.sleep` and `ava.decision`. Keep it short: no transition happens
  until the work ends.
- `machine.send` and `ava.start_run` inside a workflow need the operator. `Workflow.run()` raises
  `RuntimeError: Declared RUN/MACHINE nodes require a scheduler host`; use `workflow.test()`.
- A machine cannot be rerun from a step. Start a new key, or retry the interrupted state entry.
- Calling `.test()` inside a running event loop raises `RuntimeError: use machine.test_async/asend/
  aadvance_time inside an event loop`.
- An exhausted stub fails with `NonRetryableError: stub exhausted for step <name>`: give one value per
  expected call.

## In the UI

- The machine is listed with the flows, kind `machine`, named after its function.
- State names everywhere (UI, `ava flow show`, `ava run history`) are the variable names from the
  machine function. Event names are the strings given to `.on(...)`. Choose both for a human reader.
- The steps of a state's work show name, docstring line and output like any step
  ([python-steps.md](python-steps.md)).
- A waiting instance has status `waiting`; `ava run list` notes `waiting on event`.

## See also

- [workflows.md](workflows.md): the default DAG shape, discovery, `emits=`.
- [python-steps.md](python-steps.md): `emits=` on steps, `ctx.step_key`.
- [../orchestration/waits.md](../orchestration/waits.md): events and timers inside a DAG.
- [../orchestration/control-flow.md](../orchestration/control-flow.md): `ava.start_run`, `ava.call`.
- [../run/testing.md](../run/testing.md): test drivers and stubs.
- [../reference/ava-cli.md](../reference/ava-cli.md), [../reference/operator-api.md](../reference/operator-api.md): every command and route.
