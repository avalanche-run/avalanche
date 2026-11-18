"""Saved step outputs: what a later rerun reuses instead of running a step again.

The run worker saves each step's return value as JSON plus copies of the file
content it refers to, in the same JSON shape as Delta's step results. The folder
next to the state database holds one folder per run, and one per saved output::

    step-outputs/<sha256(run_id)[:32]>/<token>/
        manifest.json        digests of everything below
        value.json           the return value
        files/<sha256>       read-only copy of each ava.File's content
        workspaces/<token>/  read-only copy of each other workspace the value names
        run-workspace/       read-only copy of the run's workspace, if the value names it

The operator indexes a folder only after the worker finished writing it, by the
digest of its manifest, so a folder without an index row is a leftover.

Steps edit the run's one workspace in place, so a value naming it is saved as the
``Workspace()`` placeholder plus a copy of the folder as the step left it. A rerun
starts its own workspace from the copy of the last reused step that saved one, and
the placeholder then names that workspace.

Saving never fails a run: anything that goes wrong marks the step not reusable,
with the reason. Loading checks every digest and refuses what no longer matches.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from avalanche.file import _FILE_SERIALIZER_CONTEXT_KEY, File
from avalanche.references import (
    LocalReferenceBinder,
    current_run_workspace,
    run_workspace_reference,
)
from avalanche.reruns import (
    NotReusableStepOutput,
    SavedOutputIndex,
    SavedOutputUnavailableError,
    SavedStepOutput,
)
from avalanche.runtime import Rerun
from avalanche.workspace import (
    _WORKSPACE_SERIALIZER_CONTEXT_KEY,
    Workspace,
    safe_relative_path,
    snapshot_workspaces,
)

from .state.step_outputs import (
    MAX_REASON_LENGTH,
    SHA256_PATTERN,
    ReusedOutputRecord,
    ReusedOutputsTable,
    SavedOutputRecord,
    SavedOutputRow,
    SavedOutputStatus,
    StepOutputsTable,
)
from .state.store import OperatorStateStore

STEP_OUTPUT_FOLDER_NAME = "step-outputs"
MAX_SAVED_JSON_BYTES = 64 * 1024 * 1024
MAX_SAVED_CONTENT_BYTES = 2 * 1024 * 1024 * 1024
MAX_SAVED_WORKSPACE_ENTRIES = 100_000

_MANIFEST_FILE = "manifest.json"
_VALUE_FILE = "value.json"
_FILES_FOLDER = "files"
_WORKSPACES_FOLDER = "workspaces"
_RUN_WORKSPACE_FOLDER = "run-workspace"
_READ_CHUNK_SIZE = 1024 * 1024
_FILE_WIRE_KEYS = frozenset(
    {"kind", "version", "ref", "sha256", "size", "name", "content_type"}
)
_WORKSPACE_REFERENCE_KEYS = frozenset({"kind", "version", "reference"})
_MAP_ITEM = re.compile(r"(?P<group>.+)\[(?P<index>0|[1-9][0-9]*)\]")
_VALUE_ADAPTER = TypeAdapter(object)


def step_output_run_folder_name(run_id: str) -> str:
    """The run's folder name; a run ID supplied by a caller can hold any characters."""
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]


