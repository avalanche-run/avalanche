"""The MCP servers of one running agent step: connect, offer tools, forward calls, close.

A step's servers are connected when the step starts and closed when it ends.
All of a step's connections live on one private event loop in one thread, for
two reasons. An agent runtime may call a tool from the step's own loop, from
another thread or from a loop of its own, and an MCP connection only works on
the loop that opened it. And a connection must be closed by the task that
opened it, which the step's own task cannot promise when it is cancelled.

The MCP library is imported here, by a step that declares a server, and never
when a workflow is imported, declared or listed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import json
import keyword
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Coroutine, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import IO, TYPE_CHECKING

from pydantic import JsonValue, TypeAdapter, ValidationError

from .._optional import import_optional
from ..policies import NonRetryableError
from .accounts import AccessTokenCredential, AccountLoginExpiredError, ApiKeyCredential
from .core import (
    MCP,
    Catalog,
    IntegrationError,
    IntegrationNotConnectedError,
    IntegrationReconnectError,
    IntegrationTool,
    MCPServerUnavailableError,
    MCPToolError,
    StepAccount,
    StepAccounts,
    _no_account_store,
)
from .providers import (
    CatalogToolSession,
    catalog_connection_problem,
    delivered_tool_session,
    integration_provider,
)

if TYPE_CHECKING:
    import httpx2
    from jsonschema.protocols import Validator
    from mcp import Client
    from mcp.client._transport import TransportStreams
    from mcp.types import CallToolResult

logger = logging.getLogger(__name__)

# Connecting covers reaching the server or starting its process, the MCP
# handshake and the tool listing.
CONNECT_TIMEOUT_SECONDS = 30.0
# One tool call. A server that never answers fails the call, not the step.
TOOL_CALL_TIMEOUT_SECONDS = 300.0
# Closing stops a process in at most a few seconds; past this the thread is left.
CLOSE_TIMEOUT_SECONDS = 20.0
_STDERR_TAIL_CHARACTERS = 2_000

_GUARD = Path(__file__).with_name("_mcp_process_guard.py")
_NOT_A_NAME_CHARACTER = re.compile(r"[^A-Za-z0-9_]")
_SCHEMA = TypeAdapter(dict[str, JsonValue])
_JSON = TypeAdapter(JsonValue)
_ANNOTATIONS: Mapping[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


@dataclass(frozen=True)
class _OfferedTool:
    """One tool a server offers: its name there, its name for the agent, its schema."""

    server_name: str
    agent_name: str
    description: str | None
    input_schema: dict[str, JsonValue]

    @property
    def parameters(self) -> tuple[str, ...]:
        """The schema's top-level argument names, required ones first."""
        properties = self.input_schema.get("properties")
        names = list(properties) if isinstance(properties, dict) else []
        required = self.input_schema.get("required")
        first = [name for name in names if isinstance(required, list) and name in required]
        return (*first, *(name for name in names if name not in first))


@dataclass
class _Server:
    """One declared server and the state of its connection."""

    declaration: MCP
    account: StepAccount | None
    session: CatalogToolSession | None = field(default=None, repr=False)
    # Resolved with the server's tools once it is connected, or with why not.
    ready: concurrent.futures.Future[tuple[_OfferedTool, ...]] = field(
        default_factory=concurrent.futures.Future
    )
    client: Client | None = None
    # Why a connection that was open is not any more.
    closed: str | None = None
    # The statuses of the HTTP answers, kept to tell a refused login from an outage.
    statuses: list[int] = field(default_factory=list)


