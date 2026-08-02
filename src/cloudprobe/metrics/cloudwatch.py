"""CloudWatch custom-metric publisher.

One responsibility: take already-formed metrics and publish them to CloudWatch
via ``PutMetricData``, batched to the API's per-call limit.

What this module deliberately does **not** do:

* Create an AWS client.  The client is injected — mirroring the discovery
  layer's contract — so this module is unit-testable against a fake and the
  caller owns credentials, region and retry policy.
* Collect probe results, evaluate thresholds, create alarms, or publish to SNS.
  It publishes measurements and nothing more; judging them lives in
  ``alerting/`` and turning a ``ProbeResult`` into metrics lives in the metrics
  dispatcher (a later commit).
* Retry.  A failed call raises; the caller (or a future dispatcher) decides
  what a failure means, exactly as discovery lets its caller downgrade a run.

The publisher stays importable without botocore present: it depends on the
structural shape of a CloudWatch client, not on the SDK package.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

# CloudWatch accepts at most 20 metric data points per PutMetricData call.
_MAX_METRICS_PER_REQUEST = 20

# The project's custom namespace (architecture §3.2 / §7.1).  Overridable per
# publisher so tests and alternate deployments are not locked to it.
_DEFAULT_NAMESPACE = "CloudProbe/Network"


class MetricsError(Exception):
    """Base class for every error this module raises.

    Callers catch this one type to handle any metrics failure without importing
    botocore, keeping the SDK an implementation detail.
    """


class InvalidMetricError(MetricsError):
    """A metric was constructed with values CloudWatch would reject."""


class MetricPublishError(MetricsError):
    """The CloudWatch client failed to accept a PutMetricData request."""


class CloudWatchClient(Protocol):
    """The subset of a Boto3 CloudWatch client this module uses.

    Declared structurally so metrics depend on Boto3's interface rather than
    importing Boto3.  A real ``boto3.client("cloudwatch")`` satisfies it.
    """

    def put_metric_data(self, **kwargs: Any) -> Any:
        ...  # pragma: no cover - structural type declaration


@dataclass(frozen=True)
class Metric:
    """One CloudWatch metric data point, valid by construction.

    Validation lives here so the publisher only ever handles metrics CloudWatch
    can accept.  ``unit`` defaults to ``"None"`` — a valid CloudWatch unit — and
    ``dimensions`` map to the ``Name``/``Value`` pairs CloudWatch expects.
    """

    name: str
    value: float
    unit: str = "None"
    dimensions: Mapping[str, str] = field(default_factory=dict)
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise InvalidMetricError("metric name must be a non-empty string")
        if not math.isfinite(self.value):
            raise InvalidMetricError(
                f"metric {self.name!r} value must be finite, got {self.value!r}"
            )

    def to_datum(self) -> dict[str, Any]:
        """Render this metric as a CloudWatch ``MetricData`` entry."""
        datum: dict[str, Any] = {
            "MetricName": self.name,
            "Value": self.value,
            "Unit": self.unit,
        }
        if self.dimensions:
            datum["Dimensions"] = [
                {"Name": key, "Value": val} for key, val in self.dimensions.items()
            ]
        if self.timestamp is not None:
            datum["Timestamp"] = self.timestamp
        return datum


@dataclass(frozen=True)
class PublishResult:
    """The outcome of a successful publish.

    ``metric_count`` is how many data points were sent; ``request_count`` is how
    many ``PutMetricData`` calls that took after batching.
    """

    metric_count: int
    request_count: int


class CloudWatchMetricsPublisher:
    """Publish custom metrics to CloudWatch under a configurable namespace.

    The client is injected; the namespace is held on the instance so the
    publisher satisfies a simple ``publish(metrics) -> PublishResult`` call
    without per-call boilerplate.
    """

    def __init__(
        self,
        client: CloudWatchClient,
        namespace: str = _DEFAULT_NAMESPACE,
    ) -> None:
        self._client = client
        self._namespace = namespace

    def publish(self, metrics: Iterable[Metric]) -> PublishResult:
        """Publish ``metrics`` in batches of at most 20 per request.

        Args:
            metrics: Metrics to send.  Each is valid by construction.  An empty
                iterable sends nothing and makes no API call.

        Returns:
            A ``PublishResult`` recording how many metrics and requests were
            sent.

        Raises:
            MetricPublishError: The CloudWatch client rejected a request.
        """
        data = [metric.to_datum() for metric in metrics]

        request_count = 0
        for batch in _chunk(data, _MAX_METRICS_PER_REQUEST):
            try:
                self._client.put_metric_data(Namespace=self._namespace, MetricData=batch)
            except Exception as exc:
                # Anything the injected client raises is, to us, a publish
                # failure — translated so callers never see a raw SDK error.
                raise MetricPublishError(
                    f"PutMetricData failed for namespace {self._namespace!r}: {exc}"
                ) from exc
            request_count += 1

        return PublishResult(metric_count=len(data), request_count=request_count)


def _chunk(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    """Yield successive slices of ``items`` no longer than ``size``."""
    for start in range(0, len(items), size):
        yield items[start : start + size]
