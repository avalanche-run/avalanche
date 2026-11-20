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


class ConnectionResolutionError(NonRetryableError):
    """Safe declaration identity for a host to present an actionable refusal."""

    integration: str
    connection_slug: str | None


class ConnectionNotResolvedError(ConnectionResolutionError):
    code: ClassVar[str] = "connection_not_resolved"

    def __init__(self, flow: str, integration: str) -> None:
        self.integration, self.connection_slug = integration, None
        super().__init__(
            f"Flow {flow!r} uses {integration}, but no {integration} connection is assigned "
            "to the flow, its project or the organization. Assign one, then deploy again."
        )


class ConnectionAmbiguousError(ConnectionResolutionError):
    code: ClassVar[str] = "connection_ambiguous"

    def __init__(self, flow: str, integration: str, level: str, slugs: Sequence[str]) -> None:
        self.integration, self.connection_slug = integration, None
        listed = " and ".join(repr(slug) for slug in slugs)
        count = "two" if len(slugs) == 2 else str(len(slugs))
        super().__init__(
            f"Flow {flow!r} uses {integration}, and {count} {integration} connections are "
            f"assigned to {level}: {listed}. Remove one, or name one with "
            f"ava.catalog.{integration}(connection=...)."
        )


class ConnectionNotAssignedError(ConnectionResolutionError):
    code: ClassVar[str] = "connection_not_assigned"

    def __init__(self, flow: str, integration: str, slug: str) -> None:
        self.integration, self.connection_slug = integration, slug
        super().__init__(
            f"Flow {flow!r} names connection {slug!r}, which is not assigned to the flow, "
            "its project or the organization."
        )


class ConnectionNotFoundError(ConnectionResolutionError):
    code: ClassVar[str] = "connection_not_found"

    def __init__(self, flow: str, integration: str, slug: str) -> None:
        self.integration, self.connection_slug = integration, slug
        super().__init__(
            f"Flow {flow!r} names connection {slug!r}, which does not exist "
            f"(or is not a {integration} connection)."
        )


class ConnectionPerUserTriggeredError(ConnectionResolutionError):
    code: ClassVar[str] = "connection_per_user_triggered"

    def __init__(self, flow: str, integration: str, slug: str) -> None:
        self.integration, self.connection_slug = integration, slug
        super().__init__(
            f"Flow {flow!r} has a schedule or webhook, and its {integration} connection "
            f"{slug!r} is per user. A triggered run has no user. Assign a shared connection."
        )


class ConnectionUserNotConnectedError(ConnectionResolutionError):
    code: ClassVar[str] = "connection_user_not_connected"

    def __init__(self, flow: str, integration: str, slug: str) -> None:
        self.integration, self.connection_slug = integration, slug
        super().__init__(
            f"Connect your {integration} account ({slug!r}) to run flow {flow!r}, "
            "then start the run again."
        )


def resolve_connections(
    declared: Mapping[str, StepIntegrations],
    connections: Sequence[Connection],
    assignments: Sequence[OrgAssignment | ProjectAssignment | FlowAssignment],
    *,
    flow: str,
    project: str,
    triggered: Literal["schedule", "webhook"] | None,
) -> dict[str, dict[str, Connection]]:
    """Resolve assignments at deploy/schedule admission, without checking accounts.

    Hosts supply one organization's validated scope. Account readiness and the
    starting user's instance are run-start decisions, not deployment decisions.
    Both admission phases use this single assignment/trigger rule.
    """
    by_slug = {connection.slug: connection for connection in connections}
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
    result: dict[str, dict[str, Connection]] = {}
    for step, declarations in declared.items():
        bindings: dict[str, Connection] = {}
        for entry in declarations.integrations:
            if entry.kind != "catalog":
                continue
            if entry.connection is not None:
                connection = by_slug.get(entry.connection)
                if connection is None or connection.integration != entry.name:
                    raise ConnectionNotFoundError(flow, entry.name, entry.connection)
                if connection.id not in assigned:
                    raise ConnectionNotAssignedError(flow, entry.name, entry.connection)
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
            if connection.kind == "per_user" and triggered is not None:
                raise ConnectionPerUserTriggeredError(flow, entry.name, connection.slug)
            key = entry.connection if entry.connection is not None else entry.name
            bindings[key] = connection
        result[step] = bindings
    return result


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
    connect_action: str | None = None,
) -> dict[str, dict[str, ConnectionInstance]]:
    """Bind the resolved connections to connected instances at run admission.

    Hosts supply ONE organization's scope. Flow identity is (project, flow slug).
    ``starter`` is the starting human, or None; token-owner policy belongs to the
    host, with no identity fallback here. Connector/MCP declarations are ignored.
    """
    selected = resolve_connections(
        declared, connections, assignments, flow=flow, project=project, triggered=triggered
    )
    by_instance = {(item.connection_id, item.user_id): item for item in instances}
    result: dict[str, dict[str, ConnectionInstance]] = {}
    for step, resolved in selected.items():
        bindings: dict[str, ConnectionInstance] = {}
        for key, connection in resolved.items():
            if connection.kind == "per_user":
                instance = (
                    by_instance.get((connection.id, starter)) if starter is not None else None
                )
                if instance is None or instance.state != "connected":
                    raise ConnectionUserNotConnectedError(
                        flow, connection.integration, connection.slug
                    )
            else:
                instance = by_instance.get((connection.id, None))
                if instance is not None and instance.state == "reconnect_needed":
                    raise IntegrationReconnectError(
                        step,
                        connection.integration,
                        "ava connect",
                        f"connection {connection.slug!r} requires a new login",
                        connection_slug=connection.slug,
                        connect_action=connect_action,
                    )
                if instance is None or instance.state != "connected":
                    raise IntegrationNotConnectedError(
                        step,
                        [connection.slug],
                        "ava connect",
                        integration=connection.integration,
                        connection_slug=connection.slug,
                        connect_action=connect_action,
                    )
            bindings[key] = instance
        result[step] = bindings
    return result
