"""Alarm definitions — the validated shape of a CloudWatch metric alarm.

This module is the static half of alarm binding (project-structure §6.6): it
owns the ``AlarmDefinition`` every ``PutMetricAlarm`` request is rendered
from, the injected-client contract the publisher depends on, and the
exceptions the alarm lifecycle raises.  The translating half lives in
``binder.py``.

Validation lives here so the publisher only ever sends CloudWatch alarms
CloudWatch can accept — the same "valid by construction" discipline the
metrics layer applies to ``Metric``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from cloudprobe.alerting.models import AlertingError
from cloudprobe.config.models import AlertSeverity

# The four comparison operators CloudWatch metric alarms support.  There is no
# equality operator: an "equals" threshold cannot be expressed as a metric
# alarm, and the binder rejects it.
CLOUDWATCH_COMPARISON_OPERATORS = frozenset(
    {
        "GreaterThanThreshold",
        "GreaterThanOrEqualToThreshold",
        "LessThanThreshold",
        "LessThanOrEqualToThreshold",
    }
)

# Statistic values PutMetricAlarm accepts (sample-based alarms).
_CLOUDWATCH_STATISTICS = frozenset({"SampleCount", "Average", "Sum", "Minimum", "Maximum"})

# TreatMissingData values: how the alarm behaves when the metric stream has
# gaps.  CloudProbe defaults to "notBreaching" so a data gap does not by
# itself fire an alarm (architecture §8.4).
_TREAT_MISSING_DATA_VALUES = frozenset({"breaching", "notBreaching", "ignore", "missing"})


class InvalidAlarmError(AlertingError):
    """An alarm definition CloudWatch would reject."""


class AlarmPublishError(AlertingError):
    """The CloudWatch client failed to accept a PutMetricAlarm request."""


class CloudWatchAlarmClient(Protocol):
    """The subset of a Boto3 CloudWatch client this module uses.

    Declared structurally so alarms depend on Boto3's interface rather than
    importing Boto3.  A real ``boto3.client("cloudwatch")`` satisfies it.
    """

    def put_metric_alarm(self, **kwargs: Any) -> Any:
        ...  # pragma: no cover - structural type declaration


@dataclass(frozen=True)
class AlarmDefinition:
    """One CloudWatch metric alarm, valid by construction.

    ``threshold``, ``comparison_operator`` and the evaluation window are the
    predicate; ``metric_name``, ``namespace`` and ``dimensions`` are the
    identity of the metric stream the alarm watches; ``severity`` is carried
    through so a future notification sink can route on it.  Rendered into a
    ``PutMetricAlarm`` request by :meth:`to_payload`.
    """

    alarm_name: str
    description: str
    metric_name: str
    namespace: str
    dimensions: Mapping[str, str]
    threshold: float
    comparison_operator: str
    severity: AlertSeverity
    statistic: str = "Average"
    period_seconds: int = 60
    evaluation_periods: int = 1
    treat_missing_data: str = "notBreaching"

    def __post_init__(self) -> None:
        if not self.alarm_name:
            raise InvalidAlarmError("alarm name must be a non-empty string")
        if not math.isfinite(self.threshold):
            raise InvalidAlarmError(
                f"alarm {self.alarm_name!r} threshold must be finite, "
                f"got {self.threshold!r}"
            )
        if self.comparison_operator not in CLOUDWATCH_COMPARISON_OPERATORS:
            raise InvalidAlarmError(
                f"alarm {self.alarm_name!r} uses unsupported comparison operator "
                f"{self.comparison_operator!r}"
            )
        if self.statistic not in _CLOUDWATCH_STATISTICS:
            raise InvalidAlarmError(
                f"alarm {self.alarm_name!r} uses unsupported statistic "
                f"{self.statistic!r}"
            )
        if self.period_seconds <= 0 or self.evaluation_periods <= 0:
            raise InvalidAlarmError(
                f"alarm {self.alarm_name!r} period and evaluation periods must be "
                "positive"
            )
        if self.treat_missing_data not in _TREAT_MISSING_DATA_VALUES:
            raise InvalidAlarmError(
                f"alarm {self.alarm_name!r} uses unsupported TreatMissingData "
                f"value {self.treat_missing_data!r}"
            )

    def to_payload(self) -> dict[str, Any]:
        """Render this alarm as a ``PutMetricAlarm`` request.

        Notification actions are deliberately absent: this commit wires no
        SNS topics, so no ``Actions`` lists are sent.
        """
        return {
            "AlarmName": self.alarm_name,
            "AlarmDescription": self.description,
            "MetricName": self.metric_name,
            "Namespace": self.namespace,
            "Statistic": self.statistic,
            "Dimensions": [
                {"Name": key, "Value": value} for key, value in self.dimensions.items()
            ],
            "Period": self.period_seconds,
            "EvaluationPeriods": self.evaluation_periods,
            "Threshold": self.threshold,
            "ComparisonOperator": self.comparison_operator,
            "TreatMissingData": self.treat_missing_data,
        }
