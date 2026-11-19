"""A step that decides from inside itself not to do its work ends as skipped."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from types import UnionType
from typing import Annotated, Any, NoReturn, Union, get_args, get_origin, get_type_hints

MAX_SKIP_REASON_CHARS = 1000
DEFAULT_SKIP_REASON = "Skipped by the step"


class StepSkipped(Exception):  # noqa: N818 - an outcome, not an error
    """Raised by :func:`skip`; the host records the step as skipped, never failed.

    It is not retried. Steps that depend on a skipped step are skipped in turn.
    Catching it inside the step (for example with ``except Exception``) cancels
    the skip, so let it propagate.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def skip(reason: str | None = None) -> NoReturn:
    """End the current step at once as skipped, with an optional reason to show."""
    if reason is None:
        reason = DEFAULT_SKIP_REASON
    elif not isinstance(reason, str):
        raise TypeError("skip reason must be a string")
    elif not reason.strip():
        raise ValueError("skip reason must not be blank")
    elif len(reason) > MAX_SKIP_REASON_CHARS:
        raise ValueError(f"skip reason must be at most {MAX_SKIP_REASON_CHARS} characters")
    raise StepSkipped(reason)


def upstream_skip_reason(upstream_slug: str) -> str:
    """The reason shown on a step skipped because a step it depends on was skipped."""
    return f"Upstream step {upstream_slug!r} was skipped"


@dataclass(frozen=True)
class SkippedValue:
    """Stands in for a skipped step's output until a consumer step is bound."""

    upstream_slug: str


def accepts_none(annotation: object) -> bool:
    """Whether a parameter annotation declares that it accepts None."""
    if annotation is None or annotation is type(None):
        return True
    origin = get_origin(annotation)
    if origin is Annotated:
        return accepts_none(get_args(annotation)[0])
    if origin is Union or origin is UnionType:
        return any(accepts_none(arg) for arg in get_args(annotation))
    return False


def bind_skipped_inputs(
    fn: Callable[..., object], args: list[object], kwargs: dict[str, object]
) -> tuple[list[object], dict[str, object]]:
    """Give None for a skipped step's output where the parameter accepts None.

    Any other input from a skipped step (an unannotated or non-nullable parameter)
    raises :class:`StepSkipped`, so the consumer is skipped in turn. Inputs keep
    their positions: nothing is dropped or shifted.
    """
    if not any(isinstance(value, SkippedValue) for value in (*args, *kwargs.values())):
        return args, kwargs
    signature = inspect.signature(fn)
    try:
        hints = get_type_hints(fn, include_extras=True)
    except Exception:  # an unresolvable annotation declares nothing about None
        hints = {}
    params = list(signature.parameters.values())
    positional_kinds = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    positional = [param.name for param in params if param.kind in positional_kinds]
    var_positional = next(
        (param.name for param in params if param.kind is inspect.Parameter.VAR_POSITIONAL), None
    )
    var_keyword = next(
        (param.name for param in params if param.kind is inspect.Parameter.VAR_KEYWORD), None
    )

    def bound(name: str | None, value: object) -> object:
        if not isinstance(value, SkippedValue):
            return value
        if name is not None and name in hints and accepts_none(hints[name]):
            return None
        raise StepSkipped(upstream_skip_reason(value.upstream_slug))

    bound_args = [
        bound(positional[index] if index < len(positional) else var_positional, value)
        for index, value in enumerate(args)
    ]
    bound_kwargs = {
        name: bound(name if name in signature.parameters else var_keyword, value)
        for name, value in kwargs.items()
    }
    return bound_args, bound_kwargs


def fills_one_input(fn: Callable[..., object]) -> bool:
    """Whether a step's output fills exactly one implicit (``>>``) input.

    An ordinary step that returns a tuple or list fills one input per element, so
    a skipped step stands in for one input only when its return annotation rules
    out a tuple or list.
    """
    try:
        annotation = get_type_hints(fn).get("return")
    except Exception:  # an unresolvable annotation declares nothing
        return False
    return annotation is not None and not _may_be_sequence(annotation)


def _may_be_sequence(annotation: object) -> bool:
    if annotation is object or annotation is Any:
        return True
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        return any(_may_be_sequence(arg) for arg in get_args(annotation))
    candidate = origin or annotation
    return isinstance(candidate, type) and issubclass(candidate, (tuple, list))
