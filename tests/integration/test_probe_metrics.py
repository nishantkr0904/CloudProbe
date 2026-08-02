"""Integration: probe results → CloudWatch metrics.

Exercises the boundary between the probe engine and the metrics layer
(architecture §10.2 "Metrics → moto CloudWatch").  A real ``boto3`` CloudWatch
client backed by ``moto`` satisfies the metrics layer's ``CloudWatchClient``
protocol, so the publisher's ``PutMetricData`` request is built, sent and
accepted exactly as it would be against AWS — including the batching the
publisher performs.

No real CloudWatch is touched: ``moto`` patches the ``botocore`` endpoint, so
no credentials and no network are required.
"""

from __future__ import annotations

from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

from cloudprobe.config.models import ProbeType, Target
from cloudprobe.metrics import CloudWatchMetricsPublisher, Metric
from cloudprobe.probes import ProbeErrorClass, ProbeResult

_TARGET = Target(
    target_id="web-1",
    host="10.20.1.10",
    port=443,
    probe_types=[ProbeType.TCP],
)


def _metrics_from(results: list[ProbeResult]) -> list[Metric]:
    """Map probe results onto the metric shape the publisher accepts.

    Availability is emitted for every result; latency only for successes,
    which is the distinction architecture §7.1 draws.
    """
    metrics: list[Metric] = []
    for result in results:
        dimensions = {"TargetId": result.target.target_id, "ProbeType": result.probe_type.value}
        metrics.append(
            Metric(
                name="ProbeAvailability",
                value=1.0 if result.success else 0.0,
                dimensions=dimensions,
                timestamp=result.timestamp,
            )
        )
        if result.success:
            metrics.append(
                Metric(
                    name="ProbeLatencyMs",
                    value=result.latency_ms,
                    unit="Milliseconds",
                    dimensions=dimensions,
                    timestamp=result.timestamp,
                )
            )
    return metrics


def _result(success: bool, latency_ms: float) -> ProbeResult:
    return ProbeResult(
        target=_TARGET,
        probe_type=ProbeType.TCP,
        success=success,
        latency_ms=latency_ms,
        timestamp=datetime(2026, 8, 3, 6, 0, 0, tzinfo=timezone.utc),
        error_class=None if success else ProbeErrorClass.TIMEOUT,
    )


@pytest.mark.integration
class TestProbeResultsToMetrics:
    def test_probe_results_publish_to_cloudwatch(self) -> None:
        with mock_aws():
            client = boto3.client("cloudwatch", region_name="us-east-1")
            publisher = CloudWatchMetricsPublisher(client, namespace="CloudProbe/Test")

            results = [
                _result(success=True, latency_ms=12.5),
                _result(success=False, latency_ms=0.0),
            ]
            outcome = publisher.publish(_metrics_from(results))

            # Two availability points plus one latency point for the success.
            assert outcome.metric_count == 3
            assert outcome.request_count == 1

    def test_a_large_run_is_batched_into_multiple_requests(self) -> None:
        with mock_aws():
            client = boto3.client("cloudwatch", region_name="us-east-1")
            publisher = CloudWatchMetricsPublisher(client, namespace="CloudProbe/Test")

            # 25 successful results produce 50 metrics, exceeding the 20-per-
            # request CloudWatch limit the publisher batches against.
            results = [_result(success=True, latency_ms=10.0) for _ in range(25)]
            outcome = publisher.publish(_metrics_from(results))

            assert outcome.metric_count == 50
            assert outcome.request_count == 3

    def test_publishing_no_metrics_makes_no_request(self) -> None:
        with mock_aws():
            client = boto3.client("cloudwatch", region_name="us-east-1")
            publisher = CloudWatchMetricsPublisher(client, namespace="CloudProbe/Test")

            outcome = publisher.publish([])

            assert outcome.metric_count == 0
            assert outcome.request_count == 0
