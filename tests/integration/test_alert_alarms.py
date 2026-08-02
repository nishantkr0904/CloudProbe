"""Integration: probe results → alert manager → CloudWatch alarm publisher.

Exercises the boundary between the alert manager and the CloudWatch alarm
publisher (architecture §10.2 "Alerting → moto alarms + SNS").  A real
``boto3`` CloudWatch client backed by ``moto`` satisfies the
``CloudWatchAlarmClient`` protocol, so an alert produced by the manager is
translated into an ``AlarmDefinition`` and its ``PutMetricAlarm`` request is
accepted by the fake service.

No real CloudWatch is touched: ``moto`` keeps the alarm state in memory and
requires no credentials or network.
"""

from __future__ import annotations

from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

from cloudprobe.alerting import (
    AlertManager,
    AlertRuleSpec,
    CloudWatchAlarmPublisher,
    ComparisonOperator,
    MetricKind,
)
from cloudprobe.config.models import AlertRule, AlertSeverity, ProbeType, Target
from cloudprobe.probes import ProbeResult

_TARGET = Target(
    target_id="web-1",
    host="10.20.1.10",
    port=443,
    probe_types=[ProbeType.TCP],
)


def _result(latency_ms: float) -> ProbeResult:
    return ProbeResult(
        target=_TARGET,
        probe_type=ProbeType.TCP,
        success=True,
        latency_ms=latency_ms,
        timestamp=datetime(2026, 8, 3, 6, 0, 0, tzinfo=timezone.utc),
    )


def _rule(threshold: float) -> AlertRuleSpec:
    return AlertRuleSpec(
        rule=AlertRule(
            rule_id="high-latency",
            probe_type=ProbeType.TCP,
            severity=AlertSeverity.CRITICAL,
        ),
        metric=MetricKind.LATENCY_MS,
        operator=ComparisonOperator.GT,
        threshold_value=threshold,
    )


@pytest.mark.integration
class TestAlertsToCloudWatchAlarms:
    def test_a_breached_alert_becomes_a_cloudwatch_alarm(self) -> None:
        with mock_aws():
            client = boto3.client("cloudwatch", region_name="us-east-1")
            publisher = CloudWatchAlarmPublisher(client, namespace="CloudProbe/Test")

            alerts = AlertManager().evaluate(_result(latency_ms=250.0), [_rule(100.0)])
            assert len(alerts) == 1 and alerts[0].breached is True

            definition = publisher.publish(alerts[0])

            alarms = client.describe_alarms(AlarmNames=[definition.alarm_name])["MetricAlarms"]
            assert len(alarms) == 1
            # The rule's metric, operator and threshold survived translation
            # into the terms CloudWatch itself understands.
            assert alarms[0]["MetricName"] == "ProbeLatencyMs"
            assert alarms[0]["Namespace"] == "CloudProbe/Test"
            assert alarms[0]["ComparisonOperator"] == "GreaterThanThreshold"
            assert alarms[0]["Threshold"] == 100.0

    def test_binding_the_same_alert_twice_leaves_one_alarm(self) -> None:
        """Binding is idempotent (architecture §8.2).

        ``translate`` is a pure function of the *rule*, so a repeated
        ``PutMetricAlarm`` updates the existing alarm rather than accumulating
        duplicates — a property only a real alarm store can demonstrate.
        """
        with mock_aws():
            client = boto3.client("cloudwatch", region_name="us-east-1")
            publisher = CloudWatchAlarmPublisher(client, namespace="CloudProbe/Test")

            alert = AlertManager().evaluate(_result(latency_ms=250.0), [_rule(100.0)])[0]
            first = publisher.publish(alert)
            second = publisher.publish(alert)

            assert first == second
            alarms = client.describe_alarms(AlarmNames=[first.alarm_name])["MetricAlarms"]
            assert len(alarms) == 1

    def test_a_clear_alert_binds_the_same_alarm_as_a_breach(self) -> None:
        """A non-breaching evaluation still yields an alarm.

        The alarm watches the metric stream; CloudWatch — not CloudProbe —
        decides when it fires.  So the definition must not depend on the
        observed value, and binding a clear alert produces the identical alarm.
        """
        with mock_aws():
            client = boto3.client("cloudwatch", region_name="us-east-1")
            publisher = CloudWatchAlarmPublisher(client, namespace="CloudProbe/Test")

            clear = AlertManager().evaluate(_result(latency_ms=50.0), [_rule(100.0)])[0]
            breach = AlertManager().evaluate(_result(latency_ms=250.0), [_rule(100.0)])[0]
            assert clear.breached is False and breach.breached is True

            definition = publisher.publish(clear)

            assert definition == publisher.translate(breach)
            alarms = client.describe_alarms(AlarmNames=[definition.alarm_name])["MetricAlarms"]
            assert len(alarms) == 1
