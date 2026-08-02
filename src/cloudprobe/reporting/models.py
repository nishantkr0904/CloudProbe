"""In-memory report contracts — the shape a run collapses into.

The reporting layer materialises the outcome of a run into a single ``Report``
aggregate (project-structure §6.7, architecture §9.1).  This module owns that
aggregate and its parts: run metadata, an inventory summary, success/failure
tallies (overall and grouped), latency statistics, an alert summary, and the
raw ``ProbeResult`` and ``Alert`` values a renderer later walks.

Every type here is a frozen dataclass built from the standard library.  A
report is a fact about a run that already happened, so it must not be edited
after assembly, and it needs no Pydantic — these values are produced internally
from already-validated inputs, never parsed from user YAML.

This module holds no aggregation logic (that is ``summary.py``), renders
nothing, opens no socket and touches no AWS: it is the static contract the
assembler builds and a renderer will consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from cloudprobe.alerting.models import Alert
from cloudprobe.config.models import AlertSeverity, ProbeType
from cloudprobe.probes.base import ProbeResult


class ReportingError(Exception):
    """Base class for every error the reporting layer raises.

    Callers catch this one type to handle any reporting failure without
    importing anything beyond the package's public surface.
    """


class InvalidReportError(ReportingError):
    """Report metadata was built from values that cannot describe a run.

    An empty run id, or a completion instant before the start, is a caller
    error the report must reject rather than carry silently.
    """


class RunMode(str, Enum):
    """How the run that produced this report was driven.

    String-valued to match the ``CLOUDPROBE_MODE`` environment variable and
    the one-shot / scheduler split of architecture §7.4, and so the value
    survives verbatim into a rendered report.
    """

    ONESHOT = "oneshot"
    SCHEDULER = "scheduler"


@dataclass(frozen=True)
class RunMetadata:
    """Identity and timing of a single run.

    Validated at construction because these are the caller-supplied facts a
    report cannot be trusted without: an anonymous run cannot be filed, and a
    run cannot finish before it starts.  ``hostname`` is captured from the
    machine, never from the network.
    """

    run_id: str
    mode: RunMode
    started_at: datetime
    completed_at: datetime
    hostname: str

    def __post_init__(self) -> None:
        if not self.run_id:
            raise InvalidReportError("run_id must not be empty")
        if self.completed_at < self.started_at:
            raise InvalidReportError(
                f"completed_at {self.completed_at.isoformat()} precedes "
                f"started_at {self.started_at.isoformat()}"
            )

    @property
    def duration_seconds(self) -> float:
        """Wall-clock seconds the run occupied, never negative."""
        return (self.completed_at - self.started_at).total_seconds()


@dataclass(frozen=True)
class OutcomeStats:
    """Success and failure tallies for a set of probe results.

    One shape used three ways — overall, per probe type, per target — so the
    same counts mean the same thing everywhere they appear.
    """

    total: int
    successes: int
    failures: int

    @property
    def success_ratio(self) -> float | None:
        """Fraction of results that succeeded, or ``None`` when there are none.

        ``None`` — not ``0.0`` or ``1.0`` — because an empty set has no ratio
        to report, and inventing one would misrepresent the run.
        """
        if self.total == 0:
            return None
        return self.successes / self.total


@dataclass(frozen=True)
class LatencyStatistics:
    """Latency distribution over the *successful* probes of a run.

    Computed for successes only (architecture §7.1): a timed-out probe has no
    meaningful latency, so folding failures in would poison the percentiles.
    Absence of any success is represented by ``None`` at the call site, not by
    a zero-filled instance.
    """

    count: int
    minimum: float
    maximum: float
    mean: float
    p50: float
    p95: float
    p99: float


@dataclass(frozen=True)
class InventorySummary:
    """The shape of the fleet a run exercised.

    Derived from the targets the run's results reference — the static-vs-
    dynamic split of architecture §9.1 needs discovery's inventory, which
    reporting may not import (§6.7), so it is deferred to a later commit.
    """

    target_count: int
    vpc_count: int
    subnet_count: int


@dataclass(frozen=True)
class AlertSummary:
    """How many rules fired, and at what severity.

    ``total`` counts every evaluated alert; ``breached`` counts the ones that
    fired; ``by_severity`` breaks the breaches down so a report can lead with
    its most serious findings.
    """

    total: int
    breached: int
    by_severity: dict[AlertSeverity, int]


@dataclass(frozen=True)
class Report:
    """The complete in-memory outcome of one run.

    Carries both the aggregates a human reads and the raw ``results`` and
    ``breaches`` a renderer walks row by row: ``render(report, format)`` is
    handed only this object (§6.7), and CSV is one row per ``ProbeResult``
    (architecture §9.2), so the rows must live here.
    """

    metadata: RunMetadata
    inventory: InventorySummary
    outcomes: OutcomeStats
    outcomes_by_probe_type: dict[ProbeType, OutcomeStats]
    outcomes_by_target: dict[str, OutcomeStats]
    latency: LatencyStatistics | None
    alerts: AlertSummary
    results: tuple[ProbeResult, ...]
    breaches: tuple[Alert, ...]
