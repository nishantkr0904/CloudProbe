"""Unit tests for scheduled job construction.

Jobs are pure data plus an injected callable, so these tests need no fakes
beyond a recorder: nothing here touches a scheduler, a clock or a network.

Covers:
    - a schedule becomes a job carrying its cadence and limits
    - jobs are built in configuration order, one per schedule
    - the job id is stable and derived from the probe type
    - running a job invokes the injected action with its probe type
    - the action is not invoked at construction time
    - every job shares the one injected action
    - duplicate probe types are rejected
    - duplicate job error is a scheduler error
    - jobs are immutable
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from cloudprobe.config.models import (
    CloudProbeConfig,
    ProbeType,
    Schedule,
    Target,
    Threshold,
)
from cloudprobe.scheduler import (
    DuplicateJobError,
    ProbeJob,
    SchedulerError,
    build_jobs,
)


class _Recorder:
    """Records every probe type it is invoked with."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.calls: list[ProbeType] = []

    def __call__(self, probe_type: ProbeType) -> None:
        self.calls.append(probe_type)
        if self._error is not None:
            raise self._error


def _target(**overrides: Any) -> Target:
    base: dict[str, Any] = {
        "target_id": "web-1",
        "host": "10.20.1.10",
        "port": 443,
        "probe_types": [ProbeType.TCP],
        "tags": {"tier": "web"},
    }
    base.update(overrides)
    return Target(**base)


def _schedule(**overrides: Any) -> Schedule:
    base: dict[str, Any] = {
        "probe_type": ProbeType.TCP,
        "cron_expression": "*/1 * * * *",
        "timeout_seconds": 10,
        "max_concurrency": 5,
    }
    base.update(overrides)
    return Schedule(**base)


def _config(schedules: list[Schedule] | None = None) -> CloudProbeConfig:
    """A valid config whose targets are covered by the given schedules.

    ``model_construct`` is deliberately avoided: these tests should consume the
    same validated object the loader produces.
    """
    schedules = [_schedule()] if schedules is None else schedules
    probe_types = [s.probe_type for s in schedules] or [ProbeType.TCP]
    return CloudProbeConfig(
        targets=[_target(probe_types=list(dict.fromkeys(probe_types)))],
        thresholds=[Threshold(probe_type=pt) for pt in dict.fromkeys(probe_types)],
        schedules=schedules,
    )


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


# ---------------------------------------------------------------------------
# Construction from configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildJobs:
    def test_schedule_becomes_a_job_carrying_its_cadence_and_limits(self) -> None:
        config = _config(
            [_schedule(cron_expression="*/5 * * * *", timeout_seconds=3, max_concurrency=7)]
        )
        (job,) = build_jobs(config, _Recorder())
        assert job.probe_type is ProbeType.TCP
        assert job.cron_expression == "*/5 * * * *"
        assert job.timeout_seconds == 3
        assert job.max_concurrency == 7

    def test_one_job_is_built_per_schedule_in_configuration_order(self) -> None:
        config = _config(
            [
                _schedule(probe_type=ProbeType.TCP),
                _schedule(probe_type=ProbeType.ICMP),
                _schedule(probe_type=ProbeType.SSH),
            ]
        )
        jobs = build_jobs(config, _Recorder())
        assert [job.probe_type for job in jobs] == [
            ProbeType.TCP,
            ProbeType.ICMP,
            ProbeType.SSH,
        ]

    def test_every_job_shares_the_injected_action(self) -> None:
        action = _Recorder()
        config = _config([_schedule(probe_type=ProbeType.TCP), _schedule(probe_type=ProbeType.UDP)])
        for job in build_jobs(config, action):
            job.run()
        assert action.calls == [ProbeType.TCP, ProbeType.UDP]

    def test_building_jobs_does_not_invoke_the_action(self) -> None:
        action = _Recorder()
        build_jobs(_config(), action)
        assert action.calls == []


# ---------------------------------------------------------------------------
# Job identity and execution
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProbeJob:
    def test_job_id_is_derived_from_the_probe_type(self) -> None:
        assert _job(probe_type=ProbeType.ICMP).job_id == "cloudprobe-icmp"

    def test_job_id_is_stable_across_equal_jobs(self) -> None:
        action = _Recorder()
        assert _job(action=action).job_id == _job(action=action).job_id

    def test_running_a_job_invokes_the_action_with_its_probe_type(self) -> None:
        action = _Recorder()
        _job(probe_type=ProbeType.UDP, action=action).run()
        assert action.calls == [ProbeType.UDP]

    def test_running_a_job_twice_invokes_the_action_twice(self) -> None:
        action = _Recorder()
        job = _job(action=action)
        job.run()
        job.run()
        assert action.calls == [ProbeType.TCP, ProbeType.TCP]

    def test_action_failures_propagate_to_the_caller(self) -> None:
        job = _job(action=_Recorder(error=RuntimeError("probe exploded")))
        with pytest.raises(RuntimeError, match="probe exploded"):
            job.run()

    def test_job_is_immutable(self) -> None:
        with pytest.raises(FrozenInstanceError):
            _job().probe_type = ProbeType.ICMP  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Invalid configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDuplicateSchedules:
    def test_two_schedules_for_one_probe_type_are_rejected(self) -> None:
        config = _config(
            [
                _schedule(probe_type=ProbeType.TCP, cron_expression="*/1 * * * *"),
                _schedule(probe_type=ProbeType.TCP, cron_expression="*/5 * * * *"),
            ]
        )
        with pytest.raises(DuplicateJobError) as excinfo:
            build_jobs(config, _Recorder())
        assert "tcp" in str(excinfo.value)

    def test_duplicate_job_error_is_a_scheduler_error(self) -> None:
        config = _config([_schedule(), _schedule()])
        with pytest.raises(SchedulerError):
            build_jobs(config, _Recorder())
