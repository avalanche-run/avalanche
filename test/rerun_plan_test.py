"""Reruns from saved step outputs: the checks refuse or plan, the engine reuses."""

import json
from pathlib import Path
from typing import Literal

import dataframely as dy
import polars as pl
import pytest
from pydantic import BaseModel, Field

import avalanche as ava
from avalanche.iceberg import IcebergNs, IcebergNsConfig, IcebergTable
from avalanche.reruns import (
    NotReusableStepOutput,
    PlannedRerun,
    RerunProblemReason,
    RerunRefusedError,
    SavedOutputIndex,
    SavedOutputUnavailableError,
    SavedStepOutput,
    plan_rerun,
)
from avalanche.workspace import Workspace
from runtime.operator.hooks import RunHooks


class Report(BaseModel):
    total: int


class FakeOutputs:
    """A runtime's saved outputs: JSON documents by slug, plus file copies by sha256."""

    def __init__(
        self,
        saved: dict[str, object],
        *,
        not_reusable: dict[str, str] | None = None,
        unavailable: dict[str, str] | None = None,
        file_copies: dict[str, Path] | None = None,
        run_active: bool = False,
        output_run_ids: dict[str, str] | None = None,
    ) -> None:
        self.documents = {slug: json.dumps(value) for slug, value in saved.items()}
        self.unavailable = unavailable or {}
        self.file_copies = file_copies or {}
        self.loaded: list[str] = []
        run_ids = output_run_ids or {}
        self.index = SavedOutputIndex(
            run_id="source",
            run_active=run_active,
            outputs={
                **{
                    slug: SavedStepOutput(slug=slug, run_id=run_ids.get(slug, "source"))
                    for slug in saved
                },
                **{
                    slug: NotReusableStepOutput(slug=slug, run_id="source", reason=reason)
                    for slug, reason in (not_reusable or {}).items()
                },
            },
        )

    def load_output_json(self, output: SavedStepOutput) -> str:
        if output.slug in self.unavailable:
            raise SavedOutputUnavailableError(self.unavailable[output.slug])
        self.loaded.append(output.slug)
        return self.documents[output.slug]

    def bind_file(self, reference: ava.File) -> Path:
        return self.file_copies[reference.sha256]

    def bind_workspace(self, reference: Workspace) -> Path:
        raise AssertionError("no workspace outputs in these flows")


def rerun(*start: str, mode: str = "autorun") -> ava.Rerun:
    return ava.Rerun(run_id="source", start=start, mode=mode)


def refusal_of(workflow, request, outputs):
    with pytest.raises(RerunRefusedError) as refused:
        plan_rerun(workflow, request, outputs.index, outputs)
    return refused.value.refusal


def reasons_of(refusal) -> list[tuple[str | None, RerunProblemReason]]:
    return [(problem.step, problem.reason) for problem in refusal.problems]


def chain_flow():
    """a >> b >> c >> d, passing values, recording what runs."""
    ran: list[str] = []

    @ava.step
    def a() -> int:
        ran.append("a")
        return 1

    @ava.step
    def b(value: int) -> Report:
        ran.append("b")
        return Report(total=value)

    @ava.step
    def c(report: Report) -> int:
        ran.append("c")
        assert isinstance(report, Report)
        return report.total + 1

    @ava.step
    def d(value: int) -> int:
        ran.append("d")
        return value + 1

    @ava.workflow
    def flow():
        return a() >> b() >> c() >> d()

    return flow(), ran


CLEAN = {"a": 1, "b": {"total": 7}, "c": 8, "d": 9}


def test_clean_rerun_plans_runs_and_reuses_with_the_output_read_only_where_needed():
    workflow, _ = chain_flow()
    plan = plan_rerun(workflow, rerun("c"), FakeOutputs(CLEAN).index, FakeOutputs(CLEAN))
    assert plan.run == ("c", "d")
    assert [(step.slug, step.source_run_id, step.output_read) for step in plan.reused] == [
        ("a", "source", False),
        ("b", "source", True),
    ]


def test_lazy_runs_only_the_start_steps_while_autorun_cascades():
    workflow, _ = chain_flow()
    outputs = FakeOutputs(CLEAN)
    assert plan_rerun(workflow, rerun("b", mode="lazy"), outputs.index, outputs).run == ("b",)
    assert plan_rerun(workflow, rerun("b"), outputs.index, outputs).run == ("b", "c", "d")


