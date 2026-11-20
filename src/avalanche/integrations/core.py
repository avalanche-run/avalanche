"""Integrations: the outside services a step declares it uses.

A step lists its integrations with ``integrations=[...]``. A member is a
:class:`Connector` instance, whose ``@ava.tool`` methods call an HTTP API
through a client that is already logged in, or an :class:`MCP` server
declaration. Declaring, importing and listing integrations never calls a
network and never reads an account; the account of each integration is looked
up when a step that declares it starts.
"""

from __future__ import annotations

import inspect
import re
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, ClassVar, Literal, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic_core import to_jsonable_python

from ..policies import NonRetryableError
from .accounts import (
    AccessTokenCredential,
    AccountLoginExpiredError,
    AccountSource,
    ApiKeyCredential,
    Credential,
    current_account_source,
)
from .declaration import (
    ApiKeyDeclaration,
    CatalogDeclaration,
    ConnectorDeclaration,
    IntegrationDeclaration,
    IntegrationToolDeclaration,
    MCPDeclaration,
    OAuth2Declaration,
    StepIntegrations,
)

if TYPE_CHECKING:
    from .client import Client

# Lowercase words joined by single "-" or "_": safe in a command line, a URL
# and, once "-" is written "_", a tool name on every agent runtime.
_NAME = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
_HEADER = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+")
_TOOL_MARKER = "__ava_tool__"
# A running step asks its account source for a new access token when the one
# it holds has less than this left.
TOKEN_RENEW_SECONDS = 30.0


class IntegrationError(NonRetryableError, ValueError):
    """An invalid integration declaration, or a Connector used outside its step.

    The cause is in the workflow's code or setup, so a step that raises it is
    not retried.
    """


class IntegrationNotConnectedError(NonRetryableError):
    """A step declares an integration that has no account on this host.

    Raised before the step's own code runs. Retrying cannot help, so the step
    fails at once.
    """

    def __init__(self, step_name: str, names: Sequence[str], connect_command: str) -> None:
        self.step_name = step_name
        self.names = tuple(names)
        commands = " and ".join(f"`{connect_command} {name}`" for name in self.names)
        subject = "integration" if len(self.names) == 1 else "integrations"
        listed = ", ".join(repr(name) for name in self.names)
        super().__init__(
            f"step {step_name!r} uses {subject} {listed} with no connected account. "
            f"Connect with {commands}, then start the run again."
        )


class IntegrationReconnectError(NonRetryableError):
    """An integration's browser login no longer works: it expired or was revoked.

    Raised before the step's own code when the login cannot be refreshed, and
    by a call the service answers with "not logged in". Only connecting again
    can help, so the step is not retried.
    """

    def __init__(self, step_name: str, name: str, connect_command: str, reason: str) -> None:
        self.step_name = step_name
        self.name = name
        super().__init__(
            f"step {step_name!r}: the login of integration {name!r} is no longer valid "
            f"({reason}). Connect it again with `{connect_command} {name}`, then start "
            "the run again."
        )


class MCPServerUnavailableError(RuntimeError):
    """An MCP server could not be reached, or did not answer in time, at step start.

    The cause may pass, so the step's retries apply.
    """


class MCPToolError(RuntimeError):
    """A call to an MCP server's tool failed, or its arguments were refused.

    The agent's runtime reports it to the model as a failed tool call, like an
    exception from any other tool.
    """


