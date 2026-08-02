"""CloudProbe metrics layer — public surface.

All CloudWatch custom-metric publication is isolated behind this package.  The
scheduler, alerting and reporting layers import the publisher and its metric
and result types from here, never from Boto3 or the internal submodule directly.

This layer emits measurements.  It evaluates no thresholds, creates no alarms,
publishes no SNS notifications and does not collect probe results itself — those
belong to the layers above it.
"""

from cloudprobe.metrics.cloudwatch import (
    CloudWatchClient,
    CloudWatchMetricsPublisher,
    InvalidMetricError,
    Metric,
    MetricPublishError,
    MetricsError,
    PublishResult,
)

__all__ = [
    "CloudWatchClient",
    "CloudWatchMetricsPublisher",
    "InvalidMetricError",
    "Metric",
    "MetricPublishError",
    "MetricsError",
    "PublishResult",
]