def test_returned_step_outside_the_run_set_is_needed_wherever_it_sits():
    workflow, ran = chain_flow()
    saved = FakeOutputs({"a": 1, "b": {"total": 7}, "d": 40})
    plan = plan_rerun(workflow, rerun("c", mode="lazy"), saved.index, saved)
    assert plan.run == ("c",)
    assert workflow.run(planned_rerun=PlannedRerun(plan, saved)).result(timeout=5) == 40
    assert ran == ["c"]

    missing = FakeOutputs({"a": 1, "b": {"total": 7}})
    refusal = refusal_of(workflow, rerun("c", mode="lazy"), missing)
    assert reasons_of(refusal) == [("d", RerunProblemReason.MISSING_FROM_SOURCE)]
    assert refusal.earliest_start == ("c", "d")


def test_returned_step_on_another_branch_comes_back_or_refuses_the_rerun():
    @ava.step
    def p() -> int:
        return 1

    @ava.step
    def q(value: int) -> int:
        return value + 1

    @ava.step
    def r() -> int:
        raise AssertionError("the other branch must not run")

    @ava.step
    def s(value: int) -> int:
        raise AssertionError("the other branch must not run")

    @ava.workflow
    def flow():
        return q(p()), s(r())

    workflow = flow()
    saved = FakeOutputs({"p": 1, "s": 20})
    plan = plan_rerun(workflow, rerun("q"), saved.index, saved)
    assert plan.run == ("q",)
    assert {step.slug: step.output_read for step in plan.reused} == {"p": True, "s": True}
    assert workflow.run(planned_rerun=PlannedRerun(plan, saved)).result(timeout=5) == (2, 20)

    not_saved = FakeOutputs({"p": 1, "r": 3}, not_reusable={"s": "a data frame"})
    refusal = refusal_of(workflow, rerun("q"), not_saved)
    assert reasons_of(refusal) == [("s", RerunProblemReason.NOT_REUSABLE)]
    assert refusal.earliest_start == ("q", "s")


def test_start_at_the_first_step_reuses_nothing_and_needs_no_outputs():
    workflow, ran = chain_flow()
    outputs = FakeOutputs({})
    plan = plan_rerun(workflow, rerun("a"), outputs.index, outputs)
    assert plan.reused == ()
    assert plan.run == ("a", "b", "c", "d")
    assert outputs.loaded == []
    assert ran == []


def test_renamed_needed_step_is_refused_with_the_step_to_rerun_from():
    workflow, _ = chain_flow()
    outputs = FakeOutputs({"a": 1, "b_old_name": {"total": 7}})
    refusal = refusal_of(workflow, rerun("c"), outputs)
    assert reasons_of(refusal) == [("b", RerunProblemReason.MISSING_FROM_SOURCE)]
    assert refusal.earliest_start == ("b",)


def test_renamed_step_nothing_reads_does_not_block():
    workflow, _ = chain_flow()
    outputs = FakeOutputs({"a_old_name": 1, "b": {"total": 7}})
    plan = plan_rerun(workflow, rerun("c"), outputs.index, outputs)
    assert [step.slug for step in plan.reused] == ["b"]


def test_not_reusable_step_blocks_only_when_read():
    workflow, _ = chain_flow()
    needed = FakeOutputs({"a": 1}, not_reusable={"b": "a data frame"})
    refusal = refusal_of(workflow, rerun("c"), needed)
    assert reasons_of(refusal) == [("b", RerunProblemReason.NOT_REUSABLE)]
    assert refusal.problems[0].detail == "a data frame"
    assert refusal.earliest_start == ("b",)

    unread = FakeOutputs({"b": {"total": 7}}, not_reusable={"a": "a data frame"})
    plan = plan_rerun(workflow, rerun("c"), unread.index, unread)
    assert [(step.slug, step.output_read) for step in plan.reused] == [
        ("a", False),
        ("b", True),
    ]


def test_earliest_start_walks_past_every_step_that_blocks_the_next():
    workflow, _ = chain_flow()
    outputs = FakeOutputs({}, not_reusable={"a": "frame", "b": "frame", "c": "frame"})
    refusal = refusal_of(workflow, rerun("d"), outputs)
    assert reasons_of(refusal) == [("c", RerunProblemReason.NOT_REUSABLE)]
    assert refusal.earliest_start == ("a",)


