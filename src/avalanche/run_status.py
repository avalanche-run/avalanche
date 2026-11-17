"""List-safe run status semantics shared by local and hosted runtimes.

Raw lifecycle values remain host-specific. No exception text belongs in a summary.
All reducers here inspect only fixed-width counters and bounded identifiers.
"""

from __future__ import annotations

import re
from typing import Literal

HumanStatus = Literal[
    "queued", "running", "waiting", "succeeded", "failed", "cancelled", "unknown"
]
WaitingOn = tuple[int, int, int, int, int]
HUMAN_STATUSES: tuple[HumanStatus, ...] = (
    "queued",
    "running",
    "waiting",
    "succeeded",
    "failed",
    "cancelled",
    "unknown",
)
LIVE_STATUSES = frozenset({"queued", "running", "waiting", "unknown"})
TERMINAL_STATUSES = frozenset(HUMAN_STATUSES) - LIVE_STATUSES
WAITING_ON_SLOTS = ("operator", "decision", "event", "run", "sleep")
WAIT_KIND_SLOT = {
    "decision": 1,
    "event": 2,
    "machine": 2,
    "run": 3,
    "sleep": 4,
    "machine_timer": 4,
}
EMPTY_WAITING_ON: WaitingOn = (0, 0, 0, 0, 0)
LOCAL_STATUS_MAP: dict[str, HumanStatus] = {
    "requesting": "queued",
    "pending": "queued",
    "running": "running",
    "waiting": "waiting",
    "success": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
}
LIFECYCLE_STATUS_MAP: dict[str, HumanStatus] = {
    "succeeded": "succeeded",
    "cancelled": "cancelled",
    "failed": "failed",
    "rejected": "failed",
    "expired": "failed",
    "integrity": "unknown",
    "unknown": "unknown",
    "submitted": "queued",
    "queued": "queued",
    "resumable": "queued",
    "parked": "waiting",
    "waiting": "waiting",
    "admitted": "running",
}
ERROR_TEMPLATES = {
    "node_failed_type": "{error_type} in step {step}",
    "node_failed": "Step {step} failed ({reason})",
    "node_timeout": "Step {step} timed out",
    "worker_lost": "Worker lost while running step {step}",
    "wait_expired_step": "A wait expired in step {step}",
    "wait_expired": "A wait expired",
    "run_rejected": "Rejected at admission",
    "integrity": "Projection integrity fault",
    "coordinator_failed": "Run coordinator failed; see run activity",
    "coordinator_protocol": "Run coordinator protocol failed; see run activity",
    "coordinator_exited": "Run coordinator exited without an outcome; see run activity",
    "result_store_failed": "Workflow result could not be recorded; see run activity",
    "unknown": "Run failed",
}
_ERROR_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")


def human_status(lifecycle: str, active: int, open_waits: int) -> HumanStatus:
    """Hosted lifecycle mapping, with terminal/queued precedence over waits."""
    if lifecycle == "admitted" and open_waits > 0 and active == 0:
        return "waiting"
    return LIFECYCLE_STATUS_MAP[lifecycle]


def local_human_status(raw: str, open_waits: int, running_outside_wait: int) -> HumanStatus:
    if raw == "running" and open_waits > 0 and running_outside_wait == 0:
        return "waiting"
    return LOCAL_STATUS_MAP[raw]


def waiting_on_label(counts: WaitingOn) -> str:
    total = sum(counts)
    if not total:
        return ""
    first = next(slot for slot, count in zip(WAITING_ON_SLOTS, counts) if count)
    return f"waiting on {first}" + (f" +{total - 1}" if total > 1 else "")


def error_step(node_id: str) -> str | None:
    """Drop, never truncate, the exact detail identity when it exceeds the bound."""
    return node_id if len(node_id.encode()) <= 128 else None


def error_message(
    code: str,
    step: str | None = None,
    *,
    error_type: str | None = None,
    reason: str = "step_failed",
) -> str:
    """Format trusted closed codes and a validated class name, never exception text.

    ``reason`` is the caller's closed NodeError code or failure reason, not a
    free-form error message. Step identity is truncated only inside the template.
    """
    short_step = "" if step is None else step.encode()[:48].decode("utf-8", "ignore")
    template = code if code in ERROR_TEMPLATES else "unknown"
    if code == "node_failed":
        if reason == "node_timeout":
            template = "node_timeout"
        elif error_type is not None and _ERROR_TYPE.fullmatch(error_type):
            template = "node_failed_type"
    elif code == "wait_expired" and step is not None:
        template = "wait_expired_step"
    return (
        ERROR_TEMPLATES[template]
        .format(step=short_step, error_type=error_type, reason=reason)
        .encode()[:120]
        .decode("utf-8", "ignore")
    )
