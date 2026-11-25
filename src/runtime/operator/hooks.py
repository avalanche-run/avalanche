"""RunHooks — callbacks fired during Workflow.run() execution."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import JsonValue

from avalanche.mapping import NodesExpanded

from .machine_steps import HostStepOutput, MachineStepRequest


@dataclass
class RunHooks:
    """Optional callbacks injected into Workflow.run() for execution monitoring.

    When hooks are active, Workflow.run() reports on_node_success only after
    actual completion, not just submission. Independent local branches can
    still overlap in the thread pool.
    """

    machine_step: Callable[[MachineStepRequest], Future[HostStepOutput]] | None = None
    machine_payload: JsonValue = None
    resume_results: dict[str, str] | None = None
    on_nodes_expanded: Callable[[NodesExpanded], None] | None = None
    on_wait_armed: Callable[[str, str], None] | None = None
    on_wait_resolved: Callable[[str], None] | None = None
    on_node_start: Callable[[str], None] | None = None
    on_node_success: Callable[[str], None] | None = None
    on_node_failure: Callable[[str, Exception], None] | None = None
    on_node_retry: Callable[[str, int, int, Exception], None] | None = None
    """An attempt failed and the step will run again. Called as
    on_node_retry(node_id, attempt, max_attempts, exc), where attempt is the failed
    attempt, counted from 1, and max_attempts the most the step's policy allows. The
    next attempt gets its own on_node_start."""
    on_node_skipped: Callable[[str, str], None] | None = None
    """The step ended as skipped: it called ava.skip, a step it depends on was
    skipped, or it is a run start switched off with when=False (its None still feeds
    dependents). Called as on_node_skipped(node_id, reason). A step skipped because of
    another one gets no on_node_start."""
    on_node_reused: Callable[[str, str], None] | None = None
    """A rerun skipped this step and reuses its saved output. Called before any step
    starts as on_node_reused(node_id, source_run_id), where source_run_id is the run
    holding the output. A reused step gets no on_node_start or on_node_success."""
    cancel_requested: Callable[[], bool] | None = None
    wrap_fn: Callable[[str, Callable], Callable] | None = None
    """Optional function wrapper applied before executor.submit().
    Called as wrap_fn(node_id, fn) -> wrapped_fn.
    Used to capture node logs and evidence."""
    unwrap_result: Callable[[str, Any], Any] | None = None
    """Optional result unwrapper called after executor.get().
    Called as unwrap_result(node_id, raw_result) -> actual_result.
    Used to extract side-channel data (logs) from wrapped results."""
