"""Reruns that reuse a finished run's step outputs: the checks and the plan they return.

A runtime fills the saved-output index, calls ``plan_rerun`` before anything runs,
and starts the workflow with the resulting ``PlannedRerun``.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)
from pydantic.errors import PydanticSchemaGenerationError, PydanticUserError

from .dag import (
    MapFuture,
    NodeFuture,
    ParallelTasks,
    Workflow,
    _reverse_graph,
    _skipped_input_reads,
)
from .references import ReferenceBinder, bind_references
from .runtime import Rerun
from .waits import WaitNode


class _RerunModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SavedStepOutput(_RerunModel):
    """A step's successful return value, saved and loadable through a reader."""

    status: Literal["saved"] = "saved"
    slug: str
    run_id: str
    """The run whose storage holds the output; a reused step keeps the original run."""


class NotReusableStepOutput(_RerunModel):
    """A step that succeeded with a return value that could not be saved as JSON."""

    status: Literal["not_reusable"] = "not_reusable"
    slug: str
    run_id: str
    reason: str


type SavedOutputEntry = Annotated[
    SavedStepOutput | NotReusableStepOutput, Field(discriminator="status")
]


class SavedOutputIndex(_RerunModel):
    """What one run saved, by step slug. A slug that is absent was not saved at all."""

    run_id: str
    run_active: bool
    """The run is still going, so it cannot be rerun yet."""
    outputs: Mapping[str, SavedOutputEntry]


class SavedOutputUnavailableError(Exception):
    """A saved output cannot be loaded, for example because a file copy is missing."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SavedOutputReader(Protocol):
    """Loads saved outputs for the rerun checks."""

    def load_output_json(self, output: SavedStepOutput) -> str:
        """Return the saved JSON document, verifying any file copies it refers to.

        Multi-return steps and map groups are one JSON array. Raises
        ``SavedOutputUnavailableError`` when the output or a file copy cannot be used.
        """
        ...


class SavedOutputProvider(SavedOutputReader, ReferenceBinder, Protocol):
    """Loads saved outputs for a running rerun and binds the files they refer to.

    Binding covers only outputs this instance already loaded, so load first.
    """


class RerunProblemReason(StrEnum):
    UNKNOWN_START_STEP = "unknown_start_step"
    MISSING_FROM_SOURCE = "missing_from_source"
    NOT_REUSABLE = "not_reusable"
    OUTPUT_UNAVAILABLE = "output_unavailable"
    OUTPUT_DOES_NOT_FIT = "output_does_not_fit"
    SOURCE_RUN_ACTIVE = "source_run_active"


class RerunProblem(_RerunModel):
    """One reason a rerun cannot start. ``step`` is None for the whole source run."""

    step: str | None
    reason: RerunProblemReason
    detail: str


class RerunRefusal(_RerunModel):
    problems: tuple[RerunProblem, ...]
    earliest_start: tuple[str, ...]
    """The nearest start steps that would pass the checks. Empty when no start can pass
    yet (the source run is still going) or the request named no step in this code."""


class RerunRefusedError(Exception):
    """The rerun was refused before anything ran; ``refusal`` lists every problem."""

    def __init__(self, refusal: RerunRefusal) -> None:
        problems = "; ".join(
            f"{problem.step or 'source run'}: {problem.detail}" for problem in refusal.problems
        )
        super().__init__(f"Rerun refused: {problems}")
        self.refusal = refusal


class ReusedStep(_RerunModel):
    slug: str
    source_run_id: str
    """The run holding this step's output, which the new run's view links to."""
    output_read: bool
    """A running step or the workflow's return reads the value, so it gets loaded."""


class RerunPlan(_RerunModel):
    """Which steps run and which are reused. It holds slugs, so it can cross processes."""

    source_run_id: str
    start: tuple[str, ...]
    mode: Literal["autorun", "lazy"]
    run: tuple[str, ...]
    reused: tuple[ReusedStep, ...]

    def rerun_spec(self) -> Rerun:
        return Rerun(
            run_id=self.source_run_id,
            start=self.start,
            mode=self.mode,
            row_sources={step.slug: step.source_run_id for step in self.reused},
        )


@dataclass(frozen=True)
class PlannedRerun:
    """A checked plan and the provider that supplies its reused outputs."""

    plan: RerunPlan
    provider: SavedOutputProvider


