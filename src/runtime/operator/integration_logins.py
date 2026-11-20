"""Browser logins the operator runs: one loopback listener and the logins waiting on it.

``ava connect`` and the browser UI both ask the operator to start a login. The
operator returns the consent page's address, the caller opens it, and the
service sends the browser back to this listener. The listener binds the
loopback address only, exists only while a login is waiting, and takes only a
request whose ``state`` belongs to a login it started. The login itself, with
its PKCE and state checks and its token exchange, is
:mod:`avalanche.integrations.oauth`; tokens never leave this process.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import html
import logging
import threading
import uuid
from collections.abc import Awaitable, Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Literal
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from avalanche.integrations.accounts import OAuthCredential
from avalanche.integrations.oauth import LoginBrowser, LoginError, login_state

logger = logging.getLogger(__name__)

DEFAULT_LOGIN_PORT = 7436
LOGIN_HOST = "127.0.0.1"
CALLBACK_PATH = "/callback"
# How long a person has to finish the consent page.
LOGIN_TIMEOUT_SECONDS = 600.0
# Reaching the service and learning the consent page's address.
LOGIN_START_TIMEOUT_SECONDS = 60.0
# How long the browser's page waits to say whether the login worked.
_PAGE_WAIT_SECONDS = 60.0
# Finished logins kept for a caller that asks about them later.
_FINISHED_KEPT = 64

Login = Callable[[LoginBrowser], Awaitable[OAuthCredential]]


def login_redirect_uri(port: int) -> str:
    """The address a service sends the browser back to; register it with the vendor."""
    return f"http://{LOGIN_HOST}:{port}{CALLBACK_PATH}"


class StartIntegrationLoginRequest(BaseModel):
    """A call to start a browser login; errors about it never echo the secret."""

    model_config = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )

    name: str
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    kind: Literal["shared", "per_user"] = "shared"
    # A Connector's app at the service. An MCP server's client comes from the
    # server or its declaration; only its secret, when it has one, is given here.
    client_id: str | None = None
    client_secret: str | None = Field(default=None, repr=False)


class IntegrationLogin(BaseModel):
    """One browser login and how far it is; never a token, a code or a secret."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    login_id: str
    name: str
    # The consent page to open in a browser on the operator's machine.
    authorization_url: str
    status: Literal["waiting", "connected", "failed"]
    error: str | None


class IntegrationLoginError(ValueError):
    """A login could not be started; the message says why."""


class UnknownLoginError(LookupError):
    """No login with this ID is waiting or recently finished."""


class _Session:
    def __init__(self, name: str) -> None:
        self.login_id = uuid.uuid4().hex
        self.name = name
        self.authorization_url: concurrent.futures.Future[str] = concurrent.futures.Future()
        self.returned: concurrent.futures.Future[str] = concurrent.futures.Future()
        # Resolved with ``None`` when the account is stored, or with why not.
        self.outcome: concurrent.futures.Future[str | None] = concurrent.futures.Future()

    def view(self) -> IntegrationLogin:
        error = self.outcome.result() if self.outcome.done() else None
        return IntegrationLogin(
            login_id=self.login_id,
            name=self.name,
            authorization_url=self.authorization_url.result(),
            status=(
                "waiting"
                if not self.outcome.done()
                else "connected"
                if error is None
                else "failed"
            ),
            error=error,
        )


class _Browser:
    """The login's side of the listener: publish the address, wait for the return."""

    def __init__(self, logins: IntegrationLogins, session: _Session) -> None:
        self.redirect_uri = login_redirect_uri(logins.port)
        self._logins = logins
        self._session = session

    async def open(self, authorization_url: str) -> None:
        self._logins._expect(login_state(authorization_url), self._session)
        self._session.authorization_url.set_result(authorization_url)

    async def returned(self) -> str:
        return await asyncio.wrap_future(self._session.returned)