class ApiKey(BaseModel):
    """Login with an API key sent in one request header.

    ``ava.ApiKey(header="X-Api-Key")`` sends the key as is;
    ``ava.ApiKey(header="Authorization", prefix="Bearer ")`` sends it after a
    prefix. The key itself is never in code: connect it with ``ava connect``.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    header: str = Field(pattern=_HEADER.pattern)
    prefix: str = ""

    def declaration(self) -> ApiKeyDeclaration:
        return ApiKeyDeclaration(header=self.header, prefix=self.prefix)


class OAuth2(BaseModel):
    """Login through the browser, with the OAuth 2 authorization code flow.

    ``ava.OAuth2(authorize_url=..., token_url=..., scopes=[...])`` names the
    service's consent page and its token address. The client ID and secret of
    the app you registered with the service are never in code: ``ava connect``
    asks for them. Requests then carry the access token as
    ``Authorization: Bearer``, and an expired token is refreshed by itself.

    ``authorize_params`` adds query parameters to the consent page, for a
    service that needs one to hand out a refresh token (Google wants
    ``{"access_type": "offline", "prompt": "consent"}``). ``client_auth`` says
    where the service reads the client secret in a token request: ``"basic"``,
    the Authorization header, or ``"post"``, the form body.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    authorize_url: str = Field(pattern=r"^https?://")
    token_url: str = Field(pattern=r"^https?://")
    scopes: tuple[str, ...] = ()
    authorize_params: Mapping[str, str] = Field(default_factory=dict)
    client_auth: Literal["basic", "post"] = "basic"

    def __init__(
        self,
        *,
        authorize_url: str,
        token_url: str,
        scopes: Sequence[str] = (),
        authorize_params: Mapping[str, str] | None = None,
        client_auth: Literal["basic", "post"] = "basic",
    ) -> None:
        if isinstance(scopes, str):
            raise IntegrationError("ava.OAuth2 scopes must be a list of strings")
        super().__init__(
            authorize_url=authorize_url,
            token_url=token_url,
            scopes=tuple(scopes),
            authorize_params=dict(authorize_params or {}),
            client_auth=client_auth,
        )

    def declaration(self) -> OAuth2Declaration:
        return OAuth2Declaration(
            authorize_url=self.authorize_url,
            token_url=self.token_url,
            scopes=self.scopes,
            authorize_params=dict(self.authorize_params),
            client_auth=self.client_auth,
        )


def tool[F: Callable[..., object]](method: F) -> F:
    """Mark a Connector method as a tool.

    An agent step that declares the Connector offers the method to its agent as
    ``<integration>__<method>``; a plain step calls it as a normal method. The
    method's docstring is the tool's description and its parameters need type
    annotations.
    """
    if not inspect.isfunction(method):
        raise IntegrationError("@ava.tool decorates a method of an ava.Connector subclass")
    setattr(method, _TOOL_MARKER, True)
    return method


def _checked_name(owner: str, name: object) -> str:
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise IntegrationError(
            f"{owner} name must be lowercase letters and digits joined by single '-' or "
            f"'_', such as 'acme-crm'; got {name!r}"
        )
    return name


def _checked_label(owner: str, field: str, value: object) -> str | None:
    if value is not None and (not isinstance(value, str) or not value):
        raise IntegrationError(f"{owner} {field} must be a non-empty string")
    return value


class Integration:
    """What every integration has: a ``name`` and an optional label and icon.

    ``name`` is also the name of its account: one account is stored per
    integration name, per host. ``icon`` is the path of an image file in the
    project.
    """

    name: str
    display_name: str | None = None
    icon: str | None = None

    @property
    def tool_prefix(self) -> str:
        """The integration's part of ``<integration>__<tool>``: its name, ``-`` as ``_``."""
        return self.name.replace("-", "_")

    @property
    def requires_account(self) -> bool:
        raise NotImplementedError

    def declaration(self) -> IntegrationDeclaration:
        raise NotImplementedError


