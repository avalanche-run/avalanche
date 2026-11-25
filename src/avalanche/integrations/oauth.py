"""Browser login (OAuth 2) for integrations: the login itself and the refresh rule.

Every host uses this module, so the rules are the same locally and when
hosted. A host supplies a :class:`LoginBrowser`, which sends the person to the
consent page and returns the address they came back to, and stores the
:class:`~.accounts.OAuthCredential` a login returns. Before it hands an
account to a step it calls :func:`fresh`, which refreshes an expired access
token.

A host that makes these requests from a network a workflow's author must not
reach (a hosted control plane) passes an :class:`AddressPolicy`: every request
of a login or a refresh then connects only to the address the policy returned
for its host.

The official MCP library does an MCP server's login: it discovers the login
from the server, registers a client when the server allows it, and exchanges
the result. Authlib does a Connector's declared login and every refresh.
Neither is imported until a login or a refresh runs.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import parse_qs, urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError

from .._optional import import_optional
from .accounts import AccountLoginExpiredError, AccountUnavailableError, OAuthCredential
from .connect import IntegrationConnectError
from .declaration import (
    ConnectorDeclaration,
    IntegrationDeclaration,
    MCPDeclaration,
    OAuth2Declaration,
)

if TYPE_CHECKING:
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

# An access token with less than this left is refreshed before a step gets it.
REFRESH_MARGIN_SECONDS = 60.0
# One request to a login service: discovery, registration, exchange or refresh.
LOGIN_REQUEST_TIMEOUT_SECONDS = 30.0
CLIENT_NAME = "Avalanche"


class LoginError(Exception):
    """A browser login did not complete; the message says why, without secrets."""


class AddressRefusedError(LoginError):
    """A host's :class:`AddressPolicy` refused an address a login or refresh needs."""


class AddressPolicy(Protocol):
    """Which addresses a host lets a login and a refresh connect to.

    Called for every request with the URL's scheme and host. It returns the IP
    address to connect to, which the request is then pinned to (the host name
    is still what the certificate is checked against), or raises
    :class:`AddressRefusedError`.
    """

    async def __call__(self, scheme: str, host: str) -> str: ...


def _pinned[T](base: type[T], transport: type, limits: object, addresses: AddressPolicy) -> T:
    """A transport of one HTTP library that connects where ``addresses`` says.

    ``httpx`` and ``httpx2`` have the same transport interface, so one body
    serves a client of either.
    """

    class Pinned(base):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            # No kept connections: one to an address is never reused for another name.
            self._inner = transport(limits=limits)

        async def handle_async_request(self, request):  # type: ignore[no-untyped-def]
            host = request.url.host
            address = await addresses(request.url.scheme, host)
            # A copy goes out: an auth flow sends the same request again after
            # a login, and it must still name the host, not the address.
            # Its headers, Host included, are the request's own.
            pinned = type(request)(
                request.method,
                request.url.copy_with(host=address),
                headers=request.headers,
                stream=request.stream,
                extensions={**request.extensions, "sni_hostname": host},
            )
            return await self._inner.handle_async_request(pinned)

        async def aclose(self) -> None:
            await self._inner.aclose()

    return Pinned()


def _transport(client: type, addresses: AddressPolicy | None):  # type: ignore[no-untyped-def]
    """The pinned transport for an HTTP client class, or ``None`` without a policy.

    Authlib's client and the MCP library's are built on ``httpx`` or on
    ``httpx2``, whichever their release uses; the transport has to come from
    the same library as the client it is given to.
    """
    if addresses is None:
        return None
    import sys

    library = next(
        sys.modules[base.__module__.partition(".")[0]]
        for base in client.__mro__
        if base.__module__.partition(".")[0] in ("httpx", "httpx2")
    )
    return _pinned(
        library.AsyncBaseTransport,
        library.AsyncHTTPTransport,
        library.Limits(max_keepalive_connections=0),
        addresses,
    )


class LoginBrowser(Protocol):
    """How a host takes a person through the consent page.

    ``redirect_uri`` is the address the service sends the browser back to. The
    host must hand :meth:`returned` only an address it received for the login
    it started: one whose ``state`` is the ``state`` of the address given to
    :meth:`open`.
    """

    redirect_uri: str

    async def open(self, authorization_url: str) -> None:
        """Send the person to the consent page."""
        ...

    async def returned(self) -> str:
        """The full address the browser came back to, query included."""
        ...