class IntegrationLogins:
    """The browser logins of one operator."""

    def __init__(
        self,
        port: int,
        store: Callable[[str, OAuthCredential], None],
        *,
        timeout: float = LOGIN_TIMEOUT_SECONDS,
    ) -> None:
        self.port = port
        self._store = store
        self._timeout = timeout
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}
        # state -> the login that started with it and still waits for its return.
        self._expected: dict[str, _Session] = {}
        self._listener: ThreadingHTTPServer | None = None
        self._closed = False

    def start(self, name: str, login: Login) -> IntegrationLogin:
        """Start a login and return once its consent page's address is known."""
        session = _Session(name)
        with self._lock:
            if self._closed:
                raise IntegrationLoginError("The operator is closing.")
            finished = [key for key, old in self._sessions.items() if old.outcome.done()]
            for key in finished[: max(0, len(finished) - _FINISHED_KEPT)]:
                del self._sessions[key]
            self._sessions[session.login_id] = session
        threading.Thread(
            target=self._run,
            args=(session, login),
            name=f"avalanche-login-{name}",
            daemon=True,
        ).start()
        done, _ = concurrent.futures.wait(
            [session.authorization_url, session.outcome],
            timeout=LOGIN_START_TIMEOUT_SECONDS,
            return_when=concurrent.futures.FIRST_COMPLETED,
        )
        if session.authorization_url in done:
            return session.view()
        with self._lock:
            del self._sessions[session.login_id]
        if session.outcome in done:
            why = session.outcome.result() or "it ended before the consent page"
            raise IntegrationLoginError(f"Could not start the login of {name!r}: {why}.")
        raise IntegrationLoginError(
            f"Could not start the login of {name!r}: the service did not answer within "
            f"{LOGIN_START_TIMEOUT_SECONDS:g} seconds."
        )

    def get(self, login_id: str, wait_seconds: float) -> IntegrationLogin:
        """How far a login is, after waiting up to ``wait_seconds`` for it to end."""
        with self._lock:
            session = self._sessions.get(login_id)
        if session is None or not session.authorization_url.done():
            raise UnknownLoginError(f"No login {login_id!r} is waiting or recently ended.")
        concurrent.futures.wait([session.outcome], timeout=max(0.0, wait_seconds))
        return session.view()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            waiting, self._expected = list(self._expected.values()), {}
        for session in waiting:
            session.returned.set_exception(LoginError("the operator closed"))

    def _run(self, session: _Session, login: Login) -> None:
        async def bounded() -> OAuthCredential:
            async with asyncio.timeout(self._timeout):
                return await login(_Browser(self, session))

        outcome: str | None
        try:
            credential = asyncio.run(bounded())
            self._store(session.name, credential)
            outcome = None
        except LoginError as failed:
            outcome = str(failed)
        except TimeoutError:
            outcome = f"nobody finished the login within {self._timeout:g} seconds"
        except Exception:
            # Its cause may hold a secret, so it goes to the log without one.
            logger.error("The login of integration %r failed unexpectedly", session.name)
            outcome = "the login failed unexpectedly"
        # The listener is closed before the login reports its end, so the next
        # login finds the port free and a late request finds nothing listening.
        self._forget(session)
        session.outcome.set_result(outcome)

    def _expect(self, state: str, session: _Session) -> None:
        with self._lock:
            if self._listener is None:
                try:
                    self._listener = _listen(self)
                except OSError as busy:
                    raise LoginError(
                        f"the login listener could not use {LOGIN_HOST}:{self.port} "
                        f"({busy.strerror}). Stop what is using the port, or start "
                        "the operator with another --login-port"
                    ) from None
            self._expected[state] = session

    def _forget(self, session: _Session) -> None:
        with self._lock:
            for state in [key for key, old in self._expected.items() if old is session]:
                del self._expected[state]
            if self._expected or self._listener is None:
                return
            # Stopped under the lock: a login that starts now waits for the port.
            # A page still being answered keeps its own connection.
            listener, self._listener = self._listener, None
            listener.shutdown()
            listener.server_close()

    def _take(self, state: str) -> _Session | None:
        """The login waiting for ``state``, once: a second return is refused."""
        with self._lock:
            return self._expected.pop(state, None)


def _listen(logins: IntegrationLogins) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            url = urlsplit(self.path)
            states = parse_qs(url.query).get("state", [])
            session = (
                logins._take(states[0])
                if url.path == CALLBACK_PATH and len(states) == 1
                else None
            )
            if session is None:
                self._page(
                    400,
                    "This is not a login this operator is waiting for.",
                    "Start it again with ava connect or from the operator UI.",
                )
                return
            session.returned.set_result(f"{login_redirect_uri(logins.port)}?{url.query}")
            try:
                error = session.outcome.result(timeout=_PAGE_WAIT_SECONDS)
            except concurrent.futures.TimeoutError:
                self._page(200, "Still connecting.", "Check the terminal or the operator UI.")
                return
            if error is None:
                self._page(200, f"{session.name} is connected.", "You can close this tab.")
            else:
                self._page(200, f"{session.name} was not connected.", error)

        def _page(self, status: int, title: str, detail: str) -> None:
            body = (
                "<!doctype html><meta charset=utf-8><title>Avalanche</title>"
                '<body style="font-family:system-ui;margin:4rem auto;max-width:32rem">'
                f"<h1>{html.escape(title)}</h1><p>{html.escape(detail)}</p>"
            ).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            # The request line carries the one-time code; it is never logged.
            pass

    listener = ThreadingHTTPServer((LOGIN_HOST, logins.port), Handler)
    listener.daemon_threads = True
    threading.Thread(
        target=listener.serve_forever, name="avalanche-login-listener", daemon=True
    ).start()
    return listener


__all__ = [
    "DEFAULT_LOGIN_PORT",
    "IntegrationLogin",
    "IntegrationLoginError",
    "IntegrationLogins",
    "StartIntegrationLoginRequest",
    "UnknownLoginError",
    "login_redirect_uri",
]
