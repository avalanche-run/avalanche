"""
Data types for Avalanche execution.

Defines core types used throughout the framework:
- LineagedResult: Internal envelope carrying a result's producer lineage
- ParamContext and ParameterProvider: dependency injection for step parameters
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, ClassVar


@dataclass
class LineagedResult:
    """A node return value carrying the lineage vector of its producers.

    Reruns must record, on each produced row, which producer versions were
    consumed (`_ava_lineage_vector`). A node that consumes an upstream result
    through an ordinary Python argument would otherwise lose it once the value
    crosses the executor boundary.

    The framework wraps node results in `LineagedResult` so downstream nodes can
    merge the producer lineage regardless of executor. It is an internal
    transport type: it is always unwrapped before reaching user functions, hooks
    tests, or the final workflow return.
    """

    value: Any
    lineage_vector: dict[str, str]


def unwrap_lineaged(value: Any) -> Any:
    """Return the underlying value, stripping a LineagedResult envelope."""
    return value.value if isinstance(value, LineagedResult) else value


def lineage_of(value: Any) -> dict[str, str]:
    """Return the lineage vector carried by a value, or empty if none."""
    return dict(value.lineage_vector) if isinstance(value, LineagedResult) else {}


class ParamContext:
    """
    Parameter-level context passed to providers during resolution.

    Each parameter gets its own context instance with information about:
    - The parameter being resolved (name, position)
    - The node being executed (name, function)
    - Parent results and nodes (for zero-copy matching)
    - Runtime environment (executor, worker, etc.)

    Includes helper methods for common operations like normalizing parent results
    and matching parameters to parent outputs by position (zero-copy matching).
    """

    def __init__(
        self,
        parent_results: list[Any],
        param_position: int,
        node_name: str,
        node_slug: str | None = None,
        upstream_node_slugs: list[str] | None = None,
        run_id: str | None = None,
        rerun: Any = None,
        preserve_missing_results: bool = False,
        executor_type: str = "local",
        executor: Any = None,
    ):
        """
        Initialize execution context for parameter resolution.

        Args:
            parent_results: Raw results from parent nodes (may be tuples/lists for multi-return)
            param_position: Position of the parameter in function signature (0-indexed)
            node_name: Name of the current node function
            run_id: Optional unique ID for this workflow execution run
            executor_type: Executor type ("local")
            executor: The active executor
        """
        self.parent_results = parent_results
        self.param_position = param_position
        self.node_name = node_name
        self.node_slug = node_slug or node_name
        self.upstream_node_slugs = upstream_node_slugs or []
        self.run_id = run_id
        self.rerun = rerun
        self.preserve_missing_results = preserve_missing_results
        self.executor_type = executor_type
        self.executor = executor

    def materialize(self, value: Any) -> Any:
        """Resolve local provider values while preserving container shape."""
        if self.executor is None:
            return value
        if isinstance(value, tuple):
            return tuple(self.materialize(item) for item in value)
        if isinstance(value, list):
            return [self.materialize(item) for item in value]
        if isinstance(value, dict):
            return {k: self.materialize(v) for k, v in value.items()}
        return value

    @property
    def upstream_results(self) -> list[Any]:
        """
        All parent results flattened into a single list.

        This follows the same logic as implicit data passing: each parent's
        results are normalized (based on num_returns) and concatenated.

        Returns:
            Flattened list of all parent result items in order

        Example:
            parent_a returns (x, y) with num_returns=2
            parent_b returns z with num_returns=1
            -> [x, y, z]
        """
        flattened = []
        for result in self.parent_results:
            if result is not None:
                # Normalize: handle multi-return (tuple/list) vs single-return
                items = list(result) if isinstance(result, (tuple, list)) else [result]
                flattened.extend(items)
            elif self.preserve_missing_results:
                flattened.append(None)
        return flattened

    def get_matching_result(self) -> Any | None:
        """
        Get the upstream result that matches this parameter by position.

        Uses zero-copy matching logic: parameters are matched to parent results
        by position in the flattened results list.

        Returns:
            The upstream result at this parameter's position, or None if out of bounds

        Example:
            Upstream outputs: [x, y, z]
            Parameter position: 1
            -> Returns y
        """
        if self.param_position < len(self.upstream_results):
            return self.upstream_results[self.param_position]
        return None

    def get_matching_node_slug(self) -> str | None:
        """Get the upstream node slug that matches this parameter by position."""
        if self.param_position < len(self.upstream_node_slugs):
            return self.upstream_node_slugs[self.param_position]
        return None


class ParameterProvider(ABC):
    """
    Abstract base class for dependency injection in DAG execution.

    Providers must inherit from this class and implement all abstract methods.
    This enables extensible parameter injection (Logger, Config, etc.)
    without modifying DAG execution logic.

    Example:
        class Config(ParameterProvider):
            @classmethod
            def can_resolve(cls, param_value: Any) -> bool:
                return isinstance(param_value, Config)

            @classmethod
            def resolve(cls, param_value: Any, param_context: ParamContext) -> Any:
                # Load the configuration for this step
                return resolved_value

            @classmethod
            def create_wrapper(cls, param_value, original_fn, resolved_params):
                # Override for special behavior (context managers, transactions, etc.)
                # Default injects resolved_params into kwargs
                return custom_wrapper_fn
    """

    consumes_upstream: ClassVar[bool] = False

    @classmethod
    @abstractmethod
    def can_resolve(cls, param_value: Any) -> bool:
        """Check if this provider can handle the given parameter value."""
        ...

    @classmethod
    @abstractmethod
    def resolve(cls, param_value: Any, param_context: ParamContext) -> Any:
        """
        Resolve the parameter to its actual runtime value for injection.

        Args:
            param_value: The parameter value from node invocation
            param_context: Parameter-level context with parent results, node info, etc.
                          Use param_context for common operations:
                          - upstream_results: All upstream results as flat list (property)
                          - get_matching_result(): Match by position (zero-copy)

        Returns:
            Resolved value to inject into the function
        """
        ...

    @classmethod
    def create_wrapper(
        cls, param_value: Any, original_fn: Callable, resolved_params: dict[str, Any]
    ) -> Callable | None:
        """
        Create a wrapper function if special behavior is needed.

        Default returns None - DAG will inject resolved_params into kwargs.
        Override for special behavior (context managers, transactions, etc.).

        Args:
            param_value: The original parameter value
            original_fn: The original function to wrap
            resolved_params: Dict of param_name -> resolved_value for this provider

        Returns:
            Wrapped function for special behavior, or None for default injection
        """
        return None
