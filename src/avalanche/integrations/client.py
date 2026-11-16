"""The HTTP client a Connector uses, already logged in to its account."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from .core import IntegrationError

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: httpx.URL) -> tuple[str, str, int | None]:
    return url.scheme, url.host, url.port or _DEFAULT_PORTS.get(url.scheme)


class _AccountTransport(httpx.BaseTransport):
    """Sends requests to one origin only, and attaches the account there.

    Every request the client makes passes through here, including each hop of
    a followed redirect and a request given an absolute URL. One whose scheme,
    host or port differs from the Connector's ``base_url`` is refused before
    the credential is attached, so the account's key can only ever reach the
    service it belongs to.
    """

    def __init__(
        self,
        base_url: httpx.URL,
        account_headers: Callable[[], dict[str, str]],
        login_refused: Callable[[int], Exception] | None,
    ) -> None:
        self._base_origin = _origin(base_url)
        self._account_headers = account_headers
        self._login_refused = login_refused
        self._transport = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        scheme, host, port = _origin(request.url)
        if (scheme, host, port) != self._base_origin:
            base_scheme, base_host, base_port = self._base_origin
            raise IntegrationError(
                f"request to {scheme}://{host}:{port} refused: this Connector's client "
                f"only calls its base_url, {base_scheme}://{base_host}:{base_port}. "
                "Pass paths relative to base_url; a redirect to another origin is "
                "refused too"
            )
        # Read as each request leaves: a browser login's token is renewed in time.
        request.headers.update(self._account_headers())
        response = self._transport.handle_request(request)
        if response.status_code == 401 and self._login_refused is not None:
            response.close()
            raise self._login_refused(401)
        return response

    def close(self) -> None:
        self._transport.close()


class Client(httpx.Client):
    """An ``httpx.Client`` bound to a Connector's ``base_url`` and account.

    Avalanche builds it when a step that declares the Connector first reads
    ``self.client`` and closes it when that step ends. Paths are relative to
    ``base_url``. The client calls that origin only: an absolute URL or a
    redirect to another scheme, host or port raises ``IntegrationError``
    before anything is sent. The account's key or access token is attached as
    each request leaves, so it is not in ``client.headers``. With a browser
    login, an answer of 401 raises ``IntegrationReconnectError``: the login
    was revoked. Proxy environment variables are not applied.
    """

    def __init__(
        self,
        *,
        base_url: str,
        headers: Callable[[], dict[str, str]],
        login_refused: Callable[[int], Exception] | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            transport=_AccountTransport(httpx.URL(base_url), headers, login_refused),
            follow_redirects=False,
        )


__all__ = ["Client"]
