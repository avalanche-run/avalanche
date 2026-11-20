"""Host-neutral named-connection resolution; no I/O or provider loading."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Literal

from ..policies import NonRetryableError
from .core import IntegrationNotConnectedError, IntegrationReconnectError
from .declaration import StepIntegrations


@dataclass(frozen=True)
class Connection:
    id: str
    slug: str
    integration: str
    kind: Literal["shared", "per_user"]


@dataclass(frozen=True)
class ConnectionInstance:
    id: str
    connection_id: str
    user_id: str | None
    state: Literal["waiting", "connected", "reconnect_needed", "disconnected", "disconnecting"]


@dataclass(frozen=True)
class OrgAssignment:
    connection_id: str


@dataclass(frozen=True)
class ProjectAssignment:
    connection_id: str
    project: str


@dataclass(frozen=True)
class FlowAssignment:
    connection_id: str
    project: str
    flow: str


class ConnectionNotResolvedError(NonRetryableError):
    code: ClassVar[str] = "connection_not_resolved"

    def __init__(self, flow: str, integration: str) -> None:
        super().__init__(
            f"Flow {flow!r} uses {integration}, but no {integration} connection is assigned "
            "to the flow, its project or the organization. Assign one, then deploy again."
        )


class ConnectionAmbiguousError(NonRetryableError):
    code: ClassVar[str] = "connection_ambiguous"

    def __init__(self, flow: str, integration: str, level: str, slugs: Sequence[str]) -> None:
        listed = " and ".join(repr(slug) for slug in slugs)
        count = "two" if len(slugs) == 2 else str(len(slugs))
        super().__init__(
            f"Flow {flow!r} uses {integration}, and {count} {integration} connections are "
            f"assigned to {level}: {listed}. Remove one, or name one with "
            f"ava.catalog.{integration}(connection=...)."
        )


class ConnectionNotAssignedError(NonRetryableError):
    code: ClassVar[str] = "connection_not_assigned"

    def __init__(self, flow: str, slug: str) -> None:
        super().__init__(
            f"Flow {flow!r} names connection {slug!r}, which is not assigned to the flow, "
            "its project or the organization."
        )


class ConnectionNotFoundError(NonRetryableError):
    code: ClassVar[str] = "connection_not_found"

    def __init__(self, flow: str, integration: str, slug: str) -> None:
        super().__init__(
            f"Flow {flow!r} names connection {slug!r}, which does not exist "
            f"(or is not a {integration} connection)."
        )


class ConnectionPerUserTriggeredError(NonRetryableError):
    code: ClassVar[str] = "connection_per_user_triggered"

    def __init__(self, flow: str, integration: str, slug: str) -> None:
        super().__init__(
            f"Flow {flow!r} has a schedule or webhook, and its {integration} connection "
            f"{slug!r} is per user. A triggered run has no user. Assign a shared connection."
        )


class ConnectionUserNotConnectedError(NonRetryableError):
    code: ClassVar[str] = "connection_user_not_connected"

    def __init__(self, flow: str, integration: str, slug: str) -> None:
        super().__init__(
            f"Connect your {integration} account ({slug!r}) to run flow {flow!r}, "
            "then start the run again."
        )


def resolve(
    declared: Mapping[str, StepIntegrations],
    connections: Sequence[Connection],
    assignments: Sequence[OrgAssignment | ProjectAssignment | FlowAssignment],
    instances: Sequence[ConnectionInstance],
    *,
    flow: str,
    project: str,
    starter: str | None,
    triggered: Literal["schedule", "webhook"] | None,
) -> dict[str, dict[str, ConnectionInstance]]:
    """Bind each step's catalog keys to connected instances, or raise a refusal.

    Hosts supply validated records scoped to ONE organization (including its org
    assignments). Flow assignments are identified by (project, flow slug), not
    deployment ID. Bindings are grouped by step so keys from different steps do
    not collide. Connector/MCP declarations are outside this resolver's remit.

    ``starter`` is the starting human user, or None; identity and API-token
    policy belong to the admitting host, with no token-owner fallback here.
    ``triggered`` describes the flow's configured trigger, including at deploy.
    The local operator calls this only for an opted-in named provider.
    """
    by_slug = {connection.slug: connection for connection in connections}
    by_instance = {(item.connection_id, item.user_id): item for item in instances}
    visible: dict[str, set[str]] = {
        "the flow": set(),
        "its project": set(),
        "the organization": set(),
    }
    for assignment in assignments:
        if isinstance(assignment, OrgAssignment):
            visible["the organization"].add(assignment.connection_id)
        elif isinstance(assignment, ProjectAssignment):
            if assignment.project == project:
                visible["its project"].add(assignment.connection_id)
        elif (assignment.project, assignment.flow) == (project, flow):
            visible["the flow"].add(assignment.connection_id)
    assigned = set().union(*visible.values())
    result: dict[str, dict[str, ConnectionInstance]] = {}
    for step, declarations in declared.items():
        bindings: dict[str, ConnectionInstance] = {}
        for entry in declarations.integrations:
            if entry.kind != "catalog":
                continue
            if entry.connection is not None:
                connection = by_slug.get(entry.connection)
                if connection is None or connection.integration != entry.name:
                    raise ConnectionNotFoundError(flow, entry.name, entry.connection)
                if connection.id not in assigned:
                    raise ConnectionNotAssignedError(flow, entry.connection)
            else:
                for level, ids in visible.items():
                    candidates = sorted(
                        (
                            item
                            for item in connections
                            if item.integration == entry.name and item.id in ids
                        ),
                        key=lambda item: item.slug,
                    )
                    if len(candidates) > 1:
                        raise ConnectionAmbiguousError(
                            flow, entry.name, level, [item.slug for item in candidates]
                        )
                    if candidates:
                        connection = candidates[0]
                        break
                else:
                    raise ConnectionNotResolvedError(flow, entry.name)
            if connection.kind == "per_user":
                if triggered is not None:
                    raise ConnectionPerUserTriggeredError(flow, entry.name, connection.slug)
                instance = (
                    by_instance.get((connection.id, starter)) if starter is not None else None
                )
                if instance is None or instance.state != "connected":
                    raise ConnectionUserNotConnectedError(flow, entry.name, connection.slug)
            else:
                instance = by_instance.get((connection.id, None))
                if instance is not None and instance.state == "reconnect_needed":
                    raise IntegrationReconnectError(
                        step,
                        entry.name,
                        "ava connect",
                        f"connection {connection.slug!r} requires a new login",
                    )
                if instance is None or instance.state != "connected":
                    raise IntegrationNotConnectedError(step, [connection.slug], "ava connect")
            bindings[entry.connection if entry.connection is not None else entry.name] = (
                instance
            )
        result[step] = bindings
    return result
