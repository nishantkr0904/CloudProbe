"""Integration: reporting engine → HTML renderer.

Exercises the boundary between report assembly and the HTML renderer
(architecture §10.2 "Reporting → full-pipeline render").  ``assemble`` folds a
run's probe results and alert decisions into the canonical ``Report``, and
``render_html`` turns that exact aggregate into a standalone document —
verifying the two halves of §9 agree on the ``Report`` contract that flows
between them, rather than exercising either in isolation.

This test performs no I/O of any kind: no file is written, no browser is
launched, and the renderer opens no socket.  The assembled report is driven
entirely from in-memory probe results and alerts, and the timestamps are
explicit so nothing depends on wall-clock time (architecture §10.3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import ClassVar

import pytest

from cloudprobe.alerting import AlertManager, AlertRuleSpec, ComparisonOperator, MetricKind
from cloudprobe.config.models import AlertRule, AlertSeverity, ProbeType, Target
from cloudprobe.probes import ProbeErrorClass, ProbeResult
from cloudprobe.reporting import RunMode, assemble, build_metadata
from cloudprobe.reporting.renderers import render_html

_WEB = Target(target_id="web-1", host="10.20.1.10", port=443, probe_types=[ProbeType.TCP])
_API = Target(target_id="api-1", host="10.20.2.20", port=8080, probe_types=[ProbeType.TCP])

_WHEN = datetime(2026, 8, 3, 6, 0, 0, tzinfo=UTC)


class _TagBalanceChecker(HTMLParser):
    """Assert every non-void element is closed, and closes nest correctly."""

    _VOID: ClassVar[set[str]] = {"meta", "br", "hr", "img", "input", "link"}

    def __init__(self) -> None:
        super().__init__()
        self._stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag not in self._VOID:
            self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        assert self._stack, f"unbalanced </{tag}>"
        assert self._stack[-1] == tag, f"unbalanced </{tag}>"
        self._stack.pop()

    def assert_balanced(self) -> None:
        assert not self._stack, f"unclosed tags: {self._stack}"


def _result(target: Target, success: bool, latency_ms: float) -> ProbeResult:
    return ProbeResult(
        target=target,
        probe_type=ProbeType.TCP,
        success=success,
        latency_ms=latency_ms,
        timestamp=_WHEN,
        error_class=None if success else ProbeErrorClass.TIMEOUT,
    )


def _latency_rule(threshold: float) -> AlertRuleSpec:
    return AlertRuleSpec(
        rule=AlertRule(
            rule_id="high-latency",
            probe_type=ProbeType.TCP,
            severity=AlertSeverity.WARNING,
        ),
        metric=MetricKind.LATENCY_MS,
        operator=ComparisonOperator.GT,
        threshold_value=threshold,
    )


def _report():
    """Assemble a report through the real reporting pipeline.

    A slow success (breaches the latency rule), a healthy success and a
    failure, so the rendered document has a mixed inventory, a latency sample
    and a breached alert to show.
    """
    results = [
        _result(_WEB, success=True, latency_ms=250.0),
        _result(_API, success=True, latency_ms=12.5),
        _result(_API, success=False, latency_ms=0.0),
    ]
    manager = AlertManager()
    alerts = [
        alert for result in results for alert in manager.evaluate(result, [_latency_rule(100.0)])
    ]
    metadata = build_metadata(
        run_id="run-20260803",
        mode=RunMode.ONESHOT,
        started_at=_WHEN,
        completed_at=datetime(2026, 8, 3, 6, 0, 2, tzinfo=UTC),
        hostname="ops-laptop",
    )
    return assemble(metadata, results, alerts)


@pytest.mark.integration
class TestReportingToHtml:
    def test_an_assembled_report_renders_a_complete_document(self) -> None:
        html = render_html(_report())

        assert html.startswith("<!DOCTYPE html>")
        assert html.rstrip().endswith("</html>")
        checker = _TagBalanceChecker()
        checker.feed(html)
        checker.assert_balanced()

    def test_the_document_carries_the_runs_own_data(self) -> None:
        html = render_html(_report())

        # Metadata, both targets and the breached rule all reached the page,
        # so the renderer walked the report the assembler actually produced.
        assert "run-20260803" in html
        assert "ops-laptop" in html
        assert "web-1" in html
        assert "api-1" in html
        assert "high-latency" in html
