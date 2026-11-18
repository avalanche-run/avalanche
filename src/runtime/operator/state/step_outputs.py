"""Adapter for ``run_step_outputs``: the index of each run's saved step outputs.

A row says what became of one successful step's return value: its files were
saved (and the digest of their manifest), or it is not reusable (and why). The
files live under the step-output folder next to the database. A row is written
in the same transaction that marks its node succeeded, and goes with its run
through ``ON DELETE CASCADE``.

``run_reused_outputs`` is the same index for the steps a rerun skipped: it holds no
files, only the run holding each output, or why the output is not reusable.
"""

from __future__ import annotations

import sqlite3
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, model_validator

from .rows import row_model

_TABLE = "run_step_outputs"
_REUSED_TABLE = "run_reused_outputs"
_FIELDS = (
    "node_id, step_slug, status, reason, folder, manifest_sha256, map_parent, map_index, "
    "item_count, byte_size, saved_at"
)
_COLUMNS = f"run_id, {_FIELDS}"
_MAX_IDENTIFIER_LENGTH = 4096

OUTPUT_FOLDER_PATTERN = r"^[0-9a-f]{32}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
MAX_REASON_LENGTH = 1024


class SavedOutputStatus(StrEnum):
    """Whether a later rerun could reuse a step's return value."""

    SAVED = "saved"
    NOT_REUSABLE = "not_reusable"


class SavedOutputRecord(BaseModel):
    """What one successful step left behind, as the run worker reports it.

    A saved map group has no files of its own: ``item_count`` says how many item
    records, each naming it as ``map_parent``, make up its value.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    node_id: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)
    step_slug: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)
    status: SavedOutputStatus = Field(strict=False)
    reason: str | None = Field(default=None, min_length=1, max_length=MAX_REASON_LENGTH)
    folder: str | None = Field(default=None, pattern=OUTPUT_FOLDER_PATTERN)
    manifest_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    map_parent: str | None = Field(default=None, max_length=_MAX_IDENTIFIER_LENGTH)
    map_index: NonNegativeInt | None = None
    item_count: NonNegativeInt | None = None
    byte_size: NonNegativeInt = 0
    saved_at: float

    @model_validator(mode="after")
    def _status_decides_the_fields(self) -> Self:
        if (self.map_parent is None) != (self.map_index is None):
            raise ValueError("A map item names both its group and its index")
        has_files = self.folder is not None and self.manifest_sha256 is not None
        if (self.folder is None) != (self.manifest_sha256 is None):
            raise ValueError("A saved folder and its manifest digest go together")
        if self.status is SavedOutputStatus.NOT_REUSABLE:
            if self.reason is None or has_files or self.item_count is not None:
                raise ValueError("A not-reusable output has a reason and nothing saved")
        elif self.reason is not None or has_files == (self.item_count is not None):
            raise ValueError("A saved output has either its own files or map items")
        return self


class SavedOutputRow(SavedOutputRecord):
    """One ``run_step_outputs`` row."""

    run_id: str = Field(min_length=1)


class ReusedOutputRecord(BaseModel):
    """One step a rerun reused, as the run worker reports it.

    ``source_run_id`` is the run whose own row holds the output, however long the
    chain of reruns; ``reason`` is set when that output is not reusable.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    node_id: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)
    step_slug: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)
    source_run_id: str = Field(min_length=1)
    reason: str | None = Field(default=None, min_length=1, max_length=MAX_REASON_LENGTH)


class ReusedOutputsTable:
    """Read and write ``run_reused_outputs`` rows inside a store transaction."""

    @staticmethod
    def insert(
        connection: sqlite3.Connection, run_id: str, record: ReusedOutputRecord
    ) -> None:
        connection.execute(
            f"INSERT INTO {_REUSED_TABLE} "
            "(run_id, node_id, step_slug, source_run_id, reason) VALUES (?, ?, ?, ?, ?)",
            (
                run_id,
                record.node_id,
                record.step_slug,
                record.source_run_id,
                record.reason,
            ),
        )
        # A reused step's typed result is the one the run holding its output
        # recorded, so a resumed run restores it like any finished step.
        connection.execute(
            "INSERT INTO step_results (run_id, entry, node_id, value_json) "
            "SELECT ?, 0, ?, held.value_json FROM step_results AS held "
            f"JOIN {_TABLE} AS saved ON saved.run_id = held.run_id "
            "AND saved.node_id = held.node_id "
            "WHERE saved.run_id = ? AND saved.step_slug = ? AND held.entry = 0 "
            "ON CONFLICT (run_id, entry, node_id) DO UPDATE SET value_json=excluded.value_json",
            (run_id, record.node_id, record.source_run_id, record.step_slug),
        )

    @staticmethod
    def list_steps(
        connection: sqlite3.Connection, run_id: str
    ) -> tuple[ReusedOutputRecord, ...]:
        rows = connection.execute(
            "SELECT node_id, step_slug, source_run_id, reason "
            f"FROM {_REUSED_TABLE} WHERE run_id = ? ORDER BY step_slug",
            (run_id,),
        ).fetchall()
        return tuple(row_model(ReusedOutputRecord, row, table=_REUSED_TABLE) for row in rows)


class StepOutputsTable:
    """Read and write ``run_step_outputs`` rows inside a store transaction."""

    @staticmethod
    def insert(connection: sqlite3.Connection, run_id: str, record: SavedOutputRecord) -> None:
        connection.execute(
            f"INSERT INTO {_TABLE} ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                record.node_id,
                record.step_slug,
                record.status.value,
                record.reason,
                record.folder,
                record.manifest_sha256,
                record.map_parent,
                record.map_index,
                record.item_count,
                record.byte_size,
                record.saved_at,
            ),
        )

    @staticmethod
    def list_steps(connection: sqlite3.Connection, run_id: str) -> tuple[SavedOutputRow, ...]:
        """One row per step of the run, without the items of its map groups."""
        rows = connection.execute(
            f"SELECT {_COLUMNS} FROM {_TABLE} "
            "WHERE run_id = ? AND map_parent IS NULL ORDER BY step_slug",
            (run_id,),
        ).fetchall()
        return tuple(_row(row) for row in rows)

    @staticmethod
    def get_step(
        connection: sqlite3.Connection, run_id: str, step_slug: str
    ) -> SavedOutputRow | None:
        row = connection.execute(
            f"SELECT {_COLUMNS} FROM {_TABLE} WHERE run_id = ? AND step_slug = ?",
            (run_id, step_slug),
        ).fetchone()
        return None if row is None else _row(row)

    @staticmethod
    def list_items(
        connection: sqlite3.Connection, run_id: str, map_parent: str
    ) -> tuple[SavedOutputRow, ...]:
        """The items of one map group, in item order."""
        rows = connection.execute(
            f"SELECT {_COLUMNS} FROM {_TABLE} "
            "WHERE run_id = ? AND map_parent = ? ORDER BY map_index",
            (run_id, map_parent),
        ).fetchall()
        return tuple(_row(row) for row in rows)

    @staticmethod
    def count(connection: sqlite3.Connection, run_id: str) -> int:
        """How many rows the run has, counting map items."""
        return connection.execute(
            f"SELECT COUNT(*) FROM {_TABLE} WHERE run_id = ?", (run_id,)
        ).fetchone()[0]

    @staticmethod
    def count_all(connection: sqlite3.Connection) -> int:
        return connection.execute(f"SELECT COUNT(*) FROM {_TABLE}").fetchone()[0]


def _row(row: sqlite3.Row) -> SavedOutputRow:
    return row_model(SavedOutputRow, row, table=_TABLE)