def login_state(authorization_url: str) -> str:
    """The ``state`` of a consent page address: what names one login to its host."""
    states = parse_qs(urlsplit(authorization_url).query).get("state", [])
    if len(states) != 1 or not states[0]:
        raise LoginError("the consent page address carries no state")
    return states[0]


class _Returned(BaseModel):
    """The query of the address a browser came back to."""

    model_config = ConfigDict(strict=True, frozen=True, extra="ignore")

    code: str | None = None
    state: str | None = None
    iss: str | None = None
    error: str | None = None
    error_description: str | None = None


def _returned(url: str) -> _Returned:
    query = {name: values[0] for name, values in parse_qs(urlsplit(url).query).items()}
    returned = _Returned.model_validate(query)
    if returned.error is not None:
        detail = f": {returned.error_description}" if returned.error_description else ""
        raise LoginError(f"the service refused the login ({returned.error}{detail})")
    if not returned.code:
        raise LoginError("the service sent the browser back without a code")
    return returned


def needs_refresh(
    credential: OAuthCredential,
    *,
    now: float | None = None,
    margin: float = REFRESH_MARGIN_SECONDS,
) -> bool:
    """Whether a stored login's access token has expired or is about to."""
    if credential.expires_at is None:
        return False
    return credential.expires_at - (time.time() if now is None else now) < margin


async def fresh(
    name: str,
    credential: OAuthCredential,
    *,
    margin: float = REFRESH_MARGIN_SECONDS,
    addresses: AddressPolicy | None = None,
) -> OAuthCredential:
    """``credential`` with an access token that is valid now.

    Returns the same object when its token still has more than ``margin``
    seconds left. Otherwise it asks the service for new tokens with the
    refresh token; the caller stores the result, because a service may also
    replace the refresh token. A host whose steps cannot ask for a newer token
    while they run passes a larger ``margin``.

    Raises :class:`AccountLoginExpiredError` when the service refuses the
    refresh or there is no refresh token, and
    :class:`AccountUnavailableError` when the service could not be asked.
    """
    if not needs_refresh(credential, margin=margin):
        return credential
    if credential.refresh_token is None:
        raise AccountLoginExpiredError(
            name, "it has expired and the service gave no way to renew it"
        )
    import_optional("authlib", extra="integrations", feature="OAuth integration")
    import httpx
    from authlib.integrations.base_client import OAuthError
    from authlib.integrations.httpx_client import AsyncOAuth2Client

    extra = {} if credential.resource is None else {"resource": credential.resource}
    try:
        async with AsyncOAuth2Client(
            client_id=credential.client_id,
            client_secret=credential.client_secret,
            token_endpoint_auth_method=credential.client_auth,
            timeout=LOGIN_REQUEST_TIMEOUT_SECONDS,
            transport=_transport(AsyncOAuth2Client, addresses),
        ) as client:
            token = await client.refresh_token(
                credential.token_url, refresh_token=credential.refresh_token, **extra
            )
    except AddressRefusedError as refused:
        # Waiting cannot help: the login's token address is one the host does not call.
        raise AccountLoginExpiredError(name, str(refused)) from None
    except OAuthError as refused:
        # The service's own error code, such as invalid_grant: the login was
        # revoked, or the refresh token expired.
        raise AccountLoginExpiredError(
            name, f"the service refused to renew it: {refused.error}"
        ) from None
    except (httpx.HTTPError, ValueError) as failed:
        # An answer that is not the standard token document counts as an outage.
        raise AccountUnavailableError(name, type(failed).__name__) from None
    try:
        renewed = _TokenDocument.model_validate(dict(token))
    except ValidationError:
        raise AccountUnavailableError(
            name, "the service answered without an access token"
        ) from None
    return credential.model_copy(
        update={
            "access_token": renewed.access_token,
            # A service that does not replace the refresh token leaves it out.
            "refresh_token": renewed.refresh_token or credential.refresh_token,
            "expires_at": _expires_at(renewed.expires_in),
            "scope": renewed.scope or credential.scope,
        }
    )


class _TokenDocument(BaseModel):
    """The fields used from a token answer (RFC 6749 section 5.1)."""

    model_config = ConfigDict(frozen=True, extra="ignore", hide_input_in_errors=True)

    access_token: str
    refresh_token: str | None = None
    expires_in: float | None = None
    scope: str | None = None


def _expires_at(expires_in: float | None) -> float | None:
    return None if expires_in is None else time.time() + expires_in


