"""SNS notification publishing — delivering breach alerts to a topic.

This module is the publishing half of the SNS notification sink
(project-structure §6.6): it takes the ``Alert`` objects the alert manager
produces and sends them to a configured SNS topic via ``Publish``.  The
static shape it produces lives in ``notifications.py``.

It is a sink and nothing more: it evaluates no thresholds, creates or updates
no CloudWatch alarms, creates no topics, and does not decide whether a rule
wants SNS at all — the caller gates on ``AlertRule.notify_sns``
(configuration §6) and hands this publisher the alerts it wants delivered.

Subject and message rendering are injected callables so operators can reshape
what subscribers see without touching the transport, and every notification is
validated before a request leaves the process.
"""

from __future__ import annotations

from collections.abc import Callable

from cloudprobe.alerting.models import Alert
from cloudprobe.alerting.notifications import (
    NotificationPublishError,
    SnsClient,
    SnsNotification,
    default_message,
    default_subject,
)


class SNSNotificationPublisher:
    """Translate breach alerts into SNS notifications and publish them.

    The client is injected so the publisher is unit-testable against a fake
    and the caller owns credentials, region and retry policy — the same
    contract the metrics and alarm layers use.
    """

    def __init__(
        self,
        client: SnsClient,
        topic_arn: str,
        subject_builder: Callable[[Alert], str] = default_subject,
        message_builder: Callable[[Alert], str] = default_message,
    ) -> None:
        self._client = client
        self._topic_arn = topic_arn
        self._subject_builder = subject_builder
        self._message_builder = message_builder

    def translate(self, alert: Alert) -> SnsNotification:
        """Render ``alert`` as a notification, without publishing.

        Constructing the ``SnsNotification`` validates the topic ARN, the
        subject and the message, so an alert that cannot be delivered fails
        here — before any ``Publish`` request is attempted.

        Args:
            alert: The breach alert produced by the alert manager.

        Returns:
            A validated ``SnsNotification``.

        Raises:
            InvalidNotificationError: The topic ARN, or a rendered subject or
                message, is one SNS would reject.
        """
        return SnsNotification(
            topic_arn=self._topic_arn,
            subject=self._subject_builder(alert),
            message=self._message_builder(alert),
        )

    def publish(self, alert: Alert) -> SnsNotification:
        """Translate ``alert`` and send its ``Publish`` request.

        Args:
            alert: The breach alert to deliver.

        Returns:
            The notification that was published, for callers that want to
            record what subscribers were told.

        Raises:
            InvalidNotificationError: The alert cannot be expressed as a valid
                ``Publish`` request.
            NotificationPublishError: The SNS client rejected the request.
        """
        notification = self.translate(alert)
        try:
            self._client.publish(**notification.to_payload())
        except Exception as exc:
            # Anything the injected client raises is, to us, a publish
            # failure — translated so callers never see a raw SDK error.
            raise NotificationPublishError(
                f"SNS Publish failed for topic {notification.topic_arn!r}: {exc}"
            ) from exc
        return notification
