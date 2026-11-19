"""Lazy, host-neutral catalog provider contract.

Providers are zero-argument factories in ``avalanche.integration_providers``.
Discovery happens only when connecting an account or starting a catalog step,
never when importing or describing a workflow. Implementations own persistent
connections; the SDK holds a step's temporary pass only in memory.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from contextvars import ContextVar
from importlib.metadata import entry_points
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ..policies import NonRetryableError
from .core import (
    IntegrationNotConnectedError,
    IntegrationReconnectError,
    MCPServerUnavailableError,
)


class ProviderNotInstalledError(NonRetryableError, ValueError):
    """No installed provider can serve catalog integrations."""


class ProviderNotSignedInError(NonRetryableError, ValueError):
    """The provider supplies an actionable sign-in message."""

    def __init__(
        self,
        message: str = "The integration provider is not signed in. "
        "Follow its sign-in instructions, then connect again.",
    ) -> None:
        super().__init__(message)


class ProviderUnavailableError(MCPServerUnavailableError):
    """The provider is unreachable; retrying may help."""


class ProviderSelectionError(NonRetryableError, ValueError):
    """An explicit provider selection is missing or invalid."""


class CatalogEntry(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    name: str
    display_name: str
    icon_url: str | None = Field(default=None, pattern=r"^https?://")


class CatalogConnection(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    name: str
    state: Literal["connected", "reconnect_needed"]
    connected_at: float | None = None


class CatalogConnectionProblem(BaseModel):
    """A host's preflight refusal, transferable without a credential or a pass."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    kind: Literal["missing", "reconnect_needed"]
    names: tuple[str, ...] = Field(min_length=1)

    def raise_for_step(self, step: str, command: str) -> None:
        if self.kind == "missing":
            raise IntegrationNotConnectedError(step, self.names, command)
        raise IntegrationReconnectError(
            step, self.names[0], command, "the provider requires a new login"
        )


def catalog_connection_problem(
    names: tuple[str, ...], connections: Sequence[CatalogConnection]
) -> CatalogConnectionProblem | None:
    """One rule for local providers and controller-issued catalog sessions."""
    connected = {connection.name: connection for connection in connections}
    missing = tuple(name for name in names if name not in connected)
    if missing:
        return CatalogConnectionProblem(kind="missing", names=missing)
    reconnect = tuple(name for name in names if connected[name].state != "connected")
    if reconnect:
        return CatalogConnectionProblem(kind="reconnect_needed", names=reconnect)
    return None


class CatalogLogin(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    login_id: str
    name: str
    authorization_url: str = Field(pattern=r"^https?://")
    status: Literal["waiting", "connected", "failed"]
    error: str | None = None


class CatalogToolSession(BaseModel):
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )

    url: str = Field(pattern=r"^https?://")
    pass_value: SecretStr = Field(repr=False, exclude=True)
    expires_at: float


_DELIVERED_SESSION: ContextVar[tuple[CatalogToolSession, tuple[str, ...]] | None] = ContextVar(
    "avalanche_catalog_tool_session", default=None
)


def install_tool_session(session: CatalogToolSession, names: tuple[str, ...]) -> None:
    """Install an already authorized session in a host-owned step process.

    Like install_account_source, this is a host boundary, not workflow input.
    It never loads a member login or renews the capability.
    """
    _DELIVERED_SESSION.set((session, names))


def delivered_tool_session() -> tuple[CatalogToolSession, tuple[str, ...]] | None:
    return _DELIVERED_SESSION.get()


@runtime_checkable
class IntegrationProvider(Protocol):
    """Synchronous host boundary, called outside workflow discovery.

    Raise ProviderNotSignedInError with the provider's own sign-in guidance,
    ProviderUnavailableError for outages, AccountLoginExpiredError for a revoked
    connection, and IntegrationNotConnectedError for a missing connection.
    ``open_tool_session`` is called once per step start, for all its names.
    The returned pass is never renewed; the next call after expiry is retryable.
    """

    def entries(self) -> Sequence[CatalogEntry]: ...
    def start_login(self, name: str) -> CatalogLogin: ...
    def login_status(self, login_id: str) -> CatalogLogin: ...
    def disconnect(self, name: str) -> None: ...
    def connections(self) -> Sequence[CatalogConnection]: ...
    def open_tool_session(self, step: str, names: tuple[str, ...]) -> CatalogToolSession: ...


def integration_provider() -> IntegrationProvider:
    """Load the selected factory late, without caching account or login state."""
    installed = sorted(
        entry_points(group="avalanche.integration_providers"), key=lambda e: e.name
    )
    selected = os.environ.get("AVALANCHE_INTEGRATION_PROVIDER")
    if not installed:
        raise ProviderNotInstalledError(
            "Built-in integrations need a provider, for example Avalanche Cloud "
            "(Trampoline Delta): uv tool install trampoline-delta. Then sign in and "
            "use `ava connect <name>`."
        )
    choices = ", ".join(entry.name for entry in installed)
    if selected is None and len(installed) != 1:
        raise ProviderSelectionError(
            f"Several integration providers are installed: {choices}. "
            "Set AVALANCHE_INTEGRATION_PROVIDER to an entry name."
        )
    matches = installed if selected is None else [e for e in installed if e.name == selected]
    if len(matches) != 1:
        raise ProviderSelectionError(
            f"Integration provider {selected!r} is not a unique installed entry. "
            f"Installed providers: {choices}."
        )
    try:
        provider = matches[0].load()()
    except (ProviderNotSignedInError, ProviderUnavailableError):
        raise
    except Exception:
        # This is the untyped plug-in boundary. Loader exceptions may contain
        # credentials or machine paths, so never expose their text or traceback.
        raise ProviderUnavailableError(
            "The integration provider could not be loaded."
        ) from None
    if not isinstance(provider, IntegrationProvider):
        raise ProviderSelectionError(
            f"Integration provider {matches[0].name!r} does not implement "
            "the provider contract."
        )
    return provider
