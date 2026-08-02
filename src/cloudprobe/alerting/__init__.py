"""CloudProbe alerting layer — public surface.

This package decides whether probe results breach declared rules
(architecture §8.1).  Its scheduler and reporting consumers import the
manager, the alert rule spec, the alert and the error hierarchy from here —
never from the internal submodules directly.

This layer evaluates thresholds only.  It creates no CloudWatch alarms,
publishes no SNS notifications, and performs no storage: those belong to the
binder and sinks a later commit adds (project-structure §6.6).
"""

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
    "Alert",
    "AlertingError",
    "AlertManager",
    "AlertRuleSpec",
    "ComparisonOperator",
    "InvalidRuleError",
    "MetricKind",
    "MissingThresholdError",
]
