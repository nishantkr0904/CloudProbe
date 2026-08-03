"""Regression: the alerting surfaces — decisions, alarms and notifications.

The alerting layer emits three things an operator or an AWS API sees:

* an ``Alert`` — the decision record a report renders and a notification is
  built from.  Its field set and the string values of ``metric``, ``operator``
  and ``severity`` travel verbatim into reports and SNS bodies.
* a ``PutMetricAlarm`` payload — the exact JSON CloudWatch receives.  Key
  names, the ``Dimensions`` shape, the derived ``AlarmName``, the mapped
  ``ComparisonOperator`` and every default (``Statistic``, ``Period``,
  ``EvaluationPeriods``, ``TreatMissingData``) are an external API contract.
* an SNS ``Publish`` payload — the subject line and message body a human reads
  on a phone at 3am.  The wording *is* the product here.

Alarm binding is documented as idempotent (architecture §8.2): the definition
is a pure function of the rule, never of the observed value.  That guarantee is
pinned behaviourally, because it is what allows a caller to re-publish safely.

Why regression, not unit: ``tests/unit/alerting`` already proves the manager
picks the right applicable rules, the binder rejects ``==``, and the ARN
validator rejects malformed input.  This freezes the *emitted payloads and
message text* — the surfaces that change silently under a refactor and that
nothing else in the suite photographs.

Every client is a recording fake; no boto3, no credentials, no network.  No
production code changes are required.
"""

from __future__ import annotations

from typing import Any

import pytest

from cloudprobe.alerting import (
    AlertManager,
    AlertRuleSpec,
    CloudWatchAlarmPublisher,
    ComparisonOperator,
    MetricKind,
    SNSNotificationPublisher,
)
from cloudprobe.config.models import AlertRule, AlertSeverity, ProbeType
from cloudprobe.probes import ProbeErrorClass, ProbeResult
from tests.regression.conftest import FROZEN_TIME
from tests.regression.golden import assert_against_golden

_TOPIC_ARN = "arn:aws:sns:us-east-1:123456789012:cloudprobe-alerts"


class _RecordingCloudWatch:
    """A structural ``CloudWatchAlarmClient`` recording every alarm request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def put_metric_alarm(self, **kwargs: Any) -> None:
        self.requests.append(kwargs)


class _RecordingSns:
    """A structural ``SnsClient`` recording every Publish request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def publish(self, **kwargs: Any) -> None:
        self.requests.append(kwargs)


def _latency_rule() -> AlertRuleSpec:
    return AlertRuleSpec(
        rule=AlertRule(
            rule_id="high-latency",
            probe_type=ProbeType.TCP,
            severity=AlertSeverity.WARNING,
        ),
        metric=MetricKind.LATENCY_MS,
        operator=ComparisonOperator.GT,
        threshold_value=100.0,
    )


def _breaching_result(web_target) -> ProbeResult:
    # 250ms against a 100ms threshold: a breach, with a fixed timestamp so the
    # alert and every payload derived from it are deterministic.
    return ProbeResult(
        target=web_target,
        probe_type=ProbeType.TCP,
        success=True,
        latency_ms=250.0,
        timestamp=FROZEN_TIME,
    )


def _breaching_alert(web_target):
    alerts = AlertManager().evaluate(_breaching_result(web_target), [_latency_rule()])
    assert len(alerts) == 1
    return alerts[0]