class MCPConnections:
    """The connections to one running step's MCP servers."""

    def __init__(
        self, step_name: str, servers: Sequence[MCP | Catalog], accounts: StepAccounts
    ) -> None:
        if servers:
            import_optional("mcp", extra="integrations", feature="MCP integration")
        self._step_name = step_name
        self._connect_command = accounts.connect_command
        self._declarations = tuple(servers)
        self._accounts = accounts
        self._delivered = delivered_tool_session()
        self._servers: tuple[_Server, ...] = ()
        self._resolved: concurrent.futures.Future[tuple[_Server, ...]] = (
            concurrent.futures.Future()
        )
        self._thread = threading.Thread(
            target=self._run, name=f"avalanche-mcp-{step_name}", daemon=True
        )
        self._started = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._closed = False
        self._unavailable: IntegrationError | None = None

    def _resolve_servers(self) -> tuple[_Server, ...]:
        # Provider methods are synchronous. Run them before starting the existing
        # MCP thread's event loop, never nested inside the agent's running loop.
        step_name, servers, accounts = self._step_name, self._declarations, self._accounts
        builtins = tuple(server.name for server in servers if isinstance(server, Catalog))
        session = None
        delivered = self._delivered
        if builtins and delivered is not None:
            session, names = delivered
            if not set(builtins) <= set(names):
                raise IntegrationError("The host did not authorize this step’s built-ins.")
        elif builtins:
            provider = integration_provider()
            problem = catalog_connection_problem(builtins, provider.connections())
            command = accounts.connect_command or "ava connect"
            if problem is not None:
                problem.raise_for_step(step_name, command)
            try:
                session = provider.open_tool_session(step_name, builtins)
            except AccountLoginExpiredError as expired:
                raise IntegrationReconnectError(
                    step_name, expired.name, command, expired.reason
                ) from None
        resolved: list[_Server] = []
        for server in servers:
            if isinstance(server, Catalog):
                assert session is not None
                resolved.append(
                    _Server(
                        declaration=MCP(server.name, url=session.url, allow=server.allow),
                        account=None,
                        session=session,
                    )
                )
            else:
                resolved.append(
                    _Server(
                        declaration=server,
                        account=accounts.account(server.name)
                        if server.accepts_account
                        else None,
                    )
                )
        return tuple(resolved)

    def raise_if_unavailable(self) -> None:
        """A model cannot turn an expired pass or relay outage into step success."""
        if self._unavailable is not None:
            raise self._unavailable

    def _catalog_failure(self, message: str) -> MCPServerUnavailableError:
        self._unavailable = MCPServerUnavailableError(message)
        return self._unavailable

    async def open(self) -> tuple[IntegrationTool, ...]:
        """Connect to every server and return their tools, in declaration order.

        Raises the first server's failure. The caller closes the connections
        whether this returns or raises.
        """
        self._thread.start()
        tools: list[IntegrationTool] = []
        for server in await asyncio.wrap_future(self._resolved):
            offered = await asyncio.wrap_future(server.ready)
            tools.extend(self._tool(server, tool) for tool in offered)
        return tuple(tools)

    def close(self) -> None:
        """Close every connection and stop every process this step started.

        Blocks until they are closed. It is called from the step's ``finally``,
        also while the step's task is being cancelled, when it could not rely
        on awaiting anything.
        """
        self._closed = True
        if not self._thread.is_alive():
            return
        self._started.wait(CLOSE_TIMEOUT_SECONDS)
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None:
            try:
                loop.call_soon_threadsafe(stop.set)
            except RuntimeError:
                # The loop already ended, with its connections.
                pass
        self._thread.join(CLOSE_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            logger.warning(
                "The MCP connections of step %r did not close within %s seconds",
                self._step_name,
                CLOSE_TIMEOUT_SECONDS,
            )

    def _run(self) -> None:
        try:
            if not self._resolved.set_running_or_notify_cancel():
                return
            try:
                self._servers = self._resolve_servers()
            except Exception as error:
                self._resolved.set_exception(error)
                return
            self._resolved.set_result(self._servers)
            if not self._closed:
                asyncio.run(self._serve_all())
        finally:
            # Whatever ended the loop, nothing is left waiting for a server.
            self._started.set()
            for server in self._servers:
                self._never_ready(server)

    async def _serve_all(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._started.set()
        # A task per server: one that fails or dies does not close the others.
        await asyncio.gather(*(self._serve(server) for server in self._servers))

    async def _serve(self, server: _Server) -> None:
        """Hold one server's connection open until the step ends."""
        import anyio

        assert self._loop is not None and self._stop is not None
        with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as stderr:
            try:
                # The connection's own scopes stay open for the whole step, so the
                # connect timeout cancels one scope around them all.
                with anyio.CancelScope() as scope:
                    timeout = self._loop.call_later(CONNECT_TIMEOUT_SECONDS, scope.cancel)
                    try:
                        async with self._connected(server, stderr) as offered:
                            timeout.cancel()
                            server.ready.set_result(offered)
                            await self._stop.wait()
                    finally:
                        timeout.cancel()
                if scope.cancelled_caught:
                    raise TimeoutError
            except Exception as exc:
                error = self._connection_error(server, exc, _tail(stderr))
                if server.ready.done():
                    server.closed = str(error)
                else:
                    server.ready.set_exception(error)
            finally:
                server.client = None
                self._never_ready(server)

    def _never_ready(self, server: _Server) -> None:
        """Fail a server's connection that ended before it had connected or failed."""
        if not server.ready.done():
            server.ready.set_exception(
                MCPServerUnavailableError(
                    f"step {self._step_name!r}: the connection to MCP server "
                    f"{server.declaration.name!r} ended before it was ready"
                )
            )

    @asynccontextmanager
    async def _connected(
        self, server: _Server, stderr: IO[str]
    ) -> AsyncIterator[tuple[_OfferedTool, ...]]:
        from mcp import Client
        from mcp.types import Implementation

        async with AsyncExitStack() as stack:
            client = Client(
                await self._transport(server, stderr, stack),
                client_info=Implementation(name="avalanche", version=version("avalanche-ai")),
            )
            await stack.enter_async_context(client)
            offered = await self._offered(server.declaration, client)
            server.client = client
            yield offered

    async def _transport(
        self, server: _Server, stderr: IO[str], stack: AsyncExitStack
    ) -> AbstractAsyncContextManager[TransportStreams]:
        from mcp import StdioServerParameters

        declaration = server.declaration
        if declaration.command is not None:
            program = shutil.which(declaration.command[0])
            if program is None:
                raise FileNotFoundError(declaration.command[0])
            command = [program, *declaration.command[1:]]
            if os.name == "posix":
                # The guard stops the server when this process dies without
                # closing it, however it dies.
                command = [sys.executable, "-I", "-S", str(_GUARD), str(os.getpid()), *command]
            from mcp import stdio_client

            # The process gets the step's environment, like any program a step starts.
            return stdio_client(
                StdioServerParameters(
                    command=command[0], args=command[1:], env=dict(os.environ)
                ),
                errlog=stderr,
            )
        import httpx2
        from mcp.client.streamable_http import streamable_http_client

        assert declaration.url is not None

        async def record(response: httpx2.Response) -> None:
            server.statuses.append(response.status_code)
            if server.session is not None and response.status_code in (401, 403):
                self._catalog_failure("The catalog tool session expired; retry the step.")
            if server.session is not None and response.status_code == 409:
                self._unavailable = IntegrationNotConnectedError(
                    self._step_name,
                    [server.declaration.name],
                    self._connect_command or "ava connect",
                )

        # The library follows a redirect only within the server's own origin,
        # so the account's header never leaves for another host.
        http = await stack.enter_async_context(
            httpx2.AsyncClient(
                auth=(
                    _session_auth(server.session, server.declaration.name)
                    if server.session is not None
                    else None
                    if server.account is None
                    else _account_auth(server.account)
                ),
                timeout=httpx2.Timeout(CONNECT_TIMEOUT_SECONDS, read=TOOL_CALL_TIMEOUT_SECONDS),
                event_hooks={"response": [record]},
            )
        )
        return streamable_http_client(declaration.url, http_client=http)

    async def _offered(self, declaration: MCP, client: Client) -> tuple[_OfferedTool, ...]:
        """Ask the server for its tools and keep those the declaration allows."""
        listed = []
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor)
            listed.extend(page.tools)
            cursor = page.next_cursor
            if cursor is None:
                break
        names = [tool.name for tool in listed]
        if declaration.allow is not None:
            unknown = sorted(set(declaration.allow) - set(names))
            if unknown:
                raise IntegrationError(
                    f"step {self._step_name!r}: ava.MCP {declaration.name!r} allows tools "
                    f"the server does not offer: {unknown}. It offers {sorted(names)}"
                )
        offered = []
        for tool in listed:
            if declaration.allow is not None and tool.name not in declaration.allow:
                continue
            schema = _checked_schema(
                self._step_name, declaration.name, tool.name, tool.input_schema
            )
            offered.append(
                _OfferedTool(
                    server_name=tool.name,
                    agent_name=declaration.tool_prefix
                    + "__"
                    + _NOT_A_NAME_CHARACTER.sub("_", tool.name),
                    description=tool.description,
                    input_schema=schema,
                )
            )
        agent_names = [tool.agent_name for tool in offered]
        clashes = sorted({name for name in agent_names if agent_names.count(name) > 1})
        if clashes:
            raise IntegrationError(
                f"step {self._step_name!r}: MCP server {declaration.name!r} offers tools "
                f"whose names become the same tool name {clashes} once characters other "
                "than letters, digits and '_' are written '_'; keep one with allow=[...]"
            )
        return tuple(offered)

    def _connection_error(self, server: _Server, exc: Exception, stderr: str) -> Exception:
        """Say in the step's words why a server could not be used."""
        if isinstance(exc, IntegrationError):
            return exc
        declaration = server.declaration
        cause = _first_cause(exc)
        # An error that already says what to do, such as a login to connect again.
        if isinstance(cause, NonRetryableError):
            return cause
        if server.session is not None:
            if self._unavailable is not None:
                return self._unavailable
            return MCPServerUnavailableError(
                f"step {self._step_name!r}: catalog tool server is unavailable"
            )
        step, name = self._step_name, declaration.name
        if declaration.command is not None:
            program = declaration.command[0]
            if isinstance(cause, FileNotFoundError):
                return IntegrationError(
                    f"step {step!r} could not start MCP server {name!r}: the program "
                    f"{program!r} was not found"
                )
            if isinstance(cause, TimeoutError):
                return MCPServerUnavailableError(
                    f"step {step!r}: MCP server {name!r} ({program}) did not answer within "
                    f"{CONNECT_TIMEOUT_SECONDS:g} seconds of starting"
                )
            output = f" Its error output ended with:\n{stderr}" if stderr else ""
            return IntegrationError(
                f"step {step!r}: MCP server {name!r} ({program}) stopped, or did not speak "
                f"MCP: {_described(cause)}.{output}"
            )
        refused = next((status for status in server.statuses if status in (401, 403)), None)
        if (
            refused == 401
            and server.account is not None
            and isinstance(server.account.current(), AccessTokenCredential)
        ):
            # A browser login the server no longer accepts: it was revoked.
            return server.account.refused(refused)
        if refused is not None and server.account is None:
            if self._connect_command is None:
                return IntegrationError(
                    f"MCP server {name!r} asks for a login. " + _no_account_store(step, [name])
                )
            if refused == 401:
                return IntegrationNotConnectedError(step, [name], self._connect_command)
        if refused is not None:
            connect = self._connect_command or "ava connect"
            return IntegrationError(
                f"step {step!r}: MCP server {name!r} refused "
                + ("the request" if server.account is None else "its stored account")
                + f" (HTTP {refused}). Connect it again with `{connect} {name}`; with a "
                "key, pass --header NAME if the server expects it in another header "
                "than Authorization"
            )
        reason = (
            f"no answer within {CONNECT_TIMEOUT_SECONDS:g} seconds"
            if isinstance(cause, TimeoutError)
            else _described(cause)
        )
        return MCPServerUnavailableError(
            f"step {step!r} could not use MCP server {name!r} at {declaration.url}: {reason}"
        )

    def _tool(self, server: _Server, offered: _OfferedTool) -> IntegrationTool:
        """The agent's function for one server tool.

        It checks the arguments against the tool's schema, forwards the call to
        the server on the connections' loop, and returns the result as JSON
        data. It can be awaited from any loop or thread.
        """
        validator = _validator(offered.input_schema)
        names = offered.parameters

        async def call(*args: object, **kwargs: object) -> JsonValue:
            if len(args) > len(names):
                raise MCPToolError(
                    f"{offered.agent_name} takes its arguments by name: {list(names)}"
                )
            arguments = {**dict(zip(names, args)), **kwargs}
            problems = sorted(
                ("/".join(str(part) for part in error.absolute_path) or "arguments")
                + f": {error.message}"
                for error in validator.iter_errors(arguments)
            )
            if problems:
                raise MCPToolError(
                    f"invalid arguments for {offered.agent_name}: "
                    + "; ".join(problems)
                    + f". The tool expects: {json.dumps(offered.input_schema)}"
                )
            checked = _SCHEMA.validate_python(arguments)
            return await self._on_loop(self._call(server, offered, checked), offered.agent_name)

        call.__name__ = call.__qualname__ = offered.agent_name
        # A runtime that shows tools to its model as Python functions reads these.
        call.__doc__ = (
            f"{offered.description or 'No description.'}\n\n"
            f"Arguments, by name (JSON Schema): {json.dumps(offered.input_schema)}"
        )
        try:
            call.__signature__ = _signature(offered)  # type: ignore[attr-defined]
        except (TypeError, ValueError) as exc:
            raise IntegrationError(
                f"step {self._step_name!r}: tool {offered.server_name!r} of MCP server "
                f"{server.declaration.name!r} has arguments that cannot be offered to "
                f"an agent: {exc}"
            ) from exc
        return IntegrationTool(
            name=offered.agent_name,
            description=offered.description,
            function=call,
            input_schema=offered.input_schema,
        )

    async def _on_loop(self, call: Coroutine[None, None, JsonValue], tool: str) -> JsonValue:
        """Await ``call`` on the connections' loop from whichever loop this is."""
        loop = self._loop
        if self._closed or loop is None:
            call.close()
            raise MCPToolError(f"{tool}: step {self._step_name!r} has ended")
        if loop is asyncio.get_running_loop():
            return await call
        try:
            future = asyncio.run_coroutine_threadsafe(call, loop)
        except RuntimeError:
            call.close()
            raise MCPToolError(f"{tool}: step {self._step_name!r} has ended") from None
        return await asyncio.wrap_future(future)

    async def _call(
        self, server: _Server, offered: _OfferedTool, arguments: dict[str, JsonValue]
    ) -> JsonValue:
        import anyio
        import httpx2
        from mcp import MCPError

        tool, name = offered.agent_name, server.declaration.name
        self.raise_if_unavailable()
        if server.session is not None and time.time() >= server.session.expires_at:
            raise self._catalog_failure("The catalog tool session expired; retry the step.")
        client = server.client
        if client is None:
            if server.session is not None:
                raise self._catalog_failure("The catalog tool server is unavailable.")
            why = f": {server.closed}" if server.closed else ""
            raise MCPToolError(f"{tool}: the connection to MCP server {name!r} is closed{why}")
        try:
            with anyio.fail_after(TOOL_CALL_TIMEOUT_SECONDS):
                result = await client.call_tool(offered.server_name, arguments)
        except MCPServerUnavailableError:
            raise self._catalog_failure(
                "The catalog tool session expired; retry the step."
            ) from None
        except TimeoutError:
            if server.session is not None:
                self.raise_if_unavailable()
                raise self._catalog_failure("The catalog tool server is unavailable.") from None
            raise MCPToolError(
                f"{tool}: MCP server {name!r} did not answer within "
                f"{TOOL_CALL_TIMEOUT_SECONDS:g} seconds"
            ) from None
        except (
            MCPError,
            httpx2.HTTPError,
            OSError,
            anyio.BrokenResourceError,
            anyio.ClosedResourceError,
        ) as exc:
            if server.session is not None:
                self.raise_if_unavailable()
                raise self._catalog_failure("The catalog tool server is unavailable.") from None
            raise MCPToolError(
                f"{tool}: MCP server {name!r} failed: {_described(exc)}"
            ) from exc
        if result.is_error:
            raise MCPToolError(f"{tool}: {_text(result) or 'the tool reported an error'}")
        if result.structured_content is not None:
            return _JSON.validate_python(result.structured_content)
        return _text(result)


def _session_auth(session: CatalogToolSession, name: str) -> httpx2.Auth:
    import httpx2

    class SessionAuth(httpx2.Auth):
        def auth_flow(self, request: httpx2.Request):
            if time.time() >= session.expires_at:
                raise MCPServerUnavailableError(
                    "The catalog tool session expired; retry the step."
                )
            request.headers["Authorization"] = "Bearer " + session.pass_value.get_secret_value()
            request.headers["X-Avalanche-Integration"] = name
            yield request

    return SessionAuth()


def _account_auth(account: StepAccount) -> httpx2.Auth:
    """Attach a remote server's stored account to each request as it leaves.

    A browser login's access token is sent as a bearer token and is renewed
    when it is about to expire, however long the step runs. A key connected
    with ``ava connect NAME --api-key --header H`` is sent as it is in ``H``;
    without ``--header`` it is a bearer token, the form the MCP standard uses.
    """
    import httpx2

    class AccountAuth(httpx2.Auth):
        def auth_flow(self, request: httpx2.Request):
            match account.current():
                case (
                    AccessTokenCredential(token=token)
                    | ApiKeyCredential(key=token, header=None)
                ):
                    request.headers["Authorization"] = f"Bearer {token}"
                case ApiKeyCredential(key=key, header=header):
                    request.headers[header] = key
            yield request

    return AccountAuth()


def _checked_schema(step: str, server: str, tool: str, schema: object) -> dict[str, JsonValue]:
    """A server's argument schema as JSON data, refused when it is not a JSON Schema."""
    from jsonschema.exceptions import SchemaError

    try:
        checked = _SCHEMA.validate_python(schema)
        type(_validator(checked)).check_schema(checked)
    except (ValidationError, SchemaError) as exc:
        raise IntegrationError(
            f"step {step!r}: MCP server {server!r} describes the arguments of its tool "
            f"{tool!r} with an invalid JSON Schema: {exc}"
        ) from exc
    return checked


def _validator(schema: dict[str, JsonValue]) -> Validator:
    from jsonschema.validators import validator_for

    return validator_for(schema)(schema)


def _signature(offered: _OfferedTool) -> inspect.Signature:
    """A Python signature for a schema's arguments, for runtimes that show one.

    Names that are not Python identifiers cannot be parameters; the tool then
    also takes the rest by keyword, as in ``tool(**{"from": "monday"})``. The
    schema, in the docstring, stays the rule.
    """
    properties = offered.input_schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    required = offered.input_schema.get("required")
    parameters = []
    extra = False
    for name in offered.parameters:
        # ``from`` and ``class`` are valid argument names for a server, not for Python.
        if not name.isidentifier() or keyword.iskeyword(name):
            extra = True
            continue
        described = properties[name]
        kind = described.get("type") if isinstance(described, dict) else None
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=inspect.Parameter.empty
                if isinstance(required, list) and name in required
                else None,
                annotation=_ANNOTATIONS.get(kind, object) if isinstance(kind, str) else object,
            )
        )
    if extra:
        # The catch-all must not take the name of one of the tool's own arguments.
        catch_all = "arguments"
        while catch_all in properties:
            catch_all += "_"
        parameters.append(inspect.Parameter(catch_all, inspect.Parameter.VAR_KEYWORD))
    return inspect.Signature(parameters, return_annotation=object)


def _text(result: CallToolResult) -> str:
    """A result's text; content that is not text is named, not passed on."""
    from mcp.types import TextContent

    return "\n".join(
        block.text
        if isinstance(block, TextContent)
        else f"[{block.type} content, which is not passed to the agent]"
        for block in result.content
    )


def _first_cause(exc: BaseException) -> BaseException:
    """The first error inside the task-group wrappers a failed connection raises."""
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc


def _described(exc: BaseException) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _tail(stderr: IO[str]) -> str:
    stderr.seek(0)
    return stderr.read()[-_STDERR_TAIL_CHARACTERS:].strip()


__all__ = [
    "CLOSE_TIMEOUT_SECONDS",
    "CONNECT_TIMEOUT_SECONDS",
    "TOOL_CALL_TIMEOUT_SECONDS",
    "MCPConnections",
]
