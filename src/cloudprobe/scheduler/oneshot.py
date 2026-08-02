"""One-shot execution — a single pass over every scheduled probe type.

This is the ``--once`` half of the pipeline driver (project-structure §6.9,
architecture §7.4 "One-shot"): it invokes each job's action exactly once and
returns, so CI, the Docker ``oneshot`` mode, host ``cron`` and Kubernetes
CronJob all work through the same small contract.

It uses no scheduler library and reads no clock: cadence is irrelevant when
each job runs once.  Cron expressions are therefore not interpreted here, which
is why a malformed cadence still permits a one-shot run.

A failing action does not abort the pass.  A monitoring run should attempt every
probe type and report what happened, so failures are captured per job and
returned for the caller to act on.
"""

from __future__ import annotations

from dataclasses import dataclass

from cloudprobe.config.models import CloudProbeConfig, ProbeType
from cloudprobe.scheduler.jobs import JobAction, ProbeJob, build_jobs


@dataclass(frozen=True)
class JobOutcome:
    """What happened when one job's action was invoked.

    ``error`` is the exception the action raised, or ``None`` on success.
    """

    probe_type: ProbeType
    error: Exception | None = None

    @property
    def succeeded(self) -> bool:
        """Whether the action completed without raising."""
        return self.error is None


@dataclass(frozen=True)
class RunSummary:
    """The result of one complete pass over every job."""

    outcomes: list[JobOutcome]

    @property
    def succeeded(self) -> bool:
        """Whether every job's action completed without raising."""
        return all(outcome.succeeded for outcome in self.outcomes)

    @property
    def failures(self) -> list[JobOutcome]:
        """The outcomes whose actions raised."""
        return [outcome for outcome in self.outcomes if not outcome.succeeded]


def run_jobs_once(jobs: list[ProbeJob]) -> RunSummary:
    """Invoke every job's action once, in order.

    Args:
        jobs: The jobs to run.

    Returns:
        A summary carrying one outcome per job, in the order given.
    """
    outcomes: list[JobOutcome] = []
    for job in jobs:
        try:
            job.run()
        except Exception as exc:
            # One probe type failing must not deny the operator results for the
            # rest, so the failure is recorded and the pass continues.
            outcomes.append(JobOutcome(probe_type=job.probe_type, error=exc))
        else:
            outcomes.append(JobOutcome(probe_type=job.probe_type))
    return RunSummary(outcomes=outcomes)


def run_once(config: CloudProbeConfig, action: JobAction) -> RunSummary:
    """Run one pass over every probe type ``config`` schedules.

    Args:
        config: A validated configuration whose ``schedules`` declare the probe
            types to run.
        action: The callable invoked once per probe type.

    Returns:
        A summary carrying one outcome per configured schedule.

    Raises:
        DuplicateJobError: Two schedules declare the same probe type.
    """
    return run_jobs_once(build_jobs(config, action))