class Connector(Integration):
    """An outside HTTP service, described by a subclass.

    A subclass sets ``base_url`` and ``auth`` as class attributes and marks its
    tools with ``@ava.tool``::

        class AcmeCrm(ava.Connector):
            name = "acme-crm"
            auth = ava.ApiKey(header="X-Api-Key")
            base_url = "https://api.acme.com"

            @ava.tool
            def find_customer(self, email: str) -> Customer:
                return self.client.get("/customers", params={"email": email}).json()

    Each instance is one account, named by its ``name``: ``AcmeCrm()`` uses the
    class's name and ``AcmeCrm("acme-crm-eu")`` is a second, separate account.
    Building an instance stores nothing and calls nothing.
    """

    auth: ClassVar[ApiKey | OAuth2 | None] = None
    base_url: ClassVar[str]
    _tool_methods: ClassVar[tuple[str, ...]] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        names = []
        for attribute in dir(cls):
            if attribute.startswith("__"):
                continue
            member = inspect.getattr_static(cls, attribute)
            if getattr(member, _TOOL_MARKER, False):
                names.append(attribute)
        cls._tool_methods = tuple(sorted(names))

    def __init__(
        self,
        name: str | None = None,
        *,
        display_name: str | None = None,
        icon: str | None = None,
    ) -> None:
        owner = type(self).__name__
        if type(self) is Connector:
            raise IntegrationError("ava.Connector is a base class; subclass it")
        declared_name = name if name is not None else getattr(type(self), "name", None)
        if declared_name is None:
            raise IntegrationError(
                f"{owner} has no name: set a `name` class attribute or pass one, "
                f"such as {owner}('acme-crm')"
            )
        self.name = _checked_name(owner, declared_name)
        self.display_name = _checked_label(
            owner, "display_name", display_name or type(self).display_name
        )
        self.icon = _checked_label(owner, "icon", icon or type(self).icon)
        base_url = getattr(type(self), "base_url", None)
        if not isinstance(base_url, str) or not base_url.startswith(("https://", "http://")):
            raise IntegrationError(
                f"{owner}.base_url must be an http(s) URL such as 'https://api.acme.com'"
            )
        if type(self).auth is not None and not isinstance(type(self).auth, ApiKey | OAuth2):
            raise IntegrationError(
                f"{owner}.auth must be ava.ApiKey(...), ava.OAuth2(...) or None"
            )

    @property
    def requires_account(self) -> bool:
        return type(self).auth is not None

    @property
    def client(self) -> Client:
        """The HTTP client for this Connector's account, already logged in.

        Available only while a step that declares this Connector is running.
        """
        accounts = _STEP_ACCOUNTS.get()
        if accounts is None:
            raise IntegrationError(
                f"{type(self).__name__}.client is only available inside a running step "
                f"that declares the Connector: add it to that step's integrations=[...]"
            )
        return accounts.client(self)

    def tool_names(self) -> dict[str, str]:
        """Each tool's agent-facing name, mapped to the method that implements it."""
        return {f"{self.tool_prefix}__{method}": method for method in self._tool_methods}

    def declaration(self) -> ConnectorDeclaration:
        auth = type(self).auth
        return ConnectorDeclaration(
            name=self.name,
            display_name=self.display_name,
            icon=self.icon,
            base_url=type(self).base_url,
            auth=None if auth is None else auth.declaration(),
            tools=tuple(
                IntegrationToolDeclaration(
                    name=tool_name,
                    method=method,
                    description=inspect.getdoc(getattr(type(self), method)) or "",
                )
                for tool_name, method in self.tool_names().items()
            ),
        )


class Catalog(Integration):
    """A provider-hosted built-in, available to agent steps only.

    Display names and icons belong to the provider. Constructing a declaration
    does not discover, import, or contact one.
    """

    def __init__(
        self,
        name: str,
        *,
        allow: Sequence[str] | None = None,
        connection: str | None = None,
    ) -> None:
        self.name = _checked_name("ava.Catalog", name)
        self.allow = None if allow is None else _string_tuple(name, "allow", allow)
        self.connection = (
            None if connection is None else _checked_name("ava.Catalog connection", connection)
        )

    def __call__(
        self, *, connection: str | None = None, allow: Sequence[str] | None = None
    ) -> Catalog:
        return Catalog(
            self.name,
            connection=self.connection if connection is None else connection,
            allow=self.allow if allow is None else allow,
        )

    @property
    def tool_prefix(self) -> str:
        return (self.name if self.connection is None else self.connection).replace("-", "_")

    @property
    def requires_account(self) -> bool:
        # Catalog accounts belong to the provider, not the host's account store.
        return False

    def declaration(self) -> CatalogDeclaration:
        return CatalogDeclaration(name=self.name, allow=self.allow, connection=self.connection)