def test_output_fit_follows_the_declared_type_in_the_code_that_will_run():
    class Gained(BaseModel):
        total: int
        required_now: str

    class GainedOptional(BaseModel):
        total: int
        note: str | None = None

    def flow_returning(model):
        @ava.step
        def b() -> model:
            raise AssertionError("skipped")

        @ava.step
        def c(value: model) -> None:
            raise AssertionError("not run by the checks")

        @ava.workflow
        def flow():
            b() >> c()

        return flow()

    outputs = FakeOutputs({"b": {"total": 7}})
    refusal = refusal_of(flow_returning(Gained), rerun("c"), outputs)
    assert reasons_of(refusal) == [("b", RerunProblemReason.OUTPUT_DOES_NOT_FIT)]
    assert refusal.earliest_start == ("b",)
    assert plan_rerun(flow_returning(GainedOptional), rerun("c"), outputs.index, outputs)


def test_output_saved_by_field_name_fits_a_model_using_aliases():
    class Aliased(BaseModel):
        total_count: int = Field(alias="totalCount")

    @ava.step
    def b() -> Aliased:
        raise AssertionError("skipped")

    @ava.step
    def c(value: Aliased) -> int:
        return value.total_count

    @ava.workflow
    def flow():
        return b() >> c()

    outputs = FakeOutputs({"b": {"total_count": 3}})
    assert plan_rerun(flow(), rerun("c"), outputs.index, outputs).run == ("c",)


def test_return_type_json_cannot_rebuild_is_refused():
    @ava.step
    def b() -> pl.DataFrame:
        raise AssertionError("skipped")

    @ava.step
    def c(value: pl.DataFrame) -> None:
        raise AssertionError("not run by the checks")

    @ava.workflow
    def flow():
        b() >> c()

    outputs = FakeOutputs({"b": {"total": 7}})
    refusal = refusal_of(flow(), rerun("c"), outputs)
    assert reasons_of(refusal) == [("b", RerunProblemReason.OUTPUT_DOES_NOT_FIT)]


def test_unavailable_file_copy_is_refused():
    workflow, _ = chain_flow()
    outputs = FakeOutputs({"a": 1, "b": {"total": 7}}, unavailable={"b": "sha256 mismatch"})
    refusal = refusal_of(workflow, rerun("c"), outputs)
    assert reasons_of(refusal) == [("b", RerunProblemReason.OUTPUT_UNAVAILABLE)]


def test_source_run_still_going_and_every_other_problem_are_listed_together():
    workflow, _ = chain_flow()
    outputs = FakeOutputs({"a": 1}, run_active=True)
    refusal = refusal_of(workflow, rerun("c", "ghost"), outputs)
    assert reasons_of(refusal) == [
        (None, RerunProblemReason.SOURCE_RUN_ACTIVE),
        ("ghost", RerunProblemReason.UNKNOWN_START_STEP),
        ("b", RerunProblemReason.MISSING_FROM_SOURCE),
    ]
    assert refusal.earliest_start == ()


def test_only_unknown_start_steps_has_no_step_to_suggest():
    workflow, _ = chain_flow()
    outputs = FakeOutputs(CLEAN)
    refusal = refusal_of(workflow, rerun("ghost"), outputs)
    assert reasons_of(refusal) == [("ghost", RerunProblemReason.UNKNOWN_START_STEP)]
    assert refusal.earliest_start == ()


def test_earliest_start_keeps_lazy_start_steps_since_nothing_cascades():
    workflow, _ = chain_flow()
    outputs = FakeOutputs({"a": 1, "c": 8})
    refusal = refusal_of(workflow, rerun("c", mode="lazy"), outputs)
    assert refusal.earliest_start == ("b", "c", "d")


def test_workflow_return_of_a_skipped_upstream_step_is_needed():
    @ava.step
    def a() -> int:
        return 1

    @ava.step
    def b() -> int:
        return 2

    @ava.workflow
    def flow():
        first = a()
        return first, first >> b()

    outputs = FakeOutputs({"b": 2}, not_reusable={"a": "frame"})
    refusal = refusal_of(flow(), rerun("b"), outputs)
    assert reasons_of(refusal) == [("a", RerunProblemReason.NOT_REUSABLE)]


