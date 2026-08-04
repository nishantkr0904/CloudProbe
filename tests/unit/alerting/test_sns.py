"""Unit tests for the SNS notification publisher.

The SNS client is a hand-written fake rather than ``moto``: the publisher
takes an injected client, so a fake exercises the real translate, validation
and publish code path with no SDK, no credentials and no network.

Covers:
    - an alert translates into a complete Publish payload
    - default subject carries severity, rule id and target id
    - default message carries rule, target, breach detail and alarm name
    - custom subject and message builders are honoured
    - a successful publish calls the client once and returns the notification
    - SDK failure translated into NotificationPublishError with the cause
      chained
    - invalid topic ARNs rejected at translation and before any client call
    - invalid notification configuration rejected at construction
    - publish error is an alerting error
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from cloudprobe.alerting import (
    Alert,
    AlertingError,
    ComparisonOperator,
    InvalidNotificationError,
    MetricKind,
    NotificationPublishError,
    SnsNotification,
    SNSNotificationPublisher,
)
from cloudprobe.alerting.notifications import default_message, default_subject
from cloudprobe.config.models import AlertSeverity, ProbeType, Target

_MOMENT = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
_TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:cloudprobe-alerts"


class _FakeSnsClient:
    """Records every Publish call, or raises a configured error."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return {"MessageId": "00000000-0000-0000-0000-000000000000"}


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


def _publisher(client: _FakeSnsClient, **overrides: Any) -> SNSNotificationPublisher:
    base: dict[str, Any] = {"topic_arn": _TOPIC_ARN}
    base.update(overrides)
    return SNSNotificationPublisher(client, **base)


# ---------------------------------------------------------------------------
# Translation: payload shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTranslate:
    def test_alert_translates_to_a_complete_notification(self) -> None:
        notification = SNSNotificationPublisher(_FakeSnsClient(), _TOPIC_ARN).translate(_alert())
        assert isinstance(notification, SnsNotification)
        assert notification.topic_arn == _TOPIC_ARN
        assert notification.subject == "CloudProbe WARNING: tcp-latency on web-1"
        assert "tcp-latency" in notification.message
        assert "web-1" in notification.message

    def test_payload_has_the_publish_shape(self) -> None:
        payload = (
            SNSNotificationPublisher(_FakeSnsClient(), _TOPIC_ARN).translate(_alert()).to_payload()
        )
        assert payload["TopicArn"] == _TOPIC_ARN
        assert payload["Subject"] == "CloudProbe WARNING: tcp-latency on web-1"
        assert "tcp-latency" in payload["Message"]


# ---------------------------------------------------------------------------
# Subject generation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubject:
    def test_default_subject_names_severity_rule_and_target(self) -> None:
        assert default_subject(_alert()) == "CloudProbe WARNING: tcp-latency on web-1"

    def test_subject_labels_critical_severity(self) -> None:
        alert = _alert(severity=AlertSeverity.CRITICAL)
        assert default_subject(alert) == "CloudProbe CRITICAL: tcp-latency on web-1"

    def test_subject_labels_informational_severity(self) -> None:
        alert = _alert(severity=AlertSeverity.INFO)
        assert default_subject(alert) == "CloudProbe INFO: tcp-latency on web-1"

    def test_subject_uses_the_alert_rule_and_target_ids(self) -> None:
        alert = _alert(rule_id="latency", target=_target(target_id="db-2"))
        assert default_subject(alert) == "CloudProbe WARNING: latency on db-2"


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMessage:
    def test_default_message_carries_rule_and_target_selector(self) -> None:
        message = default_message(_alert())
        assert "CloudProbe alert: tcp-latency" in message
        assert "Target: web-1 (10.20.1.10)" in message

    def test_default_message_carries_breach_detail(self) -> None:
        message = default_message(_alert())
        assert "Breach: latency_ms > 100.0 (observed 250.0)" in message

    def test_default_message_carries_severity_probe_and_timestamp(self) -> None:
        message = default_message(_alert())
        assert "Severity: warning" in message
        assert "Probe type: tcp" in message
        assert "Timestamp: 2026-08-02T12:00:00+00:00" in message

    def test_default_message_links_back_to_the_cloudwatch_alarm(self) -> None:
        message = default_message(_alert())
        assert "Alarm: tcp-latency-web-1-latency_ms" in message

    def test_default_message_states_the_breach_flag(self) -> None:
        assert "Breached: yes" in default_message(_alert(breached=True))
        assert "Breached: no" in default_message(_alert(breached=False))

    def test_custom_message_builder_is_honoured(self) -> None:
        publisher = _publisher(
            _FakeSnsClient(), message_builder=lambda alert: f"custom {alert.rule_id}"
        )
        assert publisher.translate(_alert()).message == "custom tcp-latency"

    def test_custom_subject_builder_is_honoured(self) -> None:
        publisher = _publisher(
            _FakeSnsClient(),
            subject_builder=lambda alert: f"{alert.severity.value} {alert.target.target_id}",
        )
        assert publisher.translate(_alert()).subject == "warning web-1"


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPublish:
    def test_successful_publish_calls_client_once_and_returns_notification(self) -> None:
        client = _FakeSnsClient()
        notification = SNSNotificationPublisher(client, _TOPIC_ARN).publish(_alert())
        assert len(client.calls) == 1
        assert client.calls[0]["TopicArn"] == _TOPIC_ARN
        assert isinstance(notification, SnsNotification)

    def test_published_payload_is_the_translated_notification(self) -> None:
        client = _FakeSnsClient()
        publisher = SNSNotificationPublisher(client, _TOPIC_ARN)
        notification = publisher.translate(_alert())
        publisher.publish(_alert())
        assert client.calls[0] == notification.to_payload()

    def test_sdk_error_is_translated_to_notification_publish_error(self) -> None:
        client = _FakeSnsClient(error=RuntimeError("AuthorizationError"))
        with pytest.raises(NotificationPublishError) as excinfo:
            SNSNotificationPublisher(client, _TOPIC_ARN).publish(_alert())
        assert "SNS Publish failed" in str(excinfo.value)

    def test_sdk_error_cause_is_chained(self) -> None:
        client = _FakeSnsClient(error=RuntimeError("AuthorizationError"))
        with pytest.raises(NotificationPublishError) as excinfo:
            SNSNotificationPublisher(client, _TOPIC_ARN).publish(_alert())
        assert isinstance(excinfo.value.__cause__, RuntimeError)

    def test_publish_error_is_an_alerting_error(self) -> None:
        client = _FakeSnsClient(error=RuntimeError("boom"))
        with pytest.raises(AlertingError):
            SNSNotificationPublisher(client, _TOPIC_ARN).publish(_alert())


