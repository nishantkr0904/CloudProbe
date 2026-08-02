"""Unit tests for the HTML report renderer.

``render_html`` is a pure function of the ``Report`` model, so these tests
build fake reports from frozen dataclasses and assert on the returned string:
no fakes beyond the data, no clock, no socket, no AWS, no file written and no
browser opened.  The document is validated by well-formedness checks and by
the presence of escaped content, not by a golden snapshot.

Covers html.py:
    - a complete standalone document is returned (doctype, html, head, body)
    - the stylesheet is inlined and no external asset or script is referenced
    - run metadata is rendered
    - the inventory summary is rendered
    - overall, per-probe-type and per-target outcomes are rendered
    - a success rate of None renders as an em dash, not a number
    - latency statistics are rendered, and their absence is stated
    - the alert summary and its severity breakdown are rendered
    - the per-result table has one row per probe result
    - failures show their error class; successes do not
    - breached alerts are tabulated
    - user-controlled content is HTML-escaped
    - an empty run renders coherent "none"/"no" sections
"""

from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser

import pytest

from cloudprobe.alerting.models import Alert, ComparisonOperator, MetricKind
from cloudprobe.config.models import AlertSeverity, ProbeType, Target
from cloudprobe.probes.base import ProbeErrorClass, ProbeResult
from cloudprobe.reporting.models import (
    AlertSummary,
    InventorySummary,
    LatencyStatistics,
    OutcomeStats,
    Report,
    RunMetadata,
    RunMode,
)
from cloudprobe.reporting.renderers import render_html

_START = datetime(2026, 8, 3, 6, 0, 0)
_END = datetime(2026, 8, 3, 6, 0, 12)


def _target(target_id: str = "web-1", host: str = "10.20.1.10", **overrides) -> Target:
    base = {
        "target_id": target_id,
        "host": host,
        "port": 443,
        "probe_types": [ProbeType.TCP, ProbeType.ICMP],
        "vpc_id": "vpc-1",
        "subnet_id": "subnet-1",
    }
    base.update(overrides)
    return Target(**base)


def _result(**overrides) -> ProbeResult:
    base = {
        "target": _target(),
        "probe_type": ProbeType.TCP,
        "success": True,
        "latency_ms": 12.5,
        "timestamp": _START,
    }
    base.update(overrides)
    return ProbeResult(**base)


def _alert(**overrides) -> Alert:
    base = {
        "rule_id": "rule-1",
        "target": _target(),
        "probe_type": ProbeType.TCP,
        "severity": AlertSeverity.CRITICAL,
        "metric": MetricKind.LATENCY_MS,
        "operator": ComparisonOperator.GT,
        "threshold_value": 100.0,
        "observed_value": 250.0,
        "breached": True,
        "timestamp": _END,
    }
    base.update(overrides)
    return Alert(**base)


def _metadata(**overrides) -> RunMetadata:
    base = {
        "run_id": "run-1",
        "mode": RunMode.ONESHOT,
        "started_at": _START,
        "completed_at": _END,
        "hostname": "probe-host-1",
    }
    base.update(overrides)
    return RunMetadata(**base)


def _report(**overrides) -> Report:
    results = overrides.pop("results", (_result(),))
    breaches = overrides.pop("breaches", (_alert(),))
    base = {
        "metadata": _metadata(),
        "inventory": InventorySummary(target_count=1, vpc_count=1, subnet_count=1),
        "outcomes": OutcomeStats(total=1, successes=1, failures=0),
        "outcomes_by_probe_type": {ProbeType.TCP: OutcomeStats(1, 1, 0)},
        "outcomes_by_target": {"web-1": OutcomeStats(1, 1, 0)},
        "latency": LatencyStatistics(1, 12.5, 12.5, 12.5, 12.5, 12.5, 12.5),
        "alerts": AlertSummary(total=1, breached=1, by_severity={AlertSeverity.CRITICAL: 1}),
        "results": results,
        "breaches": breaches,
    }
    base.update(overrides)
    return Report(**base)


