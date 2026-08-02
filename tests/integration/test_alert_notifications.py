"""Integration: probe results → alert manager → SNS notification publisher.

Exercises the boundary between the alert manager and the SNS sink
(architecture §10.2 "Alerting → moto alarms + SNS").  A real ``boto3`` SNS
client backed by ``moto`` satisfies the ``SnsClient`` protocol, so an alert
produced by the manager is rendered into an ``SnsNotification`` and its
``Publish`` request is accepted by the fake service — against a topic ARN the
fake service itself minted, which is what makes this more than a unit test.

No real SNS is touched and no subscriber is notified: ``moto`` keeps the topic
in memory and requires no credentials or network.
"""

from __future__ import annotations

from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

from cloudprobe.alerting import (
    AlertManager,
    AlertRuleSpec,
    ComparisonOperator,
    MetricKind,
    NotificationPublishError,
    SNSNotificationPublisher,
)
from cloudprobe.config.models import AlertRule, AlertSeverity, ProbeType, Target
from cloudprobe.probes import ProbeErrorClass, ProbeResult

_TARGET = Target(
    target_id="web-1",
    host="10.20.1.10",
    port=443,
    probe_types=[ProbeType.TCP],
)


def _failed_result() -> ProbeResult:
    return ProbeResult(
        target=_TARGET,
        probe_type=ProbeType.TCP,
        success=False,
        latency_ms=0.0,
        timestamp=datetime(2026, 8, 3, 6, 0, 0, tzinfo=timezone.utc),
        error_class=ProbeErrorClass.TIMEOUT,
    )


def _availability_rule() -> AlertRuleSpec:
    return AlertRuleSpec(
        rule=AlertRule(
            rule_id="target-down",
            probe_type=ProbeType.TCP,
            severity=AlertSeverity.CRITICAL,
        ),
        metric=MetricKind.AVAILABILITY,
        operator=ComparisonOperator.LT,
        threshold_value=1.0,
    )


@pytest.mark.integration
class TestAlertsToSnsNotifications:
    def test_a_breached_alert_is_published_to_a_topic(self) -> None:
        with mock_aws():
            client = boto3.client("sns", region_name="us-east-1")
            topic_arn = client.create_topic(Name="cloudprobe-alerts")["TopicArn"]
            publisher = SNSNotificationPublisher(client, topic_arn)

            alerts = AlertManager().evaluate(_failed_result(), [_availability_rule()])
            assert len(alerts) == 1 and alerts[0].breached is True

            notification = publisher.publish(alerts[0])

            # The ARN the fake service minted passed the publisher's own
            # validation, and the rendered notification carries the breach.
            assert notification.topic_arn == topic_arn
            assert "target-down" in notification.subject
            assert "web-1" in notification.message

    def test_publishing_to_an_unknown_topic_fails_as_a_publish_error(self) -> None:
        """A missing topic surfaces as this layer's own error type.

        Only a real SNS implementation rejects an unknown ARN, so this is where
        the sink's translation of SDK failures can be observed end to end.
        """
        with mock_aws():
            client = boto3.client("sns", region_name="us-east-1")
            # Well-formed but never created, so the ARN passes construction
            # validation and is refused by the service instead.
            publisher = SNSNotificationPublisher(
                client, "arn:aws:sns:us-east-1:123456789012:no-such-topic"
            )

            alert = AlertManager().evaluate(_failed_result(), [_availability_rule()])[0]

            with pytest.raises(NotificationPublishError):
                publisher.publish(alert)
