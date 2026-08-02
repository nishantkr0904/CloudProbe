"""CloudWatch alarm binding — turning breach alerts into alarm definitions.

This module is the translating half of alarm binding (project-structure
§6.6): it takes the ``Alert`` objects the alert manager produces and turns
them into CloudWatch alarms via ``PutMetricAlarm``.  It creates or updates
alarm *definitions* and nothing else — no SNS topics, no ``SetAlarmState``,
no probe execution, no metrics publishing.  The static shape it produces
lives in ``alarms.py``.

Binding is idempotent (architecture §8.2): ``translate`` is a pure function
of the *rule* — rule id, target, severity, metric, operator and threshold —
and ignores the observed value and breach flag.  Translating the same rule
twice yields an identical definition, so the caller can issue
``PutMetricAlarm`` only for real differences.

The comparison operators this layer evaluates (``Alert`` carries ``>``, ``>=``,
``<``, ``<=``, ``==``) are not all expressible as CloudWatch metric alarms,
which accept only four comparison operators.  ``==`` has no alarm form; the
binder raises rather than emit an alarm that could never fire.
"""

from __future__ import annotations

from collections.abc import Mapping

from cloudprobe.alerting.alarms import (
    AlarmDefinition,
    AlarmPublishError,
    CloudWatchAlarmClient,
    InvalidAlarmError,
)
from cloudprobe.alerting.models import Alert, ComparisonOperator, MetricKind
from cloudprobe.config.models import AlertSeverity

# The project's custom namespace — the same one the metrics publisher emits
# under, so alarms and the metric stream they watch agree.
_NAMESPACE = "CloudProbe/Network"

# Each evaluation operator maps to exactly one CloudWatch comparison
# operator.  ``==`` is deliberately absent: CloudWatch cannot express it.
_OPERATOR_MAP: Mapping[ComparisonOperator, str] = {
    ComparisonOperator.GT: "GreaterThanThreshold",
    ComparisonOperator.GE: "GreaterThanOrEqualToThreshold",
    ComparisonOperator.LT: "LessThanThreshold",
    ComparisonOperator.LE: "LessThanOrEqualToThreshold",
}

# Each evaluated metric maps to the CloudWatch custom metric the metrics
# layer publishes under the project namespace (config-doc §3).
_METRIC_NAME_MAP: Mapping[MetricKind, str] = {
    MetricKind.LATENCY_MS: "ProbeLatencyMs",
    MetricKind.AVAILABILITY: "ProbeAvailability",
    MetricKind.PACKET_LOSS: "ProbePacketLoss",
}

# One severity per line, so the description reads as documentation rather
# than an opaque string.
_SEVERITY_DESCRIPTIONS: Mapping[AlertSeverity, str] = {
    AlertSeverity.INFO: "informational alarm",
    AlertSeverity.WARNING: "warning alarm",
    AlertSeverity.CRITICAL: "critical alarm",
}


class CloudWatchAlarmPublisher:
    """Translate breach alerts into CloudWatch alarms and publish them.

    The client is injected so the publisher is unit-testable against a fake
    and the caller owns credentials, region and retry policy — the same
    contract the metrics and discovery layers use.
    """

    def __init__(
        self,
        client: CloudWatchAlarmClient,
        namespace: str = _NAMESPACE,
    ) -> None:
        self._client = client
        self._namespace = namespace

    def translate(self, alert: Alert) -> AlarmDefinition:
        """Render ``alert`` as an alarm definition, without publishing.

        Pure with respect to the rule: the observed value and breach flag do
        not influence the result, so binding stays idempotent (§8.2).

        Args:
            alert: The breach alert produced by the alert manager.

        Returns:
            A validated ``AlarmDefinition``.

        Raises:
            InvalidAlarmError: The alert cannot be expressed as a CloudWatch
                alarm — for example an ``==`` predicate, which CloudWatch
                does not support.
        """
        operator = _OPERATOR_MAP.get(alert.operator)
        if operator is None:
            raise InvalidAlarmError(
                f"rule {alert.rule_id!r} uses comparison operator "
                f"{alert.operator.value!r}, which CloudWatch metric alarms "
                "do not support"
            )
        metric_name = _METRIC_NAME_MAP.get(alert.metric)
        if metric_name is None:  # pragma: no cover - MetricKind is closed
            raise InvalidAlarmError(
                f"rule {alert.rule_id!r} uses metric {alert.metric.value!r}, "
                "which has no alarm mapping"
            )
        return AlarmDefinition(
            alarm_name=_alarm_name(alert.rule_id, alert.target.target_id, alert.metric),
            description=f"{_SEVERITY_DESCRIPTIONS[alert.severity]} for "
            f"{alert.target.target_id} ({alert.metric.value} "
            f"{alert.operator.value} {alert.threshold_value})",
            metric_name=metric_name,
            namespace=self._namespace,
            dimensions=_dimensions(alert),
            threshold=alert.threshold_value,
            comparison_operator=operator,
            severity=alert.severity,
        )

    def publish(self, alert: Alert) -> AlarmDefinition:
        """Translate ``alert`` and send its ``PutMetricAlarm`` request.

        Args:
            alert: The breach alert to bind as an alarm.

        Returns:
            The definition that was published, for callers that want to
            record what an alarm now looks like.

        Raises:
            InvalidAlarmError: The alert cannot be expressed as a CloudWatch
                alarm.
            AlarmPublishError: The CloudWatch client rejected the request.
        """
        definition = self.translate(alert)
        try:
            self._client.put_metric_alarm(**definition.to_payload())
        except Exception as exc:
            # Anything the injected client raises is, to us, a publish
            # failure — translated so callers never see a raw SDK error.
            raise AlarmPublishError(
                f"PutMetricAlarm failed for alarm {definition.alarm_name!r}: {exc}"
            ) from exc
        return definition


def _alarm_name(rule_id: str, target_id: str, metric: MetricKind) -> str:
    """Build the stable alarm name from the rule, target and metric.

    ``rule_id`` is the documented name prefix (config-doc §6); the target and
    metric are appended because one rule can match many targets and alarm
    names must be unique per account and region.
    """
    return f"{rule_id}-{target_id}-{metric.value}"


def _dimensions(alert: Alert) -> Mapping[str, str]:
    """The dimensions the alarm watches: probe type, target id, and tags.

    Target tags are forwarded to metrics as dimensions (config-doc §2), and
    an alarm only evaluates against a metric stream whose dimensions match
    exactly — so the alarm carries the same identity the metric will.
    """
    dimensions: dict[str, str] = {
        "ProbeType": alert.probe_type.value,
        "TargetId": alert.target.target_id,
    }
    dimensions.update(alert.target.tags)
    return dimensions