def test_control_only_edge_does_not_read_the_output():
    @ava.step
    def a() -> None:
        raise AssertionError("skipped")

    @ava.step
    def b(ctx: ava.RunContext) -> str:
        return ctx.rerun.run_id

    @ava.workflow
    def flow():
        return a() >> b()

    workflow = flow()
    outputs = FakeOutputs({}, not_reusable={"a": "unsaved"})
    plan = plan_rerun(workflow, rerun("b"), outputs.index, outputs)
    assert workflow.run(planned_rerun=PlannedRerun(plan, outputs)).result(timeout=5) == "source"
    assert outputs.loaded == []


def test_engine_gives_the_next_step_the_output_rebuilt_as_declared_and_marks_it_reused():
    workflow, ran = chain_flow()
    outputs = FakeOutputs(
        {"a": 1, "b": {"total": 7}}, output_run_ids={"a": "origin", "b": "origin"}
    )
    plan = plan_rerun(workflow, rerun("c"), outputs.index, outputs)
    outputs.loaded.clear()
    started: list[str] = []
    succeeded: list[str] = []
    reused: list[tuple[str, str]] = []
    result = workflow.run(
        hooks=RunHooks(
            on_node_start=started.append,
            on_node_success=succeeded.append,
            on_node_reused=lambda node, source: reused.append((node, source)),
        ),
        planned_rerun=PlannedRerun(plan, outputs),
    ).result(timeout=5)
    assert result == 9
    assert ran == ["c", "d"]
    assert outputs.loaded == ["b"]
    assert reused == [("a_1", "origin"), ("b_1", "origin")]
    assert started == ["c_1", "d_1"]
    assert succeeded == ["c_1", "d_1"]


def test_engine_rejects_both_rerun_forms_together():
    workflow, _ = chain_flow()
    outputs = FakeOutputs(CLEAN)
    plan = plan_rerun(workflow, rerun("c"), outputs.index, outputs)
    with pytest.raises(ValueError, match="not both"):
        workflow.run(rerun=rerun("c"), planned_rerun=PlannedRerun(plan, outputs))


def test_engine_rebuilds_multi_return_and_tuple_indexes():
    @ava.step(num_returns=2)
    def pair() -> tuple[int, Report]:
        raise AssertionError("skipped")

    @ava.step
    def consume(count: int, report: Report) -> int:
        assert isinstance(report, Report)
        return count + report.total

    @ava.workflow
    def flow():
        return pair() >> consume()

    workflow = flow()
    outputs = FakeOutputs({"pair": [2, {"total": 3}]})
    plan = plan_rerun(workflow, rerun("consume"), outputs.index, outputs)
    assert workflow.run(planned_rerun=PlannedRerun(plan, outputs)).result(timeout=5) == 5


def test_multi_return_without_annotation_needs_the_declared_number_of_values():
    @ava.step(num_returns=2)
    def pair():
        raise AssertionError("skipped")

    @ava.step
    def consume(left, right):
        raise AssertionError("not run by the checks")

    @ava.workflow
    def flow():
        pair() >> consume()

    outputs = FakeOutputs({"pair": [1, 2, 3]})
    refusal = refusal_of(flow(), rerun("consume"), outputs)
    assert reasons_of(refusal) == [("pair", RerunProblemReason.OUTPUT_DOES_NOT_FIT)]


def test_engine_binds_a_reused_file_to_the_saved_copy(tmp_path):
    source = tmp_path / "report.txt"
    source.write_text("saved")
    saved_file = ava.File.from_path(source)
    document = saved_file.model_dump(mode="json")
    copy = tmp_path / "copy.txt"
    copy.write_text("saved")

    @ava.step
    def produce() -> ava.File:
        raise AssertionError("skipped")

    @ava.step
    def read(file: ava.File) -> str:
        assert file.path == copy
        return file.read_bytes().decode()

    @ava.workflow
    def flow():
        return produce() >> read()

    workflow = flow()
    outputs = FakeOutputs({"produce": document}, file_copies={saved_file.sha256: copy})
    plan = plan_rerun(workflow, rerun("read"), outputs.index, outputs)
    assert workflow.run(planned_rerun=PlannedRerun(plan, outputs)).result(timeout=5) == "saved"