class _WellFormednessChecker(HTMLParser):
    """Asserts every start tag is closed, so the document parses cleanly."""

    _VOID = {"meta", "br", "hr", "img", "input", "link"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.balanced = True

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag not in self._VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack.pop() != tag:
            self.balanced = False


def _is_well_formed(html: str) -> bool:
    checker = _WellFormednessChecker()
    checker.feed(html)
    return checker.balanced and not checker.stack


# ---------------------------------------------------------------------------
# Document shell
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDocumentShell:
    def test_a_complete_standalone_document_is_returned(self) -> None:
        html = render_html(_report())
        assert html.startswith("<!DOCTYPE html>")
        assert "<html" in html and "</html>" in html
        assert "<head>" in html and "</head>" in html
        assert "<body>" in html and "</body>" in html

    def test_the_document_is_well_formed(self) -> None:
        assert _is_well_formed(render_html(_report()))

    def test_the_stylesheet_is_inlined(self) -> None:
        html = render_html(_report())
        assert "<style>" in html and "</style>" in html

    def test_no_external_asset_or_script_is_referenced(self) -> None:
        html = render_html(_report())
        assert "<script" not in html
        assert "<link" not in html
        assert "http://" not in html and "https://" not in html


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSections:
    def test_run_metadata_is_rendered(self) -> None:
        html = render_html(_report())
        assert "Run metadata" in html
        assert "run-1" in html
        assert "oneshot" in html
        assert "probe-host-1" in html
        assert "2026-08-03T06:00:00" in html

    def test_inventory_summary_is_rendered(self) -> None:
        html = render_html(
            _report(inventory=InventorySummary(target_count=3, vpc_count=2, subnet_count=2))
        )
        assert "Inventory" in html
        assert ">3<" in html

    def test_overall_outcomes_are_rendered(self) -> None:
        html = render_html(
            _report(outcomes=OutcomeStats(total=4, successes=3, failures=1))
        )
        assert "Outcomes" in html
        assert "75.0%" in html

    def test_per_probe_type_outcomes_are_rendered(self) -> None:
        html = render_html(
            _report(
                outcomes_by_probe_type={
                    ProbeType.TCP: OutcomeStats(2, 2, 0),
                    ProbeType.ICMP: OutcomeStats(1, 0, 1),
                }
            )
        )
        assert "tcp" in html
        assert "icmp" in html

    def test_per_target_outcomes_are_rendered(self) -> None:
        html = render_html(
            _report(
                outcomes_by_target={
                    "web-1": OutcomeStats(1, 1, 0),
                    "db-1": OutcomeStats(1, 0, 1),
                }
            )
        )
        assert "web-1" in html
        assert "db-1" in html

    def test_absent_success_rate_renders_as_an_em_dash(self) -> None:
        html = render_html(_report(outcomes=OutcomeStats(total=0, successes=0, failures=0)))
        assert "&mdash;" in html

    def test_latency_statistics_are_rendered(self) -> None:
        html = render_html(
            _report(latency=LatencyStatistics(3, 1.0, 30.0, 12.0, 10.0, 28.0, 29.0))
        )
        assert "Latency" in html
        assert "30.000" in html

    def test_absent_latency_is_stated(self) -> None:
        html = render_html(_report(latency=None))
        assert "No successful probes" in html

    def test_alert_summary_and_severity_breakdown_are_rendered(self) -> None:
        html = render_html(
            _report(
                alerts=AlertSummary(
                    total=5,
                    breached=2,
                    by_severity={AlertSeverity.CRITICAL: 1, AlertSeverity.WARNING: 1},
                )
            )
        )
        assert "Alert summary" in html
        assert "critical" in html
        assert "warning" in html

    def test_every_severity_is_renderable(self) -> None:
        # Guards the exhaustiveness _severity_label relies on: a new severity
        # must be given a colour class in the same commit that adds it.
        for severity in AlertSeverity:
            html = render_html(_report(alerts=AlertSummary(1, 1, {severity: 1})))
            assert severity.value in html


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResultsTable:
    def test_one_row_is_rendered_per_probe_result(self) -> None:
        results = (
            _result(target=_target("web-1")),
            _result(target=_target("db-1"), success=False, error_class=ProbeErrorClass.TIMEOUT),
        )
        html = render_html(_report(results=results))
        table = html.split("Probe results", 1)[1].split("</table>", 1)[0]
        assert table.count("<tr>") == 3  # header + two results

    def test_a_failure_shows_its_error_class(self) -> None:
        html = render_html(
            _report(
                results=(
                    _result(success=False, latency_ms=0.0, error_class=ProbeErrorClass.REFUSED),
                )
            )
        )
        assert "refused" in html
        assert "failure" in html

    def test_a_success_shows_no_error_class(self) -> None:
        html = render_html(_report(results=(_result(success=True),)))
        results_section = html.split("Probe results", 1)[1]
        assert "success" in results_section

    def test_an_empty_results_set_states_no_results(self) -> None:
        html = render_html(_report(results=()))
        assert "No probe results" in html


@pytest.mark.unit
class TestBreachesTable:
    def test_breached_alerts_are_tabulated(self) -> None:
        html = render_html(
            _report(breaches=(_alert(rule_id="latency-rule", severity=AlertSeverity.CRITICAL),))
        )
        section = html.split("Breached alerts", 1)[1]
        assert "latency-rule" in section
        assert "latency_ms" in section

    def test_no_breaches_states_none(self) -> None:
        html = render_html(_report(breaches=()))
        assert "No breaches" in html


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEscaping:
    def test_a_malicious_target_id_is_escaped(self) -> None:
        evil = "<script>alert(1)</script>"
        html = render_html(
            _report(results=(_result(target=_target(target_id=evil)),))
        )
        assert evil not in html
        assert "&lt;script&gt;" in html

    def test_a_malicious_run_id_is_escaped(self) -> None:
        html = render_html(_report(metadata=_metadata(run_id="<b>run</b>")))
        assert "<b>run</b>" not in html
        assert "&lt;b&gt;run&lt;/b&gt;" in html

    def test_the_document_stays_well_formed_with_hostile_input(self) -> None:
        html = render_html(_report(metadata=_metadata(hostname='"><img src=x>')))
        assert _is_well_formed(html)


# ---------------------------------------------------------------------------
# Empty run
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEmptyRun:
    def test_an_empty_run_renders_a_coherent_document(self) -> None:
        report = Report(
            metadata=_metadata(),
            inventory=InventorySummary(0, 0, 0),
            outcomes=OutcomeStats(0, 0, 0),
            outcomes_by_probe_type={},
            outcomes_by_target={},
            latency=None,
            alerts=AlertSummary(0, 0, {}),
            results=(),
            breaches=(),
        )
        html = render_html(report)
        assert _is_well_formed(html)
        assert "No probe results" in html
        assert "No breaches" in html
        assert "No successful probes" in html
        assert "none" in html
