# Evaluations and agent traces

Two things recorded for every agent call. An **evaluation** is a set of classifier questions
asked about the call's inputs, outputs or trace, giving quality scores per run. A **trace** is
the record of what the agent did: model, turns, tool calls, tokens, cost.

## When to use it

- Add `evaluations=` to an agent step to measure its quality on every run: grounded, complete, right tone, followed the procedure.
- Read the trace to debug an agent step or to track cost and turn counts.

Not for:

| Need | Use | File |
|---|---|---|
| Changing what the workflow does based on a judgment | A `@ava.classifier_step` after the agent step | [classifier-steps.md](classifier-steps.md) |
| Blocking a result until a person approves | A decision | [../orchestration/approvals.md](../orchestration/approvals.md) |
| Asserting behaviour in tests | Ordinary tests | [../run/testing.md](../run/testing.md) |

Evaluations observe; they never change, delay or fail the step.

## Interface

### `ava.Evaluations`

```python
ava.Evaluations(metrics, composites={}, classifier=None, model=None, timeout=None)
```

| Option | Type | Default | Meaning |
|---|---|---|---|
| `metrics` | `Mapping[str, ava.Metric]`, at least one | required | Named questions. |
| `composites` | `Mapping[str, Callable[[ava.ClassificationResult], float]]` | `{}` | Named numbers in `[0, 1]` computed from the answers. |
| `classifier` | `ava.classifiers.TypeSafe()`, `OpenAIDecisions()`, `GeminiDecision()` | `None` | Provider that answers. `None`: the workflow's `classifier_defaults`, else TypeSafe. |
| `model` | `str` | `None` | One of the provider's models. `None`: workflow default, else the provider's default. |
| `timeout` | `float > 0` | `None` | Seconds per request. `None`: workflow default, else `10.0`. |

Provider, model and key rules are the same as for classifier steps; see [classifier-steps.md](classifier-steps.md).

### `ava.Metric`

```python
ava.Metric(*, state, question)
```

| Option | Type | Meaning |
|---|---|---|
| `state` | `Callable[[ava.EvalContext], str \| dict \| list]` | Synchronous selector: picks the evidence the question is asked about. Must return text or finite JSON. |
| `question` | `dict` | One classifier question: `{"type": "noul" \| "choice" \| "score", "instructions": ..., "criteria": ...}`. Shapes in [classifier-steps.md](classifier-steps.md). |

### `ava.EvalContext`

What a selector receives, for the successful agent call:

| Attribute | Value |
|---|---|
| `ctx.inputs` | The signature's validated inputs: `ctx.inputs.<input field>`. |
| `ctx.output` | The signature's validated outputs: `ctx.output.<output field>` (singular `output`). |
| `ctx.trace` | The call's `AgentTrace`: `ctx.trace.header`, `ctx.trace.turns`. |

### Composites

A composite is `lambda result: number`, where `result` is an `ava.ClassificationResult` with
`result.nouls[name].noul`, `result.scores[name].score`, `result.choices[name].probabilities[option]`.
It must return a finite `int` or `float` in `[0, 1]`: divide an N-level score by `N - 1`.

### When and where it runs

1. The agent call returns and its outputs validate against the signature.
2. Avalanche snapshots the inputs, outputs, trace and the declaration (limit 8 MiB) and hands it to the operator. No selector or model call runs inside the step.
3. The operator evaluates it in a separate process: runs the selectors, asks the questions (metrics whose selectors return identical state share one request), computes the composites.
4. The record is stored with the run. It may complete after the run has finished.

Only runs started through an operator (`ava dev`, `ava operator`, hosted) are evaluated. With
`workflow().run(executor=ava.LocalExecutor())` the step logs
`Evaluations for agent step '...' were not evaluated: embedded execution has no operator evaluation collector.`

### Evaluation record

| Field | Meaning |
|---|---|
| `evaluation_id`, `run_id`, `node_id` | Identity; one record per evaluated agent call. |
| `status` | `pending`, `completed` or `failed`. |
| `created_at`, `ended_at` | Timestamps. |
| `result.classifier` | Provider used. |
| `result.classification` | `model`, `answers` (per metric), `usage` (`input_tokens`, `output_tokens`). |
| `result.composites` | `{name: float}`. |
| `error` | Why a failed evaluation failed, e.g. `EvaluationError: Evaluation for metrics 'faithful', 'coverage' failed: classifier step 'evaluations' failed: TYPESAFE_API_KEY is required to call a classifier`. |

Read them:

- UI: open the run, select the agent step, "Evaluations" tab (metrics with answers and probabilities, then composites).
- REST, on the operator's web port: `GET /api/v1/runs/{run_id}/evaluations` (optional `?node_id=<node>`). See [../reference/operator-api.md](../reference/operator-api.md).

### Agent trace

`result.trace` in the step function, `ctx.trace` in a selector: an `AgentTrace` with `header` and `turns`.

Header (`trace.header`):

| Field | Meaning |
|---|---|
| `status` | `"succeeded"`, `"failed"` or `"cancelled"`. |
| `stop_reason` | Why the call ended, in the runtime's words (e.g. `submit`, `final_output`). |
| `model` | Model that ran the call, when known. |
| `duration_ms` | Wall time of the call. |
| `usage` | `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `cost_usd`. A figure is `None` when the runtime did not report it; `OpenAIAgent` reports no cost. |
| `turn_count`, `tool_call_count` | Totals over the turns. |
| `external_run_id`, `session_id` | The runtime's own ids, when it has them. |
| `runtime_kind`, `trace_format` | `predict_rlm` / `claude_agent` / `openai_agent`, and the format of the native part. |
| `native` | Runtime-specific summary (below). |

Turn (`trace.turns[i]`, `index` starts at 1):

| Field | Meaning |
|---|---|
| `message` | What the model wrote this turn. |
| `reasoning` | The provider's reasoning text, when the runtime exposes it. |
| `tool_calls` | Each with `name`, `arguments`, `result`, `error`, `duration_ms`, `call_id`, `caller` (`"model"` or `"code"`). |
| `usage`, `duration_ms` | Tokens and time of the turn. |
| `failed` | Whether the runtime counts the turn as failed. |
| `native` | Runtime-specific part (below). |

| Runtime | `turn.native` | `header.native` |
|---|---|---|
| PredictRLM | `code`, `output`, `untruncated_output`, `error`, `finish_reason`, `usage` (`main`, `sub`), `predict_calls` | `sub_model`, `max_iterations`, `usage` (`main`, `sub`) |
| ClaudeAgent | `message_id`, `model`, `stop_reason`, `api_error` | `result_subtype`, `num_turns`, `duration_api_ms`, `permission_denials`, `errors` |
| OpenAIAgent | `response_id`, `model`, `reasoning_tokens`, `refusal`, `answer` | `max_turns`, `requests`, `reasoning_tokens`, `last_response_id`, `errors` |

On PredictRLM a turn is one iteration; its `tool_calls` are the tools its code called, then its `predict` sub-model calls.

What is kept, and limits:

- The call's inputs and outputs, up to 4 MiB together; the largest fields beyond that are left out and named.
- `ava.File` / `ava.Workspace` values are kept as references (workspace, path, name, content type, size, sha256), never bytes or host paths.
- On Claude and OpenAI, a tool argument or result over 256 KiB is replaced by a marker.
- On Claude and OpenAI, the prompt of the first model request: system text, messages, tool definitions, output schema.
- A failed or cancelled call still records a final trace event with its status and error.

Read traces:

- UI: open the run, select the agent step, "Trace" tab: turn by turn, with tool calls, tokens and cost. It fills in live while the step runs.
- Terminal UI: `ava tui --connect HOST:PORT --run RUN_ID --node NODE_ID`.
- Header only: `ava run show RUN_ID --json` and REST `GET /api/v1/runs/{run_id}` have `nodes[].trace.header` (model, usage, cost, turn and tool-call counts, duration, stop reason).
- `GET /api/v1/runs/{run_id}/activity?node_id=<node>` lists the call's events as an index, without their content. No REST route and no `ava` command returns the turns; `ava result RUN_ID --node NODE_ID --json` returns the step's output only.
- Full trace from a script: the operator's Python client, `provider.hydrate_trace(run_id, node_id)`; see [../run/local.md](../run/local.md).
- In process: `ava.agent.capture_agent_evidence(listener, errors="ignore")` is a context manager; `listener` receives each live event and the final trace event of agent calls made inside it.

## Example

```python
from pydantic import BaseModel

import avalanche as ava


class Article(BaseModel):
    title: str
    body: str


class SummarizeArticle(ava.Signature):
    """Summarize the article in at most three sentences, using only its content."""

    article: Article = ava.InputField(desc="The article to summarize.")
    summary: str = ava.OutputField(desc="At most three sentences.")


def source_and_summary(ctx: ava.EvalContext) -> dict[str, str]:
    """The evidence both metrics judge: the article body and the summary."""
    return {"article": ctx.inputs.article.body, "summary": ctx.output.summary}