def test_reused_map_group_feeds_downstream_and_a_running_map_reads_a_reused_list():
    ran: list[str] = []

    @ava.step
    def items() -> list[int]:
        raise AssertionError("skipped")

    @ava.step
    def double(value: int) -> int:
        ran.append(f"double:{value}")
        return value * 2

    @ava.step
    def add(value: int) -> int:
        ran.append(f"add:{value}")
        return value + 1

    @ava.step
    def total(values: list[int]) -> int:
        ran.append("total")
        return sum(values)

    @ava.workflow
    def flow():
        doubled = double.map(items())
        added = add.map(doubled)
        return added >> total()

    workflow = flow()
    outputs = FakeOutputs({"items": [1, 2], "double": [2, 4], "add": [3, 5]})

    plan = plan_rerun(workflow, rerun("total"), outputs.index, outputs)
    assert workflow.run(planned_rerun=PlannedRerun(plan, outputs)).result(timeout=5) == 8
    assert ran == ["total"]

    ran.clear()
    plan = plan_rerun(workflow, rerun("add"), outputs.index, outputs)
    assert workflow.run(planned_rerun=PlannedRerun(plan, outputs)).result(timeout=5) == 8
    assert sorted(ran) == ["add:2", "add:4", "total"]

    ran.clear()
    plan = plan_rerun(workflow, rerun("double"), outputs.index, outputs)
    assert workflow.run(planned_rerun=PlannedRerun(plan, outputs)).result(timeout=5) == 8
    assert sorted(ran) == ["add:2", "add:4", "double:1", "double:2", "total"]


def test_reused_decision_output_is_rebuilt_as_its_model():
    class Approval(ava.Event):
        kind: Literal["approve"] = "approve"

    @ava.step
    def prepare() -> int:
        raise AssertionError("skipped")

    @ava.step
    def act(decision: Approval) -> str:
        assert isinstance(decision, Approval)
        return decision.kind

    @ava.workflow
    def flow():
        document = prepare()
        gate = ava.decision(
            Approval, prompt="Approve", show=document, timeout=None, slug="gate"
        )
        return document >> gate >> act()

    workflow = flow()
    outputs = FakeOutputs({"prepare": 1, "gate": {"kind": "approve"}})
    plan = plan_rerun(workflow, rerun("act"), outputs.index, outputs)
    assert (
        workflow.run(planned_rerun=PlannedRerun(plan, outputs)).result(timeout=5) == "approve"
    )

    waiting_again = plan_rerun(workflow, rerun("gate"), outputs.index, outputs)
    assert [(step.slug, step.output_read) for step in waiting_again.reused] == [
        ("prepare", True)
    ]


class RowSchema(dy.Schema):
    id = dy.Int64(nullable=False)
    value = dy.String(nullable=False)


@pytest.fixture
def stream_ns(tmp_path):
    class StreamNamespace(IcebergNs):
        ns_config = IcebergNsConfig(name="rerun-plan", base_location=str(tmp_path))
        rows = IcebergTable(schema=RowSchema)

    ns = StreamNamespace(
        catalog="rerun-plan-catalog",
        load_catalog_props={"type": "sql", "uri": f"sqlite:///{tmp_path / 'catalog.db'}"},
    )
    ns.push()
    return ns


def test_stream_reader_keeps_row_replay_beside_a_reused_value(stream_ns):
    """The Stream parent needs no saved output; the value parent gets its saved one."""
    calls: list[str] = []
    rows = pl.DataFrame({"id": [1], "value": ["alpha"]})

    @ava.source
    def load(*, table=stream_ns.rows):
        calls.append("load")
        return table.append(rows)

    @ava.source
    def label() -> str:
        calls.append("label")
        return "tag"

    @ava.step
    def consume(name: str, df=ava.Stream(stream_ns.rows)) -> str:
        calls.append("consume")
        return f"{name}:{df['value'].to_list()}"

    @ava.workflow
    def flow():
        return (label() & load()) >> consume()

    workflow = flow()
    first = workflow.run(run_id="source").result(timeout=30)
    assert first == "tag:['alpha']"

    calls.clear()
    outputs = FakeOutputs({"label": "tag"}, not_reusable={"load": "an append result"})
    plan = plan_rerun(workflow, rerun("consume"), outputs.index, outputs)
    assert {step.slug: step.output_read for step in plan.reused} == {
        "load": False,
        "label": True,
    }
    result = workflow.run(planned_rerun=PlannedRerun(plan, outputs)).result(timeout=30)
    assert result == "tag:['alpha']"
    assert calls == ["consume"]


