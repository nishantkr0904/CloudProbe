"""Unit tests for the CloudWatch metrics publisher.

The CloudWatch client is a hand-written fake rather than ``moto``: the publisher
takes an injected client, so a fake exercises the real batching, dimension and
namespace code path with no SDK, no credentials and no network.

Covers:
    - successful publication and the PublishResult it returns
    - batch splitting at 20 metrics per PutMetricData request
    - namespace forwarding (default and override)
    - dimension forwarding into CloudWatch's Name/Value shape
    - SDK failure translated into MetricPublishError
    - an empty metric list makes no API call
    - invalid metrics rejected at construction
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from cloudprobe.metrics.cloudwatch import (
    CloudWatchMetricsPublisher,
    InvalidMetricError,
    Metric,
    MetricPublishError,
    MetricsError,
    PublishResult,
)


class _FakeCloudWatchClient:
    """Records every PutMetricData call, or raises a configured error."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def put_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def _metrics(count: int) -> list[Metric]:
    return [Metric(name=f"ProbeSuccess{i}", value=float(i)) for i in range(count)]


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuccess:
    def test_single_metric_is_published(self) -> None:
        client = _FakeCloudWatchClient()
        result = CloudWatchMetricsPublisher(client).publish([Metric("ProbeSuccess", 1.0)])
        assert isinstance(result, PublishResult)
        assert len(client.calls) == 1

    def test_result_counts_metrics_and_requests(self) -> None:
        client = _FakeCloudWatchClient()
        result = CloudWatchMetricsPublisher(client).publish(_metrics(3))
        assert result.metric_count == 3
        assert result.request_count == 1

    def test_metric_datum_shape_is_forwarded(self) -> None:
        client = _FakeCloudWatchClient()
        CloudWatchMetricsPublisher(client).publish(
            [Metric("ProbeLatencyMs", 12.5, unit="Milliseconds")]
        )
        datum = client.calls[0]["MetricData"][0]
        assert datum["MetricName"] == "ProbeLatencyMs"
        assert datum["Value"] == 12.5
        assert datum["Unit"] == "Milliseconds"

    def test_timestamp_is_forwarded_when_set(self) -> None:
        client = _FakeCloudWatchClient()
        moment = datetime(2026, 8, 2, tzinfo=UTC)
        CloudWatchMetricsPublisher(client).publish([Metric("M", 1.0, timestamp=moment)])
        assert client.calls[0]["MetricData"][0]["Timestamp"] == moment

    def test_timestamp_is_omitted_when_unset(self) -> None:
        client = _FakeCloudWatchClient()
        CloudWatchMetricsPublisher(client).publish([Metric("M", 1.0)])
        assert "Timestamp" not in client.calls[0]["MetricData"][0]


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBatching:
    def test_twenty_metrics_are_one_request(self) -> None:
        client = _FakeCloudWatchClient()
        result = CloudWatchMetricsPublisher(client).publish(_metrics(20))
        assert result.request_count == 1
        assert len(client.calls) == 1

    def test_twenty_one_metrics_split_into_two_requests(self) -> None:
        client = _FakeCloudWatchClient()
        result = CloudWatchMetricsPublisher(client).publish(_metrics(21))
        assert result.request_count == 2
        assert len(client.calls[0]["MetricData"]) == 20
        assert len(client.calls[1]["MetricData"]) == 1

    def test_large_batch_splits_evenly(self) -> None:
        client = _FakeCloudWatchClient()
        result = CloudWatchMetricsPublisher(client).publish(_metrics(45))
        assert result.request_count == 3
        assert [len(call["MetricData"]) for call in client.calls] == [20, 20, 5]

    def test_no_batch_exceeds_the_limit(self) -> None:
        client = _FakeCloudWatchClient()
        CloudWatchMetricsPublisher(client).publish(_metrics(100))
        assert all(len(call["MetricData"]) <= 20 for call in client.calls)


# ---------------------------------------------------------------------------
# Namespace and dimensions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNamespaceAndDimensions:
    def test_default_namespace_is_forwarded(self) -> None:
        client = _FakeCloudWatchClient()
        CloudWatchMetricsPublisher(client).publish([Metric("M", 1.0)])
        assert client.calls[0]["Namespace"] == "CloudProbe/Network"

    def test_custom_namespace_is_forwarded(self) -> None:
        client = _FakeCloudWatchClient()
        CloudWatchMetricsPublisher(client, namespace="CloudProbe/Test").publish([Metric("M", 1.0)])
        assert client.calls[0]["Namespace"] == "CloudProbe/Test"

    def test_dimensions_are_forwarded_as_name_value_pairs(self) -> None:
        client = _FakeCloudWatchClient()
        CloudWatchMetricsPublisher(client).publish(
            [Metric("M", 1.0, dimensions={"ProbeType": "tcp", "TargetId": "web-1"})]
        )
        dimensions = client.calls[0]["MetricData"][0]["Dimensions"]
        assert {"Name": "ProbeType", "Value": "tcp"} in dimensions
        assert {"Name": "TargetId", "Value": "web-1"} in dimensions

    def test_dimensions_are_omitted_when_empty(self) -> None:
        client = _FakeCloudWatchClient()
        CloudWatchMetricsPublisher(client).publish([Metric("M", 1.0)])
        assert "Dimensions" not in client.calls[0]["MetricData"][0]


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmptyInput:
    def test_empty_list_makes_no_api_call(self) -> None:
        client = _FakeCloudWatchClient()
        result = CloudWatchMetricsPublisher(client).publish([])
        assert client.calls == []
        assert result.metric_count == 0
        assert result.request_count == 0


# ---------------------------------------------------------------------------
# Failure translation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailureTranslation:
    def test_sdk_error_becomes_publish_error(self) -> None:
        client = _FakeCloudWatchClient(error=RuntimeError("Throttling"))
        with pytest.raises(MetricPublishError):
            CloudWatchMetricsPublisher(client).publish([Metric("M", 1.0)])

    def test_publish_error_is_a_metrics_error(self) -> None:
        client = _FakeCloudWatchClient(error=RuntimeError("boom"))
        with pytest.raises(MetricsError):
            CloudWatchMetricsPublisher(client).publish([Metric("M", 1.0)])

    def test_original_sdk_error_is_chained(self) -> None:
        original = RuntimeError("AccessDenied")
        client = _FakeCloudWatchClient(error=original)
        with pytest.raises(MetricPublishError) as excinfo:
            CloudWatchMetricsPublisher(client).publish([Metric("M", 1.0)])
        assert excinfo.value.__cause__ is original

    def test_failure_on_second_batch_still_translated(self) -> None:
        client = _FakeCloudWatchClient(error=RuntimeError("429"))
        with pytest.raises(MetricPublishError):
            CloudWatchMetricsPublisher(client).publish(_metrics(21))


# ---------------------------------------------------------------------------
# Metric validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMetricValidation:
    def test_empty_name_is_rejected(self) -> None:
        with pytest.raises(InvalidMetricError):
            Metric(name="", value=1.0)

    def test_nan_value_is_rejected(self) -> None:
        with pytest.raises(InvalidMetricError):
            Metric(name="M", value=float("nan"))

    def test_infinite_value_is_rejected(self) -> None:
        with pytest.raises(InvalidMetricError):
            Metric(name="M", value=float("inf"))

    def test_valid_metric_is_accepted(self) -> None:
        metric = Metric(name="ProbeSuccess", value=0.0)
        assert metric.value == 0.0
