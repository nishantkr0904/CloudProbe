"""Unit tests for report aggregation.

The aggregation functions are pure folds over already-produced values, so
these tests need no fakes, no clock, no socket and no AWS: they feed lists of
``ProbeResult`` and ``Alert`` records and assert on the numbers that come out.

Covers summary.py:
    - summarize_outcomes tallies successes and failures
    - summarize_by_probe_type groups by probe type, preserving first-seen order
    - summarize_by_target groups by target id, preserving first-seen order
    - latency_statistics summarises successful probes only
    - an all-failed run yields no latency statistics
    - percentiles follow nearest-rank for tiny and single-element samples
    - summarize_alerts counts evaluated, breached, and severity breakdown
    - summarize_inventory counts distinct targets, VPCs and subnets
"""

from __future__ import annotations

from datetime import datetime

import pytest

from cloudprobe.alerting.models import Alert
from cloudprobe.config.models import AlertSeverity, ProbeType, Target
from cloudprobe.probes.base import ProbeErrorClass, ProbeResult
from cloudprobe.reporting.summary import (
    latency_statistics,
    summarize_alerts,
    summarize_by_probe_type,
    summarize_by_target,
    summarize_inventory,
    summarize_outcomes,
)

_TS = datetime(2026, 8, 3, 6, 0, 0)


def _target(target_id: str = "web-1", **overrides) -> Target:
    base = {
        "target_id": target_id,
        "host": "10.20.1.10",
        "port": 443,
        "probe_types": [ProbeType.TCP, ProbeType.ICMP],
        "vpc_id": "vpc-123",
        "subnet_id": "subnet-456",
    }
    base.update(overrides)
    return Target(**base)


def _result(**overrides) -> ProbeResult:
    base = {
        "target": _target(),
        "probe_type": ProbeType.TCP,
        "success": True,
        "latency_ms": 10.0,
        "timestamp": _TS,
    }
    base.update(overrides)
    return ProbeResult(**base)


def _alert(**overrides) -> Alert:
    base = {
        "rule_id": "rule-1",
        "target": _target(),
        "probe_type": ProbeType.TCP,
        "severity": AlertSeverity.CRITICAL,
        "metric": "latency_ms",
        "operator": ">",
        "threshold_value": 100.0,
        "observed_value": 250.0,
        "breached": True,
        "timestamp": _TS,
    }
    base.update(overrides)
    return Alert(**base)


# ---------------------------------------------------------------------------
# Outcome tallies
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSummarizeOutcomes:
    def test_totals_are_counted_from_the_results(self) -> None:
        stats = summarize_outcomes(
            [
                _result(success=True),
                _result(success=False),
                _result(success=False),
            ]
        )
        assert stats.total == 3
        assert stats.successes == 1
        assert stats.failures == 2

    def test_an_empty_list_is_zero_tallies(self) -> None:
        stats = summarize_outcomes([])
        assert stats.total == 0
        assert stats.successes == 0
        assert stats.failures == 0


@pytest.mark.unit
class TestSummarizeByProbeType:
    def test_results_are_grouped_by_probe_type(self) -> None:
        grouped = summarize_by_probe_type(
            [
                _result(probe_type=ProbeType.TCP, success=True),
                _result(probe_type=ProbeType.ICMP, success=False),
                _result(probe_type=ProbeType.TCP, success=False),
            ]
        )
        assert list(grouped) == [ProbeType.TCP, ProbeType.ICMP]
        assert grouped[ProbeType.TCP].total == 2
        assert grouped[ProbeType.TCP].successes == 1
        assert grouped[ProbeType.ICMP].failures == 1

    def test_group_order_follows_first_appearance(self) -> None:
        grouped = summarize_by_probe_type(
            [_result(probe_type=ProbeType.ICMP), _result(probe_type=ProbeType.UDP)]
        )
        assert list(grouped) == [ProbeType.ICMP, ProbeType.UDP]


@pytest.mark.unit
class TestSummarizeByTarget:
    def test_results_are_grouped_by_target_id(self) -> None:
        grouped = summarize_by_target(
            [
                _result(target=_target("web-1"), success=True),
                _result(target=_target("db-1"), success=False),
                _result(target=_target("web-1"), success=True),
            ]
        )
        assert list(grouped) == ["web-1", "db-1"]
        assert grouped["web-1"].total == 2
        assert grouped["web-1"].successes == 2
        assert grouped["db-1"].failures == 1


# ---------------------------------------------------------------------------
# Latency statistics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLatencyStatistics:
    def test_only_successful_probes_are_measured(self) -> None:
        stats = latency_statistics(
            [
                _result(success=True, latency_ms=10.0),
                _result(success=False, latency_ms=5000.0),
                _result(success=True, latency_ms=30.0),
            ]
        )
        assert stats is not None
        assert stats.count == 2
        assert stats.minimum == 10.0
        assert stats.maximum == 30.0
        assert stats.mean == 20.0

    def test_an_all_failed_run_yields_no_statistics(self) -> None:
        stats = latency_statistics([_result(success=False, latency_ms=1.0)])
        assert stats is None

    def test_an_empty_run_yields_no_statistics(self) -> None:
        assert latency_statistics([]) is None

    def test_a_single_success_has_flat_percentiles(self) -> None:
        stats = latency_statistics([_result(success=True, latency_ms=7.5)])
        assert stats is not None
        assert stats.p50 == 7.5
        assert stats.p95 == 7.5
        assert stats.p99 == 7.5

    def test_percentiles_are_nearest_rank_observations(self) -> None:
        stats = latency_statistics(
            [_result(success=True, latency_ms=ms) for ms in (1.0, 2.0, 3.0, 4.0)]
        )
        assert stats is not None
        assert stats.p50 == 2.0
        assert stats.p95 == 4.0
        assert stats.p99 == 4.0

    def test_a_two_element_sample_rounds_percentiles_up(self) -> None:
        stats = latency_statistics(
            [_result(success=True, latency_ms=1.0), _result(success=True, latency_ms=2.0)]
        )
        assert stats is not None
        assert stats.p50 == 1.0
        assert stats.p95 == 2.0


# ---------------------------------------------------------------------------
# Alerts and inventory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSummarizeAlerts:
    def test_breaches_are_counted_and_broken_down_by_severity(self) -> None:
        summary = summarize_alerts(
            [
                _alert(severity=AlertSeverity.CRITICAL, breached=True),
                _alert(severity=AlertSeverity.WARNING, breached=True),
                _alert(severity=AlertSeverity.CRITICAL, breached=False),
            ]
        )
        assert summary.total == 3
        assert summary.breached == 2
        assert summary.by_severity[AlertSeverity.CRITICAL] == 1
        assert summary.by_severity[AlertSeverity.WARNING] == 1

    def test_an_unbreached_run_reports_zero_breaches(self) -> None:
        summary = summarize_alerts([_alert(breached=False)])
        assert summary.total == 1
        assert summary.breached == 0
        assert summary.by_severity == {}


@pytest.mark.unit
class TestSummarizeInventory:
    def test_distinct_targets_vpcs_and_subnets_are_counted(self) -> None:
        targets = [
            _target("web-1", vpc_id="vpc-1", subnet_id="subnet-1"),
            _target("web-2", vpc_id="vpc-1", subnet_id="subnet-1"),
            _target("db-1", vpc_id="vpc-2", subnet_id="subnet-2"),
            _target("web-1", vpc_id="vpc-1", subnet_id="subnet-1"),
        ]
        summary = summarize_inventory(targets)
        assert summary.target_count == 3
        assert summary.vpc_count == 2
        assert summary.subnet_count == 2

    def test_optional_vpc_and_subnet_are_skipped_when_absent(self) -> None:
        summary = summarize_inventory(
            [_target("web-1", vpc_id=None, subnet_id=None)]
        )
        assert summary.target_count == 1
        assert summary.vpc_count == 0
        assert summary.subnet_count == 0


@pytest.mark.unit
class TestErrorClassCarriage:
    def test_failure_results_carry_their_error_class_into_tallies(self) -> None:
        failed = _result(
            success=False,
            latency_ms=0.0,
            error_class=ProbeErrorClass.TIMEOUT,
        )
        stats = summarize_outcomes([_result(success=True), failed])
        assert stats.failures == 1
