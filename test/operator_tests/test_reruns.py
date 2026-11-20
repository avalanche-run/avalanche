"""Reruns through a real operator: `ava run --rerun-of`, gRPC, restarts and chains."""

from __future__ import annotations

import base64
import http.client
import json
import os
import queue
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import grpc
import pytest

from ava_cli.app import main as ava_main
from avalanche.reruns import (
    NotReusableStepOutput,
    RerunProblemReason,
    RerunRefusedError,
    SavedStepOutput,
)
from avalanche.runtime import Rerun
from runtime.operator.client import GrpcStateProvider, StreamState
from runtime.operator.models import NodeStatus, RunState, RunStatus
from runtime.operator.operator import Operator, UnknownRerunSourceError
from runtime.operator.proto import operator_pb2 as pb
from runtime.operator.proto import operator_pb2_grpc as pb_grpc
from runtime.operator.server import serve
from runtime.operator.state.runs import RunsTable
from runtime.operator.step_outputs import step_output_run_folder_name
from runtime.operator.wait_control import WaitControlRequest
from runtime.operator.web import start_browser_server

_TERMINAL = {RunStatus.SUCCESS, RunStatus.FAILED, RunStatus.CANCELLED}
_LIST_WAITS = WaitControlRequest(kind="list")

# Each step appends its name to executed.log, the proof of what ran again.
_PRELUDE = """
import os
from pathlib import Path

import avalanche as ava


def ran(name):
    with Path(os.environ["RERUN_TEST_LOG"]).open("a") as log:
        log.write(name + "\\n")
"""

_CHAIN = (
    _PRELUDE
    + """

@ava.source
def a() -> str:
    ran("a")
    return "from a"


@ava.step
def b(text: str) -> dict[str, str]:
    ran("b")
    return {"text": text + " and b"}


@ava.step(retries=0)
def c(saved: dict[str, str]) -> str:
    ran("c")
    {c_body}


@ava.step
def d(text: str) -> str:
    ran("d")
    return text + " and d"


@ava.workflow
def chain():
    return d(c(b(a())))
"""
)
_BROKEN_C = _CHAIN.replace("{c_body}", 'raise RuntimeError("c is broken")')
_FIXED_C = _CHAIN.replace("{c_body}", 'return saved["text"] + " and c"')
_RENAMED_B = _FIXED_C.replace("def b(", "def b_renamed(").replace(
    "c(b(a()))", "c(b_renamed(a()))"
)

_LINKED = (
    _PRELUDE
    + """
import tempfile

from avalanche.references import local_workspace


@ava.source
def linked() -> ava.Workspace:
    ran("linked")
    root = Path(tempfile.mkdtemp())
    (root / "target.txt").write_text("target")
    (root / "link").symlink_to(root / "target.txt")
    return local_workspace(root)


@ava.step
def reads_linked(workspace: ava.Workspace) -> str:
    ran("reads_linked")
    return "read"


@ava.step
def last_step(text: str) -> str:
    ran("last_step")
    return text


@ava.workflow
def with_linked():
    return last_step(reads_linked(linked()))
"""
)

_CALLS = (
    _PRELUDE
    + """

class Ask(ava.BaseInput):
    id: int


@ava.step
def answer(value: Ask) -> int:
    return value.id


@ava.machine(input=Ask, key=("id",))
def approval():
    pending = ava.state(answer, initial=True)
    done = ava.state(answer, final=True)
    pending.on("approved") >> done


@ava.source
def before_call() -> str:
    ran("before_call")
    return "approved"


@ava.step
def after_call(answer: int, text: str) -> str:
    ran("after_call")
    return f"{text} {answer}"


@ava.workflow(input=Ask)
def calls_approval():
    return after_call(ava.call(approval, ava.input), before_call())
"""
)

