"""Commit conflict handling shared by optional storage backends."""

from functools import cache


class CommitConflictError(Exception):
    """A backend could not commit a metadata transaction due to a conflict."""


@cache
def commit_failed_exceptions() -> tuple[type[Exception], ...]:
    """Include native Iceberg conflicts only when that backend is installed."""
    try:
        from pyiceberg.exceptions import CommitFailedException as IcebergCommitFailedException
    except ModuleNotFoundError as exc:
        if exc.name != "pyiceberg":
            raise
        return (CommitConflictError,)
    return (CommitConflictError, IcebergCommitFailedException)
