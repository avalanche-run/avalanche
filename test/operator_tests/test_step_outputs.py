"""The operator saves every successful step's return value for later reruns."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pydantic import TypeAdapter

import avalanche as ava
from ava_cli.app import main as ava_main
from avalanche.references import bind_references
from avalanche.reruns import (
    NotReusableStepOutput,
    SavedOutputUnavailableError,
    SavedStepOutput,
)
from runtime.operator.models import NodeStatus, RunState, RunStatus
from runtime.operator.operator import Operator
from runtime.operator.step_outputs import (
    LocalSavedOutputProvider,
    step_output_run_folder_name,
)

_FLOWS = """
import os
import tempfile
import time
from datetime import timedelta
from pathlib import Path

import polars as pl
from pydantic import BaseModel, Field

import avalanche as ava
from avalanche.references import local_workspace


class Report(BaseModel):
    title: str = Field(alias="heading")
    attachment: ava.File


@ava.source
def first() -> str:
    return "one"


@ava.step
def second(text: str) -> dict:
    return {"text": text, "count": 2}


@ava.step(retries=0)
def third(saved: dict) -> str:
    raise RuntimeError("third always fails")


@ava.workflow
def chain():
    return third(second(first()))


@ava.source
def a_frame() -> pl.DataFrame:
    return pl.DataFrame({"a": [1]})


@ava.step
def frame_height(frame: pl.DataFrame) -> int:
    return len(frame)


@ava.workflow
def unsaveable():
    return frame_height(a_frame())


@ava.source
def a_file() -> ava.File:
    path = Path(tempfile.mkdtemp()) / "data.txt"
    path.write_text("file content")
    return ava.File.from_path(path)


@ava.source
def a_nested_file() -> list[dict[str, Report]]:
    path = Path(tempfile.mkdtemp()) / "nested.txt"
    path.write_text("nested content")
    return [{"report": Report(heading="h", attachment=ava.File.from_path(path))}]


@ava.source
def a_workspace() -> ava.Workspace:
    root = Path(tempfile.mkdtemp())
    (root / "inner").mkdir()
    (root / "inner" / "tree.txt").write_text("tree content")
    return local_workspace(root)


@ava.source
def a_captured_workspace() -> ava.Workspace:
    root = Path(tempfile.mkdtemp())
    (root / "captured.txt").write_text("captured content")
    return ava.Workspace.from_path(root)


@ava.source(num_returns=2)
def two_values() -> tuple[int, str]:
    return 1, "b"


@ava.workflow
def with_files():
    a_file()
    a_nested_file()
    a_workspace()
    a_captured_workspace()
    two_values()


@ava.source
def items() -> list[int]:
    return [1, 2, 3]


@ava.source
def no_items() -> list[int]:
    return []


@ava.step
def double(item: int) -> int:
    return item * 2


@ava.step
def sometimes_linked(item: int):
    if item != 2:
        return item
    root = Path(tempfile.mkdtemp())
    (root / "target.txt").write_text("target")
    (root / "link").symlink_to(root / "target.txt")
    return local_workspace(root)


@ava.workflow
def mapped():
    double.map(items())
    double.map(no_items())
    sometimes_linked.map(items())


@ava.source
def before_sleep() -> str:
    return "before"


@ava.workflow
def with_wait():
    return before_sleep() >> ava.sleep(duration=timedelta(milliseconds=1))


@ava.step(retries=1)
def flaky() -> str:
    marker = Path(os.environ["STEP_OUTPUTS_MARKER"])
    if not marker.exists():
        marker.write_text("failed once")
        raise RuntimeError("first attempt")
    return "second attempt"


@ava.workflow
def retried():
    return flaky()


@ava.source
def done_first() -> str:
    return "kept"


@ava.step
def wait_long(text: str) -> str:
    time.sleep(600)
    return text


@ava.workflow
def killed():
    return wait_long(done_first())
"""

_TERMINAL = {RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.CANCELLED}

_KILLED_OPERATOR = """
import json, sys, time
from runtime.operator.models import NodeStatus
from runtime.operator.operator import Operator

operator = Operator(
    [sys.argv[1]], watch=False, schedule=False, state_db=sys.argv[2], prepare_timeout=120
)
run_id = operator.start_run("killed")
deadline = time.monotonic() + 120
while operator.get_run(run_id).nodes.get("wait_long_1") is None or (
    operator.get_run(run_id).nodes["wait_long_1"].status is not NodeStatus.RUNNING
):
    assert time.monotonic() < deadline, "run did not reach its second step"
    time.sleep(0.05)