_FLOWS = (
    _PRELUDE
    + """
import time

from pydantic import BaseModel



class Numbers(ava.BaseInput):
    first: int = 1
    second: int = 2


class Tenant(ava.RunContext):
    who: str = "nobody"
    extra: int = 0


@ava.source
def read_input(payload: Numbers, ctx: Tenant) -> list:
    ran("read_input")
    return [payload.first, payload.second, ctx.who, ctx.extra]


@ava.workflow(input=Numbers, context=Tenant)
def with_input():
    return read_input()


class Documents(ava.BaseInput):
    document: ava.File
    label: str = "none"


@ava.source
def read_document(payload: Documents) -> str:
    ran("read_document")
    return f"{payload.label}:{payload.document.read_bytes().decode()}"


@ava.workflow(input=Documents)
def with_document():
    return read_document()


class TwoDocuments(ava.BaseInput):
    first: ava.File
    second: ava.File
    more: list[ava.File] = []


@ava.source
def read_documents(payload: TwoDocuments) -> list[str]:
    ran("read_documents")
    return [
        file.read_bytes().decode()
        for file in [payload.first, payload.second, *payload.more]
    ]


@ava.workflow(input=TwoDocuments)
def with_two_documents():
    return read_documents()


@ava.source
def make_file() -> ava.File:
    ran("make_file")
    path = Path(os.environ["RERUN_TEST_LOG"]).parent / "made.txt"
    path.write_text("made once")
    return ava.File.from_path(path)


@ava.step
def read_file(file: ava.File) -> str:
    ran("read_file")
    return file.read_bytes().decode()


@ava.workflow
def with_file():
    return read_file(make_file())


@ava.source
def draft() -> ava.Workspace:
    ran("draft")
    (ava.Workspace().path / "notes.txt").write_text("draft")
    return ava.Workspace()


@ava.step
def revise(workspace: ava.Workspace) -> str:
    ran("revise")
    notes = workspace.path / "notes.txt"
    notes.write_text(notes.read_text() + " revised")
    return notes.read_text()


@ava.workflow
def with_run_workspace():
    return revise(draft())


@ava.source
def numbers() -> list[int]:
    ran("numbers")
    return [1, 2]


@ava.step
def between(rows: list[int]) -> int:
    ran("between")
    return sum(rows)


@ava.step
def last(total: int) -> int:
    ran("last")
    return total + 1


@ava.workflow
def with_numbers():
    return last(between(numbers()))


@ava.source
def before_gate() -> str:
    ran("before_gate")
    return "ready"


class Approval(BaseModel):
    answer: str


@ava.step
def after_approval(approval: Approval, text: str) -> str:
    ran("after_approval")
    return f"{text} and {approval.answer}"


@ava.workflow
def with_gate():
    ready = before_gate()
    gate = ava.wait_for_event(Approval, key="go", timeout=None)
    return after_approval(gate, ready)


@ava.source
def before_slow() -> str:
    ran("before_slow")
    return "before"


@ava.step
def slow(text: str) -> str:
    ran("slow")
    time.sleep(600)
    return text + " and slow"


@ava.step
def after_slow(text: str) -> str:
    ran("after_slow")
    return text + " and after"


@ava.workflow
def still_going():
    return after_slow(slow(before_slow()))


@ava.source
def numbers() -> list[int]:
    ran("numbers")
    return [1, 2, 3]


@ava.step
def double(item: int) -> int:
    ran("double")
    return item * 2


@ava.step
def total(values: list[int]) -> int:
    ran("total")
    return sum(values)


@ava.workflow
def with_map():
    return total(double.map(numbers()))


@ava.source
def other_a() -> str:
    return "other"


@ava.workflow
def other_flow():
    return other_a()
"""
)


@dataclass
class _Env:
    flows: Path
    state_db: Path
    log: Path

    def executed(self) -> list[str]:
        return self.log.read_text().split() if self.log.exists() else []

    def forget_executed(self) -> None:
        self.log.write_text("")


@pytest.fixture
def env(tmp_path, monkeypatch) -> _Env:
    flows = tmp_path / "flows"
    flows.mkdir()
    (flows / "chain.py").write_text(_BROKEN_C)
    (flows / "flows.py").write_text(_FLOWS)
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "executed.log"
    monkeypatch.setenv("RERUN_TEST_LOG", str(log))
    return _Env(flows=flows, state_db=state / "state.db", log=log)


def _operator(env: _Env) -> Operator:
    return Operator(
        [str(env.flows)],
        watch=False,
        schedule=False,
        webhook_port=0,
        state_db=env.state_db,
        prepare_timeout=120,
    )


@pytest.fixture
def operator(env) -> Iterator[Operator]:
    running = _operator(env)
    try:
        yield running
    finally:
        running.close()


def _wait(operator: Operator, run_id: str) -> RunState:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = operator.get_run(run_id)
        if run is not None and run.status in _TERMINAL:
            return run
        time.sleep(0.03)
    pytest.fail(f"Run {run_id} did not finish")


def _run(operator: Operator, flow: str, **kwargs) -> RunState:
    return _wait(operator, operator.start_run(flow, **kwargs))


def _rerun(operator: Operator, flow: str, source: RunState, *start: str, **kwargs) -> RunState:
    mode = kwargs.pop("mode", "autorun")
    return _run(
        operator, flow, rerun=Rerun(run_id=source.run_id, start=start, mode=mode), **kwargs
    )


def _statuses(run: RunState) -> dict[str, NodeStatus]:
    return {node.name: node.status for node in run.nodes.values()}


def _reused_from(run: RunState) -> dict[str, str | None]:
    return {node.name: node.reused_from_run_id for node in run.nodes.values()}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _OperatorProcess:
    """`ava operator` as a user starts it, in its own process."""

    def __init__(self, env: _Env) -> None:
        self.port = _free_port()
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ava_cli",
                "operator",
                str(env.flows),
                "--port",
                str(self.port),
                "--webhook-port",
                "0",
                "--no-web",
                "--state-db",
                str(env.state_db),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.address = f"127.0.0.1:{self.port}"
        channel = grpc.insecure_channel(self.address)
        try:
            # Discovery imports the flows before the port answers.
            grpc.channel_ready_future(channel).result(timeout=120)
        finally:
            channel.close()

    @contextmanager
    def provider(self) -> Iterator[GrpcStateProvider]:
        provider = GrpcStateProvider(self.address)
        try:
            yield provider
        finally:
            provider.close()

    def kill(self) -> None:
        self.process.send_signal(signal.SIGKILL)
        self.process.wait(timeout=30)

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=30)


@contextmanager
def _served(operator: Operator) -> Iterator[tuple[pb_grpc.OperatorServiceV2Stub, str]]:
    port = _free_port()
    server = serve(operator, port=port, block=False)
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield pb_grpc.OperatorServiceV2Stub(channel), f"127.0.0.1:{port}"
    finally:
        channel.close()
        server.stop(grace=0).wait()


def _wait_remote(provider: GrpcStateProvider, run_id: str) -> RunState:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = provider.get_run(run_id)
        if run is not None and run.status in _TERMINAL:
            return run
        time.sleep(0.05)
    pytest.fail(f"Run {run_id} did not finish")