async def connector_login(
    declaration: OAuth2Declaration,
    *,
    client_id: str,
    client_secret: str | None,
    browser: LoginBrowser,
    addresses: AddressPolicy | None = None,
) -> OAuthCredential:
    """Log in to a Connector's service through the browser and return the tokens.

    The client ID and secret are those of the app registered with the service.
    Without a secret the client is a public one, protected by PKCE alone.
    """
    import_optional("authlib", extra="integrations", feature="OAuth integration")
    import httpx
    from authlib.common.security import generate_token
    from authlib.integrations.base_client import OAuthError
    from authlib.integrations.httpx_client import AsyncOAuth2Client

    client_auth: Literal["client_secret_basic", "client_secret_post", "none"] = (
        "none" if client_secret is None else f"client_secret_{declaration.client_auth}"
    )
    verifier = generate_token(64)
    try:
        async with AsyncOAuth2Client(
            client_id=client_id,
            client_secret=client_secret,
            scope=" ".join(declaration.scopes) or None,
            redirect_uri=browser.redirect_uri,
            code_challenge_method="S256",
            token_endpoint_auth_method=client_auth,
            timeout=LOGIN_REQUEST_TIMEOUT_SECONDS,
            transport=_transport(AsyncOAuth2Client, addresses),
        ) as client:
            authorization_url, state = client.create_authorization_url(
                declaration.authorize_url,
                code_verifier=verifier,
                **declaration.authorize_params,
            )
            await browser.open(authorization_url)
            returned_url = await browser.returned()
            _returned(returned_url)
            token = await client.fetch_token(
                declaration.token_url,
                authorization_response=returned_url,
                state=state,
                code_verifier=verifier,
            )
    except OAuthError as refused:
        detail = f": {refused.description}" if refused.description else ""
        raise LoginError(f"the service refused the login ({refused.error}{detail})") from None
    except httpx.HTTPError as failed:
        raise LoginError(
            f"the token address {declaration.token_url} could not be used: "
            f"{type(failed).__name__}"
        ) from None
    try:
        tokens = _TokenDocument.model_validate(dict(token))
    except ValidationError:
        raise LoginError("the service answered the login without an access token") from None
    return OAuthCredential(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_at=_expires_at(tokens.expires_in),
        scope=tokens.scope,
        token_url=declaration.token_url,
        client_id=client_id,
        client_secret=client_secret,
        client_auth=client_auth,
        resource=None,
    )


Login = Callable[[LoginBrowser], Awaitable[OAuthCredential]]


def declared_login(
    declaration: IntegrationDeclaration,
    *,
    client_id: str | None,
    client_secret: str | None,
    addresses: AddressPolicy | None = None,
) -> Login:
    """The browser login a declaration asks for, ready for a host to run.

    A Connector's login takes the client ID, and the secret if it has one, of
    the app registered with its service. An MCP server's client comes from the
    server or from its declaration; only that client's secret is given here.
    Raises :class:`~.connect.IntegrationConnectError` when the declaration has
    no browser login or the client details do not fit it.
    """
    name = declaration.name
    match declaration:
        case ConnectorDeclaration(auth=OAuth2Declaration() as auth):
            if client_id is None:
                raise IntegrationConnectError(
                    f"Connector {name!r} logs in through the browser with the app "
                    "you registered at the service: give its client ID.",
                    "client_id_required",
                )
            app_id = client_id
            return lambda browser: connector_login(
                auth,
                client_id=app_id,
                client_secret=client_secret,
                browser=browser,
                addresses=addresses,
            )
        case MCPDeclaration(url=str() as url, client_id=declared_client):
            if client_id is not None:
                raise IntegrationConnectError(
                    f"MCP server {name!r} takes its client ID from its declaration, "
                    "ava.MCP(..., client_id=...), or registers one by itself.",
                    "client_id_not_accepted",
                )
            if client_secret is not None and declared_client is None:
                raise IntegrationConnectError(
                    f"MCP server {name!r} declares no client_id, so a client secret "
                    "has no client to belong to.",
                    "client_secret_not_accepted",
                )
            return lambda browser: mcp_login(
                url,
                client_id=declared_client,
                client_secret=client_secret,
                browser=browser,
                addresses=addresses,
            )
        case ConnectorDeclaration():
            raise IntegrationConnectError(
                f"Connector {name!r} does not declare a browser login (ava.OAuth2).",
                "no_browser_login",
            )
        case _:
            raise IntegrationConnectError(
                f"MCP server {name!r} is a local process, which has no login.",
                "no_browser_login",
            )


class _LoginMemory:
    """What the MCP library stores during one login: the tokens and the client."""

    def __init__(self, client: OAuthClientInformationFull | None) -> None:
        self.tokens: OAuthToken | None = None
        self.client = client

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client = client_info


