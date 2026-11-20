"""Accounts for a run's steps: the worker asks, the operator answers from its store.

A run's worker process cannot open the operator's state database, which the
operator holds locked. Each run therefore gets one private pipe. When a step
that declares integrations starts, the worker sends the names it needs and the
operator replies with the accounts it has. Credentials travel only over that
pipe, between two local processes, and are never written to the run's events.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from multiprocessing.connection import Connection
from typing import Literal

from pydantic import BaseModel, ConfigDict

from avalanche.integrations import connect
from avalanche.integrations.accounts import (
    AccountLoginExpiredError,
    AccountUnavailableError,
    ApiKeyCredential,
    Credential,
)

# Re-exported: the operator's callers catch it from this module.
from avalanche.integrations.connect import IntegrationConnectError as IntegrationConnectError
from avalanche.integrations.declaration import CatalogDeclaration, IntegrationDeclaration
from avalanche.integrations.providers import (
    CatalogConnection,
    CatalogEntry,
    NamedCatalogConnection,
    ProviderNotInstalledError,
    ProviderNotSignedInError,
    ProviderSelectionError,
    ProviderUnavailableError,
    integration_provider,
    named_integration_provider,
)

from .models import WorkflowDescriptor
from .state.integration_accounts import ConnectedAccount

MAX_ACCOUNT_MESSAGE_BYTES = 1024 * 1024


class AccountsRequest(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    names: tuple[str, ...]


class AccountsResponse(BaseModel):
    """The accounts found; a requested name without one is left out.

    A browser login is sent as an access token that is valid now. One that
    could not be refreshed is named in ``expired`` (connect it again) or in
    ``unavailable`` (the cause may pass), each with the reason.
    """

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )

    accounts: dict[str, Credential]
    expired: dict[str, str] = {}
    unavailable: dict[str, str] = {}


AccountLookup = Callable[[Sequence[str]], AccountsResponse]


class OperatorAccounts:
    """The worker's account source: one request and reply per step start."""

    connect_command = "ava connect"

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        # Steps of one run start on several threads; a request owns the pipe
        # until its reply is read.
        self._lock = threading.Lock()

    def accounts(self, names: tuple[str, ...]) -> Mapping[str, Credential]:
        with self._lock:
            self._connection.send_bytes(AccountsRequest(names=names).model_dump_json().encode())
            reply = self._connection.recv_bytes(MAX_ACCOUNT_MESSAGE_BYTES)
        response = AccountsResponse.model_validate_json(reply)
        for name, reason in response.expired.items():
            raise AccountLoginExpiredError(name, reason)
        for name, reason in response.unavailable.items():
            raise AccountUnavailableError(name, reason)
        return response.accounts


def serve_account_requests(connection: Connection, lookup: AccountLookup) -> None:
    """Answer one worker's account requests until it exits, then close the pipe."""
    try:
        while True:
            try:
                raw = connection.recv_bytes(MAX_ACCOUNT_MESSAGE_BYTES)
            except (EOFError, OSError):
                # The worker exited and closed its end.
                return
            request = AccountsRequest.model_validate_json(raw)
            response = lookup(request.names)
            try:
                connection.send_bytes(response.model_dump_json().encode())
            except OSError:
                return
    finally:
        connection.close()


def start_account_server(
    connection: Connection, lookup: AccountLookup, *, run_id: str
) -> threading.Thread:
    thread = threading.Thread(
        target=serve_account_requests,
        args=(connection, lookup),
        name=f"avalanche-accounts-{run_id}",
        daemon=True,
    )
    thread.start()
    return thread


class UnknownIntegrationError(LookupError):
    """No discovered workflow declares the named integration."""


class IntegrationUse(BaseModel):
    """One step that declares an integration."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    workflow_id: str
    workflow: str
    node_id: str


class ConnectionStatus(BaseModel):
    """One named connection, with the starting human's state and no credentials."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    slug: str
    kind: Literal["shared", "per_user"]
    state: Literal["connected", "reconnect_needed", "disconnected"]
    instances: int
    assignments: tuple[Literal["operator"], ...] = ("operator",)


class IntegrationStatus(BaseModel):
    """A declared integration and whether it has an account; never the credential.

    ``requires_account`` is false for a Connector without ``auth`` and for an
    MCP server, whose need for a login is learned from the server.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    name: str
    declaration: IntegrationDeclaration
    requires_account: bool
    # The ways to connect it, the default first; empty when it takes no account.
    login: tuple[connect.LoginKind, ...]
    connected: bool
    connected_at: float | None
    used_by: tuple[IntegrationUse, ...]
    display_name: str | None = None
    icon_url: str | None = None
    account_state: Literal["connected", "missing", "none", "reconnect_needed"] = "missing"
    provider_error: str | None = None
    connections: tuple[ConnectionStatus, ...] = ()
    named_connections: bool = False


class IntegrationStatusList(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    integrations: tuple[IntegrationStatus, ...]


class ConnectIntegrationRequest(BaseModel):
    """A connect call's body; errors about it never echo the credential."""

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )

    name: str
    credential: ApiKeyCredential


