"""Aggregation — turning a list of results into the numbers a report shows.

Each function here is a pure fold over already-produced ``ProbeResult`` and
``Alert`` values (architecture §9.1): given the same input it returns the same
output, reads no clock, opens no socket and touches no AWS.  The assembler
composes them into a ``Report``; keeping them separate lets every statistic be
tested on its own, without building a whole report around it.

Latency is measured over successful probes only (architecture §7.1): a failed
probe has no latency worth summarising.
"""

from __future__ import annotations

from collections import Counter
from statistics import mean

from cloudprobe.alerting.models import Alert
from cloudprobe.config.models import AlertSeverity, ProbeType, Target
from cloudprobe.probes.base import ProbeResult
from cloudprobe.reporting.models import (
    AlertSummary,
    InventorySummary,
    LatencyStatistics,
    OutcomeStats,
)


def _percentile(ordered: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an already-sorted, non-empty sample.

    Nearest-rank — not interpolation — because it always returns an observed
    value and behaves correctly for the tiny samples a single run produces.
    """
    rank = max(1, -(-len(ordered) * fraction // 1))  # ceil, no float error
    return ordered[int(rank) - 1]


def summarize_outcomes(results: list[ProbeResult]) -> OutcomeStats:
    """Tally successes and failures across ``results``."""
    successes = sum(1 for result in results if result.success)
    return OutcomeStats(
        total=len(results),
        successes=successes,
        failures=len(results) - successes,
    )


def summarize_by_probe_type(
    results: list[ProbeResult],
) -> dict[ProbeType, OutcomeStats]:
    """Group ``results`` by probe type, preserving first-seen order."""
    grouped: dict[ProbeType, list[ProbeResult]] = {}
    for result in results:
        grouped.setdefault(result.probe_type, []).append(result)
    return {probe_type: summarize_outcomes(group) for probe_type, group in grouped.items()}


def summarize_by_target(results: list[ProbeResult]) -> dict[str, OutcomeStats]:
    """Group ``results`` by target id, preserving first-seen order."""
    grouped: dict[str, list[ProbeResult]] = {}
    for result in results:
        grouped.setdefault(result.target.target_id, []).append(result)
    return {target_id: summarize_outcomes(group) for target_id, group in grouped.items()}


def latency_statistics(results: list[ProbeResult]) -> LatencyStatistics | None:
    """Summarise latency over the successful probes in ``results``.

    Returns ``None`` when no probe succeeded: an empty distribution has no
    minimum, mean or percentile to report.
    """
    latencies = sorted(result.latency_ms for result in results if result.success)
    if not latencies:
        return None
    return LatencyStatistics(
        count=len(latencies),
        minimum=latencies[0],
        maximum=latencies[-1],
        mean=mean(latencies),
        p50=_percentile(latencies, 0.50),
        p95=_percentile(latencies, 0.95),
        p99=_percentile(latencies, 0.99),
    )


def summarize_alerts(alerts: list[Alert]) -> AlertSummary:
    """Count evaluated alerts, breaches, and breaches by severity."""
    breaches = [alert for alert in alerts if alert.breached]
    by_severity: Counter[AlertSeverity] = Counter(alert.severity for alert in breaches)
    return AlertSummary(
        total=len(alerts),
        breached=len(breaches),
        by_severity=dict(by_severity),
    )


def summarize_inventory(targets: list[Target]) -> InventorySummary:
    """Count the distinct targets, VPCs and subnets a run touched.

    ``vpc_id`` and ``subnet_id`` are optional on a ``Target``; only the ones
    actually present are counted.
    """
    return InventorySummary(
        target_count=len({target.target_id for target in targets}),
        vpc_count=len({t.vpc_id for t in targets if t.vpc_id is not None}),
        subnet_count=len({t.subnet_id for t in targets if t.subnet_id is not None}),
    )
