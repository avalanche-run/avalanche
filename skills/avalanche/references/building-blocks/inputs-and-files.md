# Inputs and files

How data enters a run (a typed input model), how steps read it (`ava.input` or an injected parameter),
and how files and folders travel between steps (`ava.File`, `ava.Workspace`).

## When to use it

- `input=` model: every workflow that needs a value from its caller (CLI, UI, cron, webhook, parent run).
- `ava.File`: one file as input or output (a PDF, a CSV, a generated report).
- `ava.Workspace`: a folder several steps or agents read and write (a checkout, a set of drafts).
- Plain model fields: small structured data. Do not put file bytes, base64 or long documents in
  outputs; write a file and return an `ava.File`.
- Values chosen by a person during the run are not input: see
  [../orchestration/approvals.md](../orchestration/approvals.md).

## Run input

### Interface

```python
class MyInput(ava.BaseInput):      # a Pydantic model; unknown fields are refused
    field: type = default

@ava.workflow(input=MyInput)
def my_flow(): ...
```

| Way to read it | Written where | What the step gets |
|---|---|---|
| Parameter annotated `MyInput` (any name) | step signature | The whole validated input model, injected. |
| `ava.input.<field>` | argument in the workflow function | That field's value. |
| `ava.input.<field>.<sub>` | argument in the workflow function | A nested attribute. |
| `ava.input` | argument in the workflow function | The whole input model. |

`ava.input` is a placeholder resolved when the step starts. It works as a positional or keyword
argument of a step call, and as the list argument of `.map(...)`
([../orchestration/control-flow.md](../orchestration/control-flow.md)).

| How a run gets its input | |
|---|---|
| `ava run FLOW --input '{"field": 1}'` | JSON object. |
| `ava run FLOW --file FIELD=PATH` | Sets a top-level `ava.File` field. Repeatable. |
| `ava run FLOW --workspace FIELD=DIR` | Sets a top-level `ava.Workspace` field. Repeatable. |
| UI start form | Built from the model: field names, types, defaults, `Field(description=...)`. |
| `flow().run(input={...})` or `input=MyInput(...)` | In-process run. |
| Triggers, parent runs | See [../connect/triggers.md](../connect/triggers.md), [../orchestration/control-flow.md](../orchestration/control-flow.md). |

### Rules

- The model must subclass `ava.BaseInput`: `TypeError: workflow input type must inherit from
  ava.BaseInput`.
- A run started without input builds `MyInput()`. Give every field a default unless the caller must
  supply it. Missing required field on the operator: `Input is missing required fields: <names>`.
- Invalid input fails the run once, with no retry, error code `input_invalid` and the model's messages:
  `The run's input is invalid: <field>: <message> (MyInput)`. Unknown fields count as invalid.
- Put the rules in the model (`Field(ge=1)`, validators), not in the first step.
- `ava.input` works only as a direct argument. Inside a list or dict it is passed through unresolved.
- Only attribute access: `ava.input.items[0]` is not supported. Pass the list and index in the step.
- `ava.input.<name>` on a missing attribute fails the step: `AttributeError: Node '<step>' references
  ava.input.<name> in workflow '<flow>', but attribute '<name>' is missing on MyInput.`
- `ava.input` in a workflow without `input=` fails: `ValueError: Node '<step>' references ava.input.x
  in workflow '<flow>', but no run input is available.`
- `--input`, `--file` and `--workspace` must not name the same field: `Duplicate input field '<name>'`.
- Input values are stored with the run and shown in the UI. Do not pass secrets as input.

## `ava.File`

A reference to one file: where it is, its size, its SHA-256. The bytes never travel in step outputs or
results.

### Interface

```python
ava.File.from_path(path, *, content_type=None, workspace=None) -> ava.File
```

| Member | Meaning |
|---|---|
| `File.from_path(path, content_type=None, workspace=None)` | Reference an existing regular file. Does not read it. `workspace` is a named `ava.Workspace` that contains the file; leave it out in step code. |
| `.name` | File name (`str \| None`). |
| `.size` | Size in bytes. |
| `.content_type` | Media type given at creation (`str \| None`). |
| `.sha256` | Hex digest; set when the value is saved. |
| `.path` | `pathlib.Path` of the file on this host. |
| `.open(mode="rb")` | Read-only handle. Modes `"r"`, `"rb"`, `"rt"` only. |
| `.read_bytes()` | Whole content as `bytes`. |

