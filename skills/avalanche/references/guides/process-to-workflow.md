# From a process to a workflow

How to turn what a user describes ("when an invoice arrives we check it, someone approves it,
then we pay it") into an Avalanche workflow. Follow the steps in order; each one ends with
something you can show the user.

## When to use it

- The user describes an outcome or a process, not a workflow.
- Not needed when the user asks for a specific change to an existing workflow: read the file
  for that feature instead ([choose-a-feature.md](choose-a-feature.md)).

## 1. Get the facts you cannot guess

Read the project first (existing workflows, models, integrations). Then ask only what is
still unknown, in one message. Do not ask what you can decide yourself.

| Ask | Why it changes the design |
|---|---|
| What starts it? A person, a schedule, another system, an incoming request | Picks the trigger: [triggers.md](../connect/triggers.md) |
| What comes in? One item or many, a file, a record | Input model, files, `.map`: [inputs-and-files.md](../building-blocks/inputs-and-files.md) |
| What must exist at the end, and where does it go? | Output model and the destination step |
| Which parts need judgment or reading, and which follow a rule? | Agent or classifier step against plain Python |
| Does a person decide anything? Who, and what if they do not answer? | `ava.decision` and its timeout: [approvals.md](../orchestration/approvals.md) |
| Does it wait for something outside? For how long? | `ava.wait_for_event`, `ava.sleep`: [waits.md](../orchestration/waits.md) |
| Which outside systems does it read or change? | Integrations: [integrations.md](../connect/integrations.md) |
| What must never happen twice (a payment, an email)? | Idempotent destination, run key: [retries.md](../orchestration/retries.md) |

If the user cannot answer, choose the simplest option, say what you chose, and go on.

## 2. Write the process as a list of steps

One line per step, verb first, with what goes in and what comes out. Show it to the user
before writing code; it is cheap to change.

```text
load_invoice      file            -> text
extract_invoice   text            -> Invoice           (agent: reading)
check_totals      Invoice         -> Invoice           (rule)
categorize        Invoice         -> Category          (classifier: one of 6)
approve_payment   Invoice         -> approved | rejected (person, above 1,000)
record_payment    Invoice         -> receipt           (side effect)
```

Cutting rules:

- A step does one thing you can name with a verb and an object. If the name needs "and",
  cut it in two.
- Cut where the kind of work changes: reading, rule, judgment, person, outside system.
- Cut before anything that can fail for outside reasons (network, model), so a rerun starts
  there and reuses what came before.
- Do not cut finer than a user would want to see in the graph. Five to ten steps is normal.

## 3. Choose the kind of each step

| The step | Kind | File |
|---|---|---|
| Follows a rule you can write | `@ava.step` | [python-steps.md](../building-blocks/python-steps.md) |
| Brings data in / sends the result out | `@ava.source` / `@ava.dest` | [python-steps.md](../building-blocks/python-steps.md) |
| Answers a closed question (one of N, yes or no, a score) | `@ava.classifier_step` | [classifier-steps.md](../building-blocks/classifier-steps.md) |
| Reads, writes, extracts, reasons, uses tools | `@ava.agent_step` | [agent-steps.md](../building-blocks/agent-steps.md) |
| Needs a person | `ava.decision` | [approvals.md](../orchestration/approvals.md) |
| Does the same thing for each item | `.map` on any of the above | [control-flow.md](../orchestration/control-flow.md) |
| Is a process of its own, reused elsewhere | child workflow | [control-flow.md](../orchestration/control-flow.md) |

Keep agent steps few and narrow. An agent step that extracts, checks and decides at once is
three steps: the extraction is the agent's, the check is Python, the decision may be a
classifier. Narrow agent steps are cheaper, easier to evaluate and easier to rerun.

## 4. Define the data before the functions

Write one Pydantic model per thing that moves between steps (`Invoice`, `Category`,
`Receipt`) and one for the run input. Then every step is a typed function between two
models, and an agent step's signature is its input and output models with a description on
each field.

## 5. Wire the graph

Start linear with `>>`. Add `&` only where steps are independent, and always in parentheses.
When a step's output is used by more than one later step, give that step call its own
variable. Never keep a `>>` chain in a variable to pass it on: as an argument, a chain
resolves to its **first** step's output, not its last.

```python
@ava.workflow(input=InvoiceRun)
def process_invoice():
    """Read an invoice, check it, get approval and record the payment."""
    invoice = check_totals(extract_invoice())       # extract_invoice(run: InvoiceRun) takes the run input
    category = categorize(invoice)
    approval = ava.decision(
        Approval,                                   # an ava.Event with kind: "approve" | "reject"
        prompt="Pay this invoice?",
        show=invoice,
        timeout=timedelta(days=2),
        on_timeout=Approval(kind="reject"),
        slug="approve_payment",
    )
    return record_payment(invoice, category, approval)
```

Passing a future as an argument (`categorize(invoice)`) is the clearest form when a value is
used by more than one step. A person's decision is declared in the workflow function, not
inside a step. See [workflows.md](../building-blocks/workflows.md) for how
outputs reach parameters.

## 6. Run it, then harden it

1. Run the plain parts first, with the agent and classifier steps stubbed or scripted
   ([testing.md](../run/testing.md)). Fix the wiring.
2. Run with real models on one real example. Read the agent's trace, not just the output.
3. Then add what the process needs to be trusted, one at a time:

| Need | Add | File |
|---|---|---|
| A wrong agent answer must be caught | an evaluation on the agent step, or a rule step after it | [evaluations.md](../building-blocks/evaluations.md) |
| An error that will never pass | `raise ava.NonRetryableError(...)` | [retries.md](../orchestration/retries.md) |
| A side effect must not repeat | idempotency with the step key; run key on the workflow | [retries.md](../orchestration/retries.md) |
| Some items should stop early | `ava.skip` | [control-flow.md](../orchestration/control-flow.md) |
| It must start by itself | cron or webhook on the workflow | [triggers.md](../connect/triggers.md) |

## 7. Hand it over

Tell the user, briefly: the steps and what each does, how to run it (`ava dev`, then the UI
or `ava run`), what you ran and what you saw, what you could not run (for example for lack
of a provider key), and the choices you made for them in step 1.

## Rules

- Do not start from a state machine because the process "has states". A sequence with a
  wait or an approval in it is still a DAG. See [state-machines.md](../building-blocks/state-machines.md)
  for the cases that do need one.
- Do not put the whole process in one agent step with a long prompt. The workflow is the
  process; the agent does the parts that need judgment.
- Do not hide a decision inside an agent step when the user said a person decides it.
- Do not add retries, evaluations, keys and schedules before the plain version runs.
- Mock what you cannot reach (an outside system, a model) at the step boundary, and say so.

## See also

- [choose-a-feature.md](choose-a-feature.md), [patterns.md](patterns.md), [debugging.md](debugging.md)
- [workflows.md](../building-blocks/workflows.md)
