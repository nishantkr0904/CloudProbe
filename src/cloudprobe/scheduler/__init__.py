"""CloudProbe scheduler layer — public surface.

This package is the pipeline driver (project-structure §6.9, architecture
§7.4).  It does not add a layer to the stack: it re-enters it, invoking a
caller-supplied action on the cadences declared in ``configs/schedules.yaml``
(configuration §5), or exactly once for ``--once`` and Docker ``oneshot`` mode.

The CLI and the Docker entrypoint import the job definitions, the cron
scheduler, the one-shot entry point and the error hierarchy from here — never
from the internal submodules directly.

This layer orchestrates; it does not perform.  It executes no probes, calls no
AWS or SSH client, publishes no metrics, evaluates no alerts, renders no
reports and writes no files.  Every unit of work is the injected callable.  The
pipeline that callable will eventually run is a later commit.
"""

from cloudprobe.scheduler.cron import (
    CronScheduler,
    InvalidCronExpressionError,
    Scheduler,
    SchedulerAlreadyStartedError,
    SchedulerNotStartedError,
    build_trigger,
)
from cloudprobe.scheduler.jobs import (
    DuplicateJobError,
    JobAction,
    ProbeJob,
    SchedulerError,
    build_jobs,
)
from cloudprobe.scheduler.oneshot import (
    JobOutcome,
    RunSummary,
    run_jobs_once,
    run_once,
)

__all__ = [
    "CronScheduler",
    "DuplicateJobError",
    "InvalidCronExpressionError",
    "JobAction",
    "JobOutcome",
    "ProbeJob",
    "RunSummary",
    "Scheduler",
    "SchedulerAlreadyStartedError",
    "SchedulerError",
    "SchedulerNotStartedError",
    "build_jobs",
    "build_trigger",
    "run_jobs_once",
    "run_once",
]
