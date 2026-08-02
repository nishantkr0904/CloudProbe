"""CloudProbe alerting layer — public surface.

This package decides whether probe results breach declared rules and binds
those breaches as CloudWatch alarms (architecture §8; project-structure
§6.6).  Its scheduler and reporting consumers import the manager, the alert
rule spec, the alert, the alarm definition, the alarm publisher and the error
hierarchy from here — never from the internal submodules directly.

This layer evaluates thresholds and creates or updates alarm definitions
only.  It publishes no SNS notifications and performs no storage: those
belong to the sinks a later commit adds.
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
    "InvalidRuleError",
    "MetricKind",
    "MissingThresholdError",
]