summary_evaluations = ava.Evaluations(
    metrics={
        "faithful": ava.Metric(
            state=source_and_summary,
            question={
                "type": "noul",
                "instructions": "Is every claim in the summary supported by the article?",
            },
        ),
        "coverage": ava.Metric(
            state=source_and_summary,
            question={
                "type": "score",
                "instructions": "How much of the article's main point does the summary keep?",
                "criteria": ["Misses the point", "Partly covers it", "Covers it fully"],
            },
        ),
        "efficient": ava.Metric(
            state=lambda ctx: {"turns": ctx.trace.header.turn_count},
            question={
                "type": "noul",
                "instructions": "Did the agent finish in three turns or fewer?",
            },
        ),
    },
    composites={
        "quality": lambda result: (
            0.6 * result.nouls["faithful"].noul + 0.4 * result.scores["coverage"].score / 2
        ),
    },
    timeout=30.0,
)


@ava.source
def load_article() -> Article:
    """Load one sample article."""
    return Article(title="Launch", body="The team shipped version 2 on Monday after a week of tests.")


@ava.agent_step(
    SummarizeArticle,
    runtime=ava.runtimes.PredictRLM(lm="openai/gpt-5.6-terra", max_iterations=8),
    evaluations=summary_evaluations,
)
async def summarize_article(article: Article, *, agent: ava.Agent) -> str:
    """Summarize the article with an agent; evaluations judge the result."""
    result = await agent(article=article)
    usage = result.trace.header.usage
    print(result.trace.header.turn_count, usage.cost_usd if usage else None)
    return result.outputs.summary


@ava.workflow
def article_summary():
    """Summarize an article and evaluate the summary."""
    return load_article() >> summarize_article()
```

`faithful` and `coverage` use the same selector, so they are answered in one request;
`efficient` selects different state and is a second request. `coverage` has 3 levels (0 to 2),
so the composite divides its score by 2.

## Rules

- `evaluations=` takes an `ava.Evaluations` object: `TypeError: ava.agent_step evaluations must be an ava.Evaluations declaration`.
- Selectors and composites must be synchronous: `EvaluationError: Metric state selector must be a synchronous callable`.
- A selector returns `str`, `dict` or `list` of plain JSON. Convert models with `.model_dump(mode="json")`. Otherwise the evaluation fails with `State validation for metric '...' failed: ...; select text or finite JSON, converting Python-only values explicitly`.
- Share one selector function between metrics that judge the same evidence: one request instead of several.
- A bad question fails at declaration: `EvaluationError: Invalid metric question: ValidationError; use one classifier Noul, Score, or Choice question`. The message does not name the bad key; check the shape in [classifier-steps.md](classifier-steps.md).
- Provider limits apply to metrics too, e.g. `Invalid metric question: question 'm' cannot be sent to OpenAI Decisions: a noul takes no true/false criteria; put them in the instructions`.
- A composite outside `[0, 1]` fails the evaluation: `Composite '...' must return a finite number in [0, 1]; normalize an N-level Score with score / (N - 1)`.
- Read fields in selectors as plain attribute access (`ctx.output.summary`, `ctx.trace.turns[0]`). The UI lists these paths per metric; a selector it cannot read statically is shown as "custom".
- The provider's key (`TYPESAFE_API_KEY` by default) must be visible to the operator: in its environment or the nearest `.env`. A missing key gives a `failed` record, not a failed run.
- Do not rely on evaluation results inside the workflow: they are not available to later steps.
- A failed agent call, or outputs that do not match the signature, is not evaluated.
- Trace values are data, not secrets storage: tool arguments and results are kept and shown. Do not pass credentials through tool arguments.

`EvaluationError` is `avalanche.evaluations.EvaluationError` (a `RuntimeError`).

## In the UI

- Workflow view, agent step, "Evals" tab: each metric's question, the provider, model and timeout, the composite names, and which input, output or trace paths each selector reads.
- Run view, agent step, "Evaluations" tab: status, each metric's answer with probabilities, and the composites. "No evaluation recorded for this execution." when there is none.
- Run view, agent step, "Trace" tab: model, tokens, cost, duration, then each turn. Metric and composite names are shown as written, so name them for a reader (`faithful`, not `m1`).

## See also

- [agent-steps.md](agent-steps.md): where `evaluations=` goes; `result.trace`.
- [classifier-steps.md](classifier-steps.md): question shapes, providers, `ClassificationResult`.
- [runtimes.md](runtimes.md): what each runtime reports.
- [../guides/debugging.md](../guides/debugging.md): using traces to debug.
- [../reference/operator-api.md](../reference/operator-api.md): REST routes.
