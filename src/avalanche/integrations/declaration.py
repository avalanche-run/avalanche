"""Static description of the integrations a step declares, published by discovery.

A declaration says which outside services a step uses without importing a
client, calling a network or reading an account. Credentials never enter a
declaration: an account is stored per integration name by the host that runs
the workflow.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(
    strict=True,
    frozen=True,
    extra="forbid",
    json_schema_serialization_defaults_required=True,
)


class ApiKeyDeclaration(BaseModel):
    """A key sent in one request header, after an optional prefix such as ``Bearer ``."""

    model_config = _STRICT

    kind: Literal["api_key"] = "api_key"
    header: str
    prefix: str


class OAuth2Declaration(BaseModel):
    """A browser login (OAuth 2 authorization code): where to send the person and
    where to exchange the result. The client ID and secret are not here: they
    are given when the account is connected.
    """

    model_config = _STRICT

    kind: Literal["oauth2"] = "oauth2"
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    # Extra query parameters of the consent page, such as access_type=offline.
    authorize_params: dict[str, str]
    # Where the client secret goes in a token request: "basic" (the
    # Authorization header) or "post" (the form body).
    client_auth: Literal["basic", "post"]


ConnectorAuthDeclaration = Annotated[
    ApiKeyDeclaration | OAuth2Declaration, Field(discriminator="kind")
]


class IntegrationToolDeclaration(BaseModel):
    """One Connector tool: the name an agent calls it by and the method behind it."""

    model_config = _STRICT

    name: str
    method: str
    description: str


class ConnectorDeclaration(BaseModel):
    """A Connector instance; ``auth`` is ``None`` when the service needs no account."""

    model_config = _STRICT

    kind: Literal["connector"] = "connector"
    name: str
    display_name: str | None
    icon: str | None
    base_url: str
    auth: ConnectorAuthDeclaration | None
    tools: tuple[IntegrationToolDeclaration, ...]


class MCPDeclaration(BaseModel):
    """An MCP server: a remote ``url`` or a local ``command``, never both.

    ``allow`` is ``None`` when the agent may use every tool the server offers.
    ``client_id`` is the client a browser login uses with a server that does
    not register clients by itself; ``None`` lets the server register one.
    """

    model_config = _STRICT

    kind: Literal["mcp"] = "mcp"
    name: str
    display_name: str | None
    icon: str | None
    url: str | None
    command: tuple[str, ...] | None
    allow: tuple[str, ...] | None
    client_id: str | None = None


IntegrationDeclaration = Annotated[
    ConnectorDeclaration | MCPDeclaration, Field(discriminator="kind")
]


class StepIntegrations(BaseModel):
    """Every integration one step declares, in declaration order."""

    model_config = _STRICT

    integrations: tuple[IntegrationDeclaration, ...]


__all__ = [
    "ApiKeyDeclaration",
    "ConnectorAuthDeclaration",
    "ConnectorDeclaration",
    "IntegrationDeclaration",
    "IntegrationToolDeclaration",
    "MCPDeclaration",
    "OAuth2Declaration",
    "StepIntegrations",
]