class _OutputDoesNotFitError(Exception):
    pass


def _rebuild_output(future: NodeFuture, output_json: str) -> object:
    """Rebuild a saved JSON document into the type the step declares in this code."""
    node = future.node
    annotation = inspect.signature(node.fn).return_annotation
    if isinstance(node, WaitNode):
        annotation = inspect.Signature.empty if node.model is None else node.model
        if node.spec.map_max_items is not None and node.model is not None:
            annotation = list[node.model]
    if annotation is inspect.Signature.empty:
        annotation = JsonValue
    if isinstance(future, MapFuture):
        annotation = list[annotation]
    try:
        # Saved JSON holds field names, while validation otherwise expects aliases.
        value = TypeAdapter(annotation).validate_json(output_json, by_alias=True, by_name=True)
    except (
        ValidationError,
        PydanticSchemaGenerationError,
        PydanticUserError,
        NameError,
        TypeError,
    ) as exc:
        raise _OutputDoesNotFitError(str(exc)) from exc
    if node.num_returns == 1 or isinstance(future, MapFuture):
        return value
    if not isinstance(value, (list, tuple)) or len(value) != node.num_returns:
        raise _OutputDoesNotFitError(f"expected {node.num_returns} return values")
    return tuple(value)


def load_reused_step(
    future: NodeFuture, step: ReusedStep, provider: SavedOutputProvider
) -> object:
    """Load one reused output as the declared type, with its files bound to their copies."""
    output_json = provider.load_output_json(
        SavedStepOutput(slug=step.slug, run_id=step.source_run_id)
    )
    return bind_references(_rebuild_output(future, output_json), provider)


def _returned_node_ids(returned: object) -> Iterator[str]:
    if isinstance(returned, tuple):
        for item in returned:
            yield from _returned_node_ids(item)
    elif isinstance(returned, ParallelTasks):
        for branch in returned.branches:
            yield from _returned_node_ids(branch)
    elif isinstance(returned, NodeFuture):
        target = returned.chain_end
        if target is returned:
            yield returned.future_id
        else:
            yield from _returned_node_ids(target)


@dataclass(frozen=True)
class _Attempt:
    order: tuple[str, ...]
    reused: tuple[str, ...]
    read: frozenset[str]
    blocked: dict[str, RerunProblem]


