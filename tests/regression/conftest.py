"""Shared fixtures and the ``--update-goldens`` flag for the regression tier.

The flag is what makes golden updates deliberate: comparisons run by default,
and a golden file is only rewritten when a reviewer passes ``--update-goldens``
and signs off on the diff (project-structure §tests/regression).

The fixtures below build the deterministic inputs the golden tests freeze —
fixed timestamps, fixed identifiers, no clocks, no randomness — so a golden
artifact is a pure function of code, never of when the suite ran.

The regression tier imports its own helper (``golden.py``) and its own
fixtures rather than sharing ``tests/unit`` ones: it exercises the same public
surfaces as the unit tests, but pins them from a different angle — exact
emitted shape instead of individual field values — so sharing fixtures would
only couple the two tiers' setup to each other.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cloudprobe.alerting import AlertManager, AlertRuleSpec, ComparisonOperator, MetricKind
from cloudprobe.config.models import AlertRule, AlertSeverity, ProbeType, Target
from cloudprobe.probes import ProbeErrorClass, ProbeResult
from cloudprobe.reporting import Report, RunMode, assemble, build_metadata

# One fixed instant for every timestamp in the suite: golden artifacts must not
# depend on wall-clock time (architecture §10.3).
FROZEN_TIME = datetime(2026, 8, 3, 6, 0, 0, tzinfo=UTC)
FROZEN_END = datetime(2026, 8, 3, 6, 0, 2, tzinfo=UTC)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-goldens",
        action="store_true",
        default=False,
        help="Rewrite golden files instead of comparing (requires reviewer sign-off).",
    )


@pytest.fixture
def update_goldens(request: pytest.FixtureRequest) -> bool:
    """Whether this run should rewrite golden files rather than compare them."""
    return bool(request.config.getoption("--update-goldens"))


@pytest.fixture
def web_target() -> Target:
    """A static TCP target with AWS dimensions, used across golden artifacts."""
    return Target(
        target_id="web-1",
        host="10.20.1.10",
        port=443,
        probe_types=[ProbeType.TCP],
        vpc_id="vpc-aaa",
        subnet_id="subnet-aaa",
        instance_id="i-web1",
        label="web",
        tags={"Name": "web", "Environment": "lab"},
    )


@pytest.fixture
def api_target() -> Target:
    """A second target sharing the run's VPC but a different subnet."""
    return Target(
        target_id="api-1",
        host="10.20.2.20",
        port=8080,
        probe_types=[ProbeType.TCP],
        vpc_id="vpc-aaa",
        subnet_id="subnet-bbb",
        instance_id="i-api1",
        label="api",
        tags={"Name": "api", "Environment": "lab"},
    )


@pytest.fixture
def frozen_report(web_target: Target, api_target: Target) -> Report:
    """A fully-assembled report built through the real reporting pipeline.

    A slow success (breaches the latency rule), a healthy success and a
    failure, so every report section has data to freeze: mixed inventory,
    a latency sample, and a breached alert.
    """
    results = [
        ProbeResult(
            target=web_target,
            probe_type=ProbeType.TCP,
            success=True,
            latency_ms=250.0,
            timestamp=FROZEN_TIME,
        ),
        ProbeResult(
            target=api_target,
            probe_type=ProbeType.TCP,
            success=True,
            latency_ms=12.5,
            timestamp=FROZEN_TIME,
        ),
        ProbeResult(
            target=api_target,
            probe_type=ProbeType.TCP,
            success=False,
            latency_ms=0.0,
            timestamp=FROZEN_TIME,
            error_class=ProbeErrorClass.TIMEOUT,
        ),
    ]
    rule = AlertRuleSpec(
        rule=AlertRule(
            rule_id="high-latency",
            probe_type=ProbeType.TCP,
            severity=AlertSeverity.WARNING,
        ),
        metric=MetricKind.LATENCY_MS,
        operator=ComparisonOperator.GT,
        threshold_value=100.0,
    )
    manager = AlertManager()
    alerts = [alert for result in results for alert in manager.evaluate(result, [rule])]

    metadata = build_metadata(
        run_id="run-20260803",
        mode=RunMode.ONESHOT,
        started_at=FROZEN_TIME,
        completed_at=FROZEN_END,
        hostname="ops-laptop",
    )
    return assemble(metadata, results, alerts)