@pytest.mark.regression
class TestAlertDecisionShape:
    def test_breached_alert_shape_is_frozen(self, web_target, update_goldens) -> None:
        assert_against_golden(
            _breaching_alert(web_target),
            "alert_breached.json",
            update=update_goldens,
        )

    def test_clear_alert_shape_is_frozen(self, web_target, update_goldens) -> None:
        # An applicable rule that does *not* breach still produces an Alert, so
        # a report can show OK state alongside ALARM (architecture §8.4).
        result = ProbeResult(
            target=web_target,
            probe_type=ProbeType.TCP,
            success=True,
            latency_ms=12.5,
            timestamp=FROZEN_TIME,
        )
        alerts = AlertManager().evaluate(result, [_latency_rule()])

        assert len(alerts) == 1
        assert alerts[0].breached is False
        assert_against_golden(
            alerts[0],
            "alert_clear.json",
            update=update_goldens,
        )

    def test_availability_alert_on_failure_is_frozen(self, web_target, update_goldens) -> None:
        # A failed probe reads availability 0.0; the rule fires below 1.0.  This
        # pins how a failure — not a slow success — becomes a decision.
        spec = AlertRuleSpec(
            rule=AlertRule(
                rule_id="unavailable",
                probe_type=ProbeType.TCP,
                severity=AlertSeverity.CRITICAL,
            ),
            metric=MetricKind.AVAILABILITY,
            operator=ComparisonOperator.LT,
            threshold_value=1.0,
        )
        result = ProbeResult(
            target=web_target,
            probe_type=ProbeType.TCP,
            success=False,
            latency_ms=0.0,
            timestamp=FROZEN_TIME,
            error_class=ProbeErrorClass.TIMEOUT,
            raw="timed out",
        )

        alerts = AlertManager().evaluate(result, [spec])

        assert len(alerts) == 1
        assert alerts[0].breached is True
        assert_against_golden(
            alerts[0],
            "alert_availability_breach.json",
            update=update_goldens,
        )


@pytest.mark.regression
class TestAlarmPayload:
    def test_put_metric_alarm_payload_is_frozen(self, web_target, update_goldens) -> None:
        client = _RecordingCloudWatch()

        CloudWatchAlarmPublisher(client).publish(_breaching_alert(web_target))

        assert len(client.requests) == 1
        assert_against_golden(
            client.requests[0],
            "alarm_put_metric_alarm.json",
            update=update_goldens,
        )

    def test_binding_is_idempotent_across_observed_values(self, web_target) -> None:
        # The definition is a pure function of the rule: a breach at 250ms and a
        # clear at 12.5ms must bind the identical alarm, which is what makes
        # re-publishing safe (architecture §8.2).
        publisher = CloudWatchAlarmPublisher(_RecordingCloudWatch())
        manager = AlertManager()

        breach = manager.evaluate(_breaching_result(web_target), [_latency_rule()])[0]
        clear_result = ProbeResult(
            target=web_target,
            probe_type=ProbeType.TCP,
            success=True,
            latency_ms=12.5,
            timestamp=FROZEN_TIME,
        )
        clear = manager.evaluate(clear_result, [_latency_rule()])[0]

        assert publisher.translate(breach) == publisher.translate(clear)


@pytest.mark.regression
class TestSnsNotification:
    def test_publish_payload_is_frozen(self, web_target, update_goldens) -> None:
        client = _RecordingSns()

        SNSNotificationPublisher(client, _TOPIC_ARN).publish(_breaching_alert(web_target))

        assert len(client.requests) == 1
        assert_against_golden(
            client.requests[0],
            "sns_publish_payload.json",
            update=update_goldens,
        )

    def test_default_message_body_is_frozen(self, web_target, update_goldens) -> None:
        # The body an operator reads, frozen as text rather than JSON so the
        # golden diff shows the message exactly as it is delivered.
        notification = SNSNotificationPublisher(_RecordingSns(), _TOPIC_ARN).translate(
            _breaching_alert(web_target)
        )

        assert_against_golden(
            notification.message,
            "sns_default_message.txt",
            update=update_goldens,
        )

    def test_default_subject_is_frozen(self, web_target, update_goldens) -> None:
        notification = SNSNotificationPublisher(_RecordingSns(), _TOPIC_ARN).translate(
            _breaching_alert(web_target)
        )

        assert_against_golden(
            notification.subject,
            "sns_default_subject.txt",
            update=update_goldens,
        )