Use it as an input field (`invoice: ava.File`, `attachments: list[ava.File]`), as a step parameter,
as a step output, or as a field of an output model.

### Rules

- Create a file with normal Python (`path.write_text(...)`), then return `ava.File.from_path(path)`.
- Write output files inside `ava.Workspace().path` so they are kept with the run.
- Do not change a file after the step that returned it: later reads fail with `File sha256 or size does
  not match bound content`.
- Annotate parameters that receive a file (`ava.File`, or a model with a file field). The host rebuilds
  and binds annotated values; an unbound reference raises `RuntimeError: file reference is not bound to
  a host path`.
- `File(content=b"...")` and `.content` are deprecated and emit `DeprecationWarning`. Use
  `from_path` and `read_bytes()`.
- `ava run --file` passes a reference to the local file, so the operator must see the same filesystem.
  A file uploaded through the UI is limited to 32 MiB.
- `ava result RUN_ID --output-dir DIR` writes the result JSON and a copy of every file in the result
  into a new folder `DIR`.

## `ava.Workspace`

A folder passed by reference. `ava.Workspace()` names the current run's own folder: one per run,
created on first use, shared by all its steps, kept with the run.

### Interface

| Member | Meaning |
|---|---|
| `ava.Workspace()` | The run workspace. Create it inside a step, or pass it as an argument in the workflow function. |
| `.path` | `pathlib.Path` of the folder. Read and write it in place. |
| `ava.Workspace.from_path(directory)` | Copy a folder once into the host's store and return a reference to the copy. |
| `.reference` / `.is_reference` | The reference string, `None` / `False` for a bare `ava.Workspace()`. |

Use it as an input field (`site: ava.Workspace`, filled by `ava run --workspace site=DIR`), as a step
parameter, as a step output, or as a field of an output model.

### Rules

- `.path` works only while a step runs: `RuntimeError: Workspace.path is available only during
  Avalanche node execution`.
- Every `ava.Workspace()` in one run is the same folder. Use sub-folders per step to avoid collisions,
  above all for steps that run in parallel.
- A returned `ava.Workspace()` is saved as a reference to the run folder, not as a copy of its files.
- `--workspace FIELD=DIR` sends a copy of `DIR` to the operator. Steps edit the copy, never `DIR`.
  Symbolic links and special files are refused.
- A workspace never carries file contents in JSON. A value that tries to is refused with
  `workspace_value_unsupported`.
- Do not hard-code absolute paths in steps. Take a `File` or `Workspace` and use its `.path`.

## Example

```python
import avalanche as ava
from pydantic import BaseModel


class InvoiceInput(ava.BaseInput):
    invoice: ava.File
    label: str = "draft"


class InvoiceSummary(BaseModel):
    name: str | None
    words: int
    labelled: ava.File


@ava.step
def summarise_invoice(invoice: ava.File, label: str) -> InvoiceSummary:
    """Read the invoice and write a labelled copy into the run workspace."""
    text = invoice.read_bytes().decode()
    target = ava.Workspace().path / "labelled.txt"
    target.write_text(f"[{label}] {text}")
    return InvoiceSummary(
        name=invoice.name,
        words=len(text.split()),
        labelled=ava.File.from_path(target, content_type="text/plain"),
    )


@ava.step
def check_copy(summary: InvoiceSummary) -> str:
    """Read the labelled copy back in a later step."""
    return summary.labelled.read_bytes().decode().strip()


@ava.workflow(input=InvoiceInput)
def label_invoice():
    """Label an uploaded invoice."""
    summary = summarise_invoice(ava.input.invoice, ava.input.label)
    return summary, check_copy(summary)


class SiteInput(ava.BaseInput):
    site: ava.Workspace


@ava.step
def list_site(site: ava.Workspace) -> list[str]:
    """List the files of the input folder."""
    return sorted(p.relative_to(site.path).as_posix() for p in site.path.rglob("*") if p.is_file())


@ava.step
def write_notes() -> ava.Workspace:
    """Write a file into the run workspace and pass the workspace on."""
    workspace = ava.Workspace()
    (workspace.path / "notes.md").write_text("# notes\n")
    return workspace


@ava.step
def read_notes(workspace: ava.Workspace) -> str:
    """Read the file a previous step wrote."""
    return (workspace.path / "notes.md").read_text().strip()


@ava.workflow(input=SiteInput)
def inspect_site():
    """Read an input folder and share files through the run workspace."""
    return list_site(ava.input.site), write_notes() >> read_notes()
```

