"""Shared contracts for the probe engine.

Every probe, whatever its protocol, returns the same shape: a ``ProbeResult``.
This module owns that shape and the enumerated failure taxonomy its
``error_class`` field draws from, so downstream layers (alerting, reporting,
metrics) depend on one stable contract rather than a type per probe.

Only the failure classes a probe in this codebase can currently emit are
defined.  Each new probe adds the classes it distinguishes in the same commit
that introduces it — the enum is never populated speculatively.

``Target`` and ``ProbeType`` are owned by ``config/models.py``; this module
produces results *about* targets and invents no target shape of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from cloudprobe.config.models import ProbeType, Target


class ProbeErrorClass(str, Enum):
    """Wire-level classification carried by a failed ``ProbeResult``.

    String-valued because it is used verbatim as a metric dimension.  The
    probe engine never collapses two distinct failure modes into one value:
    a timeout is not a refusal is not a DNS failure.
    """

    TIMEOUT = "timeout"
    REFUSED = "refused"
    UNREACHABLE = "unreachable"
    DNS_FAILURE = "dns_failure"
    SOCKET_ERROR = "socket_error"
    # A probe's transport could not be executed at all — distinct from any
    # observation made over the wire.
    COMMAND_ERROR = "command_error"


@dataclass(frozen=True)
class ProbeResult:
    """The outcome of one probe execution against one target.

    A result is produced for both success and failure — a failed probe is
    data, not an exception.  ``error_class`` is ``None`` exactly when
    ``success`` is ``True``; on failure it carries the wire-level cause and
    ``raw`` the underlying error text for diagnostics.
    """

    target: Target
    probe_type: ProbeType
    success: bool
    latency_ms: float
    timestamp: datetime
    error_class: ProbeErrorClass | None = None
    raw: str | None = None
