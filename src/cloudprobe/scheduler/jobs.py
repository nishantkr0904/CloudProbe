"""Scheduled job definitions — what runs, how often, and how many at once.

This is the static half of the pipeline driver (project-structure §6.9): it
turns the validated ``Schedule`` entries the configuration layer produced into
``ProbeJob`` records the cron scheduler and the one-shot runner both consume.

A job holds *what to run* as an injected callable.  That is the whole reason
this layer can orchestrate without performing: it never imports probes, Boto3
or Paramiko, and has no idea what the action it invokes actually does.

This module schedules nothing and imports no scheduler library.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cloudprobe.config.models import CloudProbeConfig, ProbeType

# One action is invoked per probe type.  The callable is supplied by the
# caller (Phase 5's pipeline runner), so this layer stays free of probe,
# metrics and reporting imports.
JobAction = Callable[[ProbeType], None]


class SchedulerError(Exception):
    """Base class for every error the scheduler layer raises.

    Callers can catch this one type to handle any scheduling failure without
    importing APScheduler, keeping the library an implementation detail.
    """


class DuplicateJobError(SchedulerError):
    """Two schedules declare the same probe type."""


@dataclass(frozen=True)
class ProbeJob:
    """One probe type's cadence, bound to the action that executes it.

    ``timeout_seconds`` is carried but not enforced here: enforcing it means
    running a probe, which belongs to the pipeline runner.
    """

    probe_type: ProbeType
    cron_expression: str
    timeout_seconds: int
    max_concurrency: int
    action: JobAction

    @property
    def job_id(self) -> str:
        """The stable identifier this job is registered under."""
        return f"cloudprobe-{self.probe_type.value}"

    def run(self) -> None:
        """Invoke the action once, for this job's probe type."""
        self.action(self.probe_type)


def build_jobs(config: CloudProbeConfig, action: JobAction) -> list[ProbeJob]:
    """Build one job per configured schedule.

    Args:
        config: A validated configuration whose ``schedules`` declare cadences.
        action: The callable each job invokes, receiving the probe type.

    Returns:
        Jobs in configuration order, one per schedule.

    Raises:
        DuplicateJobError: Two schedules declare the same probe type.  The
            configuration layer validates that schedules *cover* the probe
            types in use, not that they are unique, so the collision is caught
            here rather than surfacing as a scheduler-library error.
    """
    jobs: list[ProbeJob] = []
    seen: set[ProbeType] = set()
    for schedule in config.schedules:
        if schedule.probe_type in seen:
            raise DuplicateJobError(
                f"probe type {schedule.probe_type.value!r} has more than one schedule"
            )
        seen.add(schedule.probe_type)
        jobs.append(
            ProbeJob(
                probe_type=schedule.probe_type,
                cron_expression=schedule.cron_expression,
                timeout_seconds=schedule.timeout_seconds,
                max_concurrency=schedule.max_concurrency,
                action=action,
            )
        )
    return jobs
