"""Real embedded execution: admission, controls, scheduling and terminal output."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

import pytest
from pydantic import BaseModel, ValidationError

import avalanche as ava
from avalanche.local_waits import LocalPrincipal, WaitRefusalError
from avalanche.policies import DecisionTimeoutError


class Approval(ava.Event):
    kind: Literal["approve", "reject"]


class Signed(BaseModel):
    envelope_id: str


REVIEWER = LocalPrincipal(name="reviewer", permissions=frozenset({"run:decide", "event:send"}))
SENDER = LocalPrincipal(name="sender", permissions=frozenset({"event:send"}))


def test_buffered_event_decision_and_slot_free_sleep():
    @ava.step
    async def produce(run: ava.RunContext) -> str:
        return run.step_key

    @ava.step
    async def combine(doc: str, approval: Approval, signed: Signed) -> str:
        assert approval.kind == "approve"
        return f"{doc}/{signed.envelope_id}"

    @ava.workflow
    def flow():
        doc = produce()
        review = ava.decision(
            Approval,
            prompt="Approve",
            show=doc,
            timeout=None,
            approvers=["reviewer"],
            slug="review",
        )
        signed = ava.wait_for_event(Signed, key="signed/exact", timeout=None)
        doc >> review >> signed >> ava.sleep(timedelta(days=1))
        return combine(doc, review, signed)

    run = flow().run(
        run_id="buffered", executor=ava.LocalExecutor(max_workers=1), fast_timers=True
    )
    receipt = run.send_event(
        "signed/exact", {"envelope_id": "a"}, idempotency_key="evt", principal=SENDER
    )
    assert run.wait_until_waiting(timeout=5)
    with pytest.raises(WaitRefusalError) as denied:
        run.decide(
            "review",
            {"kind": "approve"},
            idempotency_key="denied",
            principal=SENDER,
            reason="not an approver",
        )
    assert denied.value.code == "unauthorized"
    with pytest.raises(ValidationError):
        run.decide(
            "review",
            {"kind": "other"},
            idempotency_key="invalid",
            principal=REVIEWER,
            reason="invalid",
        )
    decision = run.decide(
        "review",
        {"kind": "approve"},
        idempotency_key="approved",
        principal=REVIEWER,
        reason="checked draft",
    )
    assert run.result(timeout=5) == "buffered:produce_1/a"
    assert (
        run.send_event(
            "signed/exact", {"envelope_id": "a"}, idempotency_key="evt", principal=SENDER
        )
        == receipt
    )
    assert (
        run.decide(
            "review",
            {"kind": "approve"},
            idempotency_key="approved",
            principal=REVIEWER,
            reason="checked draft",
        )
        == decision
    )
    assert decision.principal == "reviewer"
    assert decision.reason == "checked draft"
    assert decision.reviewed == "buffered:produce_1"
    with pytest.raises(WaitRefusalError) as conflict:
        run.send_event(
            "signed/exact", {"envelope_id": "b"}, idempotency_key="evt", principal=SENDER
        )
    assert conflict.value.code == "idempotency_conflict"
    with pytest.raises(WaitRefusalError) as invalid_conflict:
        run.send_event("signed/exact", {"wrong": True}, idempotency_key="evt", principal=SENDER)
    assert invalid_conflict.value.code == "idempotency_conflict"
    with pytest.raises(WaitRefusalError) as closed:
        run.send_event(
            "signed/exact", {"envelope_id": "b"}, idempotency_key="new", principal=SENDER
        )
    assert closed.value.code == "run_closed"


def test_controlled_deadlines_preloads_and_cancellation():
    @ava.workflow
    def flow():
        return ava.decision(
            Approval,
            prompt="Approve",
            timeout=timedelta(days=2),
            approvers=["reviewer"],
            slug="review",
        )

    # fast_timers must not manufacture an approval timeout.
    run = flow().run(fast_timers=True)
    assert run.wait_until_waiting(timeout=5)
    assert not run.done()
    run.advance_time(timedelta(days=3))
    with pytest.raises(DecisionTimeoutError):
        run.result(timeout=5)
    preloaded = flow().run(decisions={"review": Approval(kind="approve")}, principal=REVIEWER)
    assert preloaded.result(timeout=5) == Approval(kind="approve")
    cancelled = flow().run()
    assert cancelled.wait_until_waiting(timeout=5)
    assert cancelled.cancel()
    from concurrent.futures import CancelledError

    with pytest.raises(CancelledError):
        cancelled.result(timeout=5)
    with pytest.raises(WaitRefusalError):
        cancelled.decide(
            "review",
            {"kind": "approve"},
            idempotency_key="late",
            principal=REVIEWER,
            reason="too late",
        )

    @ava.workflow
    def timeout_flow():
        return ava.wait_for_event(Signed, key="x", timeout=10)

    event = timeout_flow().run()
    assert event.wait_until_waiting(timeout=5)
    with pytest.raises(WaitRefusalError):
        event.send_event("unknown", {}, idempotency_key="unknown", principal=SENDER)
    with pytest.raises(ValidationError):
        event.send_event("x", {"wrong": 1}, idempotency_key="invalid", principal=SENDER)
    event.advance_time(timedelta(seconds=11))
    assert event.result(timeout=5) is None


def test_mapped_decisions_are_independent_bounded_and_fan_in():
    @ava.source
    async def drafts() -> list[str]:
        return ["article-a", "article-b"]

    @ava.step
    async def quorum(reviews: list[Approval]) -> bool:
        return sum(review.kind == "approve" for review in reviews) >= 1

    @ava.workflow
    def flow():
        reviews = ava.decision.map(
            Approval,
            drafts(),
            prompt="Publish?",
            timeout=None,
            approvers=["reviewer"],
            max_items=2,
            slug="reviews",
        )
        return reviews >> quorum()

    run = flow().run()
    assert run.wait_until_waiting(timeout=5)
    cards = run.waits.snapshot()
    assert {card["node_id"] for card in cards} == {"decision_1[0]", "decision_1[1]"}
    first = run.decide(
        "reviews[0]",
        {"kind": "reject"},
        principal=REVIEWER,
        reason="reject only this item",
        idempotency_key="item-0",
    )
    assert first.reviewed == "article-a"
    assert not run.done()
    second = run.decide(
        "reviews[1]",
        {"kind": "approve"},
        principal=REVIEWER,
        reason="checked second article",
        idempotency_key="item-1",
    )
    assert second.reviewed == "article-b"
    assert first.address != second.address
    assert run.result(timeout=5) is True

    preloaded = flow().run(
        decisions={
            "reviews[0]": Approval(kind="approve"),
            "reviews[1]": Approval(kind="reject"),
        },
        principal=REVIEWER,
    )
    assert preloaded.result(timeout=5) is True

    @ava.workflow
    def too_large():
        return ava.decision.map(
            Approval, ["a", "b", "c"], prompt="Too many", timeout=None, max_items=2
        )

    with pytest.raises(ValueError, match="map_overflow"):
        too_large().run().result(timeout=5)


def test_nullable_event_timeout_flows_through_implicit_composition():
    @ava.step(retries=0)
    def handle_timeout(value: Signed | None) -> str:
        return "timeout" if value is None else value.envelope_id

    @ava.step(retries=0)
    def after_sleep() -> str:
        return "awake"

    @ava.workflow
    def flow():
        return ava.wait_for_event(Signed, key="message", timeout=0) >> handle_timeout()

    @ava.workflow
    def ordering():
        return ava.sleep(0) >> after_sleep()

    assert flow().run().result(timeout=5) == "timeout"
    assert ordering().run().result(timeout=5) == "awake"


def test_shared_retry_budget_terminal_errors_and_reconciliation():
    keys = []

    @ava.source(retries=2)
    async def retry(run: ava.RunContext) -> str:
        keys.append(run.step_key)
        if len(keys) == 1:
            raise RuntimeError("synthetic transient source failure")
        if len(keys) < 3:
            raise ava.OutcomeUnknownError("synthetic uncertainty")
        return run.step_key

    @ava.workflow
    def retries():
        return retry()

    assert retries().run(run_id="retry-run").result(timeout=5) == "retry-run:retry_1"
    assert keys == ["retry-run:retry_1"] * 3
    count = []

    @ava.step(retries=5)
    async def terminal() -> None:
        count.append(1)
        raise ava.NonRetryableError("bad input")

    @ava.workflow
    def terminal_flow():
        return terminal()

    with pytest.raises(ava.NonRetryableError):
        terminal_flow().run().result(timeout=5)
    assert count == [1]

    @ava.step(retries=0)
    async def unknown() -> str:
        raise ava.OutcomeUnknownError("provider outcome unknown")

    @ava.workflow
    def no_retry():
        return unknown()

    with pytest.raises(ava.OutcomeUnknownError):
        no_retry().run().result(timeout=5)

    @ava.step(retries=0, on_uncertain="hold")
    async def held(run: ava.RunContext) -> str:
        keys.append(run.step_key)
        raise ava.OutcomeUnknownError("reconcile")

    @ava.workflow
    def hold():
        return held()

    run = hold().run(run_id="hold")
    assert run.wait_until_waiting(timeout=5)
    run.decide(
        "held_1",
        {"kind": "done", "output": "receipt"},
        idempotency_key="reconcile",
        principal=REVIEWER,
        reason="authoritative recipient lookup",
    )
    assert run.result(timeout=5) == "receipt"
    assert keys[-1] == "hold:held_1"


def test_operator_shared_run_status(tmp_path):
    """A1-A6, A8-A9: real coordinator, SQLite, gRPC, CLI and stream replay."""
    import json
    import subprocess
    import sys
    import time
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    import grpc

    from runtime.operator.operator import Operator
    from runtime.operator.proto import operator_pb2 as pb
    from runtime.operator.proto import operator_pb2_grpc as rpc
    from runtime.operator.server_v2 import OperatorV2Servicer

    fixture = Path(__file__).parent / "fixtures" / "run_status.py"
    database = tmp_path / "state.db"
    op = Operator([str(fixture)], watch=False, schedule=False, state_db=database)
    calls = Counter()

    class CountCalls(grpc.ServerInterceptor):
        def intercept_service(self, continuation, details):
            calls[details.method.rsplit("/", 1)[-1]] += 1
            return continuation(details)

    server = grpc.server(ThreadPoolExecutor(max_workers=10), interceptors=[CountCalls()])
    rpc.add_OperatorServiceV2Servicer_to_server(OperatorV2Servicer(op), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub = rpc.OperatorServiceV2Stub(channel)
    waiting_process = None

    def until(predicate):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            assert op.fatal_error is None, op.fatal_error
            time.sleep(0.02)
        raise AssertionError("operator summary did not progress")

    def row(run_id, status=None, counts=None):
        result = stub.GetRunSummary(pb.GetRunSummaryRequestV2(run_id=run_id)).summary
        waits = result.waiting_on
        actual = (waits.operator, waits.decision, waits.event, waits.run, waits.sleep)
        return (
            result
            if (status is None or result.status == status)
            and (counts is None or actual == counts)
            else None
        )

    def control(run_id, kind, address, payload):
        return stub.ControlWait(
            pb.ControlWaitRequestV2(
                run_id=run_id,
                kind=kind,
                address=address,
                payload_json=json.dumps(payload),
                idempotency_key=f"{kind}:{address}",
                reason="checked by local proof",
            )
        )

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "ava_cli", *args, "--connect", f"127.0.0.1:{port}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )

    try:
        run_id = op.start_run("waits3")
        a1 = until(lambda: row(run_id, "waiting", (0, 1, 1, 0, 1)))
        assert a1.lifecycle == "running" and a1.kind == "flow"
        listed = json.loads(cli("run", "list", "--json").stdout)["runs"]
        listed = next(item for item in listed if item["run_id"] == run_id)
        assert listed["waiting_on"] == [0, 1, 1, 0, 1]
        assert listed["status"] == "waiting" and listed["lifecycle"] == "running"
        assert listed["kind"] == "flow"
        table = cli("run", "list").stdout
        assert "TYPE\tRUN\tDEFINITION\tSTATUS\tNOTE" in table
        assert "waiting on decision +2" in table
        control(run_id, "event", "paid", {"paid": True})
        a2 = until(lambda: row(run_id, "waiting", (0, 1, 0, 0, 1)))
        assert a2.revision > a1.revision
        control(run_id, "decision", "approve", {"approved": True})
        a3 = until(lambda: row(run_id, "waiting", (0, 0, 0, 0, 1)))
        assert a3.revision > a2.revision
        op.cancel_run(run_id)

        # A4/A9: expiry drives running then succeeded; the CLI opens its real
        # status stream before controls resolve the other branches. ControlWait's
        # accepted operations stay unchanged (no local-operator time travel).
        short_id = op.start_run("waits3_short")
        short_initial = until(lambda: row(short_id, "waiting", (0, 1, 1, 0, 1)))
        before = calls.copy()
        waiting_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ava_cli",
                "result",
                short_id,
                "--wait",
                "--json",
                "--timeout",
                "60",
                "--connect",
                f"127.0.0.1:{port}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        until(lambda: calls["WatchRunStatus"] == before["WatchRunStatus"] + 1)
        control(short_id, "event", "paid", {"paid": True})
        control(short_id, "decision", "approve", {"approved": True})
        running = until(
            lambda: (
                (
                    value
                    if value.human_status == "running" and value.waiting_on == (0, 0, 0, 0, 0)
                    else None
                )
                if (value := op.get_run_summary(short_id)[0])
                else None
            )
        )
        output, error = waiting_process.communicate(timeout=60)
        assert waiting_process.returncode == 0, error
        assert "finished" in output
        assert calls["GetRunSnapshot"] == before["GetRunSnapshot"]
        assert calls["GetRunSummary"] == before["GetRunSummary"] + 1
        assert calls["WatchRunStatus"] == before["WatchRunStatus"] + 1
        wait_calls = {
            name: calls[name] - before[name]
            for name in ("GetRunSnapshot", "GetRunSummary", "WatchRunStatus")
        }
        assert running.revision > short_initial.revision
        succeeded = until(lambda: row(short_id, "succeeded"))
        assert not succeeded.HasField("waiting_on")
        assert succeeded.revision > running.revision

        boom = op.start_run("boom")
        failed = until(lambda: row(boom, "failed"))
        assert failed.error_code == "node_failed"
        # Invocation IDs, not slugs, are the exact identities carried in detail.
        assert failed.error_step == "charge_card_1"
        assert failed.error_message == "ValueError in step charge_card_1"
        listed_bytes = cli("run", "list", "--json").stdout
        assert not any(secret in listed_bytes for secret in ("sk-", "jane", "acme", "declined"))
        listed_failure = next(
            item for item in json.loads(listed_bytes)["runs"] if item["run_id"] == boom
        )
        assert listed_failure["error_code"] == failed.error_code
        assert listed_failure["error_step"] == failed.error_step
        assert listed_failure["error_message"] == failed.error_message
        assert int(listed_failure["revision"]) == failed.revision
        detail = json.loads(cli("run", "show", boom, "--json").stdout)
        assert any("sk-test-4242" in node.get("error", "") for node in detail["nodes"])
        assert int(detail["summary"]["revision"]) == int(listed_failure["revision"])
        baseline = failed.SerializeToString(deterministic=True)
        from google.protobuf.json_format import MessageToDict

        evidence = {
            "A1": MessageToDict(a1, preserving_proto_field_name=True),
            "A2": MessageToDict(a2, preserving_proto_field_name=True),
            "A3": MessageToDict(a3, preserving_proto_field_name=True),
            "A4": {
                "running_revision": running.revision,
                "succeeded_revision": succeeded.revision,
            },
            "A5": MessageToDict(failed, preserving_proto_field_name=True),
            "A6_detail_revision": int(detail["summary"]["revision"]),
            "A9_calls": wait_calls,
            "row_protobuf_bytes": {"waiting": a1.ByteSize(), "failed": failed.ByteSize()},
        }
        (tmp_path / "shared-status-evidence.json").write_text(json.dumps(evidence, indent=2))
        print("SHARED_STATUS_EVIDENCE", json.dumps(evidence, sort_keys=True))

        from runtime.operator.client import GrpcStateProvider
        from runtime.operator.models import StreamResetNotice

        provider = GrpcStateProvider(f"127.0.0.1:{port}")
        try:
            snapshot_calls = calls["GetRunSnapshot"]
            reset = provider.load_reset_baseline(
                StreamResetNotice(
                    generation=1,
                    previous_event_ulid="",
                    observed_event_ulid="",
                    operator_instance_id=op.operator_instance_id,
                )
            )
            reset_rows = [item for group in reset.runs_by_workflow.values() for item in group]
            assert calls["GetRunSnapshot"] == snapshot_calls
            failed_row = next(item for item in reset_rows if item.run_id == boom)
            assert failed_row.revision == failed.revision
            assert failed_row.error_message == failed.error_message
            assert all(not item.details_hydrated for item in reset_rows)
        finally:
            provider.close()
    finally:
        if waiting_process is not None and waiting_process.poll() is None:
            waiting_process.kill()
            waiting_process.communicate(timeout=10)
        channel.close()
        server.stop(grace=0).wait()
        op.close()

    # A8: restart from the same SQLite database, with no broker or second owner.
    op = Operator([str(fixture)], watch=False, schedule=False, state_db=database)
    server = grpc.server(ThreadPoolExecutor(max_workers=10))
    rpc.add_OperatorServiceV2Servicer_to_server(OperatorV2Servicer(op), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub = rpc.OperatorServiceV2Stub(channel)
    try:
        restored = row(boom)
        assert restored.SerializeToString(deterministic=True) == baseline
        print(
            "SHARED_STATUS_RESTART",
            json.dumps(
                {
                    "run_id": boom,
                    "revision": restored.revision,
                    "byte_equal": restored.SerializeToString(deterministic=True) == baseline,
                }
            ),
        )
    finally:
        channel.close()
        server.stop(grace=0).wait()
        op.close()
