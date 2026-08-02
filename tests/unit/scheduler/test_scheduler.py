"""Unit tests for the cron scheduler and one-shot execution.

The APScheduler scheduler is a hand-written fake rather than a real
``BackgroundScheduler``: the scheduler is injected, so a fake exercises the
real registration, trigger-translation and lifecycle code path while starting
no threads and consulting no clock (architecture §10.3).  Real
``CronTrigger`` objects are still constructed — building one schedules nothing
— so cron translation is verified against APScheduler itself.

Covers cron.py:
    - each job is registered once, under a stable id
    - the trigger matches the job's cron expression
    - max_concurrency is registered as APScheduler's max_instances
    - missed runs are coalesced
    - start registers before starting, and marks the scheduler started
    - shutdown stops the scheduler and forwards the wait flag
    - a second start, and a shutdown before start, are rejected
    - invalid cron expressions are rejected with the cause chained, before
      any job is registered
    - construction from a configuration builds one job per schedule

Covers oneshot.py:
    - every job's action runs exactly once, in order
    - a successful pass reports success and no failures
    - a failing action is captured, not raised, and the pass continues
    - the summary carries the failing probe type and its exception
    - run_once builds jobs from configuration and runs them once
    - one-shot mode uses no scheduler and ignores cadence
"""

from __future__ import annotations

from typing import Any

import pytest
from apscheduler.triggers.cron import CronTrigger

from cloudprobe.config.models import (
    CloudProbeConfig,
    ProbeType,
    Schedule,
    Target,
    Threshold,
)
from cloudprobe.scheduler import (
    CronScheduler,
    InvalidCronExpressionError,
    JobOutcome,
    ProbeJob,
    RunSummary,
    SchedulerAlreadyStartedError,
    SchedulerError,
    SchedulerNotStartedError,
    build_trigger,
    run_jobs_once,
    run_once,
)


class _FakeScheduler:
    """Records every add_job, start and shutdown call.

    Starts no thread and reads no clock, so tests stay offline and
    deterministic.
    """

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.start_calls = 0
        self.shutdown_calls: list[bool] = []

    def add_job(self, func: Any, trigger: Any, **kwargs: Any) -> dict[str, Any]:
        record = {"func": func, "trigger": trigger, **kwargs}
        self.jobs.append(record)
        return record

    def start(self) -> None:
        self.start_calls += 1

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