# ---------------------------------------------------------------------------
# Invalid configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvalidConfiguration:
    def test_malformed_topic_arn_is_rejected_at_translate(self) -> None:
        publisher = _publisher(_FakeSnsClient(), topic_arn="not-an-arn")
        with pytest.raises(InvalidNotificationError) as excinfo:
            publisher.translate(_alert())
        assert "not a valid SNS topic ARN" in str(excinfo.value)

    def test_malformed_topic_arn_is_rejected_at_publish_before_any_call(self) -> None:
        client = _FakeSnsClient()
        publisher = _publisher(client, topic_arn="not-an-arn")
        with pytest.raises(InvalidNotificationError):
            publisher.publish(_alert())
        assert client.calls == []

    def test_wrong_account_id_length_is_rejected(self) -> None:
        with pytest.raises(InvalidNotificationError):
            SNSNotificationPublisher(
                _FakeSnsClient(), "arn:aws:sns:us-east-1:12345:cloudprobe-alerts"
            ).translate(_alert())

    def test_other_service_prefix_is_rejected(self) -> None:
        with pytest.raises(InvalidNotificationError):
            SNSNotificationPublisher(
                _FakeSnsClient(), "arn:aws:sqs:us-east-1:123456789012:queue"
            ).translate(_alert())

    def test_invalid_notification_error_is_an_alerting_error(self) -> None:
        with pytest.raises(AlertingError):
            SNSNotificationPublisher(_FakeSnsClient(), "not-an-arn").translate(_alert())


# ---------------------------------------------------------------------------
# Notification validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNotificationValidation:
    def test_empty_subject_is_rejected(self) -> None:
        with pytest.raises(InvalidNotificationError):
            SnsNotification(topic_arn=_TOPIC_ARN, subject="", message="m")

    def test_non_ascii_subject_is_rejected(self) -> None:
        with pytest.raises(InvalidNotificationError):
            SnsNotification(topic_arn=_TOPIC_ARN, subject="høj", message="m")

    def test_overlong_subject_is_rejected(self) -> None:
        with pytest.raises(InvalidNotificationError):
            SnsNotification(topic_arn=_TOPIC_ARN, subject="x" * 101, message="m")

    def test_empty_message_is_rejected(self) -> None:
        with pytest.raises(InvalidNotificationError):
            SnsNotification(topic_arn=_TOPIC_ARN, subject="s", message="")

    def test_overlong_message_is_rejected(self) -> None:
        with pytest.raises(InvalidNotificationError):
            SnsNotification(topic_arn=_TOPIC_ARN, subject="s", message="x" * 262_145)

    def test_invalid_notification_is_an_alerting_error(self) -> None:
        with pytest.raises(AlertingError):
            SnsNotification(topic_arn=_TOPIC_ARN, subject="", message="m")