def _ava_run(capsys, address: str, *args: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = ava_main(["run", "--connect", address, *args])
    captured = capsys.readouterr()
    return code, captured.out.strip(), captured.err


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_the_rerun_scenario_against_ava_operator(env, capsys):
    """Every rerun case of the epic through `ava operator` and `ava run`, then a kill."""
    (env.flows / "linked.py").write_text(_LINKED)
    operator = _OperatorProcess(env)
    try:
        code, first_id, _ = _ava_run(capsys, operator.address, "chain")
        assert code == 0
        with operator.provider() as provider:
            first = _wait_remote(provider, first_id)
        assert first.status is RunStatus.FAILED
        assert env.executed() == ["a", "b", "c"]

        # Fix `c`. Its output feeds `d`, so `d` runs too; `a` and `b` do not run again.
        (env.flows / "chain.py").write_text(_FIXED_C)
        env.forget_executed()
        code, fixed_id, _ = _ava_run(
            capsys, operator.address, "chain", "--rerun-of", first_id, "--from", "c"
        )
        assert code == 0
        with operator.provider() as provider:
            fixed = _wait_remote(provider, fixed_id)
            assert provider.get_run_result(fixed_id) == "from a and b and c and d"
            summaries = {summary.run_id: summary for summary in provider.list_runs("chain")}
        assert fixed.status is RunStatus.SUCCESS
        assert env.executed() == ["c", "d"]
        assert _statuses(fixed) == {
            "a": NodeStatus.REUSED,
            "b": NodeStatus.REUSED,
            "c": NodeStatus.SUCCESS,
            "d": NodeStatus.SUCCESS,
        }
        assert fixed.rerun_of_run_id == first_id
        assert summaries[fixed_id].rerun_of_run_id == first_id
        assert summaries[first_id].rerun_of_run_id is None
        assert _reused_from(fixed) == {"a": first_id, "b": first_id, "c": None, "d": None}

        # A rerun that needs a renamed step is refused, and starts nothing.
        (env.flows / "chain.py").write_text(_RENAMED_B)
        env.forget_executed()
        code, out, err = _ava_run(
            capsys, operator.address, "chain", "--rerun-of", first_id, "--from", "c"
        )
        assert code == 1
        assert out == ""
        assert "b_renamed: missing_from_source" in err
        assert "b_renamed" in err.splitlines()[-1]
        assert env.executed() == []
        with operator.provider() as provider:
            assert {run.run_id for run in provider.list_runs("chain")} == {first_id, fixed_id}
        (env.flows / "chain.py").write_text(_FIXED_C)

        # --only runs the start step alone; without it, everything after runs too.
        env.forget_executed()
        code, only_id, _ = _ava_run(
            capsys, operator.address, "chain", "--rerun-of", fixed_id, "--from", "b", "--only"
        )
        assert code == 0
        with operator.provider() as provider:
            only = _wait_remote(provider, only_id)
        assert only.status is RunStatus.SUCCESS
        assert env.executed() == ["b"]
        env.forget_executed()
        code, downstream_id, _ = _ava_run(
            capsys, operator.address, "chain", "--rerun-of", fixed_id, "--from", "b"
        )
        assert code == 0
        with operator.provider() as provider:
            downstream = _wait_remote(provider, downstream_id)
        assert downstream.status is RunStatus.SUCCESS
        assert env.executed() == ["b", "c", "d"]

        # A rerun of a rerun reads each output from the run that holds it.
        env.forget_executed()
        code, chained_id, _ = _ava_run(
            capsys, operator.address, "chain", "--rerun-of", fixed_id, "--from", "d"
        )
        assert code == 0
        with operator.provider() as provider:
            chained = _wait_remote(provider, chained_id)
            assert provider.get_run_result(chained_id) == "from a and b and c and d"
        assert env.executed() == ["d"]
        assert _reused_from(chained) == {"a": first_id, "b": first_id, "c": fixed_id, "d": None}

        # A returned File is reused from the saved copy: the original is gone.
        code, with_file_id, _ = _ava_run(capsys, operator.address, "with_file")
        assert code == 0
        with operator.provider() as provider:
            assert _wait_remote(provider, with_file_id).status is RunStatus.SUCCESS
        (env.log.parent / "made.txt").unlink()
        env.forget_executed()
        code, read_file_id, _ = _ava_run(
            capsys,
            operator.address,
            "with_file",
            "--rerun-of",
            with_file_id,
            "--from",
            "read_file",
        )
        assert code == 0
        with operator.provider() as provider:
            assert _wait_remote(provider, read_file_id).status is RunStatus.SUCCESS
            assert provider.get_run_result(read_file_id) == "made once"
        assert env.executed() == ["read_file"]

        # A workspace the operator cannot copy does not fail its run, and is refused once
        # it is needed.
        code, linked_id, _ = _ava_run(capsys, operator.address, "with_linked")
        assert code == 0
        with operator.provider() as provider:
            assert _wait_remote(provider, linked_id).status is RunStatus.SUCCESS
        env.forget_executed()
        code, out, err = _ava_run(
            capsys,
            operator.address,
            "with_linked",
            "--rerun-of",
            linked_id,
            "--from",
            "reads_linked",
        )
        assert code == 1
        assert out == ""
        assert "linked: not_reusable" in err
        assert "linked" in err.splitlines()[-1]
        assert env.executed() == []

        # A killed operator fails what it was running; a new one still reruns from it.
        operator.kill()
    finally:
        operator.stop()

    operator = _OperatorProcess(env)
    try:
        env.forget_executed()
        code, after_restart_id, _ = _ava_run(
            capsys, operator.address, "chain", "--rerun-of", fixed_id, "--from", "d", "--only"
        )
        assert code == 0
        with operator.provider() as provider:
            after_restart = _wait_remote(provider, after_restart_id)
            # What a rerun recorded survives the restart too.
            before_restart = provider.get_run(fixed_id)
        assert after_restart.status is RunStatus.SUCCESS
        assert env.executed() == ["d"]
        assert _reused_from(after_restart) == {
            "a": first_id,
            "b": first_id,
            "c": fixed_id,
            "d": None,
        }
        assert _statuses(before_restart)["a"] is NodeStatus.REUSED
    finally:
        operator.stop()


_OPERATOR_KILLED_DURING_SLOW = """
import json, sys, time
from runtime.operator.operator import Operator

operator = Operator(
    [sys.argv[1]], watch=False, schedule=False, webhook_port=0, state_db=sys.argv[2],
    prepare_timeout=120,
)
run_id = operator.start_run("still_going")
while "running" not in {
    node.status.value for node in operator.get_run(run_id).nodes.values() if node.name == "slow"
}:
    time.sleep(0.03)
print(json.dumps({"run_id": run_id}), flush=True)
time.sleep(600)
"""


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_a_run_cut_off_by_a_killed_operator_can_be_rerun_after_the_restart(env):
    killed = subprocess.Popen(
        [sys.executable, "-c", _OPERATOR_KILLED_DURING_SLOW, str(env.flows), str(env.state_db)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        run_id = json.loads(killed.stdout.readline())["run_id"]
        killed.send_signal(signal.SIGKILL)
        killed.wait(timeout=30)
    finally:
        if killed.poll() is None:
            killed.kill()
        killed.stdout.close()

    flows = env.flows / "flows.py"
    flows.write_text(flows.read_text().replace("time.sleep(600)", "pass"))
    env.forget_executed()
    operator = _operator(env)
    try:
        interrupted = operator.get_run(run_id)
        assert interrupted.status is RunStatus.FAILED
        assert _statuses(interrupted)["slow"] is NodeStatus.FAILED

        # `slow` never finished, so a start after it is refused and points back at it.
        with pytest.raises(RerunRefusedError) as refused:
            operator.start_run("still_going", rerun=Rerun(run_id=run_id, start=("after_slow",)))
        assert refused.value.refusal.earliest_start == ("slow",)

        rerun = _rerun(operator, "still_going", interrupted, "slow")
        assert rerun.status is RunStatus.SUCCESS
        assert env.executed() == ["slow", "after_slow"]
        assert _reused_from(rerun) == {"before_slow": run_id, "slow": None, "after_slow": None}
        assert operator.get_run_result(rerun.run_id) == "before and slow and after"
    finally:
        operator.close()


def test_reused_steps_reach_a_watching_client_with_their_source(env, operator):
    (env.flows / "chain.py").write_text(_FIXED_C)
    first = _run(operator, "chain")
    seen: queue.Queue[RunState] = queue.Queue()
    with _served(operator) as (_, address):
        provider = GrpcStateProvider(address)
        try:

            def recover(notice):
                baseline = provider.load_reset_baseline(notice)
                provider.acknowledge_stream_reset(
                    baseline.generation,
                    baseline.operator_instance_id,
                    baseline.as_of_event_ulid,
                )

            provider.on_run_update(seen.put)
            provider.on_stream_reset(recover)
            provider.start_stream()
            deadline = time.monotonic() + 60
            while provider.stream_state is not StreamState.LIVE:
                assert time.monotonic() < deadline
                time.sleep(0.01)
            second = _rerun(operator, "chain", first, "d")
            watched = None
            while (
                watched is None
                or watched.run_id != second.run_id
                or (watched.status not in _TERMINAL)
            ):
                watched = seen.get(timeout=deadline - time.monotonic())
            # The stream announces the run; the client fetches its steps.
            fetched = provider.get_run(second.run_id)
        finally:
            provider.close()
    assert watched.rerun_of_run_id == first.run_id
    assert fetched is not None and fetched.rerun_of_run_id == first.run_id
    assert _reused_from(fetched) == {
        "a": first.run_id,
        "b": first.run_id,
        "c": first.run_id,
        "d": None,
    }
    assert _statuses(fetched)["c"] is NodeStatus.REUSED


def test_only_runs_the_named_steps_and_autorun_runs_downstream(env, operator):
    (env.flows / "chain.py").write_text(_FIXED_C)
    first = _run(operator, "chain")
    assert first.status is RunStatus.SUCCESS

    env.forget_executed()
    only = _rerun(operator, "chain", first, "b", mode="lazy")
    # The workflow returns `d`, which the lazy rerun does not run, so it is reused.
    assert env.executed() == ["b"]
    assert only.status is RunStatus.SUCCESS
    assert _statuses(only) == {
        "a": NodeStatus.REUSED,
        "b": NodeStatus.SUCCESS,
        "c": NodeStatus.SKIPPED,
        "d": NodeStatus.REUSED,
    }

    env.forget_executed()
    autorun = _rerun(operator, "chain", first, "b")
    assert env.executed() == ["b", "c", "d"]
    assert autorun.status is RunStatus.SUCCESS


def test_a_browser_gets_the_typed_refusal_through_the_web_proxy(env, operator):
    (env.flows / "chain.py").write_text(_FIXED_C)
    first = _run(operator, "chain")
    outputs = env.state_db.parent / "step-outputs"
    shutil.rmtree(outputs / step_output_run_folder_name(first.run_id))
    with _served(operator) as (_, address):
        browser = start_browser_server(address, port=0)
        try:
            request = pb.StartRunRequestV2(
                run_id="run_refused",
                workflow_selector="chain",
                rerun=pb.RunRerunV2(source_run_id=first.run_id, start=["c"]),
            )
            payload = request.SerializeToString()
            connection = http.client.HTTPConnection(browser.host, browser.port, timeout=60)
            connection.request(
                "POST",
                "/avalanche.operator.OperatorServiceV2/StartRun",
                body=struct.pack(">BI", 0, len(payload)) + payload,
                headers={"Content-Type": "application/grpc-web+proto"},
            )
            body = connection.getresponse().read()
            connection.close()
        finally:
            browser.close()

    flags, length = struct.unpack(">BI", body[:5])
    assert flags == 0x80
    trailers = dict(line.split(": ", 1) for line in body[5 : 5 + length].decode().splitlines())
    assert trailers["grpc-status"] == str(grpc.StatusCode.FAILED_PRECONDITION.value[0])
    refusal = pb.RerunRefusalV2.FromString(base64.b64decode(trailers["rerun-refusal-bin"]))
    assert [(problem.step, problem.reason) for problem in refusal.problems] == [
        ("b", "output_unavailable")
    ]
    assert list(refusal.earliest_start) == ["a"]


def test_a_rerun_of_a_rerun_goes_straight_to_the_run_holding_each_output(env, operator):
    (env.flows / "chain.py").write_text(_FIXED_C)
    first = _run(operator, "chain")
    second = _rerun(operator, "chain", first, "c")
    third = _rerun(operator, "chain", second, "d")
    assert third.status is RunStatus.SUCCESS
    assert operator.get_run_result(third.run_id) == "from a and b and c and d"
    assert _reused_from(third) == {
        "a": first.run_id,
        "b": first.run_id,
        "c": second.run_id,
        "d": None,
    }
    index = operator.saved_output_index(third.run_id)
    assert index.outputs["b"] == SavedStepOutput(slug="b", run_id=first.run_id)
    assert index.outputs["c"] == SavedStepOutput(slug="c", run_id=second.run_id)
    assert index.outputs["d"] == SavedStepOutput(slug="d", run_id=third.run_id)

    # A held output is gone with its run, and the checks say so rather than guessing.
    outputs = env.state_db.parent / "step-outputs"
    shutil.rmtree(outputs / step_output_run_folder_name(first.run_id))
    with pytest.raises(RerunRefusedError) as refused:
        operator.start_run("chain", rerun=Rerun(run_id=third.run_id, start=("c",)))
    assert [(problem.step, problem.reason) for problem in refused.value.refusal.problems] == [
        ("b", RerunProblemReason.OUTPUT_UNAVAILABLE)
    ]


def test_a_rerun_of_a_rerun_keeps_the_saved_output_holder(env, operator):
    first = _run(operator, "with_numbers")
    assert first.status is RunStatus.SUCCESS
    entry = operator.saved_output_index(first.run_id).outputs["numbers"]

    second = _rerun(operator, "with_numbers", first, "last")
    assert second.status is RunStatus.SUCCESS
    # `second` never read the list, and still has to say which run holds it.
    carried = operator.saved_output_index(second.run_id).outputs["numbers"]
    assert carried.run_id == first.run_id
    assert type(carried) is type(entry)

    env.forget_executed()
    third = _rerun(operator, "with_numbers", second, "between")
    assert third.status is RunStatus.SUCCESS
    assert env.executed() == ["between", "last"]
    assert operator.get_run_result(third.run_id) == 4


def test_a_step_that_could_not_be_saved_stays_not_reusable_through_a_chain(env, operator):
    (env.flows / "linked.py").write_text(_LINKED)
    operator.close()
    reopened = _operator(env)
    try:
        first = _run(reopened, "with_linked")
        assert first.status is RunStatus.SUCCESS
        second = _rerun(reopened, "with_linked", first, "last_step")
        assert second.status is RunStatus.SUCCESS
        entry = reopened.saved_output_index(second.run_id).outputs["linked"]
        assert isinstance(entry, NotReusableStepOutput)
        assert entry.run_id == first.run_id
        with pytest.raises(RerunRefusedError) as refused:
            reopened.start_run(
                "with_linked", rerun=Rerun(run_id=second.run_id, start=("reads_linked",))
            )
        assert refused.value.refusal.problems[0].reason is RerunProblemReason.NOT_REUSABLE
        assert refused.value.refusal.earliest_start == ("linked",)
    finally:
        reopened.close()


def test_a_reused_map_group_keeps_its_items_through_a_chain(env, operator):
    first = _run(operator, "with_map")
    assert operator.get_run_result(first.run_id) == 12

    env.forget_executed()
    second = _rerun(operator, "with_map", first, "total")
    assert env.executed() == ["total"]
    assert operator.get_run_result(second.run_id) == 12
    assert _statuses(second)["double"] is NodeStatus.REUSED

    env.forget_executed()
    third = _rerun(operator, "with_map", second, "total")
    assert env.executed() == ["total"]
    assert operator.get_run_result(third.run_id) == 12
    assert _reused_from(third)["double"] == first.run_id

    env.forget_executed()
    remapped = _rerun(operator, "with_map", third, "double")
    assert sorted(env.executed()) == ["double", "double", "double", "total"]
    assert operator.get_run_result(remapped.run_id) == 12
    assert _reused_from(remapped)["numbers"] == first.run_id


def test_saved_files_and_the_run_workspace_come_back_checked(env, operator):
    first = _run(operator, "with_file")
    assert first.status is RunStatus.SUCCESS
    # The original is gone: the rerun can only read the saved copy.
    (env.log.parent / "made.txt").unlink()

    env.forget_executed()
    second = _rerun(operator, "with_file", first, "read_file")
    assert second.status is RunStatus.SUCCESS
    assert env.executed() == ["read_file"]
    assert operator.get_run_result(second.run_id) == "made once"

    copies = [
        path
        for path in (env.state_db.parent / "step-outputs").rglob("*")
        if path.is_file() and path.parent.name == "files"
    ]
    assert len(copies) == 1
    os.chmod(copies[0], 0o600)
    copies[0].write_text("tampered")
    with pytest.raises(RerunRefusedError) as refused:
        operator.start_run("with_file", rerun=Rerun(run_id=first.run_id, start=("read_file",)))
    assert refused.value.refusal.problems[0].reason is RerunProblemReason.OUTPUT_UNAVAILABLE

    # Steps edit the run's one workspace in place. A rerun starts its own workspace
    # as the reused step left it, not as the source run ended, and a rerun of the
    # rerun starts from the same copy.
    source = _run(operator, "with_run_workspace")
    assert operator.get_run_result(source.run_id) == "draft revised"
    env.forget_executed()
    rerun = _rerun(operator, "with_run_workspace", source, "revise")
    assert env.executed() == ["revise"]
    assert operator.get_run_result(rerun.run_id) == "draft revised"
    again = _rerun(operator, "with_run_workspace", rerun, "revise")
    assert operator.get_run_result(again.run_id) == "draft revised"

    # A copy that changed fails the rerun before any step runs.
    saved = next(
        path
        for path in (env.state_db.parent / "step-outputs").rglob("notes.txt")
        if path.parent.name == "run-workspace"
    )
    os.chmod(saved, 0o600)
    saved.write_text("tampered")
    env.forget_executed()
    broken = _rerun(operator, "with_run_workspace", source, "revise")
    assert broken.status is RunStatus.FAILED
    assert env.executed() == []


def test_a_rerun_past_an_approval_gate_does_not_wait_again(env, operator):
    run_id = operator.start_run("with_gate")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = operator.get_run(run_id)
        if run.nodes and not operator.wait_control(run_id, _LIST_WAITS).waits == []:
            break
        time.sleep(0.05)
    operator.wait_control(
        run_id,
        WaitControlRequest(
            kind="event",
            address="go",
            payload={"answer": "approved"},
            idempotency_key="approve",
        ),
    )
    first = _wait(operator, run_id)
    assert first.status is RunStatus.SUCCESS
    assert set(operator.saved_output_index(run_id).outputs) >= {"before_gate", "wait_for_event"}

    env.forget_executed()
    second = _rerun(operator, "with_gate", first, "after_approval")
    assert second.status is RunStatus.SUCCESS
    assert env.executed() == ["after_approval"]
    assert operator.get_run_result(second.run_id) == "ready and approved"
    assert _statuses(second)["wait_for_event"] is NodeStatus.REUSED


def test_the_source_input_is_reused_and_given_fields_replace_it(env, operator):
    first = _run(
        operator,
        "with_input",
        input={"first": 10, "second": 20},
        context={"who": "me", "extra": 1},
    )
    assert operator.get_run_result(first.run_id) == [10, 20, "me", 1]

    same = _rerun(operator, "with_input", first, "read_input")
    assert operator.get_run_result(same.run_id) == [10, 20, "me", 1]

    changed = _rerun(
        operator,
        "with_input",
        first,
        "read_input",
        input={"second": 99},
        context={"who": "you"},
    )
    assert operator.get_run_result(changed.run_id) == [10, 99, "you", 1]


def test_files_attached_by_the_client_are_reused_and_can_be_replaced(env, operator):
    with _served(operator) as (_, address):
        provider = GrpcStateProvider(address)
        try:
            source = _wait_remote(
                provider,
                provider.start_run(
                    "with_document", input={"label": "kept"}, files={"document": b"first"}
                ),
            )
            reused = _wait_remote(
                provider,
                provider.start_run(
                    "with_document", rerun=Rerun(run_id=source.run_id, start="read_document")
                ),
            )
            replaced = _wait_remote(
                provider,
                provider.start_run(
                    "with_document",
                    files={"document": b"second"},
                    rerun=Rerun(run_id=source.run_id, start="read_document"),
                ),
            )
        finally:
            provider.close()
    assert operator.get_run_result(reused.run_id) == "kept:first"
    assert operator.get_run_result(replaced.run_id) == "kept:second"


def _stage(stub: pb_grpc.OperatorServiceV2Stub, content: bytes) -> pb.FileAttachmentV2:
    return stub.StageInputFile(
        pb.StageInputFileRequestV2(
            workflow_selector="with_document",
            field_name="document",
            name="doc.txt",
            media_type="text/plain",
            content=content,
        )
    )


def test_source_input_files_are_reused_after_a_restart_and_can_be_replaced(env, operator):
    with _served(operator) as (stub, _):
        attached = _stage(stub, b"first document")
        stub.StartRun(
            pb.StartRunRequestV2(
                run_id="source",
                workflow_selector="with_document",
                input_json=json.dumps({"label": "kept"}),
                input_files=[attached],
            )
        )
        assert _wait(operator, "source").status is RunStatus.SUCCESS
    with operator._state_store.transaction() as connection:
        stored = RunsTable.read_request(connection, "source").input["document"]
    operator.close()

    # A reboot clears the temporary folder that registers local workspaces.
    token = stored["ref"]["workspace"].removeprefix("local-ws:v1:")
    (Path(tempfile.gettempdir()) / f"avalanche-local-refs-{os.getuid()}" / token).unlink()

    reopened = _operator(env)
    try:
        with _served(reopened) as (stub, _):
            rerun = pb.RunRerunV2(source_run_id="source", start=["read_document"])
            stub.StartRun(
                pb.StartRunRequestV2(
                    run_id="reused", workflow_selector="with_document", rerun=rerun
                )
            )
            assert _wait(reopened, "reused").status is RunStatus.SUCCESS
            assert reopened.get_run_result("reused") == "kept:first document"

            replacement = _stage(stub, b"second document")
            stub.StartRun(
                pb.StartRunRequestV2(
                    run_id="replaced",
                    workflow_selector="with_document",
                    input_files=[replacement],
                    rerun=rerun,
                )
            )
            assert _wait(reopened, "replaced").status is RunStatus.SUCCESS
            assert reopened.get_run_result("replaced") == "kept:second document"
            with reopened._state_store.transaction() as connection:
                # The replaced file is no longer this run's, so it can expire with `source`.
                assert [
                    file.sha256
                    for file in RunsTable.read_request(connection, "replaced").input_files
                ] == [replacement.sha256]
    finally:
        reopened.close()


def test_uploads_with_the_same_name_and_content_can_be_rerun(env, operator):
    def stage(stub, field: str, content: bytes = b"same") -> pb.FileAttachmentV2:
        return stub.StageInputFile(
            pb.StageInputFileRequestV2(
                workflow_selector="with_two_documents",
                field_name=field,
                name="x.csv",
                media_type="text/csv",
                content=content,
            )
        )

    def stored_keys(state: Operator, run_id: str) -> tuple[str, ...]:
        with state._state_store.transaction() as connection:
            return RunsTable.input_file_keys(connection, run_id)

    def workspaces(state: Operator, run_id: str) -> list[str]:
        with state._state_store.transaction() as connection:
            stored = RunsTable.read_request(connection, run_id).input
        return [
            item["ref"]["workspace"]
            for item in [stored["first"], stored["second"], *stored["more"]]
        ]

    rerun = pb.RunRerunV2(source_run_id="source", start=["read_documents"])
    with _served(operator) as (stub, _):
        attached = [stage(stub, field) for field in ("first", "second", "more", "more")]
        stub.StartRun(
            pb.StartRunRequestV2(
                run_id="source",
                workflow_selector="with_two_documents",
                input_files=attached,
            )
        )
        assert _wait(operator, "source").status is RunStatus.SUCCESS
        source_keys = stored_keys(operator, "source")
        assert len(set(source_keys)) == 4

        stub.StartRun(
            pb.StartRunRequestV2(
                run_id="reused", workflow_selector="with_two_documents", rerun=rerun
            )
        )
        assert _wait(operator, "reused").status is RunStatus.SUCCESS
        assert operator.get_run_result("reused") == ["same"] * 4
        assert stored_keys(operator, "reused") == source_keys

        # Replacing a field gives up that field's own upload, not its twin's.
        replacement = stage(stub, "first", b"other")
        stub.StartRun(
            pb.StartRunRequestV2(
                run_id="replaced",
                workflow_selector="with_two_documents",
                input_files=[replacement],
                rerun=rerun,
            )
        )
        assert _wait(operator, "replaced").status is RunStatus.SUCCESS
        assert operator.get_run_result("replaced") == ["other", "same", "same", "same"]
        assert stored_keys(operator, "replaced") == (*source_keys[1:], replacement.object_key)
        references = workspaces(operator, "source")
    operator.close()

    # A reboot clears the registry, here for all but one of the four references.
    registry = Path(tempfile.gettempdir()) / f"avalanche-local-refs-{os.getuid()}"
    for reference in references[:3]:
        (registry / reference.removeprefix("local-ws:v1:")).unlink()

    reopened = _operator(env)
    try:
        with _served(reopened) as (stub, _):
            stub.StartRun(
                pb.StartRunRequestV2(
                    run_id="after_reboot", workflow_selector="with_two_documents", rerun=rerun
                )
            )
            assert _wait(reopened, "after_reboot").status is RunStatus.SUCCESS
            assert reopened.get_run_result("after_reboot") == ["same"] * 4
            assert sorted(stored_keys(reopened, "after_reboot")) == sorted(source_keys)
    finally:
        reopened.close()


def test_a_refusal_lists_every_problem_over_grpc_and_records_nothing(env, operator):
    first = _run(operator, "chain")
    with _served(operator) as (stub, address):
        provider = GrpcStateProvider(address)

        def start(run_id: str, flow: str, rerun: pb.RunRerunV2) -> grpc.RpcError:
            with pytest.raises(grpc.RpcError) as failure:
                stub.StartRun(
                    pb.StartRunRequestV2(run_id=run_id, workflow_selector=flow, rerun=rerun)
                )
            return failure.value

        try:
            # `c` failed and `nope` does not exist; both are reported together.
            refused = start(
                "refused",
                "chain",
                pb.RunRerunV2(source_run_id=first.run_id, start=["d", "nope"]),
            )
            assert refused.code() is grpc.StatusCode.FAILED_PRECONDITION
            assert "nope" in refused.details()
            assert "Earliest step to rerun from: " in refused.details()
            assert operator.get_run("refused") is None

            with pytest.raises(RerunRefusedError) as typed:
                provider.start_run(
                    "chain",
                    run_id="typed",
                    rerun=Rerun(run_id=first.run_id, start=("d", "nope")),
                )
            assert {problem.step for problem in typed.value.refusal.problems} == {"nope", "c"}
            assert typed.value.refusal.earliest_start == ("c",)
            assert operator.get_run("typed") is None

            missing = start(
                "no_source", "chain", pb.RunRerunV2(source_run_id="absent", start=["c"])
            )
            assert missing.code() is grpc.StatusCode.NOT_FOUND

            other = start(
                "other_flow",
                "other_flow",
                pb.RunRerunV2(source_run_id=first.run_id, start=["c"]),
            )
            assert other.code() is grpc.StatusCode.FAILED_PRECONDITION

            for bad in (
                pb.RunRerunV2(source_run_id=first.run_id),
                pb.RunRerunV2(source_run_id=first.run_id, start=["c", "c"]),
                pb.RunRerunV2(start=["c"]),
            ):
                assert start("bad", "chain", bad).code() is grpc.StatusCode.INVALID_ARGUMENT
        finally:
            provider.close()


def test_a_refusal_with_many_long_problems_reaches_the_client_whole(env, operator):
    count = 40
    steps = "\n".join(
        f"""
@ava.source
def source_{index}() -> str:
    return "x"


@ava.step
def reader_{index}(text: str) -> str:
    return text
"""
        for index in range(count)
    )
    reads = ", ".join(f"reader_{index}(source_{index}())" for index in range(count))
    (env.flows / "flows.py").write_text(
        f"""
import avalanche as ava
{steps}

@ava.workflow
def many():
    return ({reads})
"""
    )
    operator.close()
    reopened = _operator(env)
    try:
        source = _run(reopened, "many")
        assert source.status is RunStatus.SUCCESS
        # Each saved string no longer fits, which makes for a long validation error.
        text = (env.flows / "flows.py").read_text()
        (env.flows / "flows.py").write_text(
            text.replace('-> str:\n    return "x"', "-> list[int]:\n    return [1]")
        )
        with _served(reopened) as (_, address):
            provider = GrpcStateProvider(address)
            try:
                with pytest.raises(RerunRefusedError) as refused:
                    provider.start_run(
                        "many",
                        rerun=Rerun(
                            run_id=source.run_id,
                            start=tuple(f"reader_{index}" for index in range(count)),
                        ),
                    )
            finally:
                provider.close()
        assert {problem.step for problem in refused.value.refusal.problems} == {
            f"source_{index}" for index in range(count)
        }
    finally:
        reopened.close()


def test_a_run_still_going_is_refused_and_an_unknown_one_is_not_found(env, operator):
    run_id = operator.start_run("still_going")
    deadline = time.monotonic() + 120
    while env.executed() != ["slow"] and time.monotonic() < deadline:
        time.sleep(0.03)
    try:
        with pytest.raises(RerunRefusedError) as refused:
            operator.start_run("still_going", rerun=Rerun(run_id=run_id, start=("slow",)))
        assert refused.value.refusal.problems[0].reason is RerunProblemReason.SOURCE_RUN_ACTIVE
        assert refused.value.refusal.earliest_start == ()
    finally:
        operator.cancel_run(run_id)
    with pytest.raises(UnknownRerunSourceError):
        operator.start_run("still_going", rerun=Rerun(run_id="absent", start=("slow",)))


def test_code_that_no_longer_imports_refuses_the_rerun_and_records_nothing(env, operator):
    first = _run(operator, "chain")
    (env.flows / "chain.py").write_text(_FIXED_C.replace("def a()", "def a(:"))
    with pytest.raises(RuntimeError):
        operator.start_run(
            "chain", run_id="broken", rerun=Rerun(run_id=first.run_id, start="c")
        )
    assert operator.get_run("broken") is None


@pytest.mark.parametrize(
    "args", [("--from", "c"), ("--only",), ("--rerun-of", "run_1")], ids=lambda args: args[0]
)
def test_ava_run_rejects_rerun_flags_used_alone(args, capsys):
    assert ava_main(["run", "--connect", "127.0.0.1:1", "chain", *args]) == 1
    assert capsys.readouterr().err


def _parked_child(operator: Operator, parent: str) -> str:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        children = operator.run_links(parent)["children"]
        if children and operator.get_run(children[0].child_run_id).status is RunStatus.WAITING:
            return children[0].child_run_id
        time.sleep(0.03)
    pytest.fail(f"Run {parent} did not park on its child")


def _approved(operator: Operator, parent: str) -> RunState:
    operator._machine_host.send(_parked_child(operator, parent), "approved", {}, "approve")
    return _wait(operator, parent)


def test_a_rerun_parked_on_a_call_keeps_its_reused_steps_across_a_restart(env):
    (env.flows / "calls.py").write_text(_CALLS)
    operator = _operator(env)
    try:
        first = _approved(operator, operator.start_run("calls_approval", input={"id": 1}))
        assert first.status is RunStatus.SUCCESS
        env.forget_executed()
        rerun_id = operator.start_run(
            "calls_approval",
            input={"id": 2},
            # A call is a start step and its wait; rerun from the start.
            rerun=Rerun(run_id=first.run_id, start=("start_run",)),
        )
        _parked_child(operator, rerun_id)
    finally:
        operator.close()
    reopened = _operator(env)
    try:
        rerun = _approved(reopened, rerun_id)
        assert rerun.status is RunStatus.SUCCESS
        assert reopened.get_run_result(rerun_id) == "approved 2"
        # The call's wait is named after its child, so the graph shows one `approval` node.
        names = dict(rerun.topology.display_names)
        assert sorted(names.values()) == ["after_call", "approval", "before_call", "start_run"]
        # The resumed run restored the reused step instead of running it again.
        assert env.executed() == ["after_call"]
        assert _statuses(rerun)["before_call"] is NodeStatus.REUSED
        assert _reused_from(rerun)["before_call"] == first.run_id
    finally:
        reopened.close()