class _Recorder:
    """Records every probe type it is invoked with, or raises."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[ProbeType] = []

    def __call__(self, probe_type: ProbeType) -> None:
        self.calls.append(probe_type)
        if self._error is not None:
            raise self._error


def _job(**overrides: Any) -> ProbeJob:
    base: dict[str, Any] = {
        "probe_type": ProbeType.TCP,
        "cron_expression": "*/1 * * * *",
        "timeout_seconds": 10,
        "max_concurrency": 5,
        "action": _Recorder(),
    }
    base.update(overrides)
    return ProbeJob(**base)


def _config(*probe_types: ProbeType) -> CloudProbeConfig:
    types = list(probe_types) or [ProbeType.TCP]
    return CloudProbeConfig(
        targets=[
            Target(
                target_id="web-1",
                host="10.20.1.10",
                port=443,
                probe_types=types,
            )
        ],
        thresholds=[Threshold(probe_type=pt) for pt in types],
        schedules=[
            Schedule(probe_type=pt, cron_expression="*/1 * * * *") for pt in types
        ],
    )


# ---------------------------------------------------------------------------
# Cron translation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildTrigger:
    def test_a_valid_expression_becomes_a_cron_trigger(self) -> None:
        assert isinstance(build_trigger("*/1 * * * *"), CronTrigger)

    def test_the_trigger_preserves_the_expression_fields(self) -> None:
        trigger = build_trigger("30 4 * * 1")
        fields = {field.name: str(field) for field in trigger.fields}
        assert fields["minute"] == "30"
        assert fields["hour"] == "4"
        assert fields["day_of_week"] == "1"

    def test_a_malformed_expression_is_rejected(self) -> None:
        with pytest.raises(InvalidCronExpressionError) as excinfo:
            build_trigger("*/1 * * * notaday")
        assert "not valid" in str(excinfo.value)

    def test_an_out_of_range_expression_is_rejected(self) -> None:
        with pytest.raises(InvalidCronExpressionError):
            build_trigger("99 * * * *")

    def test_the_underlying_cause_is_chained(self) -> None:
        with pytest.raises(InvalidCronExpressionError) as excinfo:
            build_trigger("99 * * * *")
        assert isinstance(excinfo.value.__cause__, ValueError)

    def test_invalid_cron_expression_error_is_a_scheduler_error(self) -> None:
        with pytest.raises(SchedulerError):
            build_trigger("99 * * * *")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistration:
    def test_each_job_is_registered_once(self) -> None:
        scheduler = _FakeScheduler()
        jobs = [_job(probe_type=ProbeType.TCP), _job(probe_type=ProbeType.ICMP)]
        CronScheduler(jobs, scheduler).register()
        assert [job["id"] for job in scheduler.jobs] == [
            "cloudprobe-tcp",
            "cloudprobe-icmp",
        ]

    def test_the_registered_callable_invokes_the_job_action(self) -> None:
        scheduler = _FakeScheduler()
        action = _Recorder()
        CronScheduler([_job(action=action)], scheduler).register()
        scheduler.jobs[0]["func"]()
        assert action.calls == [ProbeType.TCP]

    def test_the_trigger_matches_the_job_cron_expression(self) -> None:
        scheduler = _FakeScheduler()
        CronScheduler([_job(cron_expression="30 4 * * *")], scheduler).register()
        trigger = scheduler.jobs[0]["trigger"]
        fields = {field.name: str(field) for field in trigger.fields}
        assert fields["minute"] == "30"
        assert fields["hour"] == "4"

    def test_max_concurrency_is_registered_as_max_instances(self) -> None:
        scheduler = _FakeScheduler()
        CronScheduler([_job(max_concurrency=3)], scheduler).register()
        assert scheduler.jobs[0]["max_instances"] == 3

    def test_missed_runs_are_coalesced(self) -> None:
        scheduler = _FakeScheduler()
        CronScheduler([_job()], scheduler).register()
        assert scheduler.jobs[0]["coalesce"] is True

    def test_registration_does_not_start_the_scheduler(self) -> None:
        scheduler = _FakeScheduler()
        CronScheduler([_job()], scheduler).register()
        assert scheduler.start_calls == 0

    def test_no_job_is_registered_when_one_expression_is_invalid(self) -> None:
        scheduler = _FakeScheduler()
        jobs = [
            _job(probe_type=ProbeType.TCP),
            _job(probe_type=ProbeType.ICMP, cron_expression="99 * * * *"),
        ]
        with pytest.raises(InvalidCronExpressionError):
            CronScheduler(jobs, scheduler).register()
        assert scheduler.jobs == []


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLifecycle:
    def test_start_registers_the_jobs_and_starts_the_scheduler(self) -> None:
        scheduler = _FakeScheduler()
        CronScheduler([_job()], scheduler).start()
        assert len(scheduler.jobs) == 1
        assert scheduler.start_calls == 1

    def test_a_new_scheduler_is_not_started(self) -> None:
        assert CronScheduler([_job()], _FakeScheduler()).started is False

    def test_start_marks_the_scheduler_started(self) -> None:
        cron = CronScheduler([_job()], _FakeScheduler())
        cron.start()
        assert cron.started is True

    def test_starting_twice_is_rejected(self) -> None:
        cron = CronScheduler([_job()], _FakeScheduler())
        cron.start()
        with pytest.raises(SchedulerAlreadyStartedError):
            cron.start()

    def test_a_rejected_second_start_does_not_re_register_jobs(self) -> None:
        scheduler = _FakeScheduler()
        cron = CronScheduler([_job()], scheduler)
        cron.start()
        with pytest.raises(SchedulerAlreadyStartedError):
            cron.start()
        assert len(scheduler.jobs) == 1
        assert scheduler.start_calls == 1

    def test_shutdown_stops_the_scheduler(self) -> None:
        scheduler = _FakeScheduler()
        cron = CronScheduler([_job()], scheduler)
        cron.start()
        cron.shutdown()
        assert scheduler.shutdown_calls == [True]
        assert cron.started is False

    def test_shutdown_forwards_the_wait_flag(self) -> None:
        scheduler = _FakeScheduler()
        cron = CronScheduler([_job()], scheduler)
        cron.start()
        cron.shutdown(wait=False)
        assert scheduler.shutdown_calls == [False]

    def test_shutdown_before_start_is_rejected(self) -> None:
        scheduler = _FakeScheduler()
        with pytest.raises(SchedulerNotStartedError):
            CronScheduler([_job()], scheduler).shutdown()
        assert scheduler.shutdown_calls == []

    def test_a_scheduler_can_be_restarted_after_shutdown(self) -> None:
        scheduler = _FakeScheduler()
        cron = CronScheduler([_job()], scheduler)
        cron.start()
        cron.shutdown()
        cron.start()
        assert scheduler.start_calls == 2
        assert cron.started is True


# ---------------------------------------------------------------------------
# Construction from configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFromConfig:
    def test_one_job_is_registered_per_configured_schedule(self) -> None:
        scheduler = _FakeScheduler()
        cron = CronScheduler.from_config(
            _config(ProbeType.TCP, ProbeType.ICMP), _Recorder(), scheduler
        )
        cron.register()
        assert [job["id"] for job in scheduler.jobs] == [
            "cloudprobe-tcp",
            "cloudprobe-icmp",
        ]

    def test_the_configured_action_is_wired_to_each_job(self) -> None:
        scheduler = _FakeScheduler()
        action = _Recorder()
        CronScheduler.from_config(_config(ProbeType.UDP), action, scheduler).register()
        scheduler.jobs[0]["func"]()
        assert action.calls == [ProbeType.UDP]


# ---------------------------------------------------------------------------
# One-shot execution
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunJobsOnce:
    def test_every_action_runs_exactly_once_in_order(self) -> None:
        action = _Recorder()
        jobs = [
            _job(probe_type=ProbeType.TCP, action=action),
            _job(probe_type=ProbeType.ICMP, action=action),
        ]
        run_jobs_once(jobs)
        assert action.calls == [ProbeType.TCP, ProbeType.ICMP]

    def test_a_successful_pass_reports_success(self) -> None:
        summary = run_jobs_once([_job()])
        assert summary.succeeded is True
        assert summary.failures == []

    def test_the_summary_carries_one_outcome_per_job(self) -> None:
        summary = run_jobs_once(
            [_job(probe_type=ProbeType.TCP), _job(probe_type=ProbeType.SSH)]
        )
        assert [outcome.probe_type for outcome in summary.outcomes] == [
            ProbeType.TCP,
            ProbeType.SSH,
        ]

    def test_a_failing_action_is_captured_rather_than_raised(self) -> None:
        failure = RuntimeError("probe exploded")
        summary = run_jobs_once([_job(action=_Recorder(error=failure))])
        assert summary.succeeded is False
        assert summary.outcomes[0].error is failure

    def test_a_failing_job_does_not_stop_the_pass(self) -> None:
        healthy = _Recorder()
        jobs = [
            _job(probe_type=ProbeType.TCP, action=_Recorder(error=RuntimeError("boom"))),
            _job(probe_type=ProbeType.ICMP, action=healthy),
        ]
        summary = run_jobs_once(jobs)
        assert healthy.calls == [ProbeType.ICMP]
        assert [outcome.succeeded for outcome in summary.outcomes] == [False, True]

    def test_failures_name_the_offending_probe_type(self) -> None:
        jobs = [
            _job(probe_type=ProbeType.TCP),
            _job(probe_type=ProbeType.UDP, action=_Recorder(error=RuntimeError("boom"))),
        ]
        summary = run_jobs_once(jobs)
        assert [outcome.probe_type for outcome in summary.failures] == [ProbeType.UDP]

    def test_an_empty_job_list_is_a_successful_empty_pass(self) -> None:
        summary = run_jobs_once([])
        assert summary.succeeded is True
        assert summary.outcomes == []


@pytest.mark.unit
class TestRunOnce:
    def test_run_once_runs_every_configured_probe_type(self) -> None:
        action = _Recorder()
        run_once(_config(ProbeType.TCP, ProbeType.ICMP), action)
        assert action.calls == [ProbeType.TCP, ProbeType.ICMP]

    def test_run_once_returns_a_summary_for_every_schedule(self) -> None:
        summary = run_once(_config(ProbeType.TCP, ProbeType.SSH), _Recorder())
        assert isinstance(summary, RunSummary)
        assert len(summary.outcomes) == 2

    def test_run_once_ignores_cadence(self) -> None:
        """An invalid cron expression cannot stop a single pass.

        One-shot mode never builds a trigger, which is what lets host cron and
        Kubernetes CronJob deployments own the cadence themselves.
        """
        config = CloudProbeConfig(
            targets=[Target(target_id="web-1", host="10.20.1.10", probe_types=[ProbeType.TCP])],
            thresholds=[Threshold(probe_type=ProbeType.TCP)],
            schedules=[Schedule(probe_type=ProbeType.TCP, cron_expression="99 * * * *")],
        )
        action = _Recorder()
        assert run_once(config, action).succeeded is True
        assert action.calls == [ProbeType.TCP]


@pytest.mark.unit
class TestJobOutcome:
    def test_an_outcome_without_an_error_succeeded(self) -> None:
        assert JobOutcome(probe_type=ProbeType.TCP).succeeded is True

    def test_an_outcome_with_an_error_did_not_succeed(self) -> None:
        outcome = JobOutcome(probe_type=ProbeType.TCP, error=RuntimeError("boom"))
        assert outcome.succeeded is False
