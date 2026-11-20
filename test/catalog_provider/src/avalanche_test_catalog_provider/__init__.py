"""External proof provider. Discovered through package metadata, never imported by tests."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import asdict
from typing import Literal
from urllib.parse import urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, TypeAdapter

from avalanche.integrations.connections import ConnectionInstance
from avalanche.integrations.core import IntegrationNotConnectedError
from avalanche.integrations.providers import (
    CatalogConnection,
    CatalogEntry,
    CatalogLogin,
    CatalogToolSession,
    NamedCatalogConnection,
    ProviderNotSignedInError,
    ProviderUnavailableError,
)


class _SessionReply(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", hide_input_in_errors=True)

    url: str
    pass_value: str
    expires_at: float


class LegacyProvider:
    def __init__(self) -> None:
        self.url = os.environ["CATALOG_PROOF_URL"]
        if urlsplit(self.url).hostname != "127.0.0.1":
            raise ValueError("The proof provider is loopback-only")

    def _read(self, path: str) -> bytes:
        try:
            with urllib.request.urlopen(self.url + path, timeout=5) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise IntegrationNotConnectedError(
                    "catalog step", ["asana"], "ava connect"
                ) from None
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


class NamedProvider(LegacyProvider):
    def starting_user(self) -> str | None:
        return TypeAdapter(str | None).validate_json(self._read("/user"))

    def named_connections(self) -> list[NamedCatalogConnection]:
        return TypeAdapter(list[NamedCatalogConnection]).validate_json(
            self._read("/named-connections")
        )

    def start_login(
        self,
        name: str,
        *,
        slug: str | None = None,
        kind: Literal["shared", "per_user"] = "shared",
    ) -> CatalogLogin:
        return CatalogLogin.model_validate_json(
            self._read(
                "/named-login?" + urlencode({"name": name, "slug": slug or name, "kind": kind})
            )
        )

    def open_bound_tool_session(
        self, step: str, bindings: Mapping[str, ConnectionInstance]
    ) -> CatalogToolSession:
        reply = _SessionReply.model_validate_json(
            self._read(
                "/bound-session?"
                + urlencode(
                    {
                        "step": step,
                        "bindings": json.dumps(
                            {key: asdict(instance) for key, instance in bindings.items()}
                        ),
                    }
                )
            )
        )
        return CatalogToolSession(
            url=reply.url, pass_value=reply.pass_value, expires_at=reply.expires_at
        )


def Provider() -> LegacyProvider:  # noqa: N802
    # Keep existing scenarios on the installed legacy provider contract. Only
    # this proof-only opt-in exposes the dormant named connection protocol.
    return NamedProvider() if os.environ.get("CATALOG_PROOF_NAMED") == "1" else LegacyProvider()
