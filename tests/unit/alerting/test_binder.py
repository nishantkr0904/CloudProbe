"""Unit tests for the CloudWatch alarm binder.

The CloudWatch client is a hand-written fake rather than ``moto``: the
publisher takes an injected client, so a fake exercises the real translate,
dimension and namespace code path with no SDK, no credentials and no network.

Covers:
    - an alert translates into a complete PutMetricAlarm payload
    - alarm naming follows the rule-target-metric convention
    - dimensions carry probe type, target id and target tags
    - namespace forwarding (default and override)
    - severity mapping into the alarm description
    - a successful publish returns the definition and calls the client once
    - SDK failure translated into AlarmPublishError with the cause chained
    - invalid alarm configuration rejected at definition construction
    - the ``==`` operator rejected at translate (CloudWatch cannot express it)
    - translate is idempotent with respect to the rule
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from cloudprobe.alerting import (
    AlarmDefinition,
    AlarmPublishError,
    Alert,
    AlertingError,
    CloudWatchAlarmPublisher,
    ComparisonOperator,
    InvalidAlarmError,
    MetricKind,
)
from cloudprobe.config.models import AlertRule, AlertSeverity, ProbeType, Target

_MOMENT = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
_NAMESPACE = "CloudProbe/Network"


class _FakeCloudWatchClient:
    """Records every PutMetricAlarm call, or raises a configured error."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def put_metric_alarm(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


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


def _rule(**overrides: Any) -> AlertRule:
    base: dict[str, Any] = {
        "rule_id": "tcp-latency",
        "probe_type": ProbeType.TCP,
    }
    base.update(overrides)
    return AlertRule(**base)


def _alert(**overrides: Any) -> Alert:
    base: dict[str, Any] = {
        "rule_id": "tcp-latency",
        "target": _target(),
        "probe_type": ProbeType.TCP,
        "severity": AlertSeverity.WARNING,
        "metric": MetricKind.LATENCY_MS,
        "operator": ComparisonOperator.GT,
        "threshold_value": 100.0,
        "observed_value": 250.0,
        "breached": True,
        "timestamp": _MOMENT,
    }
    base.update(overrides)
    return Alert(**base)


def _publisher(client: _FakeCloudWatchClient, **overrides: Any) -> CloudWatchAlarmPublisher:
    base: dict[str, Any] = {"namespace": _NAMESPACE}
    base.update(overrides)
    return CloudWatchAlarmPublisher(client, **base)


# ---------------------------------------------------------------------------
# Translation: payload shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTranslate:
    def test_alert_translates_to_a_complete_definition(self) -> None:
        definition = CloudWatchAlarmPublisher(_FakeCloudWatchClient()).translate(_alert())
        assert isinstance(definition, AlarmDefinition)
        assert definition.alarm_name == "tcp-latency-web-1-latency_ms"
        assert definition.metric_name == "ProbeLatencyMs"
        assert definition.namespace == _NAMESPACE
        assert definition.threshold == 100.0
        assert definition.comparison_operator == "GreaterThanThreshold"

    def test_payload_has_the_put_metric_alarm_shape(self) -> None:
        definition = CloudWatchAlarmPublisher(_FakeCloudWatchClient()).translate(_alert())
        payload = definition.to_payload()
        assert payload["AlarmName"] == "tcp-latency-web-1-latency_ms"
        assert payload["MetricName"] == "ProbeLatencyMs"
        assert payload["Namespace"] == _NAMESPACE
        assert payload["Statistic"] == "Average"
        assert payload["Period"] == 60
        assert payload["EvaluationPeriods"] == 1
        assert payload["Threshold"] == 100.0
        assert payload["ComparisonOperator"] == "GreaterThanThreshold"
        assert payload["TreatMissingData"] == "notBreaching"

    def test_payload_carries_no_notification_actions(self) -> None:
        payload = CloudWatchAlarmPublisher(_FakeCloudWatchClient()).translate(_alert()).to_payload()
        assert "AlarmActions" not in payload
        assert "OKActions" not in payload
        assert "InsufficientDataActions" not in payload


# ---------------------------------------------------------------------------
# Translation: naming, dimensions, namespace, severity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNaming:
    def test_alarm_name_joins_rule_target_and_metric(self) -> None:
        alert = _alert(
            rule_id="latency",
            target=_target(target_id="db-2"),
            metric=MetricKind.AVAILABILITY,
        )
        definition = CloudWatchAlarmPublisher(_FakeCloudWatchClient()).translate(alert)
        assert definition.alarm_name == "latency-db-2-availability"


