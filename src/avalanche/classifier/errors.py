"""Errors raised by classifier declarations and calls, for every provider."""

from __future__ import annotations


class ClassifierStepError(RuntimeError):
    """An invalid classifier declaration or invocation boundary."""


class ClassifierStepExecutionError(RuntimeError):
    """A classifier call to its provider, or classifier resource cleanup, failed."""