```bash
ava run label_invoice --file invoice=./invoice.txt --input '{"label": "final"}'
ava result RUN_ID --wait --output-dir ./out      # result JSON + labelled.txt
ava run inspect_site --workspace site=./site
```

## References

Files and workspaces cross step, process and result boundaries as references: a workspace reference
string, a relative path, a size and a digest. The host turns a reference back into a real path before
your step runs, which is why `.path`, `.open()` and `.read_bytes()` work inside a step.

- You never build a reference by hand. Use `File.from_path`, `Workspace()` and `Workspace.from_path`.
- `ava.ReferenceBinder`, `ava.LocalReferenceBinder`, `ava.rebind_arguments` and
  `ava.file_reference_context` exist for hosts that run steps. Workflow code does not call them.

## Dataframes (optional extra)

`uv add 'avalanche-ai[dataframes]'` installs Polars, PyArrow and dataframely. The SDK adds helpers that
convert between lists of Pydantic models and Arrow tables, in `avalanche.model_frame`.

| Name | Signature | Meaning |
|---|---|---|
| `model_to_arrow_schema` | `(model: type[BaseModel]) -> pyarrow.Schema` | Arrow schema of a model. |
| `models_to_arrow` | `(models: Sequence[BaseModel], model: type[BaseModel]) -> pyarrow.Table` | Rows from model instances. |
| `arrow_to_models` | `(data: pyarrow.Table \| polars.DataFrame, model: type[BaseModel]) -> list[BaseModel]` | Model instances from rows. |
| `ava.Json` | marker for `Annotated[T, ava.Json]` | Store this field as a JSON string column. |

- Supported field types: `str`, `bool`, `int`, `float`, `datetime`, `date`, `bytes`, string or integer
  enums, nested models, `list[T]`, and `T | None`.
- Any other type (`dict`, `Any`, other unions) raises `UnsupportedModelFieldError: Field '<name>' has
  unsupported annotation ...; use Annotated[..., avalanche.Json]`.
- Without the extra, the first use raises `ImportError: Dataframe features require the optional
  dataframes dependency.`
- A dataframe is not a valid step output. Return a list of models, or write a file (Parquet, CSV) into
  the run workspace and return an `ava.File`.

```python
from datetime import datetime
from typing import Annotated

import avalanche as ava
from avalanche.model_frame import arrow_to_models, models_to_arrow
from pydantic import BaseModel


class Reading(BaseModel):
    sensor: str
    at: datetime
    extra: Annotated[dict[str, int], ava.Json]


table = models_to_arrow([Reading(sensor="s1", at=datetime(2026, 1, 1), extra={"k": 1})], Reading)
assert arrow_to_models(table, Reading)[0].extra == {"k": 1}
```

## Removed: tables and streams

`avalanche.iceberg`, `avalanche.lance` and `ava.Stream` were removed. Any use raises
`TablesAndStreamsRemovedError` (an `ImportError`). Do not write code against them.

## In the UI

- The start form lists the input model's fields in declaration order with type, default and
  description. A field without a default is required.
- `ava.File` and `list[ava.File]` fields get a file picker.
- A run shows the input it started with and which steps receive the run input.
- A file reference keeps its name, size and media type, and `ava result` reports them: give output
  files a meaningful name and set `content_type`.

## See also

- [workflows.md](workflows.md): `input=`, `key=`, `output=` on `@ava.workflow`.
- [python-steps.md](python-steps.md): parameter injection and return value rules.
- [agent-steps.md](agent-steps.md): giving files and workspaces to agents.
- [../run/local.md](../run/local.md): `ava run`, `ava result`.
- [../connect/triggers.md](../connect/triggers.md): input from cron and webhooks.
- [../reference/errors.md](../reference/errors.md): error names and codes.
