# Patterns

Ten complete workflows for the common shapes. Each is one file you can drop into a scanned folder
and run on a local operator. Copy the shape, then replace the step bodies.

| # | Shape | Use when |
|---|---|---|
| 1 | [Linear: extract, transform, deliver](#1-linear-extract-transform-deliver) | One thing after another. |
| 2 | [Fan out over items, then merge](#2-fan-out-over-items-then-merge) | The same work for each item of a list. |
| 3 | [Parallel branches, joined](#3-parallel-branches-joined) | A few different, independent steps feed one step. |
| 4 | [Agent extracts, classifier labels, skip routes](#4-agent-extracts-classifier-labels-skip-routes) | Free text must be read, labelled, and sent down one branch. |
| 5 | [Approve, then act](#5-approve-then-act) | A person must agree before a side effect. |
| 6 | [Wait for an outside event, with a timeout](#6-wait-for-an-outside-event-with-a-timeout) | Another system answers later, or never. |
| 7 | [Webhook start, once per event, retry-safe effect](#7-webhook-start-once-per-event-retry-safe-effect) | A sender that redelivers, and an effect that must happen once. |
| 8 | [Scheduled run](#8-scheduled-run) | The clock starts the run. |
| 9 | [Parent workflow calls a child workflow](#9-parent-workflow-calls-a-child-workflow) | A whole workflow is reused as one step. |
| 10 | [Long-running case as a state machine](#10-long-running-case-as-a-state-machine) | One entity reacts to events over days and can go back a stage. |

Commands assume a local operator on its default ports, started with `uv run ava dev flows/` (see
[../run/local.md](../run/local.md)). `ava result RUN_ID --wait --json` prints a tagged value; the
results below show the plain value inside it.

---

## 1. Linear: extract, transform, deliver

Use when the process is a straight line: get data, change it, send it.

```python
import avalanche as ava
from pydantic import BaseModel


class Order(BaseModel):
    order_id: str
    amount: float


class OrderBatch(BaseModel):
    orders: list[Order]


class Report(BaseModel):
    count: int
    total: float


@ava.source
def load_orders() -> OrderBatch:
    """Load the orders of the day."""
    return OrderBatch(orders=[Order(order_id="a1", amount=10.0), Order(order_id="b2", amount=32.5)])


@ava.step
def summarize_orders(batch: OrderBatch) -> Report:
    """Count the orders and add up their amounts."""
    return Report(count=len(batch.orders), total=sum(order.amount for order in batch.orders))


@ava.dest
def deliver_report(report: Report) -> str:
    """Deliver the report and return a receipt."""
    return f"delivered: {report.count} orders, total {report.total}"


@ava.workflow
def report_orders():
    """Load orders, summarize them, deliver the report."""
    return load_orders() >> summarize_orders() >> deliver_report()
```

```bash
ava run report_orders                  # result: "delivered: 2 orders, total 42.5"
```

- `load_orders` returns a model that holds the list, not a bare `list`: `>>` spreads a list or
  tuple over the next step's parameters. See [../building-blocks/workflows.md](../building-blocks/workflows.md).
- `@ava.source` / `@ava.dest` run like `@ava.step`; they only mark the node as data in / data out.
- Every step retries up to 8 attempts by default. Set `retries=0` on `deliver_report` if a
  repeated delivery is harmful, or use pattern 7.

## 2. Fan out over items, then merge

Use when one step must run once per item of a list that is known only at run time.

```python
import avalanche as ava
from pydantic import BaseModel


class Batch(ava.BaseInput):
    urls: list[str] = ["https://example.com/a", "https://example.com/b"]


class Page(BaseModel):
    url: str
    words: int


class Digest(BaseModel):
    pages: int
    words: int


@ava.step(retries=2)
def fetch_page(url: str, min_words: int) -> Page:
    """Fetch one page and count its words."""
    words = len(url) * 10
    if words < min_words:
        ava.skip(f"{url} is too short")
    return Page(url=url, words=words)


@ava.step
def merge_pages(pages: list[Page]) -> Digest:
    """Merge the per-page results into one digest."""
    return Digest(pages=len(pages), words=sum(page.words for page in pages))


@ava.workflow(input=Batch)
def digest_pages():
    """Fetch every page in parallel, then merge the results."""
    pages = fetch_page.map(ava.input.urls, min_words=100, max_items=32)
    return merge_pages(pages)
```

```bash
ava run digest_pages                                                       # {"pages": 2, "words": 420}
ava run digest_pages --input '{"urls": ["https://example.com/a", "x"]}'   # "x" is skipped: pages 1
```

- Set `max_items` to the longest list you expect (default 8, hard cap 64). A longer list fails the
  run with `map_overflow`; it is never cut. More than 64 items: map over chunks.
- Pass the map result as an explicit argument (`merge_pages(pages)`): the next step receives one
  `list`. Positional arguments of `.map` are iterated; keyword arguments go unchanged to every item.
- A skipped item is left out of the list; a failed item fails the whole map. Each item has its own
  retries. See [../orchestration/control-flow.md](../orchestration/control-flow.md).
- Mapping an agent step starts one agent call per item, all together, each with its own sandbox
  on PredictRLM. For more than a handful of items, give one agent call the whole list instead.

## 3. Parallel branches, joined

Use when a small, fixed number of different steps can run at the same time and one step needs
all their outputs.

```python
import avalanche as ava
from pydantic import BaseModel


class Application(ava.BaseInput):
    applicant: str
    income: int
    requested: int


class Verdict(BaseModel):
    applicant: str
    approved: bool
    reasons: list[str]


@ava.step
def check_credit(application: Application) -> bool:
    """Check that the income covers the requested amount."""
    return application.income * 3 >= application.requested


@ava.step
def check_identity(application: Application) -> bool:
    """Check that the applicant is known."""
    return bool(application.applicant.strip())


@ava.step
def decide_application(credit_ok: bool, identity_ok: bool, application: Application) -> Verdict:
    """Join both checks into one verdict."""
    reasons = [name for name, ok in (("credit", credit_ok), ("identity", identity_ok)) if not ok]
    return Verdict(applicant=application.applicant, approved=not reasons, reasons=reasons)


@ava.workflow(input=Application)
def review_application():
    """Run two independent checks in parallel, then join them."""
    return (check_credit() & check_identity()) >> decide_application()
```

```bash
ava run review_application --input '{"applicant": "Ada", "income": 1000, "requested": 5000}'
# {"applicant": "Ada", "approved": false, "reasons": ["credit"]}
```

- The parentheses are required. `a() & b() >> c()` means `a() & (b() >> c())`: `c` gets only
  `b`'s output and fails with a missing argument.
- The outputs fill the join step's parameters in the order written: `credit_ok`, then
  `identity_ok`. `application` is injected by its type, so it does not take a slot.
- The explicit form is equivalent and often clearer: `decide_application(check_credit(), check_identity())`.

## 4. Agent extracts, classifier labels, skip routes

Use when free text must be turned into a structured object (agent step), labelled with a
probability (classifier step), then handled by exactly one of several branches (skip).

```python
from typing import Literal

from pydantic import BaseModel

import avalanche as ava


class Email(ava.BaseInput):
    sender: str
    body: str


class Claim(BaseModel):
    customer: str
    order_id: str
    request: str


class Routing(BaseModel):
    route: Literal["refund", "question"]
    confidence: float


class ExtractClaim(ava.Signature):
    """Extract the customer's claim from the email. Use only what the email says."""

    email: Email = ava.InputField(desc="The email as received.")
    claim: Claim = ava.OutputField(desc="Who asks, about which order, and what they ask for.")


@ava.agent_step(
    ExtractClaim,
    runtime=ava.runtimes.PredictRLM(lm="openai/gpt-5.5", max_iterations=3),
    retries=1,
)
async def extract_claim(email: Email, *, agent: ava.Agent) -> Claim:
    """Extract the claim with an agent."""
    return (await agent(email=email)).outputs.claim


@ava.classifier_step(
    questions={
        "route": {
            "type": "choice",
            "instructions": "What does the customer want?",
            "criteria": {
                "refund": "Money back for an order.",
                "question": "Information only, no money involved.",
            },
        }
    },
    input_model=Claim,
    retries=1,
)
async def classify_claim(claim: Claim, *, classifier: ava.Classifier) -> Routing:
    """Classify the claim as a refund request or a question."""
    result = await classifier(state=claim.model_dump(mode="json"))
    route = result.choices["route"]
    return Routing(route=route.choice, confidence=route.confidence)


@ava.step
def open_refund(claim: Claim, routing: Routing) -> str:
    """Open a refund case; only for confident refund requests."""
    if routing.route != "refund" or routing.confidence < 0.7:
        ava.skip(f"not a confident refund ({routing.route}, {routing.confidence})")
    return f"refund case opened for order {claim.order_id}"


@ava.step
def queue_answer(claim: Claim, routing: Routing) -> str:
    """Queue the email for a written answer; everything that is not a confident refund."""
    if routing.route == "refund" and routing.confidence >= 0.7:
        ava.skip("handled as a refund")
    return f"answer queued for {claim.customer}"


@ava.step
def log_outcome(refund: str | None, answer: str | None) -> str:
    """Record which branch handled the email."""
    return refund or answer or "nothing done"


def wire_email(extract, classify):
    """Build the graph; the two model steps are passed in."""
    claim = extract(ava.input)
    routing = classify(claim)
    return log_outcome(open_refund(claim, routing), queue_answer(claim, routing))


@ava.workflow(input=Email)
def handle_email():
    """Extract a claim with an agent, classify it, and route it down one branch."""
    return wire_email(extract_claim, classify_claim)
```

```bash
ava run handle_email --input '{"sender": "ada@example.com", "body": "My lamp arrived broken."}'
```

- Both model steps need their provider's key in the operator's environment: `OPENAI_API_KEY`
  for this agent model, `TYPESAFE_API_KEY` for the classifier. Without one, that step fails
  after its attempts, for example `TYPESAFE_API_KEY is required to call a classifier`.
- Each branch's first step skips itself unless it is its case; the two conditions must be exact
  opposites. The join takes `X | None` parameters, so it runs with `None` for the skipped branch.
- `wire_email` takes the two model steps as arguments so the same graph can run without keys:
  a twin workflow `wire_email(extract_stand_in, classify_stand_in)`, where the stand-ins are
  plain steps returning a fixed `Claim` and `Routing`, exercises both branches on the operator.
  See [../run/testing.md](../run/testing.md#key-free-on-the-operator).
- Do not check this workflow with `handle_email().test(stub=...)`: the test driver raises on
  `ava.skip` (see [debugging.md](debugging.md#tests)). Run the twin with `.run(input=...).result()`.

See [../building-blocks/agent-steps.md](../building-blocks/agent-steps.md),
[../building-blocks/classifier-steps.md](../building-blocks/classifier-steps.md).

## 5. Approve, then act

Use when a person must agree (or choose) before the workflow does something that cannot be undone.

```python
from datetime import timedelta
from typing import Literal

import avalanche as ava
from pydantic import BaseModel


class Campaign(ava.BaseInput):
    audience: str
    topic: str


class Draft(BaseModel):
    audience: str
    subject: str
    body: str


class DraftReview(ava.Event):
    kind: Literal["approve", "reject"]
    note: str = ""


@ava.step
def draft_email(campaign: Campaign) -> Draft:
    """Draft the email the reviewer will see."""
    return Draft(
        audience=campaign.audience,
        subject=campaign.topic.title(),
        body=f"News about {campaign.topic}.",
    )


@ava.dest(retries=0)
def send_email(draft: Draft, review: DraftReview) -> str:
    """Send the approved draft; skip a rejected one."""
    if review.kind != "approve":
        ava.skip(f"rejected: {review.note or 'no note'}")
    return f"sent '{draft.subject}' to {draft.audience}"


@ava.workflow(input=Campaign)
def send_campaign():
    """Draft an email, ask a person, send it only when approved."""
    draft = draft_email()
    review = ava.decision(
        DraftReview,
        prompt="Send this email?",
        show=draft,
        timeout=timedelta(hours=24),
        on_timeout=DraftReview(kind="reject", note="no answer in 24h"),
        slug="review_draft",
    )
    return send_email(draft, review)
```

```bash
ava run send_campaign --input '{"audience": "beta users", "topic": "the new export"}'   # RUN_ID
ava run list --flow send_campaign          # waiting   waiting on decision
ava run decide RUN_ID review_draft '{"kind": "approve"}' --idempotency-key d1 --reason "reads well"
ava result RUN_ID --wait --json            # "sent 'The New Export' to beta users"
```

- The same `draft` goes to `show=` and to the acting step: the person approves exactly what is sent.
- A rejection is data, not a failure: the acting step reads `review.kind`. Here it skips, the run
  succeeds and its result is `null`.
- Always give `slug=` (the address answers are sent to) and decide `on_timeout` on purpose.
  The side effect comes after the decision, with `retries=0`. See
  [../orchestration/approvals.md](../orchestration/approvals.md).

## 6. Wait for an outside event, with a timeout

Use when the next step depends on something another system does later, and the workflow must
also go on if it never happens.

```python
from datetime import timedelta

import avalanche as ava
from pydantic import BaseModel


class Contract(ava.BaseInput):
    contract_id: str


class Signed(BaseModel):
    contract_id: str
    signer: str


@ava.step
def send_contract(contract: Contract) -> str:
    """Send the contract for signature and return its id."""
    return contract.contract_id


@ava.step
def close_contract(contract_id: str, signed: Signed | None) -> str:
    """File the signed contract, or mark it expired when nobody signed."""
    if signed is None:
        return f"{contract_id}: expired"
    return f"{contract_id}: signed by {signed.signer}"


@ava.workflow(input=Contract, key=("contract_id",))
def sign_contract():
    """Send a contract, wait up to 14 days for the signature, then close it."""
    sent = send_contract()
    signed = ava.wait_for_event(Signed, key="contract.signed", timeout=timedelta(days=14))
    sent >> signed
    return close_contract(sent, signed)
```

```bash
ava run sign_contract --input '{"contract_id": "C-9"}'                       # RUN_ID
ava run show --flow sign_contract --key 'contract_id="C-9"' --json           # find the run by its key
ava event send RUN_ID contract.signed '{"contract_id": "C-9", "signer": "Ada"}' --idempotency-key sig-1
ava result RUN_ID --wait --json                                              # "C-9: signed by Ada"
```

- A timeout is not an error: the wait's output is `None`. Type the parameter `Signed | None` and
  handle both. After 14 days with no event the result is `"C-9: expired"`.
- `sent >> signed` starts the wait only after the contract was sent. Without a link, a wait
  starts when the run starts.
- The sender needs the run id. The run key lets it find the run from the contract id: over REST,
  `GET /api/v1/runs:resolve`, then `POST /api/v1/runs/{run_id}/events/{key}`. On a local
  operator a waiting run does not survive an operator restart, and this one is keyed: the failed
  run keeps its key and cannot be rerun, so `C-9` could not be started again. For long waits use
  pattern 10 or Avalanche Cloud, or add an `attempt` field to the key (pattern 7). See
  [../orchestration/waits.md](../orchestration/waits.md).

## 7. Webhook start, once per event, retry-safe effect

Use when another system calls a URL to start the run, may deliver the same event twice, and the
run performs an outside effect that must happen once.

```python
import avalanche as ava
from pydantic import BaseModel


class PaymentEvent(ava.BaseInput):
    event_id: str
    customer: str
    amount: float
    attempt: int = 1


class Receipt(BaseModel):
    event_id: str
    idempotency_key: str
    status: str


@ava.step
def check_payment(event: PaymentEvent) -> PaymentEvent:
    """Refuse a payment that no retry can fix."""
    if event.amount <= 0:
        raise ava.NonRetryableError(f"amount must be positive, got {event.amount}")
    return event


@ava.dest(retries=3)
def record_payment(event: PaymentEvent) -> Receipt:
    """Record the payment once, however many attempts and runs handle this event."""
    # Send this as the Idempotency-Key of the outside call: it depends only on the event.
    idempotency_key = f"payment-{event.event_id}"
    return Receipt(event_id=event.event_id, idempotency_key=idempotency_key, status="recorded")


@ava.workflow(
    input=PaymentEvent,
    webhook=ava.Webhook(path="/hooks/payments"),
    key=("event_id", "attempt"),
)
def record_payment_event():
    """Record each payment event once, even when the sender delivers it twice."""
    return check_payment() >> record_payment()
```

```bash
ava webhooks list                          # url: http://127.0.0.1:7434/hooks/payments
curl -X POST http://127.0.0.1:7434/hooks/payments -H 'Content-Type: application/json' \
  -d '{"event_id": "evt_1", "customer": "ada", "amount": 20}'      # {"run_id": "run_90b5e880"}
# the same request again returns the same run id; no second run is created
ava run show --flow record_payment_event --key 'event_id="evt_1"' --key 'attempt=1' --json
# after a failure and a fix, start the same event again as a new run:
ava run record_payment_event --input '{"event_id": "evt_1", "customer": "ada", "amount": 20, "attempt": 2}'
```

Result: `{"event_id": "evt_1", "idempotency_key": "payment-evt_1", "status": "recorded"}`.

- Two layers: `key=` makes the *start* idempotent (one run per `event_id` and `attempt`,
  forever); an idempotency key built from the business id makes the *effect* idempotent across
  retries and across runs. `run.step_key` (`<run_id>:<node_id>`) would only cover the retries
  of one run: a second run has another one.
- A key is permanent and a keyed run cannot be rerun from a step: a failed run stays the run of
  its key, and starting again returns it. `attempt` is in the key for that reason: the sender
  never sends it (default `1`), and a person raises it to start the event again after a fix. See
  [../orchestration/retries.md](../orchestration/retries.md#run-keys).
- An invalid body gets `409 {"error": "workflow could not be started"}` and no run, because the
  workflow is keyed.
- `ava.NonRetryableError` fails at once instead of using 8 attempts. If the outside service has
  no idempotency key, use `retries=0, on_uncertain="hold"` and raise `ava.OutcomeUnknownError`
  when the call may have gone through.
- The local webhook listens on loopback and verifies nothing. See
  [../connect/triggers.md](../connect/triggers.md).

## 8. Scheduled run

Use when the run starts on the clock.

```python
import avalanche as ava
from pydantic import BaseModel


class Region(ava.BaseInput):
    region: str = "eu"


class Backlog(BaseModel):
    region: str
    open_tickets: int


@ava.source
def count_open_tickets(region: Region) -> Backlog:
    """Count the open tickets of the region."""
    return Backlog(region=region.region, open_tickets=7)


@ava.dest
def post_backlog(backlog: Backlog) -> str:
    """Post the morning backlog message."""
    return f"{backlog.region}: {backlog.open_tickets} open tickets"


@ava.workflow(input=Region, cron="0 8 * * 1-5")
def report_backlog():
    """Post the open-ticket backlog every weekday at 08:00."""
    return count_open_tickets() >> post_backlog()
```

```bash
ava schedule list                                            # "enabled": false
ava schedule enable report_backlog --input '{"region": "us"}'
ava run report_backlog                                       # a run by hand: "eu: 7 open tickets"
```

- On the local operator a schedule starts disabled: `cron=` alone starts nothing. Enable it once;
  the choice and the input are stored. On Avalanche Cloud it is on as soon as the flow is deployed.
- Scheduled runs use the input given to `schedule enable` (`"us: 7 open tickets"`), else the
  model's defaults. A run started by hand uses its own `--input`. Give every field a default.
- The stored input is replayed unchanged on every occurrence. Keep only settings in it (the
  region); a job for "today's items" computes the date and fetches its data in the source step.
- Runs missed while the operator was stopped are not caught up, and a slow run can overlap the
  next one. Add `key=` on a date field when a period must run once.

## 9. Parent workflow calls a child workflow

Use when a whole workflow is reused as one step of another, or when work is handed off to its
own run.

```python
from datetime import timedelta

import avalanche as ava
from pydantic import BaseModel


class Document(ava.BaseInput):
    doc_id: str
    text: str = ""


class Summary(BaseModel):
    doc_id: str
    words: int


@ava.step
def count_words(document: Document) -> Summary:
    """Count the words of the document."""
    return Summary(doc_id=document.doc_id, words=len(document.text.split()))


@ava.workflow(input=Document, output=Summary)
def summarize_document():
    """Summarize one document. Runs alone or as a child."""
    return count_words()


class Intake(ava.BaseInput):
    doc_id: str
    text: str
    archive: bool = True


@ava.step
def build_document(intake: Intake) -> Document:
    """Build the child's input from the parent's input."""
    return Document(doc_id=intake.doc_id, text=intake.text)


@ava.step
def wants_archive(intake: Intake) -> bool:
    """Tell whether the document must also be archived."""
    return intake.archive


@ava.step
def file_document(summary: Summary, archive: ava.RunStart | None) -> str:
    """File the summary and note the archive run, when one was started."""
    suffix = "" if archive is None else f", archive run {archive.run_id}"
    return f"{summary.doc_id}: {summary.words} words{suffix}"


@ava.workflow(input=Intake)
def process_intake():
    """Summarize through a child workflow and hand the archive off to another run."""
    document = build_document()
    summary = ava.call(summarize_document, document, timeout=timedelta(minutes=10))
    archive = ava.start_run(summarize_document, document, when=wants_archive())
    return file_document(summary, archive)
```

```bash
ava run process_intake --input '{"doc_id": "D-1", "text": "one two three"}'
# "D-1: 3 words, archive run run_42c3..."   and two summarize_document runs
ava run process_intake --input '{"doc_id": "D-2", "text": "one two", "archive": false}'
# "D-2: 2 words"                            start_run skipped by when=False
ava run links RUN_ID                       # the child runs of a parent
```

- `ava.call` waits and gives the child's output, typed by the child's `output=`. `ava.start_run`
  returns an `ava.RunStart` at once. Both are called in the workflow function, never in a step.
- `when=` takes a step result: `False` starts nothing and gives `None`, and later steps still run.
- A failed child fails the parent (`child_run_failed`, not retried). `timeout` fails the parent
  but does not cancel the child. Neither has `.map`: to fan out, map a step (pattern 2).
- Child runs need the operator or Cloud; in a unit test use `process_intake().test(...)`, not `.run()`.

## 10. Long-running case as a state machine

Use when one entity (a case, an order) lives for days, receives events in an order you cannot
fix in advance, and can return to an earlier stage. Otherwise prefer patterns 5 and 6.

```python
from datetime import timedelta

import avalanche as ava
from pydantic import BaseModel


class Case(ava.BaseInput):
    case_id: str
    customer: str


class Reply(BaseModel):
    text: str


class Triage(BaseModel):
    solved: bool
    note: str


@ava.step
def ask_customer(case: Case) -> str:
    """Ask the customer for details."""
    return f"asked {case.customer} about {case.case_id}"


@ava.step(emits=("solved", "needs_more"))
def triage_reply(case: Case, reply: Reply) -> ava.Event[Triage]:
    """Read the customer's reply and decide whether the case is solved."""
    solved = "thanks" in reply.text.lower()
    return ava.event("solved" if solved else "needs_more", Triage(solved=solved, note=reply.text))


@ava.step
def close_case(case: Case, triage: Triage) -> str:
    """Close a solved case."""
    return f"{case.case_id} closed: {triage.note}"


@ava.step
def expire_case(case: Case) -> str:
    """Close a case the customer never answered."""
    return f"{case.case_id} expired"


@ava.machine(input=Case, key=("case_id",))
def support_case():
    """Follow one support case until it is solved or expires."""
    waiting_for_customer = ava.state(ask_customer, initial=True)
    triaging = ava.state(triage_reply)
    closed = ava.state(close_case, final=True)
    expired = ava.state(expire_case, final=True)

    waiting_for_customer.on("customer_replied", payload=Reply) >> triaging
    waiting_for_customer.after(timedelta(days=5)) >> expired
    triaging.on("needs_more") >> waiting_for_customer
    triaging.on("solved") >> closed
```

```bash
ava run support_case --input '{"case_id": "K-1", "customer": "ada"}'          # RUN_ID
ava machine send support_case customer_replied --key case_id='"K-1"' --data '{"text": "still broken"}'
ava machine send customer_replied --run RUN_ID --data '{"text": "works, thanks"}'
ava run history RUN_ID            # waiting_for_customer -> triaging -> waiting_for_customer -> triaging -> closed
ava result RUN_ID --json          # "K-1 closed: works, thanks"
```

- State names are the variable names; event names are the strings given to `.on(...)`. Both are
  shown to users. Exactly one `initial=True`; final states have no outgoing transition.
- An event the current state does not accept is not an error: the receipt says
  `"status": "ignored"`. Read the receipt. `--key` values are JSON, so strings carry quotes.
- Machine instances, their timers and received events survive a local operator restart; flow
  runs do not. Under `ava dev` the 5-day timer can be fired at once with
  `POST /api/v1/runs/RUN_ID/machine/timers:fire-next` (result: `"K-1 expired"`).
- Test without an operator: `m = support_case.test(Case(case_id="K-9", customer="ada"))`, then
  `m.send("customer_replied", Reply(text="Thanks!"))`, `m.advance_time(timedelta(days=5))`,
  `m.state`. See [../building-blocks/state-machines.md](../building-blocks/state-machines.md).

---

## Combining patterns

| Need | Combine |
|---|---|
| Approval per item | Pattern 2 with `ava.decision.map` instead of a step: [../orchestration/approvals.md](../orchestration/approvals.md) |
| Agent over many items | A handful: pattern 2 with an agent step, `write_summary.map(items)`. More: one agent step that takes the whole list (each mapped item is its own agent call and sandbox, started together) |
| Webhook feeds a long-lived case | Pattern 7 whose steps call `ava.start_run(machine, ...)` and `machine.send(...)`: [../building-blocks/state-machines.md](../building-blocks/state-machines.md) |
| Classifier decides whether a person is asked | Pattern 4, with pattern 5's decision in the low-confidence branch |
| Nightly batch, one child per group | Pattern 8, then pattern 2 over chunks, or pattern 9 once with the whole list |

## See also

- [choose-a-feature.md](choose-a-feature.md): which feature for which need.
- [debugging.md](debugging.md): when a pattern does not run.
- [../run/local.md](../run/local.md), [../run/testing.md](../run/testing.md): running and testing these files.
