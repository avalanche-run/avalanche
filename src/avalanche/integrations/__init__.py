"""Integrations: Connectors, MCP servers and their accounts.

``ava.Connector``, ``ava.MCP``, ``ava.ApiKey``, ``ava.tool`` and ``ava.Client``
are the authoring surface. The rest is for hosts: a host stores accounts,
implements :class:`AccountSource` and installs it around the runs it starts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .accounts import (
    AccessTokenCredential,
    AccountLoginExpiredError,
    AccountSource,
    AccountUnavailableError,
    ApiKeyCredential,
    Credential,
    OAuthCredential,
    StaticAccounts,
    StoredCredential,
    install_account_source,
    use_accounts,
)
from .core import (
    MCP,
    ApiKey,
    Connector,
    Integration,
    IntegrationError,
    IntegrationNotConnectedError,
    IntegrationReconnectError,
    MCPServerUnavailableError,
    MCPToolError,
    OAuth2,
    step_integration_declarations,
    tool,
)
from .declaration import (
    ApiKeyDeclaration,
    ConnectorDeclaration,
    IntegrationDeclaration,
    IntegrationToolDeclaration,
    MCPDeclaration,
    OAuth2Declaration,
    StepIntegrations,
)

if TYPE_CHECKING:
    from .client import Client


def __getattr__(name: str):
    # The HTTP stack loads when a Connector first opens its client, not on import.
    if name == "Client":
        from .client import Client

        return Client
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MCP",
    "AccessTokenCredential",
    "AccountLoginExpiredError",
    "AccountSource",
    "AccountUnavailableError",
    "ApiKey",
    "ApiKeyCredential",
    "ApiKeyDeclaration",
    "Client",
    "Connector",
    "ConnectorDeclaration",
    "Credential",
    "Integration",
    "IntegrationDeclaration",
    "IntegrationError",
    "IntegrationNotConnectedError",
    "IntegrationReconnectError",
    "IntegrationToolDeclaration",
    "MCPDeclaration",
    "MCPServerUnavailableError",
    "MCPToolError",
    "OAuth2",
    "OAuth2Declaration",
    "OAuthCredential",
    "StaticAccounts",
    "StepIntegrations",
    "StoredCredential",
    "install_account_source",
    "step_integration_declarations",
    "tool",
    "use_accounts",
]