class MCP(Integration):
    """An MCP server a step's agent may use: a remote ``url`` or a local ``command``.

    ``allow`` lists the server's tools the agent may call; without it the agent
    gets every tool the server offers. MCP tools are for agent steps only.

    A remote server that wants a login is connected with ``ava connect``: the
    login is discovered from the server and done in the browser. ``client_id``
    is for a server that does not let a client register itself: the ID of the
    client you registered with it.

    Declaring one connects to nothing. When a step that declares it starts,
    Avalanche connects to the ``url`` over HTTP, or starts the ``command`` and
    talks to it over its standard input and output, and offers the server's
    tools to the agent as ``<integration>__<tool>``. The connection is closed,
    and the process stopped, when the step ends.
    """

    def __init__(
        self,
        name: str,
        *,
        url: str | None = None,
        command: Sequence[str] | None = None,
        allow: Sequence[str] | None = None,
        client_id: str | None = None,
        display_name: str | None = None,
        icon: str | None = None,
    ) -> None:
        self.name = _checked_name("ava.MCP", name)
        self.display_name = _checked_label("ava.MCP", "display_name", display_name)
        self.icon = _checked_label("ava.MCP", "icon", icon)
        if (url is None) == (command is None):
            raise IntegrationError(
                f"ava.MCP {name!r} takes exactly one of url= (a remote server) "
                "or command= (a local process)"
            )
        if url is not None and (
            not isinstance(url, str) or not url.startswith(("https://", "http://"))
        ):
            raise IntegrationError(f"ava.MCP {name!r} url must be an http(s) URL")
        self.url = url
        self.command = None if command is None else _string_tuple(name, "command", command)
        if self.command is not None and not self.command:
            raise IntegrationError(f"ava.MCP {name!r} command must name a program")
        self.allow = None if allow is None else _string_tuple(name, "allow", allow)
        if client_id is not None and (not isinstance(client_id, str) or not client_id):
            raise IntegrationError(f"ava.MCP {name!r} client_id must be a non-empty string")
        if client_id is not None and url is None:
            raise IntegrationError(
                f"ava.MCP {name!r} client_id is for a remote server's browser login; "
                "a command= server has none"
            )
        self.client_id = client_id

    @property
    def requires_account(self) -> bool:
        # Whether a server needs a login is learned from the server when a step
        # connects to it, so a missing account is not known to be an error here.
        return False

    @property
    def accepts_account(self) -> bool:
        """Whether a stored account is sent to the server: only a remote one takes a header."""
        return self.url is not None

    def declaration(self) -> MCPDeclaration:
        return MCPDeclaration(
            name=self.name,
            display_name=self.display_name,
            icon=self.icon,
            url=self.url,
            command=self.command,
            allow=self.allow,
            client_id=self.client_id,
        )


def _string_tuple(name: str, field: str, values: object) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Sequence):
        raise IntegrationError(f"ava.MCP {name!r} {field} must be a list of strings")
    if any(not isinstance(value, str) or not value for value in values):
        raise IntegrationError(f"ava.MCP {name!r} {field} must be a list of strings")
    return tuple(values)


