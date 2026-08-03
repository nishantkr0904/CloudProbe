"""Regression: the CloudWatch metric payload CloudProbe emits.

``Metric.to_datum`` renders one entry of a ``PutMetricData`` request — the
exact JSON AWS receives.  Its keys (``MetricName``, ``Value``, ``Unit``,
``Dimensions``, ``Timestamp``) and the ``{"Name": ..., "Value": ...}`` shape
of each dimension are an external contract with the AWS API, not an internal
detail: a renamed key or a reshaped dimension is a request AWS would reject or
mis-file, invisible to the metric's own construction logic.

This test freezes the rendered datum for a fully-populated metric (dimensions
and timestamp present) and a bare metric (defaults only), and pins the
batching contract — that publishing splits into the documented 20-per-request
CloudWatch limit — via the ``PublishResult`` a publish returns.

Why regression, not unit: ``tests/unit/metrics/test_cloudwatch.py`` already
proves construction validates, that ``to_datum`` includes dimensions only when
present, and that batching chunks correctly.  This pins the *serialized datum
shape* and the *observable publish outcome* so a refactor of the rendering or
batching internals that stays unit-green still trips if the AWS-facing shape
moves.

The CloudWatch client is a recording fake — no boto3, no credentials, no
network.  No production code changes are required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from cloudprobe.metrics import CloudWatchMetricsPublisher, Metric
from tests.regression.golden import assert_against_golden

_FROZEN = datetime(2026, 8, 3, 6, 0, 0, tzinfo=UTC)


class _RecordingCloudWatch:
    """A structural ``CloudWatchClient`` that records every request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def put_metric_data(self, **kwargs: Any) -> None:
        self.requests.append(kwargs)


@pytest.mark.regression
class TestMetricDatumShape:
    def test_full_metric_datum_is_frozen(self, update_goldens) -> None:
        metric = Metric(
            name="ProbeLatencyMs",
            value=12.5,
            unit="Milliseconds",
            dimensions={"ProbeType": "tcp", "TargetId": "web-1"},
            timestamp=_FROZEN,
        )
        assert_against_golden(
            metric.to_datum(),
            "metric_datum_full.json",
            update=update_goldens,
        )

    def test_bare_metric_datum_is_frozen(self, update_goldens) -> None:
        # No dimensions and no timestamp: the datum must omit those keys, and
        # ``unit`` must default to the CloudWatch-valid "None".
        metric = Metric(name="ProbeAvailability", value=1.0)
        assert_against_golden(
            metric.to_datum(),
            "metric_datum_bare.json",
            update=update_goldens,
        )


@pytest.mark.regression
class TestPublishBatching:
    def test_publish_batches_at_the_cloudwatch_limit(self) -> None:
        # 45 metrics must split into 20 + 20 + 5 = 3 PutMetricData calls; the
        # 20-per-request ceiling is the AWS contract this pins.
        client = _RecordingCloudWatch()
        metrics = [Metric(name="ProbeLatencyMs", value=float(i)) for i in range(45)]

        result = CloudWatchMetricsPublisher(client).publish(metrics)

        assert result.metric_count == 45
        assert result.request_count == 3
        assert [len(req["MetricData"]) for req in client.requests] == [20, 20, 5]

    def test_empty_publish_makes_no_request(self) -> None:
        client = _RecordingCloudWatch()

        result = CloudWatchMetricsPublisher(client).publish([])

        assert result.metric_count == 0
        assert result.request_count == 0
        assert client.requests == []
