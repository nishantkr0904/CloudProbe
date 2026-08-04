"""Regression: the report aggregate and its rendered HTML document.

The reporting layer turns a run into one ``Report`` aggregate, and the HTML
renderer turns that aggregate into a self-contained document an operator opens
in a browser (architecture §9.2).  Both are pure functions of their input — no
clock, no socket, no randomness — which is exactly what makes them golden-able:

* The **``Report`` aggregate** is the canonical in-memory shape the JSON, CSV
  and HTML renderers all walk.  Its key names (``metadata``, ``outcomes``,
  ``outcomes_by_probe_type``, ``latency``, ``alerts``, ``results``,
  ``breaches``) are the contract every renderer and every future storage layer
  depends on; a renamed key breaks them all at once.
* The **HTML document** is what a human actually reads.  Its structure (the
  section headings, table headers, the "Breached alerts" heading, the empty-
  state paragraph when nothing breached) is user-visible; freezing the whole
  document pins the operator-facing page, not just the data inside it.

The fixture builds a report through the real pipeline — assembled results and
alert decisions with a frozen clock — so the golden is a pure function of code.

Why regression, not unit: ``tests/unit/reporting`` asserts each aggregate field
and HTML section in isolation.  This freezes the *whole rendered surfaces* —
every key and every byte of the document together — so a refactor that keeps
each unit test green still trips if the assembled shape or page drifts.

No production code changes are required.
"""

from __future__ import annotations

import pytest

from cloudprobe.reporting import Report
from cloudprobe.reporting.renderers import render_html
from tests.regression.golden import assert_against_golden


@pytest.mark.regression
class TestReportAggregate:
    def test_report_aggregate_shape_is_frozen(
        self, frozen_report: Report, update_goldens: bool
    ) -> None:
        assert_against_golden(
            frozen_report,
            "report.json",
            update=update_goldens,
        )

    def test_latency_and_breach_summary_are_present(self, frozen_report: Report) -> None:
        # Behavioural guard on the fixture's premise: the frozen report must
        # exercise the latency and breach sections, or the golden would be
        # testing an empty page.  A slow success (250ms > 100ms threshold)
        # breaches; a second success gives latency a non-trivial distribution.
        assert frozen_report.latency is not None
        assert frozen_report.latency.count == 2
        assert frozen_report.alerts.breached == 1
        assert len(frozen_report.breaches) == 1


@pytest.mark.regression
class TestReportHtml:
    def test_html_document_is_frozen(self, frozen_report: Report, update_goldens: bool) -> None:
        assert_against_golden(
            render_html(frozen_report),
            "report.html",
            update=update_goldens,
        )

    def test_html_is_a_self_contained_document(self, frozen_report: Report) -> None:
        # The document must be complete and self-contained (architecture §9.2):
        # a doctype, a single root element, inlined styles and no external
        # asset references, so it opens with no network.
        document = render_html(frozen_report)

        assert document.startswith("<!DOCTYPE html>")
        assert '<html lang="en">' in document
        assert "<style>" in document
        assert "</style>" in document
        assert "href=" not in document
        assert "src=" not in document

    def test_html_escapes_untrusted_values(self, frozen_report: Report) -> None:
        # Every configuration- or probe-derived value reaches the page escaped,
        # so a hostname or error string can never inject markup.
        document = render_html(frozen_report)

        assert "&lt;" not in document  # nothing was literally escaped today
        for result in frozen_report.results:
            assert result.target.host in document