def step_integrations(
    owner: str,
    integrations: object,
    *,
    agent: bool,
    tools: Sequence[Callable[..., object]] = (),
) -> tuple[Integration, ...]:
    """Validate one step's ``integrations=`` when the step is defined.

    Every tool an agent step offers needs its own name: two integrations whose
    tool names collide, or an integration tool named like one of the step's
    ``tools=``, is an error here rather than a surprise in a run.
    """
    if isinstance(integrations, str) or not isinstance(integrations, Sequence):
        raise TypeError(f"{owner} integrations must be a sequence")
    for integration in integrations:
        if isinstance(integration, type) and issubclass(integration, Integration):
            raise IntegrationError(
                f"{owner} integrations takes instances: write {integration.__name__}(), "
                f"not {integration.__name__}"
            )
        if not isinstance(integration, Integration):
            raise TypeError(
                f"{owner} integrations must be ava.Connector, ava.Catalog or ava.MCP "
                f"instances, not {type(integration).__name__}"
            )
        if isinstance(integration, MCP | Catalog) and not agent:
            raise IntegrationError(
                f"{owner} cannot use MCP server {integration.name!r}: MCP tools are for "
                "agent steps only; declare it on an ava.agent_step"
            )
    connections_by_name: dict[str, list[str | None]] = {}
    for integration in integrations:
        connection = integration.connection if isinstance(integration, Catalog) else None
        connections_by_name.setdefault(integration.name, []).append(connection)
    for name, connections in connections_by_name.items():
        if len(connections) > 1 and None in connections and any(
            connection is not None for connection in connections
        ):
            raise IntegrationError(
                f"{owner} mixes bare and connection-bound declarations of {name!r}; "
                "set connection= on every declaration of this integration"
            )
        if len(connections) != len(set(connections)):
            raise IntegrationError(
                f"{owner} declares several integrations named {[name]} "
                f"with the same connection {connections}; declare each only once"
            )
    prefixes: dict[str, str] = {}
    for integration in integrations:
        other = prefixes.get(integration.tool_prefix)
        if other is not None:
            raise IntegrationError(
                f"{owner} integrations {other!r} and {integration.name!r} give their "
                f"tools the same names ({integration.tool_prefix}__...); rename one"
            )
        prefixes[integration.tool_prefix] = integration.name
    own_tools = {str(getattr(tool, "__name__", type(tool).__name__)) for tool in tools}
    clashes = sorted(
        tool_name
        for integration in integrations
        if isinstance(integration, Connector)
        for tool_name in integration.tool_names()
        if tool_name in own_tools
    )
    if clashes:
        raise IntegrationError(
            f"{owner} has tools= and integration tools with the same names {clashes}; "
            "rename the step's own tools"
        )
    return tuple(integrations)


def check_workflow_integrations(
    workflow_name: str, steps: Mapping[str, Sequence[Integration]]
) -> None:
    """Refuse one integration name declared in two different ways in a workflow.

    A name is one account. Two steps may share an integration, but two
    different declarations under one name would silently share its account.
    """
    seen: dict[tuple[str, str | None], tuple[str, IntegrationDeclaration]] = {}
    for step_name, integrations in steps.items():
        for integration in integrations:
            declaration = integration.declaration()
            connection = integration.connection if isinstance(integration, Catalog) else None
            first_step, first = seen.setdefault(
                (integration.name, connection), (step_name, declaration)
            )
            if first != declaration:
                raise IntegrationError(
                    f"workflow {workflow_name!r} declares integration {integration.name!r} "
                    f"in two different ways, in steps {first_step!r} and {step_name!r}. "
                    "One name is one account: reuse one instance, or give each its own name"
                )


def step_integration_declarations(integrations: Sequence[Integration]) -> StepIntegrations:
    """Describe a step's integrations statically, for the workflow definition."""
    return StepIntegrations(
        integrations=tuple(integration.declaration() for integration in integrations)
    )


