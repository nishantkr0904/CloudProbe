"""CloudProbe alerting layer — public surface.

This package decides whether probe results breach declared rules, binds those
breaches as CloudWatch alarms, and delivers them to an SNS topic
(architecture §8; project-structure §6.6).  Its scheduler and reporting
consumers import the manager, the alert rule spec, the alert, the alarm
definition, the alarm publisher, the notification publisher and the error
hierarchy from here — never from the internal submodules directly.

This layer evaluates thresholds, creates or updates alarm definitions and
publishes notifications.  It performs no storage: that belongs to a later
commit.
"""

from cloudprobe.alerting.alarms import (
    AlarmDefinition,
    AlarmPublishError,
    CloudWatchAlarmClient,
    InvalidAlarmError,
)
from cloudprobe.alerting.binder import CloudWatchAlarmPublisher
from cloudprobe.alerting.manager import AlertManager
from cloudprobe.alerting.models import (
    Alert,
    AlertingError,
    AlertRuleSpec,
    ComparisonOperator,
    InvalidRuleError,
    MetricKind,
    MissingThresholdError,
)
from cloudprobe.alerting.notifications import (
    InvalidNotificationError,
    NotificationPublishError,
    SnsClient,
    SnsNotification,
)
from cloudprobe.alerting.sns import SNSNotificationPublisher

__all__ = [
    "AlarmDefinition",
    "AlarmPublishError",
    "Alert",
    "AlertingError",
    "AlertManager",
    "AlertRuleSpec",
    "CloudWatchAlarmClient",
    "CloudWatchAlarmPublisher",
    "ComparisonOperator",
    "InvalidAlarmError",
    "InvalidNotificationError",
    "InvalidRuleError",
    "MetricKind",
    "MissingThresholdError",
    "NotificationPublishError",
    "SNSNotificationPublisher",
    "SnsClient",
    "SnsNotification",
]