class _Checks:
    """One rerun request's checks against one source run."""

    def __init__(
        self,
        workflow: Workflow,
        mode: Literal["autorun", "lazy"],
        index: SavedOutputIndex,
        reader: SavedOutputReader,
    ) -> None:
        self.workflow = workflow
        self.mode = mode
        self.index = index
        self.reader = reader
        self.order = tuple(workflow._topological_sort())
        self.parents = _reverse_graph(workflow.graph)
        self.output_problems: dict[str, RerunProblem | None] = {}

    def _problem(self, node_id: str, reason: RerunProblemReason, detail: str) -> RerunProblem:
        return RerunProblem(
            step=self.workflow.node_slugs[node_id], reason=reason, detail=detail
        )

    def _load_problem(self, node_id: str) -> RerunProblem | None:
        slug = self.workflow.node_slugs[node_id]
        entry = self.index.outputs.get(slug)
        if entry is None:
            return self._problem(
                node_id,
                RerunProblemReason.MISSING_FROM_SOURCE,
                f"run {self.index.run_id} has no saved output for this step",
            )
        if isinstance(entry, NotReusableStepOutput):
            return self._problem(node_id, RerunProblemReason.NOT_REUSABLE, entry.reason)
        try:
            _rebuild_output(self.workflow.nodes[node_id], self.reader.load_output_json(entry))
        except SavedOutputUnavailableError as exc:
            return self._problem(node_id, RerunProblemReason.OUTPUT_UNAVAILABLE, exc.reason)
        except _OutputDoesNotFitError as exc:
            return self._problem(node_id, RerunProblemReason.OUTPUT_DOES_NOT_FIT, str(exc))
        return None

    def _output_problem(self, node_id: str) -> RerunProblem | None:
        if node_id not in self.output_problems:
            self.output_problems[node_id] = self._load_problem(node_id)
        return self.output_problems[node_id]

    def _upstream(self, scheduled: set[str]) -> set[str]:
        upstream: set[str] = set()
        queue = list(scheduled)
        while queue:
            for parent in self.parents.get(queue.pop(), []):
                if parent not in upstream:
                    upstream.add(parent)
                    queue.append(parent)
        return upstream - scheduled

    def attempt(self, start: set[str]) -> _Attempt:
        workflow = self.workflow
        scheduled = workflow._scheduled_from(start, self.mode)
        order = tuple(node_id for node_id in self.order if node_id in scheduled)
        upstream = self._upstream(scheduled)
        read = {
            node_id
            for node_id in _returned_node_ids(workflow.returns)
            if node_id not in scheduled
        }
        for node_id in order:
            node = workflow.nodes[node_id].node
            if isinstance(node, WaitNode) and node.spec.show_node_id in upstream:
                read.add(node.spec.show_node_id)
        blocked: dict[str, RerunProblem] = {}
        for skipped in _skipped_input_reads(workflow.nodes, order, self.parents):
            read.add(skipped.producer)
        for node_id in read:
            problem = self._output_problem(node_id)
            if problem is not None:
                blocked.setdefault(node_id, problem)
        return _Attempt(
            order=order,
            reused=tuple(node_id for node_id in self.order if node_id in upstream | read),
            read=frozenset(read),
            blocked=blocked,
        )

    def earliest_start(self, start: set[str]) -> tuple[str, ...]:
        """Move the start upstream past each blocked step until the checks pass."""
        widened = set(start)
        while blocked := self.attempt(widened).blocked:
            widened |= set(blocked)
        if self.mode == "autorun":
            widened = {
                node_id
                for node_id in widened
                if not any(
                    other != node_id
                    and node_id in self.workflow._scheduled_from({other}, "autorun")
                    for other in widened
                )
            }
        return tuple(
            self.workflow.node_slugs[node_id] for node_id in self.order if node_id in widened
        )

    def reused_step(self, node_id: str, read: frozenset[str]) -> ReusedStep | None:
        slug = self.workflow.node_slugs[node_id]
        entry = self.index.outputs.get(slug)
        if entry is None:
            return None
        return ReusedStep(slug=slug, source_run_id=entry.run_id, output_read=node_id in read)


def plan_rerun(
    workflow: Workflow,
    rerun: Rerun,
    index: SavedOutputIndex,
    reader: SavedOutputReader,
) -> RerunPlan:
    """Decide what a rerun runs and reuses, or refuse it listing every problem.

    A skipped step is needed when a running step or the workflow's return reads its
    return value; a needed step must have a saved output that fits its declared return
    type in this code. Other upstream steps are reused if the source has them and never
    block.
    """
    if index.run_id != rerun.run_id:
        raise ValueError(f"Index of run {index.run_id} cannot check a rerun of {rerun.run_id}")
    checks = _Checks(workflow, rerun.mode, index, reader)
    slug_to_node_id = {slug: node_id for node_id, slug in workflow.node_slugs.items()}
    start = {slug_to_node_id[slug] for slug in rerun.start if slug in slug_to_node_id}
    problems: list[RerunProblem] = []
    if index.run_active:
        problems.append(
            RerunProblem(
                step=None,
                reason=RerunProblemReason.SOURCE_RUN_ACTIVE,
                detail=f"run {index.run_id} is still going",
            )
        )
    problems.extend(
        RerunProblem(
            step=slug,
            reason=RerunProblemReason.UNKNOWN_START_STEP,
            detail="the code that will run has no such step",
        )
        for slug in rerun.start
        if slug not in slug_to_node_id
    )
    attempt = checks.attempt(start)
    problems.extend(
        attempt.blocked[node_id] for node_id in checks.order if node_id in attempt.blocked
    )
    if problems:
        earliest = () if index.run_active or not start else checks.earliest_start(start)
        raise RerunRefusedError(RerunRefusal(problems=tuple(problems), earliest_start=earliest))
    return RerunPlan(
        source_run_id=rerun.run_id,
        start=rerun.start,
        mode=rerun.mode,
        run=tuple(workflow.node_slugs[node_id] for node_id in attempt.order),
        reused=tuple(
            step
            for node_id in attempt.reused
            if (step := checks.reused_step(node_id, attempt.read)) is not None
        ),
    )
