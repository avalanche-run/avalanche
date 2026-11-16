"""External proof provider. Discovered through package metadata, never imported by tests."""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, TypeAdapter

from avalanche.integrations.providers import (
    CatalogConnection,
    CatalogEntry,
    CatalogLogin,
    CatalogToolSession,
    ProviderNotSignedInError,
    ProviderUnavailableError,
)


class _SessionReply(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    url: str
    pass_value: str
    expires_at: float


class Provider:
    def __init__(self) -> None:
        self.url = os.environ["CATALOG_PROOF_URL"]
        if urlsplit(self.url).hostname != "127.0.0.1":
            raise ValueError("The proof provider is loopback-only")

    def _read(self, path: str) -> bytes:
        try:
            with urllib.request.urlopen(self.url + path, timeout=5) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise ProviderNotSignedInError(
                    "The proof provider is not signed in. "
                    "Run `proof login`, then connect again."
                ) from None
            raise ProviderUnavailableError("The integration provider is unreachable.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProviderUnavailableError("The integration provider is unreachable.") from None

    def entries(self) -> list[CatalogEntry]:
        return TypeAdapter(list[CatalogEntry]).validate_json(self._read("/entries"))

    def connections(self) -> list[CatalogConnection]:
        return TypeAdapter(list[CatalogConnection]).validate_json(self._read("/connections"))

    def start_login(self, name: str) -> CatalogLogin:
        return CatalogLogin.model_validate_json(
            self._read("/login?" + urlencode({"name": name}))
        )

    def login_status(self, login_id: str) -> CatalogLogin:
        return CatalogLogin.model_validate_json(
            self._read("/status?" + urlencode({"id": login_id}))
        )

    def disconnect(self, name: str) -> None:
        self._read("/disconnect?" + urlencode({"name": name}))

    def open_tool_session(self, step: str, names: tuple[str, ...]) -> CatalogToolSession:
        reply = _SessionReply.model_validate_json(
            self._read("/session?" + urlencode({"step": step, "names": ",".join(names)}))
        )
        return CatalogToolSession(
            url=reply.url, pass_value=reply.pass_value, expires_at=reply.expires_at
        )