@pytest.mark.unit
class TestDimensions:
    def test_dimensions_carry_probe_type_target_and_tags(self) -> None:
        alert = _alert(target=_target(tags={"tier": "web", "env": "prod"}))
        definition = CloudWatchAlarmPublisher(_FakeCloudWatchClient()).translate(alert)
        assert definition.dimensions == {
            "ProbeType": "tcp",
            "TargetId": "web-1",
            "tier": "web",
            "env": "prod",
        }

    def test_payload_dimensions_use_cloudwatch_name_value_shape(self) -> None:
        payload = CloudWatchAlarmPublisher(_FakeCloudWatchClient()).translate(_alert()).to_payload()
        assert payload["Dimensions"] == [
            {"Name": "ProbeType", "Value": "tcp"},
            {"Name": "TargetId", "Value": "web-1"},
            {"Name": "tier", "Value": "web"},
        ]

    def test_target_without_tags_has_only_identity_dimensions(self) -> None:
        alert = _alert(target=_target(tags={}))
        definition = CloudWatchAlarmPublisher(_FakeCloudWatchClient()).translate(alert)
        assert definition.dimensions == {"ProbeType": "tcp", "TargetId": "web-1"}


@pytest.mark.unit
class TestNamespace:
    def test_default_namespace_is_the_project_namespace(self) -> None:
        definition = CloudWatchAlarmPublisher(_FakeCloudWatchClient()).translate(_alert())
        assert definition.namespace == _NAMESPACE

    def test_custom_namespace_overrides_the_default(self) -> None:
        publisher = _publisher(_FakeCloudWatchClient(), namespace="Custom/Namespace")
        definition = publisher.translate(_alert())
        assert definition.namespace == "Custom/Namespace"


@pytest.mark.unit
class TestSeverity:
    def test_severity_is_carried_through_to_the_definition(self) -> None:
        alert = _alert(severity=AlertSeverity.CRITICAL)
        definition = CloudWatchAlarmPublisher(_FakeCloudWatchClient()).translate(alert)
        assert definition.severity is AlertSeverity.CRITICAL

    def test_severity_names_the_alarm_description(self) -> None:
        definition = CloudWatchAlarmPublisher(_FakeCloudWatchClient()).translate(
            _alert(severity=AlertSeverity.CRITICAL)
        )
        assert "critical alarm for web-1" in definition.description
        assert "latency_ms > 100.0" in definition.description


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPublish:
    def test_successful_publish_calls_client_once_and_returns_definition(self) -> None:
        client = _FakeCloudWatchClient()
        definition = CloudWatchAlarmPublisher(client).publish(_alert())
        assert len(client.calls) == 1
        assert client.calls[0]["AlarmName"] == definition.alarm_name
        assert isinstance(definition, AlarmDefinition)

    def test_published_payload_is_the_translated_definition(self) -> None:
        client = _FakeCloudWatchClient()
        publisher = CloudWatchAlarmPublisher(client)
        definition = publisher.translate(_alert())
        publisher.publish(_alert())
        assert client.calls[0] == definition.to_payload()

    def test_sdk_error_is_translated_to_alarm_publish_error(self) -> None:
        client = _FakeCloudWatchClient(error=RuntimeError("Throttling"))
        with pytest.raises(AlarmPublishError) as excinfo:
            CloudWatchAlarmPublisher(client).publish(_alert())
        assert "PutMetricAlarm failed" in str(excinfo.value)

    def test_sdk_error_cause_is_chained(self) -> None:
        client = _FakeCloudWatchClient(error=RuntimeError("Throttling"))
        with pytest.raises(AlarmPublishError) as excinfo:
            CloudWatchAlarmPublisher(client).publish(_alert())
        assert isinstance(excinfo.value.__cause__, RuntimeError)

    def test_publish_error_is_an_alerting_error(self) -> None:
        client = _FakeCloudWatchClient(error=RuntimeError("boom"))
        with pytest.raises(AlertingError):
            CloudWatchAlarmPublisher(client).publish(_alert())


