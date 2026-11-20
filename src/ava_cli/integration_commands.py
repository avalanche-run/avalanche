"""``ava connect``: list declared integrations and connect their accounts."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import webbrowser
from typing import Literal

from avalanche.integrations.accounts import ApiKeyCredential
from avalanche.integrations.declaration import (
    ApiKeyDeclaration,
    CatalogDeclaration,
    ConnectorDeclaration,
    MCPDeclaration,
    OAuth2Declaration,
)
from runtime.operator.client import GrpcStateProvider, OperatorCallError
from runtime.operator.integration_accounts import IntegrationStatus


def add_connect_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    command = subcommands.add_parser(
        "connect",
        help="connect an account for a declared integration, or list integrations",
        description=(
            "Without NAME, list the integrations the operator's workflows declare and "
            "whether each has an account. With NAME, connect its account. A Connector "
            "that declares an API key asks for the key. A Connector that declares a "
            "browser login asks for the client ID and secret of the app registered "
            "with the service, then opens the browser. A remote MCP server opens the "
            "browser for the login the server describes; --api-key stores a key "
            "instead. Keys and secrets are asked for, or read from standard input "
            "when that is not a terminal, and are never given as arguments."
        ),
    )
    command.add_argument("name", nargs="?", help="integration name, such as acme-crm")
    command.add_argument("--connect", default="localhost:7433", help="operator address")
    command.add_argument(
        "--api-key",
        action="store_true",
        help="connect a remote MCP server with an API key instead of a browser login",
    )
    command.add_argument(
        "--header",
        default=None,
        help=(
            "request header an MCP server expects its API key in (implies --api-key); "
            "a Connector declares its own and takes no --header"
        ),
    )
    command.add_argument(
        "--client-secret",
        action="store_true",
        help=(
            "ask for the secret of the client an MCP server's declaration names with "
            "client_id=, for a server whose registered client has one"
        ),
    )
    command.add_argument(
        "--no-browser",
        action="store_true",
        help="print the login address instead of opening a browser",
    )
    command.add_argument("--slug", help="immutable catalog connection slug (defaults to NAME)")
    command.add_argument(
        "--per-user", action="store_true", help="connect the starting human's account"
    )
    command.add_argument("--json", action="store_true", help="emit JSON")
    command.set_defaults(handler=_handle)


def add_disconnect_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    command = subcommands.add_parser("disconnect", help="disconnect a declared integration")
    command.add_argument("name", help="integration name")
    command.add_argument("--slug", help="catalog connection to disconnect")
    command.add_argument("--connect", default="localhost:7433", help="operator address")
    command.add_argument("--json", action="store_true", help="emit JSON")
    command.set_defaults(handler=_handle_disconnect)


def _handle_disconnect(args: argparse.Namespace) -> int:
    from .app import _make_provider

    provider = _make_provider(args.connect)
    try:
        status = provider.disconnect_integration(args.name, slug=args.slug)
        print(json.dumps(_record(status)) if args.json else f"Disconnected {status.name}.")
        return 0
    except (OperatorCallError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        provider.close()


def _label(status: IntegrationStatus) -> str:
    if status.account_state == "reconnect_needed":
        return "reconnect needed"
    if status.connected:
        return "connected"
    if isinstance(status.declaration, CatalogDeclaration):
        return "missing"
    return "not connected" if status.requires_account else "no account"


def _record(status: IntegrationStatus) -> dict[str, object]:
    return {
        "name": status.name,
        "kind": status.declaration.kind,
        "display_name": status.display_name,
        "requires_account": status.requires_account,
        "login": list(status.login),
        "connected": status.connected,
        "connected_at": status.connected_at,
        "account_state": status.account_state,
        "icon_url": status.icon_url,
        "provider_error": status.provider_error,
        "connections": [item.model_dump(mode="json") for item in status.connections],
        "named_connections": status.named_connections,
        "used_by": [f"{use.workflow}/{use.node_id}" for use in status.used_by],
    }


def _read_secret(prompt: str) -> str:
    """A value that must not be shown: asked without echo, or one line of standard input."""
    if sys.stdin.isatty():
        return getpass.getpass(prompt).strip()
    return sys.stdin.readline().strip()


def _read_key(status: IntegrationStatus) -> str:
    key = _read_secret(f"API key for {status.display_name or status.name}: ")
    if not key:
        raise ValueError("No API key was given")
    return key


def _read_client_id(status: IntegrationStatus) -> str:
    label = status.display_name or status.name
    if sys.stdin.isatty():
        client_id = input(f"Client ID of your {label} app: ").strip()
    else:
        client_id = sys.stdin.readline().strip()
    if not client_id:
        raise ValueError("No client ID was given")
    return client_id


def _connect_with_key(
    provider: GrpcStateProvider, status: IntegrationStatus, header: str | None
) -> IntegrationStatus:
    credential = ApiKeyCredential(key=_read_key(status), header=header)
    return provider.connect_integration(status.name, credential)


def _connect_in_browser(
    provider: GrpcStateProvider,
    status: IntegrationStatus,
    *,
    client_id: str | None,
    client_secret: str | None,
    open_browser: bool,
    quiet: bool,
    slug: str | None = None,
    kind: Literal["shared", "per_user"] = "shared",
) -> IntegrationStatus:
    login = provider.start_integration_login(
        status.name, client_id=client_id, client_secret=client_secret, slug=slug, kind=kind
    )
    label = status.display_name or status.name
    # Progress goes to standard error, so --json prints only the result.
    opened = open_browser and webbrowser.open(login.authorization_url)
    if not quiet or not opened:
        print(
            f"Log in to {label} in your browser"
            + ("." if opened else ", on the machine the operator runs on:")
            + f"\n  {login.authorization_url}",
            file=sys.stderr,
        )
    while login.status == "waiting":
        login = provider.integration_login(login.login_id)
    if login.status == "failed":
        raise ValueError(f"{label} was not connected: {login.error}")
    return next(item for item in provider.list_integrations() if item.name == status.name)


def _handle(args: argparse.Namespace) -> int:
    from .app import _make_provider

    provider = _make_provider(args.connect)
    use_key = args.api_key or args.header is not None
    try:
        statuses = provider.list_integrations()
        if args.name is None:
            if use_key or args.client_secret or args.slug is not None or args.per_user:
                raise ValueError("Connection options need an integration name.")
            if args.json:
                print(json.dumps([_record(status) for status in statuses]))
            elif not statuses:
                print("No discovered workflow declares an integration.")
            else:
                print("NAME\tKIND\tACCOUNT\tUSED BY")
                for status in statuses:
                    steps = ", ".join(f"{use.workflow}/{use.node_id}" for use in status.used_by)
                    print(
                        f"{status.name}\t{status.declaration.kind}\t{_label(status)}\t{steps}"
                    )
                    for connection in status.connections:
                        print(
                            f"  {connection.slug}\t{connection.kind}\t{connection.state}"
                            f"\tassigned to {', '.join(connection.assignments)}"
                        )
            return 0
        matches = [status for status in statuses if status.name == args.name]
        if not matches:
            known = ", ".join(status.name for status in statuses) or "none"
            raise ValueError(
                f"No discovered workflow declares an integration named {args.name!r}. "
                f"Declared integrations: {known}."
            )
        status = matches[0]
        if (args.slug is not None or args.per_user) and not isinstance(
            status.declaration, CatalogDeclaration
        ):
            raise ValueError("--slug and --per-user are for catalog integrations only.")
        match status.declaration:
            case CatalogDeclaration():
                if use_key or args.client_secret:
                    raise ValueError("Catalog integrations use the provider's browser login.")
                connected = _connect_in_browser(
                    provider,
                    status,
                    client_id=None,
                    client_secret=None,
                    slug=args.slug,
                    kind="per_user" if args.per_user else "shared",
                    open_browser=not args.no_browser,
                    quiet=args.json,
                )
            case ConnectorDeclaration(auth=None):
                raise ValueError(
                    f"Connector {status.name!r} declares no auth, so it needs no account."
                )
            case ConnectorDeclaration(auth=ApiKeyDeclaration() as auth):
                if args.header is not None:
                    raise ValueError(
                        f"Connector {status.name!r} declares where its key goes "
                        f"({auth.header}); connect it without --header."
                    )
                if args.client_secret:
                    raise ValueError(f"Connector {status.name!r} declares an API key.")
                connected = _connect_with_key(provider, status, None)
            case ConnectorDeclaration(auth=OAuth2Declaration()):
                if use_key or args.client_secret:
                    raise ValueError(
                        f"Connector {status.name!r} declares a browser login: connect it "
                        "without --api-key, --header or --client-secret. Its client ID "
                        "and secret are asked for."
                    )
                client_id = _read_client_id(status)
                secret = _read_secret("Client secret (empty if the app has none): ")
                connected = _connect_in_browser(
                    provider,
                    status,
                    client_id=client_id,
                    client_secret=secret or None,
                    open_browser=not args.no_browser,
                    quiet=args.json,
                )
            case MCPDeclaration(url=None):
                raise ValueError(
                    f"MCP server {status.name!r} is a local process, which has no request "
                    "header to carry a key; it reads its credentials from the environment "
                    "the operator runs in."
                )
            case MCPDeclaration() if use_key:
                if args.client_secret:
                    raise ValueError("--client-secret is for a browser login, not a key.")
                connected = _connect_with_key(provider, status, args.header)
            case MCPDeclaration(client_id=declared_client):
                if args.client_secret and declared_client is None:
                    raise ValueError(
                        f"MCP server {status.name!r} declares no client_id, so there is "
                        "no client a secret could belong to."
                    )
                secret = _read_secret("Client secret: ") if args.client_secret else ""
                connected = _connect_in_browser(
                    provider,
                    status,
                    client_id=None,
                    client_secret=secret or None,
                    open_browser=not args.no_browser,
                    quiet=args.json,
                )
        if args.json:
            print(json.dumps(_record(connected)))
        else:
            print(f"Connected {connected.name}.")
        return 0
    except (OperatorCallError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        provider.close()
