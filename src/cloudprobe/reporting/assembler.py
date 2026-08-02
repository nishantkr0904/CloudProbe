"""Report assembly — collapsing a run's results into one ``Report``.

This is the reporting layer's documented public entry point (project-structure
§6.7): ``assemble(...) -> Report``.  It is a pure transform from values that
already happened — probe results and alert decisions — into an in-memory
``Report`` (architecture §9.1).  It executes no probe, publishes nothing,
renders nothing and writes nothing; the format renderers are later commits
that consume the report it builds.

``build_metadata`` is the one place the run's environment is captured.  The
hostname comes from ``platform.node()`` — the machine itself — rather than a
name lookup, so assembling a report performs no networking.
"""

from __future__ import annotations

import platform
from datetime import datetime

from cloudprobe.alerting.models import Alert
from cloudprobe.probes.base import ProbeResult
from cloudprobe.reporting.models import Report, RunMetadata, RunMode
from cloudprobe.reporting.summary import (
    latency_statistics,
    summarize_alerts,
    summarize_by_probe_type,
    summarize_by_target,
    summarize_inventory,
    summarize_outcomes,
)


def build_metadata(
    run_id: str,
    mode: RunMode,
    started_at: datetime,
    completed_at: datetime,
    hostname: str | None = None,
) -> RunMetadata:
    """Describe the run that produced the results being assembled.

    Args:
        run_id: An identifier for the run, unique within its retention window.
        mode: How the run was driven — ``RunMode.ONESHOT`` or ``RunMode.SCHEDULER``.
        started_at: Instant the run began.
        completed_at: Instant the run ended.
        hostname: Machine that ran it; defaults to ``platform.node()``.

    Returns:
        The metadata with any non-empty ``run_id`` and non-negative duration.

    Raises:
        InvalidReportError: The run id is empty, or completion precedes start.
    """
    return RunMetadata(
        run_id=run_id,
        mode=mode,
        started_at=started_at,
        completed_at=completed_at,
        hostname=hostname if hostname is not None else platform.node(),
    )


def assemble(
    metadata: RunMetadata,
    results: list[ProbeResult],
    alerts: list[Alert],
) -> Report:
    """Fold a run's results and alert decisions into a single ``Report``.

    Args:
        metadata: The run being summarised.
        results: Every probe result the run produced, in execution order.
        alerts: Every alert decision the run produced, breached or not.

    Returns:
        A complete in-memory report: metadata, inventory and outcome
        summaries (overall and grouped), latency statistics over successes,
        the alert summary, and the raw ``results`` and breached ``alerts`` a
        renderer walks.
    """
    # Target is unhashable (its probe_types is a list), so distinct targets
    # are collected by their stable id rather than by set membership.
    targets = list({result.target.target_id: result.target for result in results}.values())
    breached = [alert for alert in alerts if alert.breached]
    return Report(
        metadata=metadata,
        inventory=summarize_inventory(targets),
        outcomes=summarize_outcomes(results),
        outcomes_by_probe_type=summarize_by_probe_type(results),
        outcomes_by_target=summarize_by_target(results),
        latency=latency_statistics(results),
        alerts=summarize_alerts(alerts),
        results=tuple(results),
        breaches=tuple(breached),
    )