# ---------------------------------------------------------------------------
# Invalid configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvalidConfiguration:
    def test_equality_operator_is_rejected_at_translate(self) -> None:
        alert = _alert(operator=ComparisonOperator.EQ)
        with pytest.raises(InvalidAlarmError) as excinfo:
            CloudWatchAlarmPublisher(_FakeCloudWatchClient()).translate(alert)
        assert "tcp-latency" in str(excinfo.value)

    def test_equality_operator_is_rejected_at_publish_before_any_call(self) -> None:
        client = _FakeCloudWatchClient()
        with pytest.raises(InvalidAlarmError):
            CloudWatchAlarmPublisher(client).publish(_alert(operator=ComparisonOperator.EQ))
        assert client.calls == []

    def test_invalid_alarm_error_is_an_alerting_error(self) -> None:
        with pytest.raises(AlertingError):
            CloudWatchAlarmPublisher(_FakeCloudWatchClient()).translate(
                _alert(operator=ComparisonOperator.EQ)
            )


# ---------------------------------------------------------------------------
# Alarm definition validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAlarmDefinitionValidation:
    def test_empty_alarm_name_is_rejected(self) -> None:
        with pytest.raises(InvalidAlarmError):
            AlarmDefinition(
                alarm_name="",
                description="d",
                metric_name="ProbeLatencyMs",
                namespace=_NAMESPACE,
                dimensions={},
                threshold=1.0,
                comparison_operator="GreaterThanThreshold",
                severity=AlertSeverity.WARNING,
            )

    def test_non_finite_threshold_is_rejected(self) -> None:
        with pytest.raises(InvalidAlarmError):
            AlarmDefinition(
                alarm_name="a",
                description="d",
                metric_name="ProbeLatencyMs",
                namespace=_NAMESPACE,
                dimensions={},
                threshold=float("inf"),
                comparison_operator="GreaterThanThreshold",
                severity=AlertSeverity.WARNING,
            )

    def test_unsupported_comparison_operator_is_rejected(self) -> None:
        with pytest.raises(InvalidAlarmError):
            AlarmDefinition(
                alarm_name="a",
                description="d",
                metric_name="ProbeLatencyMs",
                namespace=_NAMESPACE,
                dimensions={},
                threshold=1.0,
                comparison_operator="EqualToThreshold",
                severity=AlertSeverity.WARNING,
            )

    def test_unsupported_statistic_is_rejected(self) -> None:
        with pytest.raises(InvalidAlarmError):
            AlarmDefinition(
                alarm_name="a",
                description="d",
                metric_name="ProbeLatencyMs",
                namespace=_NAMESPACE,
                dimensions={},
                threshold=1.0,
                comparison_operator="GreaterThanThreshold",
                severity=AlertSeverity.WARNING,
                statistic="Median",
            )

    def test_non_positive_period_is_rejected(self) -> None:
        with pytest.raises(InvalidAlarmError):
            AlarmDefinition(
                alarm_name="a",
                description="d",
                metric_name="ProbeLatencyMs",
                namespace=_NAMESPACE,
                dimensions={},
                threshold=1.0,
                comparison_operator="GreaterThanThreshold",
                severity=AlertSeverity.WARNING,
                period_seconds=0,
            )

    def test_non_positive_evaluation_periods_is_rejected(self) -> None:
        with pytest.raises(InvalidAlarmError):
            AlarmDefinition(
                alarm_name="a",
                description="d",
                metric_name="ProbeLatencyMs",
                namespace=_NAMESPACE,
                dimensions={},
                threshold=1.0,
                comparison_operator="GreaterThanThreshold",
                severity=AlertSeverity.WARNING,
                evaluation_periods=-1,
            )

    def test_unsupported_treat_missing_data_is_rejected(self) -> None:
        with pytest.raises(InvalidAlarmError):
            AlarmDefinition(
                alarm_name="a",
                description="d",
                metric_name="ProbeLatencyMs",
                namespace=_NAMESPACE,
                dimensions={},
                threshold=1.0,
                comparison_operator="GreaterThanThreshold",
                severity=AlertSeverity.WARNING,
                treat_missing_data="maybe",
            )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdempotency:
    def test_translate_ignores_observed_value_and_breach_flag(self) -> None:
        publisher = CloudWatchAlarmPublisher(_FakeCloudWatchClient())
        breached = _alert(observed_value=500.0, breached=True)
        satisfied = _alert(observed_value=50.0, breached=False)
        assert publisher.translate(breached) == publisher.translate(satisfied)

    def test_repeated_translate_yields_identical_definitions(self) -> None:
        publisher = CloudWatchAlarmPublisher(_FakeCloudWatchClient())
        alert = _alert()
        assert publisher.translate(alert) == publisher.translate(alert)