class StepAccount:
    """One integration's account while a step runs.

    A browser login is handed to the step as an access token. When that token
    is about to expire, the account is read again from the source, which
    refreshes it, so a step that outlives its token keeps working.
    """

    def __init__(
        self, step_name: str, name: str, credential: Credential, source: AccountSource
    ) -> None:
        self._step_name = step_name
        self.name = name
        self._credential = credential
        self._source = source
        self._lock = threading.Lock()

    def current(self) -> Credential:
        """The account's credential, valid now."""
        with self._lock:
            credential = self._credential
            if (
                isinstance(credential, AccessTokenCredential)
                and credential.expires_at is not None
                and credential.expires_at - time.time() < TOKEN_RENEW_SECONDS
            ):
                credential = self._credential = self._read_again()
            return credential

    def refused(self, status: int) -> IntegrationReconnectError:
        """The error for a service that answered "not logged in" to a browser login."""
        return IntegrationReconnectError(
            self._step_name,
            self.name,
            self._source.connect_command,
            f"the service refused it with HTTP {status}",
        )

    def _read_again(self) -> Credential:
        found = read_accounts(self._step_name, self._source, (self.name,))
        if self.name not in found:
            raise IntegrationNotConnectedError(
                self._step_name, [self.name], self._source.connect_command
            )
        return found[self.name]


def read_accounts(
    step_name: str, source: AccountSource, names: tuple[str, ...]
) -> Mapping[str, Credential]:
    """Ask the source for accounts; a login that cannot be refreshed fails the step."""
    try:
        return source.accounts(names)
    except AccountLoginExpiredError as expired:
        raise IntegrationReconnectError(
            step_name, expired.name, source.connect_command, expired.reason
        ) from None


class StepAccounts:
    """The accounts of one running step, and the clients opened on them."""

    def __init__(
        self,
        step_name: str,
        integrations: Sequence[Integration],
        credentials: Mapping[str, Credential],
        *,
        source: AccountSource | None = None,
    ) -> None:
        self.step_name = step_name
        # The host's connect command; ``None`` when nothing here stores accounts.
        self.connect_command = None if source is None else source.connect_command
        self._integrations = {integration.name: integration for integration in integrations}
        self._accounts = (
            {}
            if source is None
            else {
                name: StepAccount(step_name, name, credential, source)
                for name, credential in credentials.items()
            }
        )
        self._clients: dict[str, Client] = {}
        self._lock = threading.Lock()
        self._closed = False
        for integration in integrations:
            if isinstance(integration, Connector) and integration.name in self._accounts:
                # A stored account of the wrong kind fails the step before its code.
                _connector_headers(integration, self._accounts[integration.name])

    def client(self, connector: Connector) -> Client:
        declared = self._integrations.get(connector.name)
        if type(declared) is not type(connector):
            raise IntegrationError(
                f"step {self.step_name!r} does not declare {type(connector).__name__} "
                f"{connector.name!r}: add it to the step's integrations=[...]"
            )
        with self._lock:
            if self._closed:
                raise IntegrationError(
                    f"step {self.step_name!r} has ended; its {connector.name!r} client "
                    "is closed"
                )
            client = self._clients.get(connector.name)
            if client is None:
                from .client import Client

                auth = type(connector).auth
                account = None if auth is None else self._accounts[connector.name]
                client = Client(
                    base_url=type(connector).base_url,
                    headers=(
                        dict
                        if account is None
                        else lambda: _connector_headers(connector, account)
                    ),
                    # "Not logged in" means the login is gone only for a browser
                    # login; a service may answer an API key with 401 for other causes.
                    login_refused=account.refused if isinstance(auth, OAuth2) else None,
                )
                self._clients[connector.name] = client
            return client

    def account(self, name: str) -> StepAccount | None:
        """The stored account of one declared integration, if it has one."""
        return self._accounts.get(name)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            clients, self._clients = list(self._clients.values()), {}
        for client in clients:
            client.close()


