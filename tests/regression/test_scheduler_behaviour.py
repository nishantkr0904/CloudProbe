"""Behavioural regression: the scheduler's one-shot execution contract.

The scheduler is a pipeline driver with no output to freeze as a golden file —
it invokes an injected action once per configured probe type and returns a
``RunSummary`` (architecture §10.2 gives it no regression golden surface).  Its
externally observable contract, which every consumer of a one-shot run depends
on, is behavioural:

* **One job per schedule, in configuration order.**  ``build_jobs`` turns the
  validated schedules into jobs preserving order, and a duplicate probe type is
  a ``DuplicateJobError`` — the collision the config layer does not itself
  reject.
* **One-shot runs every job exactly once.**  ``run_once`` invokes each action a
  single time; cadence is irrelevant to a one-shot pass.
* **Per-job failure isolation.**  A failing action never aborts the pass — its
  exception is captured in that job's outcome and the remaining jobs still run,
  so a monitoring run always attempts every probe type and reports what
  happened.

These are pinned as behaviour, from the public ``run_once`` / ``build_jobs``
entry points.  ``tests/unit/scheduler`` owns the fine-grained cases; this suite
guards the assembled contract — ordering, one-shot count, isolation — against a
refactor of the driver internals.

The action is a pure in-process recorder: no probe runs, no socket opens, no
clock is read.  No production code changes are required.
"""

from __future__ import annotations

import pytest

from cloudprobe.config import load
from cloudprobe.config.models import ProbeType
from cloudprobe.scheduler import DuplicateJobError, build_jobs, run_once

# Two probe types, so ordering and per-job isolation are observable.  tcp is
# declared before icmp; the one-shot pass must preserve that.
_CONFIG = """\
targets:
  - target_id: web-1
    host: 10.20.1.10
    port: 443
    probe_types: [tcp, icmp]
thresholds:
  - probe_type: tcp
  - probe_type: icmp
schedules:
  - probe_type: tcp
    cron_expression: "*/5 * * * *"
  - probe_type: icmp
    cron_expression: "*/10 * * * *"
probe:
  default_timeout_seconds: 3
"""


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_CONFIG, encoding="utf-8")
    return load(str(path))


@pytest.mark.regression
class TestOneShotSemantics:
    def test_each_probe_type_runs_exactly_once_in_order(self, config) -> None:
        invoked: list[ProbeType] = []

        summary = run_once(config, invoked.append)

        # Every configured schedule ran once, in declaration order.
        assert invoked == [ProbeType.TCP, ProbeType.ICMP]
        assert summary.succeeded is True
        assert [outcome.probe_type for outcome in summary.outcomes] == [
            ProbeType.TCP,
            ProbeType.ICMP,
        ]

    def test_job_creation_is_one_per_schedule_in_order(self, config) -> None:
        jobs = build_jobs(config, lambda _probe_type: None)

        assert [job.probe_type for job in jobs] == [ProbeType.TCP, ProbeType.ICMP]
        # The job id is the stable registration key downstream depends on.
        assert [job.job_id for job in jobs] == ["cloudprobe-tcp", "cloudprobe-icmp"]


@pytest.mark.regression
class TestFailureIsolation:
    def test_one_failing_action_does_not_abort_the_pass(self, config) -> None:
        invoked: list[ProbeType] = []

        def action(probe_type: ProbeType) -> None:
            invoked.append(probe_type)
            if probe_type is ProbeType.TCP:
                raise RuntimeError("probe blew up")

        summary = run_once(config, action)

        # The icmp job still ran despite tcp raising first.
        assert invoked == [ProbeType.TCP, ProbeType.ICMP]
        assert summary.succeeded is False

        failures = summary.failures
        assert [outcome.probe_type for outcome in failures] == [ProbeType.TCP]
        assert isinstance(failures[0].error, RuntimeError)

        # The non-failing job reports success in the same summary.
        icmp_outcome = next(o for o in summary.outcomes if o.probe_type is ProbeType.ICMP)
        assert icmp_outcome.succeeded is True


@pytest.mark.regression
class TestDuplicateScheduleRejected:
    def test_duplicate_probe_type_is_a_scheduler_error(self, tmp_path) -> None:
        # Two schedules for one probe type is a collision the config layer does
        # not reject; the scheduler must, rather than register a job twice.
        duplicate = """\
targets:
  - target_id: web-1
    host: 10.20.1.10
    port: 443
    probe_types: [tcp]
thresholds:
  - probe_type: tcp
schedules:
  - probe_type: tcp
    cron_expression: "*/5 * * * *"
  - probe_type: tcp
    cron_expression: "*/10 * * * *"
"""
        path = tmp_path / "config.yaml"
        path.write_text(duplicate, encoding="utf-8")
        config = load(str(path))

        with pytest.raises(DuplicateJobError):
            run_once(config, lambda _probe_type: None)
