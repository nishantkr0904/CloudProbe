"""HTML rendering of a ``Report`` — one self-contained document per run.

This is the ``html`` arm of the report renderers (project-structure §6.7,
architecture §9.2).  ``render_html`` is a pure function of the ``Report`` model:
given the same report it returns the same string, reads no clock, opens no
socket and touches no AWS.  It performs no aggregation — every number it shows
was computed by the assembler — so the HTML can never disagree with the JSON or
CSV renderers over the same run.

The document is self-contained by design (architecture §9.2): the stylesheet is
inlined in a ``<style>`` block, there are no external assets and no JavaScript
is required to read the content, so a report opens on any operator's laptop with
no network.

Every value that originates from configuration or probe output — hostnames,
target ids, hosts, error text — is untrusted in an HTML context and is escaped
through ``html.escape`` before it reaches the page.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from cloudprobe.alerting.models import Alert
from cloudprobe.config.models import AlertSeverity
from cloudprobe.probes.base import ProbeResult
from cloudprobe.reporting.models import (
    AlertSummary,
    InventorySummary,
    LatencyStatistics,
    OutcomeStats,
    Report,
    RunMetadata,
)

# Inlined so the document needs no external stylesheet (architecture §9.2).
_STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }
h1 { margin-bottom: 0.25rem; }
h2 { margin-top: 2rem; border-bottom: 1px solid #ddd; padding-bottom: 0.25rem; }
table { border-collapse: collapse; width: 100%; margin-top: 0.5rem; }
th, td { border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; }
th { background: #f4f4f4; }
.ok { color: #157347; font-weight: 600; }
.fail { color: #b02a37; font-weight: 600; }
.empty { color: #666; font-style: italic; }
.sev-critical { color: #b02a37; font-weight: 600; }
.sev-warning { color: #997404; font-weight: 600; }
.sev-info { color: #0a58ca; }
""".strip()

_SEVERITY_CLASS = {
    AlertSeverity.CRITICAL: "sev-critical",
    AlertSeverity.WARNING: "sev-warning",
    AlertSeverity.INFO: "sev-info",
}


def _text(value: object) -> str:
    """Escape any value for safe interpolation into HTML text."""
    return escape(str(value))


def _timestamp(moment: datetime) -> str:
    """Render an instant as an escaped ISO-8601 string."""
    return _text(moment.isoformat())


def _ratio(ratio: float | None) -> str:
    """Render a success ratio as a percentage, or an em dash when absent."""
    if ratio is None:
        return '<span class="empty">&mdash;</span>'
    return f"{ratio * 100:.1f}%"


def _severity_label(severity: AlertSeverity) -> str:
    """Render a severity as an escaped, colour-classed label.

    ``_SEVERITY_CLASS`` maps every ``AlertSeverity`` member, so the lookup
    always finds a class — no fallback branch is needed or reachable.
    """
    return f'<span class="{_SEVERITY_CLASS[severity]}">{_text(severity.value)}</span>'


def _row(cells: list[str]) -> str:
    """Wrap already-escaped cells into a table row."""
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def _outcome_cells(stats: OutcomeStats) -> list[str]:
    """The shared success/failure columns of an outcome row."""
    return [
        str(stats.total),
        f'<span class="ok">{stats.successes}</span>',
        f'<span class="fail">{stats.failures}</span>',
        _ratio(stats.success_ratio),
    ]


def _metadata_section(metadata: RunMetadata) -> str:
    rows = [
        _row(["Run ID", _text(metadata.run_id)]),
        _row(["Mode", _text(metadata.mode.value)]),
        _row(["Host", _text(metadata.hostname)]),
        _row(["Started", _timestamp(metadata.started_at)]),
        _row(["Completed", _timestamp(metadata.completed_at)]),
        _row(["Duration (s)", _text(f"{metadata.duration_seconds:.3f}")]),
    ]
    return "<h2>Run metadata</h2>\n<table>\n" + "\n".join(rows) + "\n</table>"


def _inventory_section(inventory: InventorySummary) -> str:
    rows = [
        _row(["Targets", str(inventory.target_count)]),
        _row(["VPCs", str(inventory.vpc_count)]),
        _row(["Subnets", str(inventory.subnet_count)]),
    ]
    return "<h2>Inventory</h2>\n<table>\n" + "\n".join(rows) + "\n</table>"


def _grouped_outcome_table(heading: str, label_header: str, rows: list[str]) -> str:
    """A grouped-outcome table, or a "none" row when the group is empty."""
    body = "\n".join(rows) if rows else _row(['<span class="empty">none</span>'])
    return (
        f"<h3>{heading}</h3>\n<table>\n"
        f"<tr><th>{label_header}</th><th>Total</th><th>Successes</th>"
        "<th>Failures</th><th>Success rate</th></tr>\n"
        + body
        + "\n</table>"
    )