def _connector_headers(connector: Connector, account: StepAccount) -> dict[str, str]:
    """The request headers that carry a Connector's account, read as a request leaves."""
    auth = type(connector).auth
    credential = account.current()
    match auth, credential:
        case ApiKey(), ApiKeyCredential(key=key):
            return {auth.header: f"{auth.prefix}{key}"}
        case OAuth2(), AccessTokenCredential(token=token):
            return {"Authorization": f"Bearer {token}"}
    declared = "a browser login" if isinstance(auth, OAuth2) else "an API key"
    raise IntegrationError(
        f"{type(connector).__name__} {connector.name!r} declares {declared}, but its "
        "stored account is of another kind. Connect it again"
    )


_STEP_ACCOUNTS: ContextVar[StepAccounts | None] = ContextVar(
    "avalanche_integration_step_accounts", default=None
)


@contextmanager
def bound_integrations(
    step_name: str, integrations: Sequence[Integration]
) -> Iterator[StepAccounts]:
    """Look up the step's accounts, then make them reachable for the block.

    Called before the step's own code. A declared integration that needs an
    account and has none raises :class:`IntegrationNotConnectedError` here, so
    the step fails before it starts.
    """
    needed = tuple(
        integration.name for integration in integrations if integration.requires_account
    )
    # A remote MCP server may or may not want a login: its account is sent when
    # there is one, and the server's answer says whether one was required.
    optional = tuple(
        integration.name
        for integration in integrations
        if isinstance(integration, MCP) and integration.accepts_account
    )
    source = current_account_source()
    if needed and source is None:
        raise IntegrationError(_no_account_store(step_name, needed))
    credentials: Mapping[str, Credential] = {}
    if source is not None and (needed or optional):
        credentials = read_accounts(step_name, source, (*needed, *optional))
        missing = [name for name in needed if name not in credentials]
        if missing:
            raise IntegrationNotConnectedError(step_name, missing, source.connect_command)
    accounts = StepAccounts(step_name, integrations, credentials, source=source)
    token = _STEP_ACCOUNTS.set(accounts)
    try:
        yield accounts
    finally:
        _STEP_ACCOUNTS.reset(token)
        accounts.close()


def _no_account_store(step_name: str, names: Sequence[str]) -> str:
    return (
        f"step {step_name!r} declares integrations {list(names)}, but nothing here "
        "stores accounts. Run the workflow under `ava operator` or `ava dev` and "
        "connect each one with `ava connect <name>`; a program that runs workflows "
        "itself wraps the run in avalanche.integrations.use_accounts(...)"
    )


def with_integrations[F: Callable[..., object]](
    fn: F, step_name: str, integrations: Sequence[Integration]
) -> F:
    """Wrap a plain step so its accounts are bound around every execution."""
    if inspect.iscoroutinefunction(fn):

        @wraps(fn)
        async def async_wrapper(*args: object, **kwargs: object) -> object:
            with bound_integrations(step_name, integrations):
                return await fn(*args, **kwargs)

        return async_wrapper  # type: ignore[return-value]

    @wraps(fn)
    def wrapper(*args: object, **kwargs: object) -> object:
        with bound_integrations(step_name, integrations):
            return fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


@dataclass(frozen=True)
class IntegrationTool:
    """One integration tool as an agent runtime receives it.

    A Connector tool is described by its function's signature and
    ``input_schema`` is ``None``. An MCP tool has no Python signature: the
    server's JSON Schema describes its arguments, and ``function`` takes them
    as keyword arguments.
    """

    name: str
    description: str | None
    function: Callable[..., object]
    input_schema: Mapping[str, JsonValue] | None = None


@asynccontextmanager
async def agent_integration_tools(
    integrations: Sequence[Integration],
    accounts: StepAccounts,
    *,
    own_tools: Sequence[str] = (),
) -> AsyncIterator[tuple[IntegrationTool, ...]]:
    """The tools a running agent step gets from its integrations, for the block.

    Connector tools come first, then each MCP server's. Entering connects to
    the step's MCP servers and asks each for its tools, so a server that cannot
    be reached, wants a login it does not have, or lacks an allowed tool fails
    the step before its own code runs. Leaving closes every connection and
    stops every process it started, whatever ended the step. ``own_tools`` are
    the names of the step's ``tools=``; a server tool named like one of them,
    or like another integration's tool, is an error.
    """
    tools = list(_connector_tools(integrations, accounts))
    servers = [
        integration for integration in integrations if isinstance(integration, MCP | Catalog)
    ]
    if not servers:
        yield tuple(tools)
        return
    # The MCP library loads only for a step that declares a server.
    from .mcp_servers import MCPConnections

    connections = MCPConnections(accounts.step_name, servers, accounts)
    try:
        tools.extend(await connections.open())
        taken = [*own_tools, *(tool.name for tool in tools)]
        clashes = sorted({name for name in taken if taken.count(name) > 1})
        if clashes:
            raise IntegrationError(
                f"step {accounts.step_name!r} has several tools named {clashes}: an MCP "
                "server's tool is named like one of the step's tools= or like another "
                "integration's tool; rename the step's own tools or narrow the server's "
                "allow=[...]"
            )
        yield tuple(tools)
        connections.raise_if_unavailable()
    except Exception:
        # Agent runtimes may wrap a failed tool call in an adapter error. The
        # relay failure still owns retry policy and the actionable diagnosis.
        connections.raise_if_unavailable()
        raise
    finally:
        connections.close()


def _connector_tools(
    integrations: Sequence[Integration], accounts: StepAccounts
) -> Iterator[IntegrationTool]:
    """The Connector tools of a step as plain functions for an agent runtime.

    Each function is named ``<integration>__<tool>`` and takes the method's
    parameters. It returns the method's result as JSON data, so a returned
    model reaches every runtime's agent as an object. It carries the step's
    accounts itself, because a runtime may call a tool from a thread or task
    that does not inherit the step's context. The agent receives these
    functions, never a Connector or its client.
    """
    for integration in integrations:
        if not isinstance(integration, Connector):
            continue
        for tool_name, method_name in integration.tool_names().items():
            function = _agent_tool(getattr(integration, method_name), tool_name, accounts)
            yield IntegrationTool(
                name=tool_name, description=inspect.getdoc(function), function=function
            )


def _agent_tool(
    method: Callable[..., object], tool_name: str, accounts: StepAccounts
) -> Callable[..., object]:
    if inspect.iscoroutinefunction(method):

        async def call(*args: object, **kwargs: object) -> object:
            token = _STEP_ACCOUNTS.set(accounts)
            try:
                return to_jsonable_python(await method(*args, **kwargs))
            finally:
                _STEP_ACCOUNTS.reset(token)

    else:

        def call(*args: object, **kwargs: object) -> object:
            token = _STEP_ACCOUNTS.set(accounts)
            try:
                return to_jsonable_python(method(*args, **kwargs))
            finally:
                _STEP_ACCOUNTS.reset(token)

    # Runtimes read a tool's name, description and parameters from the function.
    hints = get_type_hints(method)
    signature = inspect.signature(method)
    call.__name__ = call.__qualname__ = tool_name
    call.__doc__ = inspect.getdoc(method)
    call.__module__ = method.__module__
    call.__annotations__ = dict(hints)
    call.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=[
            parameter.replace(annotation=hints.get(name, parameter.annotation))
            for name, parameter in signature.parameters.items()
        ],
        return_annotation=hints.get("return", signature.return_annotation),
    )
    return call


__all__ = [
    "MCP",
    "ApiKey",
    "Connector",
    "Catalog",
    "Integration",
    "IntegrationError",
    "IntegrationNotConnectedError",
    "IntegrationReconnectError",
    "IntegrationTool",
    "MCPServerUnavailableError",
    "MCPToolError",
    "OAuth2",
    "StepAccount",
    "StepAccounts",
    "agent_integration_tools",
    "bound_integrations",
    "check_workflow_integrations",
    "step_integration_declarations",
    "step_integrations",
    "tool",
    "with_integrations",
]
