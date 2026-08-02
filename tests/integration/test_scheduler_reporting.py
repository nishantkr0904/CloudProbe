"""Integration: scheduler one-shot → probe execution → reporting engine.

Exercises the boundary between the pipeline driver and the reporting engine
(architecture §10.2 "Scheduler → oneshot wiring", "Reporting → full-pipeline
render").  The scheduler owns no work of its own — it invokes an injected
action once per configured probe type (§7.4).  Here that action runs a real
``TcpProbe`` and collects its results, which ``assemble`` then folds into the
canonical ``Report``.  This is the wiring a one-shot run performs: config →
scheduled jobs → probe results → report.

``TcpProbe`` reaches the network through one seam — ``socket.create_connection``
— which is monkeypatched, so no host is contacted.  The scheduler reads no
clock (a one-shot pass has no cadence), and ``build_metadata`` is handed an
explicit clock reading, so the test does not depend on wall-clock time
(architecture §10.3).
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone

import pytest

from cloudprobe.config import load
from cloudprobe.config.models import ProbeType
from cloudprobe.probes import ProbeResult, TcpProbe
from cloudprobe.reporting import Report, RunMode, assemble, build_metadata
from cloudprobe.scheduler import run_once

_CONFIG = """\
targets:
  - target_id: web-1
    host: 10.20.1.10
    port: 443
    probe_types: [tcp]
thresholds:
  - probe_type: tcp
    warn_above_ms: 200
schedules:
  - probe_type: tcp
    cron_expression: "*/5 * * * *"
probe:
  default_timeout_seconds: 3
"""


class _FakeConnection:
    """A stand-in for the socket ``create_connection`` returns."""

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


@pytest.fixture
def config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(_CONFIG, encoding="utf-8")
    return load(str(config_file))


@pytest.mark.integration
class TestSchedulerToReporting:
    def test_a_one_shot_pass_produces_a_report(self, config, monkeypatch) -> None:
        monkeypatch.setattr(
            socket,
            "create_connection",
            lambda address, timeout=None, **kwargs: _FakeConnection(),
        )

        probe = TcpProbe(config.probe.default_timeout_seconds)
        results: list[ProbeResult] = []

        def action(probe_type: ProbeType) -> None:
            # The scheduler invokes one action per probe type; it does not know
            # this one runs a probe.  Only the tcp schedule is configured.
            for target in config.targets:
                if probe_type in target.probe_types:
                    results.append(probe.run(target))

        summary = run_once(config, action)

        # Every scheduled job ran, and the action collected one result.
        assert summary.succeeded is True
        assert [outcome.probe_type for outcome in summary.outcomes] == [ProbeType.TCP]
        assert len(results) == 1 and results[0].success is True

        metadata = build_metadata(
            run_id="run-1",
            mode=RunMode.ONESHOT,
            started_at=datetime(2026, 8, 3, 6, 0, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 8, 3, 6, 0, 1, tzinfo=timezone.utc),
            hostname="test-host",
        )
        report = assemble(metadata, results, [])

        assert isinstance(report, Report)
        assert report.metadata.mode is RunMode.ONESHOT
        assert report.outcomes.total == 1
        assert report.outcomes.successes == 1
        assert report.latency is not None and report.latency.count == 1
