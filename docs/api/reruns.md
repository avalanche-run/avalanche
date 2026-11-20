# Reruns

Rerun a finished, failed or cancelled run from any step. The steps before the ones you
choose do not run again: later steps get their saved outputs. The usual case is a step
that failed: fix its code, then rerun from it.

This page covers the local operator (`ava dev`, `ava operator`). For embedded Python, see
[embedded reruns](#embedded-reruns).

## From the command line

Give the run to rerun and the step to start from. `ava run` prints the new run's ID.

```sh
ava run chain --rerun-of run_a0a8d9c6 --from c
ava run chain --rerun-of run_a0a8d9c6 --from c --only
ava run chain --rerun-of run_a0a8d9c6 --from c --from e
```

- `--from SLUG` names a step to run again, and can be repeated. Everything after it runs too.
- `--only` runs just the `--from` steps. A workflow return that does not run is reused; a
  step in between that nothing running reads is not run.
- `--input`, `--context` and `--file` are optional. A rerun reuses the source run's input,
  context and input files, and these replace only the fields they name.

A rerun always runs the code in the folder now, and always of the same flow. In the new
run, a skipped step has the status `reused` and names the run holding its output. The new
run records which run it reruns, and the run list shows the link.

## From the web UI

Open a finished, failed or cancelled run in `ava dev` or `ava web`, select a step and
choose **Rerun from here**. Choose that step and everything after it, or only that step.
The new run opens when it starts. Reused steps link to the run holding their output.

## From the TUI

In `ava tui --connect HOST:PORT`, select a step and press `R` (Shift+R). Choose this step
and everything after, or only this step. Press Enter on a reused step to open the run
holding its output.

## Which runs and steps can be reused

The checks run before anything starts. A source run still going is refused.

Steps are matched by slug. A step's slug is its function name unless you set `slug=`, so
set it to keep a step's identity when you rename the function:

```python
@ava.step(slug="b")
def b_renamed(text: str) -> dict[str, str]:
    return {"text": text + " and b"}
```

The start steps must exist in the code that will run. A skipped step only has to pass the
checks if a step that runs reads its value (directly as a parameter) or the workflow returns
it. It needs a saved successful output in the source run, and that output must validate
against the return type declared in the code that will run. Other earlier steps show as
reused when the source run has them, and never block a rerun, so a reused output may come
from older upstream code.

Otherwise the rerun is refused and nothing starts. The refusal lists every problem and the
earliest step that would work:

```text
Rerun refused, nothing was started:
  b_renamed: missing_from_source: run run_a0a8d9c6 has no saved output for this step
Earliest step to rerun from: b_renamed
```

| Reason | Meaning |
| --- | --- |
| `unknown_start_step` | A `--from` step is not in the code that will run. |
| `missing_from_source` | The source run has no saved output for a step that is needed. |
| `not_reusable` | The step's output could not be saved. |
| `output_unavailable` | The saved copy is missing or changed, or the source run was deleted. |
| `output_does_not_fit` | The saved output does not validate against the declared return type. |
| `source_run_active` | The source run is still going. |

## What is saved

The operator saves each successful step's return value as JSON next to its state database,
under `.avalanche/operator/step-outputs/`. `ava state delete`, `prune` and `clear` remove
saved outputs with their runs.

- **JSON only.** Pydantic models, dictionaries, lists and plain values are saved and rebuilt
  into the declared return type. A step returning anything else (a data frame, an array, a
  plain class, a live object) fails, as on Delta. There is no pickle.
- **A step whose files cannot be copied still succeeds.** It is marked not reusable, with the
  reason. A rerun that needs it is refused as `not_reusable`; rerun from that step or earlier.
- **Limits.** A step with more than 2 GiB of file content, or a workspace holding a link or
  more than 100,000 entries, is marked not reusable.
- **Files and workspaces.** The content of a returned `ava.File` or `ava.Workspace` is
  copied beside the JSON and checked by SHA-256 on reuse. Later steps get read-only copies,
  so a rerun still works after the original is gone. A missing or changed copy refuses the
  rerun as `output_unavailable`.
- **The run's workspace.** Steps edit the run's one workspace in place. When a step returns
  `ava.Workspace()`, its saved output keeps a copy of that workspace as the step left it, and
  a rerun starts its own workspace from the copy of the last reused step that saved one.
  A changed copy fails the rerun before any step runs.
- **A step with no return annotation** is rebuilt as plain JSON, so a `File` or model it
  returned reaches later steps as a dictionary. Annotate the return type of a step you
  expect to reuse.

## Embedded reruns

`Workflow.run(rerun=ava.Rerun(...))` is separate. It saves no outputs and can
only skip a step whose output later steps do not need, and you pass
`input=` again yourself:

```python
result = document_flow().run(
    executor=ava.LocalExecutor(),
    run_id="rerun_002",
    rerun=ava.Rerun(run_id="run_001", start=["chunk-docs"], mode="autorun"),
).result()
```
