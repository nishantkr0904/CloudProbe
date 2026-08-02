"""SNS notification contracts — the validated shape of a ``Publish`` request.

This module is the static half of the SNS notification sink (project-structure
§6.6): it owns the ``SnsNotification`` every ``Publish`` request is rendered
from, the injected-client contract the publisher depends on, the exceptions
the notification lifecycle raises, and the default subject and message
formatting.  The publishing half lives in ``sns.py``.

The default message body follows architecture §8.3: it carries the rule name,
the target selector, the breach detail and a reference back to the CloudWatch
alarm the binder creates for the same rule.

Validation lives here so the publisher only ever sends notifications SNS can
accept — the same "valid by construction" discipline the metrics layer applies
to ``Metric`` and the binder to ``AlarmDefinition``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from string import Template
from typing import Any, Protocol

from cloudprobe.alerting.models import Alert, AlertingError
from cloudprobe.config.models import AlertSeverity

# SNS topic ARNs have the shape ``arn:aws:sns:<region>:<account-id>:<topic-name>``:
# a lowercase region, a twelve-digit account id, and a topic name of
# alphanumerics, hyphens, underscores and dots (AWS SNS naming rules).
_TOPIC_ARN_PATTERN = re.compile(r"^arn:aws:sns:[a-z0-9-]+:\d{12}:[A-Za-z0-9._-]+$")

# The Publish limits SNS enforces: the subject is ASCII text of at most 100
# characters, and the message is at most 262144 bytes (256 KiB).
_SUBJECT_MAX_CHARS = 100
_MESSAGE_MAX_BYTES = 262_144


class InvalidNotificationError(AlertingError):
    """A notification SNS would reject."""


class NotificationPublishError(AlertingError):
    """The SNS client failed to accept a Publish request."""


class SnsClient(Protocol):
    """The subset of a Boto3 SNS client this module uses.

    Declared structurally so the sink depends on Boto3's interface rather than
    importing Boto3.  A real ``boto3.client("sns")`` satisfies it.
    """

    def publish(self, **kwargs: Any) -> Any:
        ...  # pragma: no cover - structural type declaration


# Severity appears in the subject as an uppercase label so an operator
# triaging a phone lock screen can rank messages at a glance.
_SEVERITY_LABELS: Mapping[AlertSeverity, str] = {
    AlertSeverity.INFO: "INFO",
    AlertSeverity.WARNING: "WARNING",
    AlertSeverity.CRITICAL: "CRITICAL",
}

_DEFAULT_MESSAGE_TEMPLATE = Template(
    "CloudProbe alert: $rule_id\n"
    "Target: $target_id ($host)\n"
    "Probe type: $probe_type\n"
    "Severity: $severity\n"
    "Breach: $metric $operator $threshold (observed $observed)\n"
    "Timestamp: $timestamp\n"
    "Breached: $breached\n"
    "Alarm: $alarm_name"
)


def default_subject(alert: Alert) -> str:
    """The default notification subject: severity, rule and target."""
    return (
        f"CloudProbe {_SEVERITY_LABELS[alert.severity]}: "
        f"{alert.rule_id} on {alert.target.target_id}"
    )


def default_message(alert: Alert) -> str:
    """The default notification body, per architecture §8.3."""
    return _DEFAULT_MESSAGE_TEMPLATE.substitute(
        rule_id=alert.rule_id,
        target_id=alert.target.target_id,
        host=alert.target.host,
        probe_type=alert.probe_type.value,
        severity=alert.severity.value,
        metric=alert.metric.value,
        operator=alert.operator.value,
        threshold=alert.threshold_value,
        observed=alert.observed_value,
        timestamp=alert.timestamp.isoformat(),
        breached="yes" if alert.breached else "no",
        alarm_name=f"{alert.rule_id}-{alert.target.target_id}-{alert.metric.value}",
    )


@dataclass(frozen=True)
class SnsNotification:
    """One SNS ``Publish`` request, valid by construction.

    ``topic_arn`` is the configured destination; ``subject`` and ``message``
    are the human-readable notification.  Validation happens at construction
    so the publisher only ever sends requests SNS can accept, mirroring
    ``AlarmDefinition``.  Rendered into a ``Publish`` request by
    :meth:`to_payload`.
    """

    topic_arn: str
    subject: str
    message: str

    def __post_init__(self) -> None:
        if _TOPIC_ARN_PATTERN.fullmatch(self.topic_arn) is None:
            raise InvalidNotificationError(
                f"topic ARN {self.topic_arn!r} is not a valid SNS topic ARN; "
                "expected arn:aws:sns:<region>:<account-id>:<topic-name>"
            )
        if not self.subject:
            raise InvalidNotificationError("notification subject must be non-empty")
        if not self.subject.isascii() or len(self.subject) > _SUBJECT_MAX_CHARS:
            raise InvalidNotificationError(
                f"notification subject must be ASCII and at most "
                f"{_SUBJECT_MAX_CHARS} characters, got {len(self.subject)}"
            )
        if not self.message:
            raise InvalidNotificationError("notification message must be non-empty")
        if len(self.message.encode("utf-8")) > _MESSAGE_MAX_BYTES:
            raise InvalidNotificationError(
                f"notification message exceeds the {_MESSAGE_MAX_BYTES}-byte "
                "SNS limit"
            )

    def to_payload(self) -> dict[str, Any]:
        """Render this notification as an SNS ``Publish`` request."""
        return {
            "TopicArn": self.topic_arn,
            "Subject": self.subject,
            "Message": self.message,
        }