def test_rerun_of_a_rerun_replays_rows_from_the_run_that_holds_them(stream_ns):
    """The middle run read no Stream, so no ancestry edge leads from it to the rows."""
    seen: list[list[str]] = []
    rows = pl.DataFrame({"id": [1], "value": ["alpha"]})

    @ava.source
    def load(*, table=stream_ns.rows):
        return table.append(rows)

    @ava.step
    def read_rows(df=ava.Stream(stream_ns.rows)) -> int:
        seen.append(df["value"].to_list())
        return df.height

    @ava.step
    def report(count: int) -> int:
        return count + 1

    @ava.workflow
    def flow():
        return load() >> read_rows() >> report()

    workflow = flow()
    assert workflow.run(run_id="first").result(timeout=30) == 2

    def index_of(run_id: str, *outputs_by_slug: tuple[str, str]) -> SavedOutputIndex:
        return SavedOutputIndex(
            run_id=run_id,
            run_active=False,
            outputs={
                slug: (
                    NotReusableStepOutput(slug=slug, run_id=holder, reason="an append result")
                    if slug == "load"
                    else SavedStepOutput(slug=slug, run_id=holder)
                )
                for slug, holder in outputs_by_slug
            },
        )

    class Documents(FakeOutputs):
        def load_output_json(self, output: SavedStepOutput) -> str:
            return {"read_rows": "1", "report": "2"}[output.slug]

    documents = Documents({})
    first = index_of("first", ("load", "first"), ("read_rows", "first"), ("report", "first"))
    middle_plan = plan_rerun(
        workflow, ava.Rerun(run_id="first", start="report"), first, documents
    )
    assert (
        workflow.run(
            run_id="middle", planned_rerun=PlannedRerun(middle_plan, documents)
        ).result(timeout=30)
        == 2
    )
    seen.clear()

    middle = index_of("middle", ("load", "first"), ("read_rows", "first"), ("report", "middle"))
    last_plan = plan_rerun(
        workflow, ava.Rerun(run_id="middle", start="read_rows"), middle, documents
    )
    assert (
        workflow.run(run_id="last", planned_rerun=PlannedRerun(last_plan, documents)).result(
            timeout=30
        )
        == 2
    )
    assert seen == [["alpha"]]


def test_stream_parent_the_source_never_ran_is_refused_instead_of_replaying_nothing(stream_ns):
    @ava.source
    def load():
        raise AssertionError("skipped")

    @ava.step
    def consume(df=ava.Stream(stream_ns.rows)):
        raise AssertionError("not run by the checks")

    @ava.workflow
    def flow():
        load() >> consume()

    outputs = FakeOutputs({})
    refusal = refusal_of(flow(), rerun("consume"), outputs)
    assert reasons_of(refusal) == [("load", RerunProblemReason.MISSING_FROM_SOURCE)]
    assert refusal.earliest_start == ("load",)


def test_stream_slot_before_a_python_slot_stays_refused_as_ambiguous(stream_ns):
    @ava.source
    def load():
        raise AssertionError("skipped")

    @ava.source
    def label() -> str:
        raise AssertionError("skipped")

    @ava.step
    def consume(df=ava.Stream(stream_ns.rows), name: str = ""):
        raise AssertionError("not run by the checks")

    @ava.workflow
    def flow():
        (load() & label()) >> consume()

    outputs = FakeOutputs({"label": "tag", "load": None})
    refusal = refusal_of(flow(), rerun("consume"), outputs)
    assert ("load", RerunProblemReason.STREAM_NOT_REPLAYABLE) in reasons_of(refusal)
    assert "load" in refusal.earliest_start


def test_indexed_stream_read_of_a_skipped_step_is_refused(stream_ns):
    @ava.source(num_returns=2)
    def split():
        raise AssertionError("skipped")

    @ava.step
    def consume(df=ava.Stream(stream_ns.rows)):
        raise AssertionError("not run by the checks")

    @ava.workflow
    def flow():
        split()[0] >> consume()

    outputs = FakeOutputs({"split": [1, 2]})
    refusal = refusal_of(flow(), rerun("consume"), outputs)
    assert reasons_of(refusal) == [("split", RerunProblemReason.STREAM_NOT_REPLAYABLE)]
    assert refusal.earliest_start == ("split",)
