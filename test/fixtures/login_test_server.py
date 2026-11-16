"""A login service, an MCP server and a small HTTP API behind it, in one process.

``python login_test_server.py PORT JOURNAL REDIRECT_URI [--no-registration]``
serves, on the loopback address (or on the one ``LOGIN_TEST_SERVER_BIND`` names,
for a test whose clients reach it through a forwarded loopback port):

- an OAuth 2.1 authorization server built on the MCP library's own routes:
  metadata, client registration, ``/authorize``, ``/token`` with PKCE, and
  refresh tokens that are replaced on every use;
- an MCP server at ``/mcp`` that wants a token from that service;
- ``/crm/customers``, an HTTP API that wants one too.

The consent page approves at once: ``/authorize`` answers with the redirect a
real service sends after a person agrees. ``--no-registration`` refuses
clients that try to register themselves; the client ``PUBLIC_CLIENT_ID`` is
then the one to use. ``APP_CLIENT_ID`` with ``APP_CLIENT_SECRET`` is the app a
Connector's owner would have registered.

``/admin/lifetime?seconds=N`` sets how long new access tokens live,
``/admin/revoke`` ends every login, and ``/admin/revoke-refresh`` ends every
refresh token only. The journal gets one JSON line per event, with every
secret the service handed out, so a test can look for them where they must
not be.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time

import uvicorn
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import MCPServer
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

APP_CLIENT_ID = "acme-calendar-app"
APP_CLIENT_SECRET = "app-secret-4d1f8a02c7b94e63"
PUBLIC_CLIENT_ID = "avalanche-at-notes"

_PORT = int(sys.argv[1])
_JOURNAL = sys.argv[2]
_REDIRECT_URI = sys.argv[3]
_REGISTRATION = "--no-registration" not in sys.argv[4:]
_URL = f"http://127.0.0.1:{_PORT}"


def _journal(**entry: object) -> None:
    with open(_JOURNAL, "a") as journal:
        journal.write(json.dumps(entry) + "\n")


class _Logins:
    """The service's clients, codes and tokens, kept in memory."""

    def __init__(self) -> None:
        self.lifetime = 3600
        self.clients: dict[str, OAuthClientInformationFull] = {
            APP_CLIENT_ID: OAuthClientInformationFull(
                client_id=APP_CLIENT_ID,
                client_secret=APP_CLIENT_SECRET,
                redirect_uris=[AnyUrl(_REDIRECT_URI)],
                token_endpoint_auth_method="client_secret_post",
                grant_types=["authorization_code", "refresh_token"],
                scope="notes",
            ),
            PUBLIC_CLIENT_ID: OAuthClientInformationFull(
                client_id=PUBLIC_CLIENT_ID,
                redirect_uris=[AnyUrl(_REDIRECT_URI)],
                token_endpoint_auth_method="none",
                grant_types=["authorization_code", "refresh_token"],
                scope="notes",
            ),
        }
        self.codes: dict[str, AuthorizationCode] = {}
        self.access: dict[str, AccessToken] = {}
        self.refresh: dict[str, RefreshToken] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not _REGISTRATION:
            raise RegistrationError(
                error="invalid_client_metadata",
                error_description="this service does not register clients by itself",
            )
        assert client_info.client_id is not None
        self.clients[client_info.client_id] = client_info
        _journal(event="registered", client_id=client_info.client_id)

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        assert client.client_id is not None
        code = secrets.token_urlsafe(32)
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + 300,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        _journal(event="consent", client_id=client.client_id, code=code, state=params.state)
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # A code works once.
        del self.codes[authorization_code.code]
        return self._issue(
            "authorization_code",
            authorization_code.client_id,
            authorization_code.scopes,
            authorization_code.resource,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        found = self.refresh.get(refresh_token)
        if found is None:
            _journal(event="refresh refused", client_id=client.client_id)
        return found

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # The refresh token is replaced: the one just used never works again.
        if self.refresh.pop(refresh_token.token, None) is None:
            raise TokenError(error="invalid_grant", error_description="refresh token was used")
        return self._issue(
            "refresh_token", refresh_token.client_id, scopes, refresh_token.resource
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        found = self.access.get(token)
        if found is None or (found.expires_at is not None and found.expires_at <= time.time()):
            return None
        return found

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self.access.pop(token.token, None)
        self.refresh.pop(token.token, None)

    def _issue(
        self, grant: str, client_id: str, scopes: list[str], resource: str | None
    ) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.access[access] = AccessToken(
            token=access,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time()) + self.lifetime,
            resource=resource,
        )
        self.refresh[refresh] = RefreshToken(
            token=refresh, client_id=client_id, scopes=scopes, resource=resource
        )
        _journal(
            event="tokens",
            grant=grant,
            client_id=client_id,
            access_token=access,
            refresh_token=refresh,
            lifetime=self.lifetime,
        )
        return OAuthToken(
            access_token=access,
            expires_in=self.lifetime,
            scope=" ".join(scopes) or None,
            refresh_token=refresh,
        )


logins = _Logins()
server = MCPServer(
    "notes",
    auth_server_provider=logins,
    auth=AuthSettings(
        issuer_url=_URL,
        resource_server_url=f"{_URL}/mcp",
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=["notes"], default_scopes=["notes"]
        ),
        required_scopes=["notes"],
        validate_token_resource=False,
    ),
)


@server.tool()
def search(query: str) -> list[str]:
    """Search the notes and return the titles found."""
    _journal(event="call", tool="search", query=query)
    return [f"note about {query}"]


@server.custom_route("/crm/customers", methods=["GET"])
async def customers(request: Request) -> Response:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    known = await logins.load_access_token(token) if scheme == "Bearer" else None
    _journal(event="api", authorized=known is not None, access_token=token or None)
    if known is None:
        return JSONResponse({"error": "invalid_token"}, status_code=401)
    return JSONResponse({"email": request.query_params["email"], "region": "us"})


@server.custom_route("/admin/lifetime", methods=["POST"])
async def lifetime(request: Request) -> Response:
    logins.lifetime = int(request.query_params["seconds"])
    return Response(status_code=204)


@server.custom_route("/admin/revoke", methods=["POST"])
async def revoke(request: Request) -> Response:
    logins.access.clear()
    logins.refresh.clear()
    return Response(status_code=204)


@server.custom_route("/admin/revoke-refresh", methods=["POST"])
async def revoke_refresh(request: Request) -> Response:
    logins.refresh.clear()
    return Response(status_code=204)


if __name__ == "__main__":
    uvicorn.run(
        server.streamable_http_app(),
        host=os.environ.get("LOGIN_TEST_SERVER_BIND", "127.0.0.1"),
        port=_PORT,
        log_level="warning",
    )
