"""Unit tests for the reporting model contracts.

The report models are frozen dataclasses built from already-validated inputs,
so these tests need no fakes, no clock, no socket and no AWS: they exercise
construction, the validation boundary on ``RunMetadata``, and the derived
properties a report presents.

Covers models.py:
    - RunMetadata accepts a valid run and records its fields
    - duration_seconds is the wall-clock span of the run
    - an empty run id is rejected
    - completion before start is rejected
    - rejection errors are reporting errors
    - OutcomeStats tallies and success_ratio semantics
    - LatencyStatistics carries the five summary numbers
    - InventorySummary counts targets, VPCs and subnets
    - AlertSummary carries total, breached and per-severity counts
    - Report carries its aggregates and the raw rows a renderer walks
    - every model is frozen
    - RunMode values match the one-shot / scheduler split
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from typing import Any

import pytest

from cloudprobe.alerting.models import Alert
from cloudprobe.config.models import (
    AlertSeverity,
    ProbeType,
    Target,
)
from cloudprobe.probes.base import ProbeResult
from cloudprobe.reporting import (
    InvalidReportError,
    ReportingError,
    RunMetadata,
    RunMode,
)
from cloudprobe.reporting.models import (
    AlertSummary,
    InventorySummary,
    LatencyStatistics,
    OutcomeStats,
    Report,
)

_START = datetime(2026, 8, 3, 6, 0, 0)
_END = datetime(2026, 8, 3, 6, 0, 12)


def _target(**overrides: Any) -> Target:
    base: dict[str, Any] = {
        "target_id": "web-1",
        "host": "10.20.1.10",
        "port": 443,
        "probe_types": [ProbeType.TCP],
        "vpc_id": "vpc-123",
        "subnet_id": "subnet-456",
    }
    base.update(overrides)
    return Target(**base)


def _result(**overrides: Any) -> ProbeResult:
    base: dict[str, Any] = {
        "target": _target(),
        "probe_type": ProbeType.TCP,
        "success": True,
        "latency_ms": 12.5,
        "timestamp": _START,
    }
    base.update(overrides)
    return ProbeResult(**base)


def _alert(**overrides: Any) -> Alert:
    base: dict[str, Any] = {
        "rule_id": "rule-1",
        "target": _target(),
        "probe_type": ProbeType.TCP,
        "severity": AlertSeverity.CRITICAL,
        "metric": "latency_ms",
        "operator": ">",
        "threshold_value": 100.0,
        "observed_value": 250.0,
        "breached": True,
        "timestamp": _END,
    }
    base.update(overrides)
    return Alert(**base)


# ---------------------------------------------------------------------------
# RunMetadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunMetadata:
    def test_metadata_records_the_run_fields(self) -> None:
        metadata = RunMetadata(
            run_id="run-1",
            mode=RunMode.ONESHOT,
            started_at=_START,
            completed_at=_END,
            hostname="probe-host-1",
        )
        assert metadata.run_id == "run-1"
        assert metadata.mode is RunMode.ONESHOT
        assert metadata.started_at == _START
        assert metadata.completed_at == _END
        assert metadata.hostname == "probe-host-1"

    def test_duration_is_the_wall_clock_span(self) -> None:
        metadata = RunMetadata(
            run_id="run-1",
            mode=RunMode.ONESHOT,
            started_at=_START,
            completed_at=_END,
            hostname="host",
        )
        assert metadata.duration_seconds == 12.0

    def test_an_empty_run_id_is_rejected(self) -> None:
        with pytest.raises(InvalidReportError):
            RunMetadata(
                run_id="",
                mode=RunMode.ONESHOT,
                started_at=_START,
                completed_at=_END,
                hostname="host",
            )

    def test_completion_before_start_is_rejected(self) -> None:
        with pytest.raises(InvalidReportError):
            RunMetadata(
                run_id="run-1",
                mode=RunMode.ONESHOT,
                started_at=_START,
                completed_at=_START - timedelta(seconds=1),
                hostname="host",
            )

    def test_rejection_errors_are_reporting_errors(self) -> None:
        with pytest.raises(ReportingError):
            RunMetadata(
                run_id="",
                mode=RunMode.ONESHOT,
                started_at=_START,
                completed_at=_END,
                hostname="host",
            )


# ---------------------------------------------------------------------------
# Statistics shapes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOutcomeStats:
    def test_ratio_is_successes_over_total(self) -> None:
        assert OutcomeStats(total=4, successes=3, failures=1).success_ratio == 0.75

    def test_ratio_is_none_for_an_empty_set(self) -> None:
        assert OutcomeStats(total=0, successes=0, failures=0).success_ratio is None

    def test_ratio_is_one_when_everything_succeeded(self) -> None:
        assert OutcomeStats(total=2, successes=2, failures=0).success_ratio == 1.0

    def test_ratio_is_zero_when_nothing_succeeded(self) -> None:
        assert OutcomeStats(total=2, successes=0, failures=2).success_ratio == 0.0


@pytest.mark.unit
class TestLatencyStatistics:
    def test_statistics_carry_the_five_summary_numbers(self) -> None:
        stats = LatencyStatistics(
            count=3, minimum=1.0, maximum=3.0, mean=2.0, p50=1.5, p95=2.5, p99=2.9
        )
        assert stats.count == 3
        assert stats.minimum == 1.0
        assert stats.maximum == 3.0
        assert stats.mean == 2.0
        assert stats.p50 == 1.5
        assert stats.p95 == 2.5
        assert stats.p99 == 2.9


@pytest.mark.unit
class TestInventorySummary:
    def test_inventory_counts_targets_vpcs_and_subnets(self) -> None:
        summary = InventorySummary(target_count=2, vpc_count=1, subnet_count=2)
        assert summary.target_count == 2
        assert summary.vpc_count == 1
        assert summary.subnet_count == 2


@pytest.mark.unit
class TestAlertSummary:
    def test_alert_summary_carries_total_breached_and_severity_breakdown(self) -> None:
        summary = AlertSummary(
            total=4,
            breached=3,
            by_severity={AlertSeverity.CRITICAL: 2, AlertSeverity.WARNING: 1},
        )
        assert summary.total == 4
        assert summary.breached == 3
        assert summary.by_severity[AlertSeverity.CRITICAL] == 2


# ---------------------------------------------------------------------------
# The Report aggregate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReport:
    def test_report_carries_aggregates_and_raw_rows(self) -> None:
        metadata = RunMetadata(
            run_id="run-1",
            mode=RunMode.SCHEDULER,
            started_at=_START,
            completed_at=_END,
            hostname="host",
        )
        result = _result()
        breach = _alert()
        report = Report(
            metadata=metadata,
            inventory=InventorySummary(target_count=1, vpc_count=1, subnet_count=1),
            outcomes=OutcomeStats(total=1, successes=1, failures=0),
            outcomes_by_probe_type={ProbeType.TCP: OutcomeStats(1, 1, 0)},
            outcomes_by_target={"web-1": OutcomeStats(1, 1, 0)},
            latency=LatencyStatistics(1, 12.5, 12.5, 12.5, 12.5, 12.5, 12.5),
            alerts=AlertSummary(total=1, breached=1, by_severity={AlertSeverity.CRITICAL: 1}),
            results=(result,),
            breaches=(breach,),
        )
        assert report.metadata is metadata
        assert report.inventory.target_count == 1
        assert report.outcomes.total == 1
        assert report.outcomes_by_probe_type[ProbeType.TCP].successes == 1
        assert report.outcomes_by_target["web-1"].failures == 0
        assert report.latency is not None
        assert report.latency.mean == 12.5
        assert report.alerts.breached == 1
        assert report.results == (result,)
        assert report.breaches == (breach,)

    def test_report_is_frozen(self) -> None:
        report = Report(
            metadata=RunMetadata(
                run_id="run-1",
                mode=RunMode.ONESHOT,
                started_at=_START,
                completed_at=_END,
                hostname="host",
            ),
            inventory=InventorySummary(0, 0, 0),
            outcomes=OutcomeStats(0, 0, 0),
            outcomes_by_probe_type={},
            outcomes_by_target={},
            latency=None,
            alerts=AlertSummary(0, 0, {}),
            results=(),
            breaches=(),
        )
        with pytest.raises(FrozenInstanceError):
            report.inventory = InventorySummary(1, 1, 1)  # type: ignore[misc]


@pytest.mark.unit
class TestRunMode:
    def test_run_mode_matches_the_oneshot_scheduler_split(self) -> None:
        assert RunMode.ONESHOT.value == "oneshot"
        assert RunMode.SCHEDULER.value == "scheduler"