class _FileCopy(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    sha256: str = Field(pattern=SHA256_PATTERN)
    size: NonNegativeInt


class _WorkspaceEntryCopy(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    path: str
    kind: Literal["directory", "file"]
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    size: NonNegativeInt | None = None

    @field_validator("path")
    @classmethod
    def _stays_inside_its_copy(cls, value: str) -> str:
        if value == ".":
            raise ValueError("Workspace entry path cannot be the root pseudo-path '.'")
        return safe_relative_path(value)

    @model_validator(mode="after")
    def _kind_decides_the_fields(self) -> Self:
        is_file = self.kind == "file"
        if (self.sha256 is not None) != is_file or (self.size is not None) != is_file:
            raise ValueError("Only a file entry has a digest and a size")
        return self


class _TreeCopy(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    entries: tuple[_WorkspaceEntryCopy, ...]


class _WorkspaceCopy(_TreeCopy):
    reference: str = Field(min_length=1)
    folder: str = Field(pattern=r"^[0-9a-f]{32}$")


class _Manifest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    version: Literal[1] = 1
    value_sha256: str = Field(pattern=SHA256_PATTERN)
    value_size: NonNegativeInt
    files: tuple[_FileCopy, ...]
    workspaces: tuple[_WorkspaceCopy, ...]
    run_workspace: _TreeCopy | None = None


@dataclass(frozen=True)
class _SavedValue:
    """One attempt's outcome: a finished folder, or the reason there is none."""

    folder: str | None
    manifest_sha256: str | None
    byte_size: int
    reason: str | None


class _ContentBudget:
    """Stop copying, rather than fill a disk, once a step's content passes the limit."""

    def __init__(self) -> None:
        self._remaining = MAX_SAVED_CONTENT_BYTES

    def spend(self, size: int) -> None:
        self._remaining -= size
        if self._remaining < 0:
            raise ValueError(
                f"The output's file content exceeds {MAX_SAVED_CONTENT_BYTES} bytes"
            )


def _save_value(run_folder: Path, value: object) -> _SavedValue:
    """Save one return value; only an interpreter-level exception leaves this function."""
    token = uuid4().hex
    folder = run_folder / token
    try:
        value_json, files, workspaces, names_run_workspace = _encode(value)
        run_folder.mkdir(mode=0o700, exist_ok=True)
        folder.mkdir(mode=0o700)
        budget = _ContentBudget()
        file_copies = tuple(
            _copy_file(file, folder / _FILES_FOLDER, budget) for file in files.values()
        )
        workspace_copies = tuple(
            _copy_workspace(workspace, folder / _WORKSPACES_FOLDER, budget)
            for workspace in workspaces.values()
        )
        run_workspace = (
            _TreeCopy(
                entries=_copy_tree(
                    current_run_workspace().path, folder / _RUN_WORKSPACE_FOLDER, budget
                )
            )
            if names_run_workspace
            else None
        )
        value_sha256 = _write_read_only(folder / _VALUE_FILE, value_json)
        manifest = _Manifest(
            value_sha256=value_sha256,
            value_size=len(value_json),
            files=file_copies,
            workspaces=workspace_copies,
            run_workspace=run_workspace,
        ).model_dump_json()
        manifest_bytes = manifest.encode("utf-8")
        manifest_sha256 = _write_read_only(folder / _MANIFEST_FILE, manifest_bytes)
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        reason = f"{type(exc).__name__}: {exc}"[:MAX_REASON_LENGTH]
        return _SavedValue(None, None, 0, reason)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return _SavedValue(
        token,
        manifest_sha256,
        len(value_json)
        + len(manifest_bytes)
        + sum(copy.size for copy in file_copies)
        + sum(
            entry.size or 0
            for copy in (
                *workspace_copies,
                *(() if run_workspace is None else (run_workspace,)),
            )
            for entry in copy.entries
        ),
        None,
    )


def _encode(
    value: object,
) -> tuple[bytes, dict[str, File], dict[str, Workspace], bool]:
    """The value's JSON, in Delta's step result shape, and the references it holds.

    The last item says whether the value names the run's workspace, which the JSON
    holds as the ``Workspace()`` placeholder.
    """
    files: dict[str, File] = {}
    workspaces: dict[str, Workspace] = {}
    names_run_workspace = False

    def observe_file(file: File) -> None:
        files[file.sha256] = file

    def serialize_workspace(workspace: Workspace) -> dict[str, object]:
        nonlocal names_run_workspace
        if workspace.reference is None or workspace.reference == run_workspace_reference():
            names_run_workspace = True
            return Workspace().manifest()
        workspaces[workspace.reference] = workspace
        return workspace.manifest()

    # Serialize a snapshot: serializing a workspace still being captured releases it,
    # and the capture the executor makes next must still see its files.
    document = _VALUE_ADAPTER.dump_python(
        snapshot_workspaces(value),
        mode="json",
        context={
            _FILE_SERIALIZER_CONTEXT_KEY: observe_file,
            _WORKSPACE_SERIALIZER_CONTEXT_KEY: serialize_workspace,
        },
    )
    _require_captured_references(document, files, workspaces)
    encoded = json.dumps(document, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_SAVED_JSON_BYTES:
        raise ValueError(f"The output's JSON exceeds {MAX_SAVED_JSON_BYTES} bytes")
    return encoded, files, workspaces, names_run_workspace


def _require_captured_references(
    document: object,
    files: Mapping[str, File],
    workspaces: Mapping[str, Workspace],
) -> None:
    """Refuse a reference whose content was not found, so a later load cannot miss it."""
    pending = [document]
    while pending:
        item = pending.pop()
        if type(item) is list:
            pending.extend(item)
        elif type(item) is dict:
            if _is_file_reference(item) and item["sha256"] not in files:
                raise ValueError("The output holds a file reference with no content to copy")
            if _is_workspace_reference(item) and item["reference"] not in workspaces:
                raise ValueError(
                    "The output holds a workspace reference with no content to copy"
                )
            pending.extend(item.values())


def _is_file_reference(item: dict[str, object]) -> bool:
    return item.get("kind") == "file" and set(item) == _FILE_WIRE_KEYS


def _is_workspace_reference(item: dict[str, object]) -> bool:
    return item.get("kind") == "workspace" and set(item) == _WORKSPACE_REFERENCE_KEYS


def _copy_file(file: File, folder: Path, budget: _ContentBudget) -> _FileCopy:
    source = file._host_path
    if source is None:
        source = LocalReferenceBinder().bind_file(file)
    folder.mkdir(mode=0o700, exist_ok=True)
    digest, size = _copy_content(source, folder / file.sha256, budget)
    if digest != file.sha256:
        raise ValueError("A file's content changed while it was being saved")
    return _FileCopy(sha256=digest, size=size)


def _copy_workspace(
    workspace: Workspace, folder: Path, budget: _ContentBudget
) -> _WorkspaceCopy:
    source = workspace._directory_path
    if source is None:
        source = LocalReferenceBinder().bind_workspace(workspace)
    token = uuid4().hex
    entries = _copy_tree(source, folder / token, budget)
    assert workspace.reference is not None
    return _WorkspaceCopy(reference=workspace.reference, folder=token, entries=entries)


def _copy_tree(
    source: Path, root: Path, budget: _ContentBudget
) -> tuple[_WorkspaceEntryCopy, ...]:
    """Copy a directory's files and folders to ``root``, refusing anything else."""
    root.mkdir(mode=0o700, parents=True)
    entries: list[_WorkspaceEntryCopy] = []
    for current, directories, names in os.walk(source, followlinks=False):
        target = root / os.path.relpath(current, source)
        for name in (*directories, *names):
            path = Path(current) / name
            relative = path.relative_to(source).as_posix()
            if len(entries) >= MAX_SAVED_WORKSPACE_ENTRIES:
                raise ValueError(
                    f"The workspace has more than {MAX_SAVED_WORKSPACE_ENTRIES} entries"
                )
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                (target / name).mkdir(mode=0o700)
                entries.append(_WorkspaceEntryCopy(path=relative, kind="directory"))
            elif stat.S_ISREG(mode):
                digest, size = _copy_content(path, target / name, budget)
                entries.append(
                    _WorkspaceEntryCopy(path=relative, kind="file", sha256=digest, size=size)
                )
            else:
                raise ValueError(f"The workspace holds an unsupported entry {relative!r}")
    return tuple(entries)


def _copy_content(source: Path, target: Path, budget: _ContentBudget) -> tuple[str, int]:
    """Copy one file to a new read-only file; return the digest of what was copied."""
    digest = hashlib.sha256()
    size = 0
    source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(source_descriptor).st_mode):
            raise ValueError(f"{source.name!r} is not a regular file")
        target_descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        try:
            while chunk := os.read(source_descriptor, _READ_CHUNK_SIZE):
                budget.spend(len(chunk))
                digest.update(chunk)
                size += len(chunk)
                view = memoryview(chunk)
                while view:
                    view = view[os.write(target_descriptor, view) :]
            os.fsync(target_descriptor)
        finally:
            os.close(target_descriptor)
    finally:
        os.close(source_descriptor)
    return digest.hexdigest(), size


def _write_read_only(path: Path, content: bytes) -> str:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        view = memoryview(content)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return hashlib.sha256(content).hexdigest()


class StepOutputWriter:
    """The run worker's side: one attempt's save per step until the step finishes.

    Steps save when their body returns, which can still be followed by a failure
    (a stream commit, say) or by another attempt. So only :meth:`finish`, called
    when the step succeeded, reports a save, and a replaced or failed attempt's
    folder is removed.
    """

    def __init__(self, run_folder: Path, step_slugs: Mapping[str, str]) -> None:
        self._run_folder = run_folder
        self._step_slugs = step_slugs
        self._lock = threading.Lock()
        self._attempts: dict[str, _SavedValue] = {}
        self._finished: dict[str, SavedOutputRecord] = {}
        self._map_sizes: dict[str, int] = {}

    def save(self, node_id: str, value: object) -> None:
        saved = _save_value(self._run_folder, value)
        with self._lock:
            replaced = self._attempts.get(node_id)
            self._attempts[node_id] = saved
        self._remove(replaced)

    def save_if_unsaved(self, node_id: str, value: object) -> None:
        """Save a value that no task produced, such as a decision's answer."""
        with self._lock:
            saved = node_id in self._attempts
        if not saved:
            self.save(node_id, value)

    def discard(self, node_id: str) -> None:
        with self._lock:
            attempt = self._attempts.pop(node_id, None)
        self._remove(attempt)

    def note_map_size(self, group: str, size: int) -> None:
        with self._lock:
            self._map_sizes[group] = size

    def finish(self, node_id: str) -> SavedOutputRecord:
        """The record of a step that succeeded: what its last attempt left behind."""
        with self._lock:
            attempt = self._attempts.pop(node_id, None)
            record = (
                self._group_record(node_id)
                if attempt is None
                else self._attempt_record(node_id, attempt)
            )
            self._finished[node_id] = record
        return record

    def _attempt_record(self, node_id: str, attempt: _SavedValue) -> SavedOutputRecord:
        item = _MAP_ITEM.fullmatch(node_id)
        group = None
        index = None
        slug = self._step_slugs.get(node_id)
        if item is not None and item["group"] in self._map_sizes:
            group = item["group"]
            index = int(item["index"])
            slug = f"{self._step_slugs[group]}[{index}]"
        return SavedOutputRecord(
            node_id=node_id,
            step_slug=node_id if slug is None else slug,
            status=(
                SavedOutputStatus.SAVED
                if attempt.reason is None
                else SavedOutputStatus.NOT_REUSABLE
            ),
            reason=attempt.reason,
            folder=attempt.folder,
            manifest_sha256=attempt.manifest_sha256,
            map_parent=group,
            map_index=index,
            byte_size=attempt.byte_size,
            saved_at=time.time(),
        )

    def _group_record(self, node_id: str) -> SavedOutputRecord:
        """A map group has no value of its own; it holds its items' outputs in order."""
        slug = self._step_slugs.get(node_id, node_id)
        size = self._map_sizes.get(node_id)
        if size is None:
            return _not_reusable(node_id, slug, "The step's value was not observed")
        for index in range(size):
            child = f"{node_id}[{index}]"
            record = self._finished.get(child)
            if record is None:
                return _not_reusable(node_id, slug, f"Item {child} has no saved output")
            if record.reason is not None:
                return _not_reusable(
                    node_id, slug, f"Item {child}: {record.reason}"[:MAX_REASON_LENGTH]
                )
        return SavedOutputRecord(
            node_id=node_id,
            step_slug=slug,
            status=SavedOutputStatus.SAVED,
            item_count=size,
            saved_at=time.time(),
        )

    def _remove(self, attempt: _SavedValue | None) -> None:
        if attempt is not None and attempt.folder is not None:
            shutil.rmtree(self._run_folder / attempt.folder, ignore_errors=True)


def _not_reusable(node_id: str, slug: str, reason: str) -> SavedOutputRecord:
    return SavedOutputRecord(
        node_id=node_id,
        step_slug=slug,
        status=SavedOutputStatus.NOT_REUSABLE,
        reason=reason,
        saved_at=time.time(),
    )


class StepOutputStore:
    """The operator's side: the folder next to the database and the reads from it.

    A ``:memory:`` database gets a private temporary folder, removed at close.
    """

    def __init__(self, state_store: OperatorStateStore) -> None:
        self._state_store = state_store
        if state_store.path is None:
            self._root = Path(tempfile.mkdtemp(prefix="avalanche-step-outputs-"))
            self._temporary = True
        else:
            self._root = state_store.path.parent / STEP_OUTPUT_FOLDER_NAME
            self._root.mkdir(mode=0o700, exist_ok=True)
            self._temporary = False

    def run_folder(self, run_id: str) -> Path:
        return self._root / step_output_run_folder_name(run_id)

    def index(self, run_id: str, *, run_active: bool) -> SavedOutputIndex:
        """What the run saved or inherited, per step slug; reads no file.

        An inherited entry names the run holding the output, so a rerun of a rerun
        goes straight to it.
        """
        with self._state_store.transaction() as connection:
            rows = StepOutputsTable.list_steps(connection, run_id)
            reused = ReusedOutputsTable.list_steps(connection, run_id)
        return SavedOutputIndex(
            run_id=run_id,
            run_active=run_active,
            outputs={
                **{row.step_slug: _index_entry(row) for row in rows},
                **{step.step_slug: _reused_entry(step) for step in reused},
            },
        )

    def provider(self) -> LocalSavedOutputProvider:
        return LocalSavedOutputProvider(self._step_rows, self._root)

    def rerun_assignment(self, rerun: Rerun, index: SavedOutputIndex) -> RerunAssignment:
        """Everything a coordinator needs to check and start a rerun of ``index``'s run.

        The coordinator cannot open the state database, which the operator holds under
        an exclusive lock, so the rows of every saved output travel with it.
        """
        steps = (
            self._step_rows(entry.run_id, entry.slug)
            for entry in index.outputs.values()
            if isinstance(entry, SavedStepOutput)
        )
        return RerunAssignment(
            rerun=rerun,
            index=index,
            outputs=tuple(step for step in steps if step is not None),
            root=str(self._root),
        )

    def _step_rows(self, run_id: str, slug: str) -> SavedStepRows | None:
        with self._state_store.transaction() as connection:
            row = StepOutputsTable.get_step(connection, run_id, slug)
            if row is None:
                return None
            items = (
                ()
                if row.item_count is None
                else StepOutputsTable.list_items(connection, run_id, row.node_id)
            )
        return SavedStepRows(row=row, items=items)

    def close(self) -> None:
        if self._temporary:
            shutil.rmtree(self._root, ignore_errors=True)


def _index_entry(row: SavedOutputRow) -> SavedStepOutput | NotReusableStepOutput:
    if row.reason is not None:
        return NotReusableStepOutput(slug=row.step_slug, run_id=row.run_id, reason=row.reason)
    return SavedStepOutput(slug=row.step_slug, run_id=row.run_id)


def _reused_entry(step: ReusedOutputRecord) -> SavedStepOutput | NotReusableStepOutput:
    if step.reason is not None:
        return NotReusableStepOutput(
            slug=step.step_slug, run_id=step.source_run_id, reason=step.reason
        )
    return SavedStepOutput(slug=step.step_slug, run_id=step.source_run_id)


class SavedStepRows(BaseModel):
    """One step's index row and, for a map group, the rows of its items in order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    row: SavedOutputRow
    items: tuple[SavedOutputRow, ...]


class RerunAssignment(BaseModel):
    """A rerun request with what its coordinator needs to check and start it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rerun: Rerun
    index: SavedOutputIndex
    outputs: tuple[SavedStepRows, ...]
    root: str

    def provider(self) -> LocalSavedOutputProvider:
        steps = {(step.row.run_id, step.row.step_slug): step for step in self.outputs}
        return LocalSavedOutputProvider(
            lambda run_id, slug: steps.get((run_id, slug)), Path(self.root)
        )


class LocalSavedOutputProvider:
    """Load saved outputs and bind the references they hold to their read-only copies.

    Loading verifies every copy, so only what this provider loaded can be bound.
    """

    def __init__(
        self, step_rows: Callable[[str, str], SavedStepRows | None], root: Path
    ) -> None:
        self._step_rows = step_rows
        self._root = root
        self._files: dict[str, Path] = {}
        self._workspaces: dict[str, Path] = {}

    def load_output_json(self, output: SavedStepOutput) -> str:
        """The output's JSON text, or :class:`SavedOutputUnavailableError` with the reason."""
        step = self._step_rows(output.run_id, output.slug)
        if step is None or step.row.status is not SavedOutputStatus.SAVED:
            raise SavedOutputUnavailableError(
                f"Run {output.run_id} has no saved output for step {output.slug}"
            )
        row, items = step.row, step.items
        if row.item_count is None:
            return self._load_folder(row)
        if [item.map_index for item in items] != list(range(row.item_count)):
            raise SavedOutputUnavailableError(
                f"Step {output.slug} of run {output.run_id} is missing saved map items"
            )
        return "[" + ",".join(self._load_folder(item) for item in items) + "]"

    def bind_file(self, reference: File) -> Path:
        path = self._files.get(reference.sha256)
        if path is None:
            raise SavedOutputUnavailableError(
                f"No loaded saved output holds a copy of file {reference.name!r}"
            )
        return path

    def bind_workspace(self, reference: Workspace) -> Path:
        path = (
            None if reference.reference is None else self._workspaces.get(reference.reference)
        )
        if path is None:
            raise SavedOutputUnavailableError(
                "No loaded saved output holds a copy of the workspace"
            )
        return path

    def seed_run_workspace(self, outputs: Iterable[SavedStepOutput], target: Path) -> None:
        """Fill the empty ``target`` from the run workspace the latest of ``outputs`` saved.

        Each copy holds the workspace as its step left it, so the latest one holds
        the edits of every step that finished before it. Files are checked against
        their digests as they are copied; the new files are the rerun's to edit.
        """
        latest: tuple[float, SavedOutputRow, _TreeCopy] | None = None
        for output in outputs:
            step = self._step_rows(output.run_id, output.slug)
            if step is None or step.row.status is not SavedOutputStatus.SAVED:
                continue
            for row in (step.row,) if step.row.item_count is None else step.items:
                copy = self._read_manifest(row).run_workspace
                if copy is not None and (latest is None or row.saved_at > latest[0]):
                    latest = (row.saved_at, row, copy)
        if latest is None:
            return
        _, row, copy = latest
        source = self._row_folder(row) / _RUN_WORKSPACE_FOLDER
        try:
            for entry in copy.entries:
                if entry.kind == "directory":
                    (target / entry.path).mkdir(mode=0o700)
                else:
                    assert entry.sha256 is not None and entry.size is not None
                    _copy_verified(
                        source / entry.path, target / entry.path, entry.sha256, entry.size
                    )
        except (OSError, ValueError) as exc:
            raise SavedOutputUnavailableError(
                f"{_row_label(row)} cannot be used: {exc}"
            ) from exc

    def _row_folder(self, row: SavedOutputRow) -> Path:
        assert row.folder is not None
        return self._root / step_output_run_folder_name(row.run_id) / row.folder

    def _read_manifest(self, row: SavedOutputRow) -> _Manifest:
        assert row.manifest_sha256 is not None
        try:
            return _Manifest.model_validate_json(
                _read_verified(
                    self._row_folder(row) / _MANIFEST_FILE,
                    row.manifest_sha256,
                    MAX_SAVED_JSON_BYTES,
                )
            )
        except (OSError, ValueError, ValidationError) as exc:
            raise SavedOutputUnavailableError(
                f"{_row_label(row)} cannot be used: {exc}"
            ) from exc

    def _load_folder(self, row: SavedOutputRow) -> str:
        manifest = self._read_manifest(row)
        folder = self._row_folder(row)
        try:
            value = _read_verified(
                folder / _VALUE_FILE, manifest.value_sha256, manifest.value_size
            )
            files = {
                copy.sha256: _verify_copy(
                    folder / _FILES_FOLDER / copy.sha256, copy.sha256, copy.size
                )
                for copy in manifest.files
            }
            workspaces = {
                copy.reference: _verify_workspace_copy(folder / _WORKSPACES_FOLDER, copy)
                for copy in manifest.workspaces
            }
            text = value.decode("utf-8")
        except (OSError, ValueError) as exc:
            raise SavedOutputUnavailableError(
                f"{_row_label(row)} cannot be used: {exc}"
            ) from exc
        self._files.update(files)
        self._workspaces.update(workspaces)
        return text


def _row_label(row: SavedOutputRow) -> str:
    return f"Saved output of step {row.step_slug} in run {row.run_id}"


def _verify_workspace_copy(folder: Path, copy: _WorkspaceCopy) -> Path:
    return _verify_tree(folder / copy.folder, copy.entries)


def _verify_tree(root: Path, entries: tuple[_WorkspaceEntryCopy, ...]) -> Path:
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError(f"{root.name!r} is not a directory")
    for entry in entries:
        path = root / entry.path
        if entry.kind == "directory":
            if not stat.S_ISDIR(path.lstat().st_mode):
                raise ValueError(f"{entry.path!r} is not a directory")
        else:
            assert entry.sha256 is not None and entry.size is not None
            _verify_copy(path, entry.sha256, entry.size)
    return root


def _copy_verified(source: Path, target: Path, sha256: str, size: int) -> None:
    digest = hashlib.sha256()
    copied = 0
    with _open_regular(source) as reader, target.open("xb") as writer:
        while chunk := reader.read(_READ_CHUNK_SIZE):
            digest.update(chunk)
            copied += len(chunk)
            writer.write(chunk)
    if copied != size or digest.hexdigest() != sha256:
        raise ValueError(f"The copy {source.name!r} does not match its digest")


def _verify_copy(path: Path, sha256: str, size: int) -> Path:
    digest = hashlib.sha256()
    copied = 0
    with _open_regular(path) as handle:
        while chunk := handle.read(_READ_CHUNK_SIZE):
            digest.update(chunk)
            copied += len(chunk)
    if copied != size or digest.hexdigest() != sha256:
        raise ValueError(f"The copy {path.name!r} does not match its digest")
    return path


def _read_verified(path: Path, sha256: str, limit: int) -> bytes:
    with _open_regular(path) as handle:
        content = handle.read(limit + 1)
    if len(content) > limit or hashlib.sha256(content).hexdigest() != sha256:
        raise ValueError(f"{path.name!r} does not match its digest")
    return content


def _open_regular(path: Path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"{path.name!r} is not a regular file")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


__all__ = [
    "MAX_SAVED_CONTENT_BYTES",
    "MAX_SAVED_JSON_BYTES",
    "STEP_OUTPUT_FOLDER_NAME",
    "LocalSavedOutputProvider",
    "RerunAssignment",
    "StepOutputStore",
    "StepOutputWriter",
    "step_output_run_folder_name",
]