def integration_statuses(
    descriptors: Iterable[WorkflowDescriptor],
    connected: Mapping[str, ConnectedAccount],
) -> tuple[IntegrationStatus, ...]:
    """One row per declared integration name, in name order.

    Steps that share a name share its account; the first declaration in
    workflow order describes the row.
    """
    declarations: dict[str, IntegrationDeclaration] = {}
    uses: dict[str, list[IntegrationUse]] = {}
    for descriptor in sorted(descriptors, key=lambda item: item.workflow_id):
        for node_id, step in descriptor.integration_declarations:
            for declaration in step.integrations:
                declarations.setdefault(declaration.name, declaration)
                uses.setdefault(declaration.name, []).append(
                    IntegrationUse(
                        workflow_id=descriptor.workflow_id,
                        workflow=descriptor.display_name,
                        node_id=node_id,
                    )
                )
    statuses = []
    entries: dict[str, CatalogEntry] = {}
    connections: dict[str, CatalogConnection] = {}
    provider_error = None
    named: dict[str, list[ConnectionStatus]] = {}
    supports_named = False
    if any(isinstance(item, CatalogDeclaration) for item in declarations.values()):
        try:
            provider = integration_provider()
            entries = {entry.name: entry for entry in provider.entries()}
            connections = {item.name: item for item in provider.connections()}
            named_provider = named_integration_provider(provider)
            supports_named = named_provider is not None
            if named_provider is not None:
                records = named_provider.named_connections()
                user = named_provider.starting_user()
                by_slug: dict[str, list[NamedCatalogConnection]] = {}
                for item in records:
                    by_slug.setdefault(item.slug, []).append(item)
                for slug, instances in sorted(by_slug.items()):
                    first = instances[0]
                    own = next(
                        (item for item in instances if user is not None and item.user == user),
                        None,
                    )
                    account = first if first.kind == "shared" else own
                    named.setdefault(first.name, []).append(
                        ConnectionStatus(
                            slug=slug,
                            kind=first.kind,
                            state="disconnected" if account is None else account.state,
                            instances=sum(item.user is not None for item in instances)
                            if first.kind == "per_user"
                            else len(instances),
                        )
                    )
        except (
            ProviderNotInstalledError,
            ProviderNotSignedInError,
            ProviderSelectionError,
            ProviderUnavailableError,
        ) as exc:
            # Keep unrelated integrations usable when catalog setup is missing.
            provider_error = str(exc)
    for name in sorted(declarations):
        declaration = declarations[name]
        account = connected.get(name)
        builtin = isinstance(declaration, CatalogDeclaration)
        entry = entries.get(name) if builtin else None
        connection = connections.get(name) if builtin else None
        requires = connect.declaration_requires_account(declaration)
        state = (
            ("missing" if connection is None else connection.state)
            if builtin
            else "connected"
            if account is not None
            else "missing"
            if requires or connect.declaration_login_kinds(declaration)
            else "none"
        )
        if builtin and supports_named:
            states = {item.state for item in named.get(name, ())}
            state = (
                "connected"
                if "connected" in states
                else ("reconnect_needed" if "reconnect_needed" in states else "missing")
            )
        statuses.append(
            IntegrationStatus(
                name=name,
                declaration=declaration,
                requires_account=connect.declaration_requires_account(declaration),
                login=connect.declaration_login_kinds(declaration),
                connected=state == "connected",
                connected_at=(None if connection is None else connection.connected_at)
                if builtin
                else None
                if account is None
                else account.connected_at,
                used_by=tuple(uses[name]),
                display_name=(None if entry is None else entry.display_name)
                if isinstance(declaration, CatalogDeclaration)
                else declaration.display_name,
                icon_url=None if entry is None else entry.icon_url,
                account_state=state,
                provider_error=provider_error if builtin else None,
                connections=tuple(named.get(name, ())) if builtin else (),
                named_connections=builtin and supports_named,
            )
        )
    return tuple(statuses)


def check_credential_fits(status: IntegrationStatus, credential: ApiKeyCredential) -> None:
    """Refuse an API key the integration's declaration cannot use."""
    connect.check_credential_fits(status.declaration, credential)
