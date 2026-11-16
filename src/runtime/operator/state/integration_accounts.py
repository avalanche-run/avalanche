"""Adapter for ``integration_accounts``: one stored account per integration name."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, TypeAdapter

from avalanche.integrations.accounts import OAuthCredential, StoredCredential

from .rows import json_value, row_model

_TABLE = "integration_accounts"
_STORED = TypeAdapter(StoredCredential)


class _AccountRow(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str
    credential_json: str
    connected_at: float


class ConnectedAccount(BaseModel):
    """That an integration has an account and since when; never its credential."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str
    connected_at: float


class IntegrationAccountsTable:
    """Read and write stored accounts inside a store transaction."""

    @staticmethod
    def write(
        connection: sqlite3.Connection,
        name: str,
        credential: StoredCredential,
        *,
        connected_at: float,
    ) -> None:
        """Store the account of ``name``, replacing the one it had."""
        connection.execute(
            f"""INSERT INTO {_TABLE} (name, credential_json, connected_at)
                VALUES (?, ?, ?)
                ON CONFLICT (name) DO UPDATE SET
                    credential_json = excluded.credential_json,
                    connected_at = excluded.connected_at""",
            (name, credential.model_dump_json(), connected_at),
        )

    @staticmethod
    def renew(
        connection: sqlite3.Connection,
        name: str,
        *,
        previous: OAuthCredential,
        renewed: OAuthCredential,
    ) -> bool:
        """Store a login's refreshed tokens; false when the account changed meanwhile.

        The account keeps its connection time. One that was connected again or
        removed while the refresh ran is left as it is.
        """
        return (
            connection.execute(
                f"UPDATE {_TABLE} SET credential_json = ? "
                "WHERE name = ? AND credential_json = ?",
                (renewed.model_dump_json(), name, previous.model_dump_json()),
            ).rowcount
            > 0
        )

    @staticmethod
    def delete(connection: sqlite3.Connection, name: str) -> bool:
        """Remove the account of ``name``; false when it had none."""
        return connection.execute(f"DELETE FROM {_TABLE} WHERE name = ?", (name,)).rowcount > 0

    @staticmethod
    def connected(connection: sqlite3.Connection) -> tuple[ConnectedAccount, ...]:
        """Every integration name that has an account, without reading a credential."""
        rows = connection.execute(
            f"SELECT name, connected_at FROM {_TABLE} ORDER BY name"
        ).fetchall()
        return tuple(row_model(ConnectedAccount, row, table=_TABLE) for row in rows)

    @staticmethod
    def credentials(
        connection: sqlite3.Connection, names: Sequence[str]
    ) -> dict[str, StoredCredential]:
        """The stored credentials among ``names``; a name without an account is left out."""
        credentials: dict[str, StoredCredential] = {}
        for name in names:
            row = connection.execute(
                f"SELECT name, credential_json, connected_at FROM {_TABLE} WHERE name = ?",
                (name,),
            ).fetchone()
            if row is None:
                continue
            stored = row_model(_AccountRow, row, table=_TABLE)
            credentials[name] = json_value(
                _STORED, stored.credential_json, table=_TABLE, column="credential_json"
            )
        return credentials
