"""Local catalog admission: adapt provider records to the shared resolver."""

from __future__ import annotations

from typing import Literal

from avalanche.integrations.connections import (
    Connection,
    ConnectionInstance,
    OrgAssignment,
    resolve,
)
from avalanche.integrations.providers import (
    CatalogBindings,
    ProviderSelectionError,
    named_integration_provider,
)

from .models import WorkflowDescriptor


def resolve_catalog_connections(
    descriptor: WorkflowDescriptor, *, triggered: Literal["schedule", "webhook"] | None = None
) -> CatalogBindings | None:
    declared = dict(descriptor.integration_declarations)
    if not any(
        entry.kind == "catalog" for step in declared.values() for entry in step.integrations
    ):
        return None
    provider = named_integration_provider()
    if provider is None:
        if any(
            entry.connection is not None
            for step in declared.values()
            for entry in step.integrations
            if entry.kind == "catalog"
        ):
            raise ProviderSelectionError("This provider does not support named connections.")
        return None
    records = provider.named_connections()
    connections = {
        item.slug: Connection(item.slug, item.slug, item.name, item.kind) for item in records
    }
    resolved = resolve(
        declared,
        tuple(connections.values()),
        [OrgAssignment(slug) for slug in connections],
        [
            ConnectionInstance(item.instance_id, item.slug, item.user, item.state)
            for item in records
        ],
        flow=descriptor.display_name,
        project="local",
        starter=provider.starting_user() if triggered is None else None,
        triggered=triggered,
    )
    # Preserve the integration as well as its optional slug: a slug on one
    # step can match an unrelated integration's bare name on another step.
    return {
        (entry.name, entry.connection): resolved[step][entry.connection or entry.name]
        for step, declarations in declared.items()
        for entry in declarations.integrations
        if entry.kind == "catalog"
    }
