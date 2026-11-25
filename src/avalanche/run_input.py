"""Build a run's input from its workflow's input model, the same way on every host.

A run whose input fails the model (a field rule or a model validator) cannot
succeed on a retry: every host fails it once, with the model's own messages.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .policies import NonRetryableError

INPUT_INVALID = "input_invalid"
_MAX_REPORTED_ERRORS = 10
# Pydantic prefixes a validator's own message with its error kind.
_KIND_PREFIXES = ("Value error, ", "Assertion failed, ")


class RunInputInvalidError(NonRetryableError):
    """The run's input does not satisfy its workflow's input model."""

    code = INPUT_INVALID


def _describe(error: Any) -> str:
    message = str(error["msg"])
    for prefix in _KIND_PREFIXES:
        message = message.removeprefix(prefix)
    location = ".".join(str(part) for part in error["loc"])
    return f"{location}: {message}" if location else message


def input_invalid_message(input_type: type, exc: ValidationError) -> str:
    """The model's messages and field locations; never the input values themselves."""
    errors = exc.errors(include_url=False, include_input=False, include_context=False)
    parts = [_describe(error) for error in errors[:_MAX_REPORTED_ERRORS]]
    if len(errors) > _MAX_REPORTED_ERRORS:
        parts.append(f"and {len(errors) - _MAX_REPORTED_ERRORS} more")
    return f"The run's input is invalid: {'; '.join(parts)} ({input_type.__name__})"


def build_run_input(input_type: type | None, raw_input: Any) -> Any:
    """The run input value, or RunInputInvalidError when the model refuses it."""
    from .dag import _build_input_value

    if input_type is None:
        return _build_input_value(None, raw_input)
    try:
        return _build_input_value(input_type, raw_input)
    except ValidationError as exc:
        raise RunInputInvalidError(input_invalid_message(input_type, exc)) from exc
