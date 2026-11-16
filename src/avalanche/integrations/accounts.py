"""Accounts: the credential a host stores for one integration name.

Accounts are never in workflow code. The host that runs a workflow (the local
operator, or a hosted project) stores one account per integration name and
installs an :class:`AccountSource`; a step asks it for the accounts of the
integrations it declares when it starts.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCredential(BaseModel):
    """An API key. Its value is left out of ``repr`` so it cannot reach a log by accident.

    ``header`` is set only for an MCP server, which does not declare where its
    key goes; a Connector declares that itself with ``ava.ApiKey``.
    """

    # A validation error names the failing field but never echoes the value.
    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )

    kind: Literal["api_key"] = "api_key"
    key: str = Field(min_length=1, repr=False)
    header: str | None = None


class AccessTokenCredential(BaseModel):
    """What a step gets from a browser login: an access token and when it stops working.

    The refresh token and the client secret stay in the host's store. A step
    that is still running when the token is about to expire asks its account
    source again and gets a fresh one.
    """

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )

    kind: Literal["access_token"] = "access_token"
    token: str = Field(min_length=1, repr=False)
    # Epoch seconds; ``None`` when the service gave no lifetime.
    expires_at: float | None


class OAuthCredential(BaseModel):
    """A browser login as a host stores it: the tokens and what a refresh needs.

    Only a host's account store holds one. A step is handed
    :meth:`access_token` and never the refresh token or the client secret.
    """

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )

    kind: Literal["oauth"] = "oauth"
    access_token: str = Field(min_length=1, repr=False)
    refresh_token: str | None = Field(repr=False)
    # Epoch seconds; ``None`` when the service gave no lifetime.
    expires_at: float | None
    scope: str | None
    token_url: str
    client_id: str
    client_secret: str | None = Field(repr=False)
    # How the client proves itself at ``token_url``.
    client_auth: Literal["client_secret_basic", "client_secret_post", "none"]
    # The MCP server the tokens are for (RFC 8707), sent again on a refresh.
    resource: str | None

    def for_step(self) -> AccessTokenCredential:
        return AccessTokenCredential(token=self.access_token, expires_at=self.expires_at)


# What a step is handed for one integration.
Credential = Annotated[ApiKeyCredential | AccessTokenCredential, Field(discriminator="kind")]
# What a host's account store holds for one integration.
StoredCredential = Annotated[ApiKeyCredential | OAuthCredential, Field(discriminator="kind")]


class AccountLoginExpiredError(Exception):
    """An account's browser login no longer works and cannot be refreshed.

    Raised by an :class:`AccountSource`. The step that asked fails without
    retries, with a message that says to connect the account again.
    """

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason
        super().__init__(f"the login of integration {name!r} is no longer valid: {reason}")


class AccountUnavailableError(RuntimeError):
    """An account could not be refreshed for a cause that may pass.

    Raised by an :class:`AccountSource`, for example when the login service
    cannot be reached. The step's retries apply.
    """

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason
        super().__init__(f"the login of integration {name!r} could not be refreshed: {reason}")


class AccountSource(Protocol):
    """Where a running step reads accounts from; implemented by each host.

    ``connect_command`` is the command a user runs to connect an account on
    this host, shown in the error of a step whose account is missing.
    """

    connect_command: str

    def accounts(self, names: tuple[str, ...]) -> Mapping[str, Credential]:
        """The stored accounts among ``names``; a name without one is left out.

        A browser login is returned as an access token that is valid now: the
        host refreshes an expired one first and stores the new tokens. It
        raises :class:`AccountLoginExpiredError` when the login cannot be
        refreshed any more and :class:`AccountUnavailableError` when the
        refresh failed for a cause that may pass.
        """
        ...


class StaticAccounts:
    """An account source over values the embedding program already holds.

    For programs that run a workflow without an operator and for tests. Read
    the keys from your own secret store; do not write them in workflow code.
    """

    def __init__(
        self, accounts: Mapping[str, Credential], *, connect_command: str = "ava connect"
    ) -> None:
        self._accounts = dict(accounts)
        self.connect_command = connect_command

    def accounts(self, names: tuple[str, ...]) -> Mapping[str, Credential]:
        return {name: self._accounts[name] for name in names if name in self._accounts}


_ACCOUNT_SOURCE: ContextVar[AccountSource | None] = ContextVar(
    "avalanche_integration_account_source", default=None
)


def current_account_source() -> AccountSource | None:
    return _ACCOUNT_SOURCE.get()


@contextmanager
def use_accounts(source: AccountSource) -> Iterator[None]:
    """Run the enclosed workflow runs with ``source`` as their account store.

    A run started inside the block keeps the source for its whole life, because
    ``Workflow.run()`` copies the calling context.
    """
    token = _ACCOUNT_SOURCE.set(source)
    try:
        yield
    finally:
        _ACCOUNT_SOURCE.reset(token)


def install_account_source(source: AccountSource) -> None:
    """Set the account store for this context and everything it starts.

    For a host process that runs one workflow and exits, such as an operator's
    run worker; use :func:`use_accounts` when the scope should end.
    """
    _ACCOUNT_SOURCE.set(source)


__all__ = [
    "AccessTokenCredential",
    "AccountLoginExpiredError",
    "AccountSource",
    "AccountUnavailableError",
    "ApiKeyCredential",
    "Credential",
    "OAuthCredential",
    "StaticAccounts",
    "StoredCredential",
    "current_account_source",
    "install_account_source",
    "use_accounts",
]
