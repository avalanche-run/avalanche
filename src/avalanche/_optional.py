"""Actionable errors at optional feature import boundaries."""

from importlib import import_module
from types import ModuleType


def import_optional(name: str, *, extra: str, feature: str) -> ModuleType:
    """Import a feature dependency without concealing broken transitive imports."""
    try:
        return import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        raise ImportError(
            f"{feature} features require the optional {extra} dependency. "
            f"Install it with `uv add 'avalanche-ai[{extra}]'` "
            f"or `uv sync --extra {extra}`."
        ) from exc
