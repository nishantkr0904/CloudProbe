"""Cron scheduling — the APScheduler-backed long-running mode.

This is the recurring half of the pipeline driver (project-structure §6.9,
architecture §7.4 "Long-running"): it registers one cron job per probe type
from ``configs/schedules.yaml`` and owns the scheduler's start and shutdown.

It is the only module in the package that imports APScheduler.  One-shot mode
deliberately does not, so host ``cron`` and Kubernetes CronJob deployments
never load a scheduler library they have no use for.

The scheduler instance is *injected* (defaulting to a real
``BackgroundScheduler``) so this class can be unit-tested against a fake that
starts no threads and consults no clock.

This module registers and triggers work.  It executes no probes, publishes no
metrics, evaluates no alerts and writes no files: every job's behaviour is the
callable the caller injected.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from cloudprobe.config.models import CloudProbeConfig
from cloudprobe.scheduler.jobs import JobAction, ProbeJob, SchedulerError, build_jobs


class InvalidCronExpressionError(SchedulerError):
    """A cron expression APScheduler cannot interpret.

    The configuration layer checks only that the expression has five fields;
    its meaning is validated here, when the scheduler starts.
    """


class SchedulerAlreadyStartedError(SchedulerError):
    """Start was requested on a scheduler that is already running."""


class SchedulerNotStartedError(SchedulerError):
    """Shutdown was requested on a scheduler that was never started."""


class Scheduler(Protocol):
    """The subset of an APScheduler scheduler this module uses.

    Declared structurally so the layer depends on APScheduler's interface
    rather than a concrete class.  A real ``BackgroundScheduler`` satisfies it.
    """

    def add_job(self, func: Callable[[], None], trigger: Any, **kwargs: Any) -> Any:
        ...  # pragma: no cover - structural type declaration

    def start(self) -> None:
        ...  # pragma: no cover - structural type declaration

    def shutdown(self, wait: bool = True) -> None:
        ...  # pragma: no cover - structural type declaration


def build_trigger(cron_expression: str) -> CronTrigger:
    """Translate a 5-field cron expression into an APScheduler trigger.

    Args:
        cron_expression: A standard ``minute hour dom month dow`` expression.

    Returns:
        The equivalent ``CronTrigger``.

    Raises:
        InvalidCronExpressionError: APScheduler rejected the expression.
    """
    try:
        return CronTrigger.from_crontab(cron_expression)
    except ValueError as exc:
        raise InvalidCronExpressionError(
            f"cron expression {cron_expression!r} is not valid: {exc}"
        ) from exc


class CronScheduler:
    """Runs probe jobs on their declared cadences until shut down."""

    def __init__(
        self,
        jobs: list[ProbeJob],
        scheduler: Scheduler | None = None,
    ) -> None:
        self._jobs = jobs
        self._scheduler: Scheduler = scheduler if scheduler is not None else BackgroundScheduler()
        self._started = False

    @classmethod
    def from_config(
        cls,
        config: CloudProbeConfig,
        action: JobAction,
        scheduler: Scheduler | None = None,
    ) -> "CronScheduler":
        """Build a scheduler for every schedule declared in ``config``."""
        return cls(build_jobs(config, action), scheduler)

    @property
    def started(self) -> bool:
        """Whether this scheduler is currently running."""
        return self._started

    def register(self) -> None:
        """Register every job with the underlying scheduler.

        Raises:
            InvalidCronExpressionError: A job's cron expression is invalid.  No
                job is registered in that case, because translation of all
                expressions completes before the first registration.
        """
        triggers = [(job, build_trigger(job.cron_expression)) for job in self._jobs]
        for job, trigger in triggers:
            self._scheduler.add_job(
                job.run,
                trigger,
                id=job.job_id,
                name=job.job_id,
                # An overrun cadence must not stack runs beyond the concurrency
                # the operator declared for this probe type.
                max_instances=job.max_concurrency,
                # Runs missed while the process was busy collapse into one.
                coalesce=True,
            )

    def start(self) -> None:
        """Register the jobs and start the scheduler.

        Raises:
            SchedulerAlreadyStartedError: The scheduler is already running.
            InvalidCronExpressionError: A job's cron expression is invalid.
        """
        if self._started:
            raise SchedulerAlreadyStartedError("scheduler is already started")
        self.register()
        self._scheduler.start()
        self._started = True

    def shutdown(self, wait: bool = True) -> None:
        """Stop the scheduler.

        Args:
            wait: Whether to let in-flight jobs finish first.

        Raises:
            SchedulerNotStartedError: The scheduler was never started.
        """
        if not self._started:
            raise SchedulerNotStartedError("scheduler was not started")
        self._scheduler.shutdown(wait=wait)
        self._started = False
