"""CloudProbe reporting layer — public surface.

This package materialises the outcome of a run into a single in-memory
``Report`` aggregate (project-structure §6.7, architecture §9.1): run
metadata, an inventory summary, success/failure tallies, latency statistics,
an alert summary, and the raw results and breaches a renderer will later walk.

``assemble`` is the documented entry point.  The CLI, storage and scheduler
import the report models, the aggregation helpers and the error hierarchy from
here — never from the internal submodules.

This layer only transforms results that already exist.  It renders no JSON,
CSV or HTML, writes no file, publishes no metric, sends no notification,
evaluates no threshold, executes no probe and calls no AWS or SSH client.
Those formats and side effects are later commits; the ``Report`` they consume
does not change.
"""

from cloudprobe.reporting.assembler import assemble, build_metadata
from cloudprobe.reporting.models import (
    AlertSummary,
    InvalidReportError,
    InventorySummary,
    LatencyStatistics,
    OutcomeStats,
    Report,
    ReportingError,
    RunMetadata,
    RunMode,
)
from cloudprobe.reporting.summary import (
    latency_statistics,
    summarize_alerts,
    summarize_by_probe_type,
    summarize_by_target,
    summarize_inventory,
    summarize_outcomes,
)

__all__ = [
    "AlertSummary",
    "InvalidReportError",
    "InventorySummary",
    "LatencyStatistics",
    "OutcomeStats",
    "Report",
    "ReportingError",
    "RunMetadata",
    "RunMode",
    "assemble",
    "build_metadata",
    "latency_statistics",
    "summarize_alerts",
    "summarize_by_probe_type",
    "summarize_by_target",
    "summarize_inventory",
    "summarize_outcomes",
]
