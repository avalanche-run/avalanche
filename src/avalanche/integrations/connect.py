"""Rules for connecting an account, shared by every host that stores accounts.

A host reads them from an integration's declaration, so the local operator and
a hosted project accept and refuse the same credentials.
"""

from __future__ import annotations

from typing import Literal

from .accounts import ApiKeyCredential
from .declaration import (
    ApiKeyDeclaration,
    CatalogDeclaration,
    ConnectorDeclaration,
    IntegrationDeclaration,
    MCPDeclaration,
    OAuth2Declaration,
)

ConnectRefusal = Literal[
    "needs_no_account",
    "header_not_accepted",
    "local_process",
    "browser_login",
    # A browser login that cannot start as asked.
    "no_browser_login",
    "client_id_required",
    "client_id_not_accepted",
    "client_secret_not_accepted",
]
# The ways an account can be connected: a pasted key, or a login in the browser.
LoginKind = Literal["api_key", "browser"]


class IntegrationConnectError(ValueError):
    """The offered credential does not fit the integration's declaration.

    ``reason`` names the rule that refused it, for a host whose API answers
    with a code instead of this message.
    """

    def __init__(self, message: str, reason: ConnectRefusal) -> None:
        super().__init__(message)
        self.reason: ConnectRefusal = reason


def declaration_requires_account(declaration: IntegrationDeclaration) -> bool:
    """Whether a step that declares this fails at start without an account.

    False for a Connector without ``auth`` and for an MCP server, whose need
    for a login is learned from the server.
    """
    return isinstance(declaration, CatalogDeclaration) or (
        isinstance(declaration, ConnectorDeclaration) and declaration.auth is not None
    )


def declaration_accepts_account(declaration: IntegrationDeclaration) -> bool:
    """Whether a stored account is ever sent for this declaration."""
    match declaration:
        case ConnectorDeclaration(auth=auth):
            return auth is not None
        case MCPDeclaration(url=url):
            return url is not None
        case CatalogDeclaration():
            return False


def declaration_login_kinds(declaration: IntegrationDeclaration) -> tuple[LoginKind, ...]:
    """The ways to connect this declaration, the default first; empty when it takes none."""
    match declaration:
        case ConnectorDeclaration(auth=ApiKeyDeclaration()):
            return ("api_key",)
        case ConnectorDeclaration(auth=OAuth2Declaration()) | CatalogDeclaration():
            return ("browser",)
        case MCPDeclaration(url=str()):
            # The login is discovered from the server; a key is the other way.
            return ("browser", "api_key")
        case _:
            return ()


def check_credential_fits(
    declaration: IntegrationDeclaration, credential: ApiKeyCredential
) -> None:
    """Refuse an API key the integration's declaration cannot use."""
    match declaration:
        case CatalogDeclaration():
            raise IntegrationConnectError(
                f"Catalog integration {declaration.name!r} requires a provider browser login.",
                "browser_login",
            )
        case ConnectorDeclaration(auth=None):
            raise IntegrationConnectError(
                f"Connector {declaration.name!r} declares no auth, so it needs no account.",
                "needs_no_account",
            )
        case ConnectorDeclaration(auth=OAuth2Declaration()):
            raise IntegrationConnectError(
                f"Connector {declaration.name!r} declares a browser login, not an API key.",
                "browser_login",
            )
        case ConnectorDeclaration(auth=ApiKeyDeclaration() as auth) if (
            credential.header is not None
        ):
            raise IntegrationConnectError(
                f"Connector {declaration.name!r} declares where its key goes "
                f"({auth.header}); connect it without a header.",
                "header_not_accepted",
            )
        case MCPDeclaration(url=None):
            raise IntegrationConnectError(
                f"MCP server {declaration.name!r} is a local process, which has no request "
                "header to carry a key; it reads its credentials from the environment "
                "the operator runs in.",
                "local_process",
            )
        case ConnectorDeclaration() | MCPDeclaration():
            return


__all__ = [
    "ConnectRefusal",
    "IntegrationConnectError",
    "LoginKind",
    "check_credential_fits",
    "declaration_accepts_account",
    "declaration_login_kinds",
    "declaration_requires_account",
]