print(json.dumps({"run_id": run_id}), flush=True)
sys.stdin.read()
"""


@pytest.fixture
def flows(tmp_path) -> Path:
    path = tmp_path / "flows" / "step_output_flows.py"
    path.parent.mkdir()
    path.write_text(_FLOWS)
    return path


@pytest.fixture
def state_db(tmp_path) -> Path:
    return tmp_path / "state" / "state.db"


def _operator(flows: Path, state_db: Path | str) -> Operator:
    return Operator(
        [str(flows)],
        watch=False,
        schedule=False,
        webhook_port=0,
        state_db=state_db,
        prepare_timeout=120,
    )


def _run(operator: Operator, workflow: str) -> RunState:
    run_id = operator.start_run(workflow)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = operator.get_run(run_id)
        if run is not None and run.status in _TERMINAL:
            return run
        time.sleep(0.03)
    pytest.fail(f"Run {run_id} did not finish")


def _load(operator: Operator, run: RunState, slug: str) -> object:
    return json.loads(
        operator.saved_output_provider().load_output_json(
            SavedStepOutput(slug=slug, run_id=run.run_id)
        )
    )


def _output_folders(state_db: Path, run_id: str) -> list[Path]:
    run_folder = state_db.parent / "step-outputs" / step_output_run_folder_name(run_id)
    return sorted(run_folder.iterdir())


def test_succeeded_steps_are_saved_and_failed_steps_are_not(flows, state_db):
    operator = _operator(flows, state_db)
    try:
        run = _run(operator, "chain")
        assert run.status is RunStatus.FAILED
        index = operator.saved_output_index(run.run_id)
        assert index.run_id == run.run_id
        assert index.run_active is False
        assert set(index.outputs) == {"first", "second"}
        assert index.outputs["first"] == SavedStepOutput(slug="first", run_id=run.run_id)
        assert _load(operator, run, "first") == "one"
        assert _load(operator, run, "second") == {"text": "one", "count": 2}
        assert operator.saved_output_index("run_unknown") is None
        with pytest.raises(SavedOutputUnavailableError):
            operator.saved_output_provider().load_output_json(
                SavedStepOutput(slug="third", run_id=run.run_id)
            )
    finally:
        operator.close()


def test_a_value_json_cannot_hold_fails_its_run_as_on_delta(flows, state_db):
    operator = _operator(flows, state_db)
    try:
        run = _run(operator, "unsaveable")
        assert run.status is RunStatus.FAILED
        assert run.nodes["a_frame_1"].status is NodeStatus.FAILED
        assert run.nodes["frame_height_1"].status is NodeStatus.SKIPPED
        # The frame's step failed after its body returned: a failed step has no saved
        # output, and nothing is left behind.
        assert operator.saved_output_index(run.run_id).outputs == {}
        run_folder = state_db.parent / "step-outputs" / step_output_run_folder_name(run.run_id)
        assert not run_folder.exists()
    finally:
        operator.close()


def test_files_and_workspaces_are_copied_and_verified_on_load(flows, state_db):
    operator = _operator(flows, state_db)
    try:
        run = _run(operator, "with_files")
        assert run.status is RunStatus.SUCCESS
        provider = operator.saved_output_provider()

        file = TypeAdapter(ava.File).validate_json(_load_json(provider, run, "a_file"))
        assert file.sha256 == hashlib.sha256(b"file content").hexdigest()
        bound = bind_references(file, provider)
        assert bound.read_bytes() == b"file content"
        assert not stat.S_IMODE(bound.path.stat().st_mode) & 0o222
        assert state_db.parent in bound.path.parents

        nested = _load(operator, run, "a_nested_file")
        attachment = nested[0]["report"]["attachment"]
        assert nested[0]["report"]["title"] == "h"
        assert attachment["sha256"] == hashlib.sha256(b"nested content").hexdigest()

        workspace = TypeAdapter(ava.Workspace).validate_json(
            _load_json(provider, run, "a_workspace")
        )
        copy = bind_references(workspace, provider)
        assert (copy.path / "inner" / "tree.txt").read_text() == "tree content"
        assert state_db.parent in copy.path.parents

        captured = bind_references(
            TypeAdapter(ava.Workspace).validate_json(
                _load_json(provider, run, "a_captured_workspace")
            ),
            provider,
        )
        assert (captured.path / "captured.txt").read_text() == "captured content"
        assert state_db.parent in captured.path.parents
        assert _load(operator, run, "two_values") == [1, "b"]

        # A copy that changed or went missing is refused, naming the step.
        os.chmod(bound.path, 0o600)
        bound.path.write_bytes(b"file CONTENT")
        with pytest.raises(SavedOutputUnavailableError, match="a_file"):
            _load_json(operator.saved_output_provider(), run, "a_file")
        bound.path.unlink()
        with pytest.raises(SavedOutputUnavailableError, match="a_file"):
            _load_json(operator.saved_output_provider(), run, "a_file")
    finally:
        operator.close()


def _load_json(provider: LocalSavedOutputProvider, run: RunState, slug: str) -> str:
    return provider.load_output_json(SavedStepOutput(slug=slug, run_id=run.run_id))


def test_only_loaded_outputs_can_be_bound(flows, state_db):
    operator = _operator(flows, state_db)
    try:
        run = _run(operator, "with_files")
        file = TypeAdapter(ava.File).validate_json(
            _load_json(operator.saved_output_provider(), run, "a_file")
        )
        with pytest.raises(SavedOutputUnavailableError):
            operator.saved_output_provider().bind_file(file)
    finally:
        operator.close()


def test_a_map_group_is_one_entry_holding_its_items_in_order(flows, state_db):
    operator = _operator(flows, state_db)
    try:
        run = _run(operator, "mapped")
        assert run.status is RunStatus.SUCCESS
        outputs = operator.saved_output_index(run.run_id).outputs
        assert set(outputs) == {
            "items",
            "items_2",
            "no_items",
            "double",
            "double_2",
            "sometimes_linked",
        }
        assert _load(operator, run, "double") == [2, 4, 6]
        assert _load(operator, run, "double_2") == []
        assert isinstance(outputs["sometimes_linked"], NotReusableStepOutput)
        assert "[1]" in outputs["sometimes_linked"].reason
    finally:
        operator.close()


def test_a_wait_step_is_saved_so_a_rerun_past_it_is_not_refused(flows, state_db):
    operator = _operator(flows, state_db)
    try:
        run = _run(operator, "with_wait")
        assert run.status is RunStatus.SUCCESS
        assert run.nodes["sleep_1"].status is NodeStatus.SUCCESS
        assert set(operator.saved_output_index(run.run_id).outputs) == {"before_sleep", "sleep"}
    finally:
        operator.close()


def test_a_retried_step_keeps_only_its_successful_attempt(
    flows, state_db, monkeypatch, tmp_path
):
    monkeypatch.setenv("STEP_OUTPUTS_MARKER", str(tmp_path / "marker"))
    operator = _operator(flows, state_db)
    try:
        run = _run(operator, "retried")
        assert run.status is RunStatus.SUCCESS
        assert _load(operator, run, "flaky") == "second attempt"
        assert len(_output_folders(state_db, run.run_id)) == 1
    finally:
        operator.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_saved_outputs_survive_a_killed_operator(flows, state_db):
    state_db.parent.mkdir()
    killed = subprocess.Popen(
        [sys.executable, "-c", _KILLED_OPERATOR, str(flows), str(state_db)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        run_id = json.loads(killed.stdout.readline())["run_id"]
        killed.send_signal(signal.SIGKILL)
        killed.wait(timeout=10)

        operator = _operator(flows, state_db)
        try:
            run = operator.get_run(run_id)
            assert run.status is RunStatus.FAILED
            index = operator.saved_output_index(run_id)
            assert index.run_active is False
            assert set(index.outputs) == {"done_first"}
            assert _load(operator, run, "done_first") == "kept"
        finally:
            operator.close()
    finally:
        if killed.poll() is None:
            killed.kill()
        killed.stdout.close()
        killed.stdin.close()


def test_a_throwaway_database_saves_to_a_folder_removed_at_close(flows):
    operator = _operator(flows, ":memory:")
    root = operator._step_outputs._root
    try:
        run = _run(operator, "chain")
        assert _load(operator, run, "first") == "one"
        assert root.is_dir()
    finally:
        operator.close()
    assert not root.exists()


def test_state_commands_report_and_remove_saved_outputs(flows, state_db, capsys):
    operator = _operator(flows, state_db)
    try:
        first = _run(operator, "chain")
        second = _run(operator, "chain")
        third = _run(operator, "chain")
    finally:
        operator.close()
    database = str(state_db)
    root = state_db.parent / "step-outputs"
    folder = root / step_output_run_folder_name(first.run_id)
    assert folder.is_dir()

    assert ava_main(["state", "info", "--state-db", database]) == 0
    assert "6 saved outputs" in capsys.readouterr().out

    assert ava_main(["state", "delete", first.run_id, "--state-db", database]) == 0
    assert "2 saved step outputs" in capsys.readouterr().out
    assert not folder.exists()
    assert (root / step_output_run_folder_name(second.run_id)).is_dir()

    assert ava_main(["state", "prune", "--keep-last", "1", "--state-db", database]) == 0
    assert not (root / step_output_run_folder_name(second.run_id)).exists()
    assert (root / step_output_run_folder_name(third.run_id)).is_dir()

    leftover = root / ("a" * 32)
    leftover.mkdir()
    capsys.readouterr()
    assert ava_main(["state", "info", "--state-db", database]) == 0
    assert str(leftover) in capsys.readouterr().out

    assert ava_main(["state", "clear", "--yes", "--state-db", database]) == 0
    assert list(root.iterdir()) == []
