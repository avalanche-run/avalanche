"""Avalanche: Pythonic workflows with local and distributed execution."""

from importlib import import_module

from pydantic import BaseModel

# DAG primitives
# Execution engines
from runtime.executor import Executor, LocalExecutor, get_default_executor

from .dag import (
    MapFuture,
    Pipeline,
    Workflow,
    dest,
    pipeline,
    source,
    step,
    transform,
    workflow,
)
from .execution_services import (
    EXECUTION_SERVICES_V1,
    ExecutionServiceReceipt,
    ExecutionServices,
    ExecutionServicesSpec,
    ExecutionTaskSpec,
)
from .input_ref import INPUT as input  # noqa: N811
from .integrations import MCP, ApiKey, Catalog, Connector, OAuth2, tool
from .machine import Machine, machine, state
from .machine_host import MachineEventReceipt, MachineHost, MachineInstance
from .mapping import DEFAULT_MAX_ITEMS, HARD_CAP, MapItemFailure, MapItemsFailed
from .model_frame import Json
from .policies import Event, NonRetryableError, OutcomeUnknownError, event
from .protocol import PROTOCOL_VERSION, ProtocolVersion
from .run_handle import RunHandle
from .run_steps import RunStart, call, start_run

# Runtime primitives
from .runtime import (  # noqa: F401
    BaseContext,
    BaseInput,
    File,
    Logger,
    Rerun,
    RunContext,
    Workspace,
)
from .skips import StepSkipped, skip
from .waits import decision, sleep, wait_for_event
from .webhook import Webhook

__version__ = "0.20.38"

from .references import (
    LocalReferenceBinder,
    ReferenceBinder,
    file_reference_context,
    rebind_arguments,
)

# Submodules and names resolved lazily on first attribute access (see __getattr__).
_LAZY_SUBMODULES = frozenset(
    {"agent", "catalog", "classifier", "classifiers", "runtimes"}
)
_LAZY_EXPORTS: dict[str, str] = {
    # Agent
    "Agent": "agent",
    "InputField": "agent",
    "OutputField": "agent",
    "Signature": "agent",
    "agent_step": "agent",
    # Integrations: the HTTP stack loads with the first client, not with avalanche.
    "Client": "integrations",
    "ConnectionAmbiguousError": "integrations",
    "ConnectionNotAssignedError": "integrations",
    "ConnectionNotFoundError": "integrations",
    "ConnectionNotResolvedError": "integrations",
    "ConnectionPerUserTriggeredError": "integrations",
    "ConnectionUserNotConnectedError": "integrations",
    # Classifier
    "Classifier": "classifier",
    "ClassificationResult": "classifier",
    "ClassifierStepError": "classifier",
    "ClassifierStepExecutionError": "classifier",
    "classifier_step": "classifier",
    # Evaluations
    "EvalContext": "evaluations",
    "Metric": "evaluations",
    "Evaluations": "evaluations",
}


def __getattr__(name: str):
    if name in {"iceberg", "lance", "Stream", "ModelStream", "Cursor", "AppendResult"}:
        from ._removed import TablesAndStreamsRemovedError

        raise TablesAndStreamsRemovedError()
    if name in _LAZY_SUBMODULES:
        value = import_module(f"{__name__}.{name}")
    elif name in _LAZY_EXPORTS:
        value = getattr(import_module(f"{__name__}.{_LAZY_EXPORTS[name]}"), name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | _LAZY_SUBMODULES)


__all__ = [
    "MapFuture",
    "ConnectionAmbiguousError",
    "ConnectionNotAssignedError",
    "ConnectionNotFoundError",
    "ConnectionNotResolvedError",
    "ConnectionPerUserTriggeredError",
    "ConnectionUserNotConnectedError",
    "MapItemFailure",
    "MapItemsFailed",
    "DEFAULT_MAX_ITEMS",
    "HARD_CAP",
    "PROTOCOL_VERSION",
    "ProtocolVersion",
    "ReferenceBinder",
    "LocalReferenceBinder",
    "rebind_arguments",
    "file_reference_context",
    # Decorators
    "source",
    "NonRetryableError",
    "OutcomeUnknownError",
    "skip",
    "StepSkipped",
    "Event",
    "event",
    "machine",
    "state",
    "Machine",
    "MachineHost",
    "RunStart",
    "start_run",
    "MachineEventReceipt",
    "MachineInstance",
    "sleep",
    "wait_for_event",
    "decision",
    "step",
    "transform",
    "dest",
    "workflow",
    "pipeline",
    "classifier_step",
    "input",
    "call",
    # Integrations
    "ApiKey",
    "Client",
    "Connector",
    "Catalog",
    "MCP",
    "OAuth2",
    "tool",
    # Workflow
    "Workflow",
    "Pipeline",
    "Webhook",
    # Executors
    "Executor",
    "LocalExecutor",
    "get_default_executor",
    "EXECUTION_SERVICES_V1",
    "ExecutionServiceReceipt",
    "ExecutionServices",
    "ExecutionServicesSpec",
    "ExecutionTaskSpec",
    # Runtime
    "BaseContext",
    "BaseInput",
    "BaseModel",
    "File",
    "Workspace",
    "Rerun",
    "RunContext",
    "RunHandle",
    "Logger",
    "Classifier",
    "ClassificationResult",
    "ClassifierStepError",
    "ClassifierStepExecutionError",
    "EvalContext",
    "Metric",
    "Evaluations",
    "Json",
]