async def mcp_login(
    url: str,
    *,
    client_id: str | None,
    client_secret: str | None,
    browser: LoginBrowser,
    addresses: AddressPolicy | None = None,
) -> OAuthCredential:
    """Log in to a remote MCP server through the browser and return the tokens.

    The login is discovered from the server as the MCP standard defines. With
    no ``client_id`` the server's login service registers a client by itself;
    a server that does not allow that takes the ID of a client registered
    with it beforehand, and that client's secret when it has one.
    """
    import_optional("mcp", extra="integrations", feature="MCP integration")
    import httpx2
    from mcp import Client
    from mcp.client.auth import OAuthClientProvider, OAuthFlowError, OAuthTokenError
    from mcp.client.auth.exceptions import OAuthRegistrationError
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared.auth import (
        AuthorizationCodeResult,
        OAuthClientInformationFull,
        OAuthClientMetadata,
    )
    from pydantic import AnyUrl

    redirect_uris = [AnyUrl(browser.redirect_uri)]
    memory = _LoginMemory(
        None
        if client_id is None
        else OAuthClientInformationFull(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uris=redirect_uris,
            token_endpoint_auth_method=(
                "none" if client_secret is None else "client_secret_post"
            ),
        )
    )

    async def returned() -> AuthorizationCodeResult:
        answer = _returned(await browser.returned())
        assert answer.code is not None
        return AuthorizationCodeResult(code=answer.code, state=answer.state, iss=answer.iss)

    provider = OAuthClientProvider(
        server_url=url,
        client_metadata=OAuthClientMetadata(
            client_name=CLIENT_NAME,
            redirect_uris=redirect_uris,
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            # A program on the person's machine cannot keep a secret; PKCE protects it.
            token_endpoint_auth_method="none",
        ),
        storage=memory,
        redirect_handler=browser.open,
        callback_handler=returned,
    )
    try:
        async with httpx2.AsyncClient(
            auth=provider,
            timeout=httpx2.Timeout(LOGIN_REQUEST_TIMEOUT_SECONDS),
            transport=_transport(httpx2.AsyncClient, addresses),
        ) as http:
            # Opening an MCP session makes the server ask for the login, and
            # shows that it accepts the tokens the login returned.
            async with Client(streamable_http_client(url, http_client=http)):
                pass
    except BaseException as failed:
        cause = _first_cause(failed)
        if isinstance(cause, LoginError):
            raise cause from None
        if isinstance(cause, OAuthRegistrationError):
            raise LoginError(
                "the server's login service did not register a client by itself "
                f"({cause}). Register one with the service and give its ID in the "
                "declaration: ava.MCP(..., client_id=...)"
            ) from None
        if isinstance(cause, OAuthFlowError | OAuthTokenError | httpx2.HTTPError):
            raise LoginError(f"the login at {url} failed: {_described(cause)}") from None
        raise
    tokens, client = memory.tokens, memory.client
    if tokens is None or client is None or client.client_id is None:
        raise LoginError(
            f"the MCP server at {url} did not ask for a login, so it needs no account"
        )
    context = provider.context
    metadata = context.oauth_metadata
    token_url = (
        str(metadata.token_endpoint)
        if metadata is not None and metadata.token_endpoint is not None
        # The MCP standard's address for a server that publishes no metadata.
        else urljoin(context.get_authorization_base_url(url), "/token")
    )
    match client.token_endpoint_auth_method:
        case "client_secret_basic" | "client_secret_post" as client_auth:
            pass
        case _:
            client_auth = "none"
    return OAuthCredential(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_at=_expires_at(tokens.expires_in),
        scope=tokens.scope,
        token_url=token_url,
        client_id=client.client_id,
        client_secret=client.client_secret if client_auth != "none" else None,
        client_auth=client_auth,
        resource=(
            context.get_resource_url()
            if context.should_include_resource_param(context.protocol_version)
            else None
        ),
    )


def _first_cause(exc: BaseException) -> BaseException:
    """The first error inside the task-group wrappers a failed connection raises."""
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc


def _described(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


__all__ = [
    "LOGIN_REQUEST_TIMEOUT_SECONDS",
    "AddressPolicy",
    "AddressRefusedError",
    "REFRESH_MARGIN_SECONDS",
    "Login",
    "LoginBrowser",
    "LoginError",
    "connector_login",
    "declared_login",
    "fresh",
    "login_state",
    "mcp_login",
    "needs_refresh",
]