def _outcomes_section(report: Report) -> str:
    overall = "<h3>Overall</h3>\n<table>\n" + (
        "<tr><th>Total</th><th>Successes</th><th>Failures</th><th>Success rate</th></tr>\n"
        + _row(_outcome_cells(report.outcomes))
    ) + "\n</table>"

    by_type = _grouped_outcome_table(
        "By probe type",
        "Probe type",
        [
            _row([_text(probe_type.value), *_outcome_cells(stats)])
            for probe_type, stats in report.outcomes_by_probe_type.items()
        ],
    )
    by_target = _grouped_outcome_table(
        "By target",
        "Target",
        [
            _row([_text(target_id), *_outcome_cells(stats)])
            for target_id, stats in report.outcomes_by_target.items()
        ],
    )

    return "<h2>Outcomes</h2>\n" + "\n".join([overall, by_type, by_target])


def _latency_section(latency: LatencyStatistics | None) -> str:
    if latency is None:
        return '<h2>Latency</h2>\n<p class="empty">No successful probes to measure.</p>'
    rows = [
        _row(["Successful probes", str(latency.count)]),
        _row(["Minimum (ms)", _text(f"{latency.minimum:.3f}")]),
        _row(["Maximum (ms)", _text(f"{latency.maximum:.3f}")]),
        _row(["Mean (ms)", _text(f"{latency.mean:.3f}")]),
        _row(["p50 (ms)", _text(f"{latency.p50:.3f}")]),
        _row(["p95 (ms)", _text(f"{latency.p95:.3f}")]),
        _row(["p99 (ms)", _text(f"{latency.p99:.3f}")]),
    ]
    return "<h2>Latency</h2>\n<table>\n" + "\n".join(rows) + "\n</table>"


def _alerts_section(alerts: AlertSummary) -> str:
    rows = [
        _row(["Evaluated", str(alerts.total)]),
        _row(["Breached", str(alerts.breached)]),
    ]
    for severity, count in alerts.by_severity.items():
        rows.append(_row([_severity_label(severity), str(count)]))
    return "<h2>Alert summary</h2>\n<table>\n" + "\n".join(rows) + "\n</table>"


def _results_table(results: tuple[ProbeResult, ...]) -> str:
    if not results:
        return '<h2>Probe results</h2>\n<p class="empty">No probe results.</p>'
    header = (
        "<tr><th>Target</th><th>Host</th><th>Probe</th><th>Status</th>"
        "<th>Latency (ms)</th><th>Error</th><th>Timestamp</th></tr>"
    )
    rows = []
    for result in results:
        status = (
            '<span class="ok">success</span>'
            if result.success
            else '<span class="fail">failure</span>'
        )
        error = _text(result.error_class.value) if result.error_class is not None else ""
        rows.append(
            _row(
                [
                    _text(result.target.target_id),
                    _text(result.target.host),
                    _text(result.probe_type.value),
                    status,
                    _text(f"{result.latency_ms:.3f}"),
                    error,
                    _timestamp(result.timestamp),
                ]
            )
        )
    return "<h2>Probe results</h2>\n<table>\n" + header + "\n" + "\n".join(rows) + "\n</table>"


def _breaches_table(breaches: tuple[Alert, ...]) -> str:
    if not breaches:
        return '<h2>Breached alerts</h2>\n<p class="empty">No breaches.</p>'
    header = (
        "<tr><th>Rule</th><th>Target</th><th>Probe</th><th>Severity</th>"
        "<th>Metric</th><th>Observed</th><th>Threshold</th><th>Timestamp</th></tr>"
    )
    rows = []
    for alert in breaches:
        rows.append(
            _row(
                [
                    _text(alert.rule_id),
                    _text(alert.target.target_id),
                    _text(alert.probe_type.value),
                    _severity_label(alert.severity),
                    _text(alert.metric.value),
                    _text(f"{alert.observed_value} {alert.operator.value}"),
                    _text(str(alert.threshold_value)),
                    _timestamp(alert.timestamp),
                ]
            )
        )
    return "<h2>Breached alerts</h2>\n<table>\n" + header + "\n" + "\n".join(rows) + "\n</table>"


def render_html(report: Report) -> str:
    """Render ``report`` as one self-contained HTML document.

    Args:
        report: The assembled report to render.

    Returns:
        A complete HTML document string: run metadata, inventory, outcome and
        latency statistics, the alert summary, and per-result and breach
        tables — with every configuration- or probe-derived value escaped.
    """
    title = _text(f"CloudProbe report {report.metadata.run_id}")
    body = "\n".join(
        [
            _metadata_section(report.metadata),
            _inventory_section(report.inventory),
            _outcomes_section(report),
            _latency_section(report.latency),
            _alerts_section(report.alerts),
            _results_table(report.results),
            _breaches_table(report.breaches),
        ]
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f"<title>{title}</title>\n"
        f"<style>\n{_STYLE}\n</style>\n"
        "</head>\n"
        "<body>\n"
        f"<h1>{title}</h1>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )
