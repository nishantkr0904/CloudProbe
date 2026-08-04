"""Unit tests for report assembly.

The assembler is a pure transform over already-produced values.  Only
``build_metadata`` reaches outside its arguments — to ``platform.node()`` for
the default hostname — so these tests inject the hostname to stay deterministic
and offline: no clock, no socket, no AWS.

The source module is ``assembler.py`` (project-structure §6.7 names the public
contract ``assemble(...) -> Report``), so this file is ``test_assembler.py``.

Covers assembler.py:
    - build_metadata records the run and defaults the hostname to the machine
    - an injected hostname overrides the default, keeping the test offline
    - build_metadata rejects an empty run id and reversed timing
    - assemble folds results into overall, per-type and per-target tallies
    - assemble summarises latency over successes only
    - assemble summarises alerts and keeps only breaches in the raw rows
    - assemble carries the raw results for a renderer to walk
    - an empty run assembles into a coherent empty report
"""

from __future__ import annotations

import platform
from datetime import datetime, timedelta
from typing import Any

import pytest

from cloudprobe.alerting.models import Alert
from cloudprobe.config.models import AlertSeverity, ProbeType, Target
from cloudprobe.probes.base import ProbeResult
from cloudprobe.reporting import (
    InvalidReportError,
    Report,
    RunMetadata,
    RunMode,
    assemble,
    build_metadata,
)

_START = datetime(2026, 8, 3, 6, 0, 0)
_END = datetime(2026, 8, 3, 6, 0, 12)


def _target(target_id: str = "web-1", **overrides: Any) -> Target:
    base: dict[str, Any] = {
        "target_id": target_id,
        "host": "10.20.1.10",
        "port": 443,
        "probe_types": [ProbeType.TCP, ProbeType.ICMP],
        "vpc_id": "vpc-1",
        "subnet_id": "subnet-1",
    }
    base.update(overrides)
    return Target(**base)


def _result(**overrides: Any) -> ProbeResult:
    base: dict[str, Any] = {
        "target": _target(),
        "probe_type": ProbeType.TCP,
        "success": True,
        "latency_ms": 10.0,
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


def _metadata() -> RunMetadata:
    return build_metadata(
        run_id="run-1",
        mode=RunMode.ONESHOT,
        started_at=_START,
        completed_at=_END,
        hostname="probe-host-1",
    )


# ---------------------------------------------------------------------------
# build_metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildMetadata:
    def test_metadata_records_the_run(self) -> None:
        metadata = build_metadata(
            run_id="run-1",
            mode=RunMode.SCHEDULER,
            started_at=_START,
            completed_at=_END,
            hostname="host",
        )
        assert metadata.run_id == "run-1"
        assert metadata.mode is RunMode.SCHEDULER
        assert metadata.hostname == "host"

    def test_hostname_defaults_to_the_machine(self) -> None:
        metadata = build_metadata(
            run_id="run-1",
            mode=RunMode.ONESHOT,
            started_at=_START,
            completed_at=_END,
        )
        assert metadata.hostname == platform.node()

    def test_an_injected_hostname_overrides_the_default(self) -> None:
        metadata = build_metadata(
            run_id="run-1",
            mode=RunMode.ONESHOT,
            started_at=_START,
            completed_at=_END,
            hostname="injected",
        )
        assert metadata.hostname == "injected"

    def test_an_empty_run_id_is_rejected(self) -> None:
        with pytest.raises(InvalidReportError):
            build_metadata(
                run_id="",
                mode=RunMode.ONESHOT,
                started_at=_START,
                completed_at=_END,
                hostname="host",
            )

    def test_reversed_timing_is_rejected(self) -> None:
        with pytest.raises(InvalidReportError):
            build_metadata(
                run_id="run-1",
                mode=RunMode.ONESHOT,
                started_at=_END,
                completed_at=_END - timedelta(seconds=1),
                hostname="host",
            )


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAssemble:
    def test_assemble_returns_a_report(self) -> None:
        report = assemble(_metadata(), [_result()], [])
        assert isinstance(report, Report)
        assert report.metadata.run_id == "run-1"

    def test_overall_outcomes_are_tallied(self) -> None:
        report = assemble(
            _metadata(),
            [_result(success=True), _result(success=False), _result(success=False)],
            [],
        )
        assert report.outcomes.total == 3
        assert report.outcomes.successes == 1
        assert report.outcomes.failures == 2

    def test_outcomes_are_grouped_by_probe_type(self) -> None:
        report = assemble(
            _metadata(),
            [_result(probe_type=ProbeType.TCP), _result(probe_type=ProbeType.ICMP)],
            [],
        )
        assert set(report.outcomes_by_probe_type) == {ProbeType.TCP, ProbeType.ICMP}

    def test_outcomes_are_grouped_by_target(self) -> None:
        report = assemble(
            _metadata(),
            [_result(target=_target("web-1")), _result(target=_target("db-1"))],
            [],
        )
        assert set(report.outcomes_by_target) == {"web-1", "db-1"}

    def test_latency_is_summarised_over_successes_only(self) -> None:
        report = assemble(
            _metadata(),
            [
                _result(success=True, latency_ms=10.0),
                _result(success=False, latency_ms=9999.0),
            ],
            [],
        )
        assert report.latency is not None
        assert report.latency.count == 1
        assert report.latency.maximum == 10.0

    def test_latency_is_none_when_nothing_succeeded(self) -> None:
        report = assemble(_metadata(), [_result(success=False)], [])
        assert report.latency is None

    def test_inventory_counts_the_targets_touched(self) -> None:
        report = assemble(
            _metadata(),
            [_result(target=_target("web-1")), _result(target=_target("db-1"))],
            [],
        )
        assert report.inventory.target_count == 2

    def test_alerts_are_summarised(self) -> None:
        report = assemble(
            _metadata(),
            [_result()],
            [_alert(breached=True), _alert(breached=False)],
        )
        assert report.alerts.total == 2
        assert report.alerts.breached == 1

    def test_raw_results_are_carried_for_rendering(self) -> None:
        first, second = _result(), _result(success=False)
        report = assemble(_metadata(), [first, second], [])
        assert report.results == (first, second)

    def test_only_breached_alerts_are_carried_as_raw_rows(self) -> None:
        breached, clear = _alert(breached=True), _alert(breached=False)
        report = assemble(_metadata(), [_result()], [breached, clear])
        assert report.breaches == (breached,)

    def test_an_empty_run_assembles_into_a_coherent_report(self) -> None:
        report = assemble(_metadata(), [], [])
        assert report.outcomes.total == 0
        assert report.outcomes_by_probe_type == {}
        assert report.outcomes_by_target == {}
        assert report.latency is None
        assert report.inventory.target_count == 0
        assert report.alerts.total == 0
        assert report.results == ()
        assert report.breaches == ()
