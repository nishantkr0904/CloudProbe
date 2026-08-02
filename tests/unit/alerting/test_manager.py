"""Unit tests for the alert manager (threshold evaluation).

The manager is pure — one ``ProbeResult`` plus rules in, ``Alert`` objects out,
no state, no sockets, no AWS — so these tests build plain results and rules and
assert on the decisions.  No moto, no botocore, no network.

Covers:
    - a breached threshold produces a breached Alert
    - a satisfied threshold produces a non-breached Alert
    - warning and critical severities carry through from the rule
    - multiple rules produce one Alert each, in order
    - an unsupported comparison operator is rejected at construction
    - a rule with no threshold value raises at evaluation
    - evaluation is deterministic across repeated calls
    - the target-tag selector filters which rules apply
    - every metric kind and every operator is exercised
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from cloudprobe.alerting import (
    Alert,
    AlertingError,
    AlertManager,
    AlertRuleSpec,
    ComparisonOperator,
    InvalidRuleError,
    MetricKind,
    MissingThresholdError,
)
from cloudprobe.config.models import AlertRule, AlertSeverity, ProbeType, Target
from cloudprobe.probes.base import ProbeErrorClass, ProbeResult

_MOMENT = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _target(**overrides: Any) -> Target:
    base: dict[str, Any] = {
        "target_id": "web-1",
        "host": "10.20.1.10",
        "port": 443,
        "probe_types": [ProbeType.TCP],
        "tags": {"tier": "web"},
    }
    base.update(overrides)
    return Target(**base)


def _result(**overrides: Any) -> ProbeResult:
    base: dict[str, Any] = {
        "target": _target(),
        "probe_type": ProbeType.TCP,
        "success": True,
        "latency_ms": 120.0,
        "timestamp": _MOMENT,
    }
    base.update(overrides)
    return ProbeResult(**base)


def _rule(**overrides: Any) -> AlertRule:
    base: dict[str, Any] = {
        "rule_id": "tcp-latency",
        "probe_type": ProbeType.TCP,
    }
    base.update(overrides)
    return AlertRule(**base)


def _spec(**overrides: Any) -> AlertRuleSpec:
    base: dict[str, Any] = {
        "rule": _rule(),
        "metric": MetricKind.LATENCY_MS,
        "operator": ComparisonOperator.GT,
        "threshold_value": 100.0,
    }
    base.update(overrides)
    return AlertRuleSpec(**base)


# ---------------------------------------------------------------------------
# Threshold exceeded / not exceeded
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestThresholdComparison:
    def test_exceeded_threshold_is_a_breach(self) -> None:
        result = _result(latency_ms=250.0)
        alerts = AlertManager().evaluate(result, [_spec(threshold_value=100.0)])
        assert len(alerts) == 1
        assert isinstance(alerts[0], Alert)
        assert alerts[0].breached is True
        assert alerts[0].observed_value == 250.0
        assert alerts[0].threshold_value == 100.0

    def test_satisfied_threshold_is_not_a_breach(self) -> None:
        result = _result(latency_ms=50.0)
        alerts = AlertManager().evaluate(result, [_spec(threshold_value=100.0)])
        assert len(alerts) == 1
        assert alerts[0].breached is False

    def test_alert_carries_result_target_and_timestamp(self) -> None:
        result = _result()
        alert = AlertManager().evaluate(result, [_spec()])[0]
        assert alert.target is result.target
        assert alert.timestamp == _MOMENT
        assert alert.rule_id == "tcp-latency"
        assert alert.probe_type is ProbeType.TCP


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSeverity:
    def test_warning_severity_is_carried_through(self) -> None:
        spec = _spec(rule=_rule(severity=AlertSeverity.WARNING))
        alert = AlertManager().evaluate(_result(latency_ms=250.0), [spec])[0]
        assert alert.severity is AlertSeverity.WARNING

    def test_critical_severity_is_carried_through(self) -> None:
        spec = _spec(rule=_rule(severity=AlertSeverity.CRITICAL))
        alert = AlertManager().evaluate(_result(latency_ms=250.0), [spec])[0]
        assert alert.severity is AlertSeverity.CRITICAL


# ---------------------------------------------------------------------------
# Multiple rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMultipleRules:
    def test_each_applicable_rule_yields_one_alert_in_order(self) -> None:
        latency = _spec(
            rule=_rule(rule_id="latency"),
            metric=MetricKind.LATENCY_MS,
            operator=ComparisonOperator.GT,
            threshold_value=100.0,
        )
        availability = _spec(
            rule=_rule(rule_id="availability"),
            metric=MetricKind.AVAILABILITY,
            operator=ComparisonOperator.LT,
            threshold_value=1.0,
        )
        alerts = AlertManager().evaluate(_result(latency_ms=250.0), [latency, availability])
        assert [a.rule_id for a in alerts] == ["latency", "availability"]

    def test_rule_for_a_different_probe_type_is_skipped(self) -> None:
        spec = _spec(rule=_rule(probe_type=ProbeType.HTTP))
        alerts = AlertManager().evaluate(_result(probe_type=ProbeType.TCP), [spec])
        assert alerts == []


# ---------------------------------------------------------------------------
# Selector (target-tag filter)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelector:
    def test_matching_tag_filter_applies(self) -> None:
        spec = _spec(rule=_rule(target_tag_filter={"tier": "web"}))
        result = _result(target=_target(tags={"tier": "web"}))
        assert len(AlertManager().evaluate(result, [spec])) == 1

    def test_non_matching_tag_filter_is_skipped(self) -> None:
        spec = _spec(rule=_rule(target_tag_filter={"tier": "db"}))
        result = _result(target=_target(tags={"tier": "web"}))
        assert AlertManager().evaluate(result, [spec]) == []

    def test_empty_tag_filter_matches_any_target(self) -> None:
        spec = _spec(rule=_rule(target_tag_filter={}))
        result = _result(target=_target(tags={}))
        assert len(AlertManager().evaluate(result, [spec])) == 1


# ---------------------------------------------------------------------------
# Metric kinds
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetricKinds:
    def test_availability_of_failed_probe_is_zero(self) -> None:
        spec = _spec(
            metric=MetricKind.AVAILABILITY,
            operator=ComparisonOperator.LT,
            threshold_value=1.0,
        )
        result = _result(success=False, error_class=ProbeErrorClass.TIMEOUT)
        alert = AlertManager().evaluate(result, [spec])[0]
        assert alert.observed_value == 0.0
        assert alert.breached is True

    def test_availability_of_successful_probe_is_one(self) -> None:
        spec = _spec(
            metric=MetricKind.AVAILABILITY,
            operator=ComparisonOperator.LT,
            threshold_value=1.0,
        )
        alert = AlertManager().evaluate(_result(success=True), [spec])[0]
        assert alert.observed_value == 1.0
        assert alert.breached is False

    def test_packet_loss_of_failed_probe_is_one(self) -> None:
        spec = _spec(
            metric=MetricKind.PACKET_LOSS,
            operator=ComparisonOperator.GE,
            threshold_value=1.0,
        )
        result = _result(success=False, error_class=ProbeErrorClass.UNREACHABLE)
        alert = AlertManager().evaluate(result, [spec])[0]
        assert alert.observed_value == 1.0
        assert alert.breached is True

    def test_packet_loss_of_successful_probe_is_zero(self) -> None:
        spec = _spec(
            metric=MetricKind.PACKET_LOSS,
            operator=ComparisonOperator.GT,
            threshold_value=0.0,
        )
        alert = AlertManager().evaluate(_result(success=True), [spec])[0]
        assert alert.observed_value == 0.0
        assert alert.breached is False


# ---------------------------------------------------------------------------
# Comparison operators
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOperators:
    @pytest.mark.parametrize(
        ("operator", "observed", "threshold", "breached"),
        [
            (ComparisonOperator.GT, 2.0, 1.0, True),
            (ComparisonOperator.GT, 1.0, 1.0, False),
            (ComparisonOperator.GE, 1.0, 1.0, True),
            (ComparisonOperator.GE, 0.5, 1.0, False),
            (ComparisonOperator.LT, 0.5, 1.0, True),
            (ComparisonOperator.LT, 1.0, 1.0, False),
            (ComparisonOperator.LE, 1.0, 1.0, True),
            (ComparisonOperator.LE, 2.0, 1.0, False),
            (ComparisonOperator.EQ, 1.0, 1.0, True),
            (ComparisonOperator.EQ, 2.0, 1.0, False),
        ],
    )
    def test_operator_semantics(
        self,
        operator: ComparisonOperator,
        observed: float,
        threshold: float,
        breached: bool,
    ) -> None:
        spec = _spec(operator=operator, threshold_value=threshold)
        alert = AlertManager().evaluate(_result(latency_ms=observed), [spec])[0]
        assert alert.breached is breached

    def test_unsupported_operator_is_rejected_at_construction(self) -> None:
        with pytest.raises(InvalidRuleError):
            AlertRuleSpec(
                rule=_rule(),
                metric=MetricKind.LATENCY_MS,
                operator="≈",  # type: ignore[arg-type]
                threshold_value=1.0,
            )

    def test_unsupported_metric_is_rejected_at_construction(self) -> None:
        with pytest.raises(InvalidRuleError):
            AlertRuleSpec(
                rule=_rule(),
                metric="jitter",  # type: ignore[arg-type]
                operator=ComparisonOperator.GT,
                threshold_value=1.0,
            )

    def test_non_finite_threshold_is_rejected_at_construction(self) -> None:
        with pytest.raises(InvalidRuleError):
            AlertRuleSpec(
                rule=_rule(),
                metric=MetricKind.LATENCY_MS,
                operator=ComparisonOperator.GT,
                threshold_value=float("inf"),
            )


# ---------------------------------------------------------------------------
# Missing threshold configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMissingThreshold:
    def test_missing_threshold_raises_on_evaluation(self) -> None:
        spec = _spec(threshold_value=None)
        with pytest.raises(MissingThresholdError):
            AlertManager().evaluate(_result(), [spec])

    def test_missing_threshold_error_is_an_alerting_error(self) -> None:
        spec = _spec(threshold_value=None)
        with pytest.raises(AlertingError):
            AlertManager().evaluate(_result(), [spec])

    def test_inapplicable_rule_without_threshold_does_not_raise(self) -> None:
        # A rule that does not apply is never evaluated, so its missing
        # threshold is not an error for this result.
        spec = _spec(rule=_rule(probe_type=ProbeType.HTTP), threshold_value=None)
        assert AlertManager().evaluate(_result(probe_type=ProbeType.TCP), [spec]) == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeterminism:
    def test_repeated_evaluation_yields_identical_alerts(self) -> None:
        manager = AlertManager()
        result = _result(latency_ms=250.0)
        specs = [_spec(threshold_value=100.0)]
        first = manager.evaluate(result, specs)
        second = manager.evaluate(result, specs)
        assert first == second

    def test_empty_rule_list_yields_no_alerts(self) -> None:
        assert AlertManager().evaluate(_result(), []) == []
