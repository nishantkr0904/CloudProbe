"""Command-line adapter — the documented ``python -m cloudprobe`` interface.

This module is intentionally thin.  It owns no business logic; it is the seam
between a command line (and, through it, the Docker entrypoint) and the public
APIs the pipeline layers already expose:

* configuration loading (:func:`cloudprobe.config.load`),
* one-shot execution (:func:`cloudprobe.scheduler.run_once`),
* long-running scheduling (:class:`cloudprobe.scheduler.CronScheduler`),
* the probe transports (:mod:`cloudprobe.probes`).

The commands here are exactly those the architecture (§4, §11.2) and
project-structure (§10) require the container to invoke — ``run`` with
``--once``/``--scheduler``, ``healthcheck``, ``config validate`` — and no more.
``pyproject.toml`` already points ``[project.scripts]`` at ``cloudprobe.cli:main``;
this fills in the entry point that contract promised.

The probe *action* the scheduler drives is supplied here rather than imported
from a pipeline runner, because the full probe→metrics→alerting→reporting fan-out
(architecture §4 steps 4-8) is a later roadmap phase.  Wiring the real probe
transports against the configured static inventory is the honest minimum that
makes one-shot and scheduler modes execute a genuine probe cycle today, using
only already-public APIs and injecting nothing this layer should not own.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Sequence

from cloudprobe import __version__
from cloudprobe.config import CloudProbeConfig, ConfigError, ProbeType, Target, load
from cloudprobe.probes import IcmpProbe, Probe, ProbeResult, TcpProbe, UdpProbe
from cloudprobe.scheduler import CronScheduler, JobAction, run_once

_LOG = logging.getLogger("cloudprobe")


def _targets_for(config: CloudProbeConfig, probe_type: ProbeType) -> list[Target]:
    """The configured targets that declare ``probe_type``."""
    return [target for target in config.targets if probe_type in target.probe_types]


def _build_action(config: CloudProbeConfig) -> JobAction:
    """A ``JobAction`` that runs one probe type against its static targets.

    The transports carry the configured default timeout; a probe type with no
    transport yet (http, ssh) is skipped rather than crashing the cycle.  A
    probe that raises is swallowed per-target: architecture §4 step 9 makes a
    failed target a *result*, not a failed run, so a cycle completes regardless.
    """
    timeout = float(config.probe.default_timeout_seconds)
    probes: dict[ProbeType, Probe] = {
        ProbeType.TCP: TcpProbe(timeout),
        ProbeType.ICMP: IcmpProbe(timeout),
        ProbeType.UDP: UdpProbe(timeout),
    }

    def action(probe_type: ProbeType) -> None:
        probe = probes.get(probe_type)
        if probe is None:
            _LOG.debug("no transport for probe type %s; skipping", probe_type.value)
            return
        for target in _targets_for(config, probe_type):
            try:
                result: ProbeResult = probe.run(target)
            except ValueError as exc:
                # The only failure a probe raises rather than records: a target
                # that declares this probe type without the port it requires.
                _LOG.warning("%s %s: %s", probe_type.value, target.target_id, exc)
                continue
            _log_result(result)

    return action


def _log_result(result: ProbeResult) -> None:
    """Emit one line per probe outcome.

    Until the metrics and reporting fan-out is wired (architecture §4 steps
    6-8), the log is the run's only observable output — and ROADMAP principle 5
    requires the observer to be observable.
    """
    if result.success:
        _LOG.info(
            "%s %s ok in %.1fms",
            result.probe_type.value,
            result.target.target_id,
            result.latency_ms,
        )
    else:
        _LOG.warning(
            "%s %s failed (%s)",
            result.probe_type.value,
            result.target.target_id,
            result.error_class.value if result.error_class else "unknown",
        )


def _cmd_run(args: argparse.Namespace) -> int:
    config = load(args.config)
    if args.scheduler:
        return _run_scheduler(config)
    return _run_once(config)


def _run_once(config: CloudProbeConfig) -> int:
    """Execute exactly one pass over every scheduled probe type, then exit."""
    summary = run_once(config, _build_action(config))
    # A clean pass is exit 0 regardless of individual probe outcomes
    # (architecture §4 step 9).
    _ = summary
    return 0


def _run_scheduler(config: CloudProbeConfig) -> int:
    """Run cron-scheduled probes until a termination signal arrives.

    Blocks on an event set by SIGTERM/SIGINT so the Docker entrypoint's
    forwarded signal (``docker stop``) triggers a clean scheduler shutdown.
    """
    scheduler = CronScheduler.from_config(config, _build_action(config))
    stop = threading.Event()

    def _handle(_signum: int, _frame: object) -> None:
        stop.set()

    previous = {sig: signal.signal(sig, _handle) for sig in (signal.SIGTERM, signal.SIGINT)}
    scheduler.start()
    try:
        stop.wait()
    finally:
        scheduler.shutdown(wait=True)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


def _cmd_healthcheck(args: argparse.Namespace) -> int:
    """Validate that the configuration loads; the container-health contract.

    Architecture §11.2 defines ``healthcheck`` as validating config and, when
    credentials are present, pinging AWS.  Config validation is the credential-
    free half and the part that must pass for the container to be considered
    healthy in the offline local/CI path; the AWS ping is deferred with the
    discovery/metrics wiring it depends on.
    """
    load(args.config)
    return 0


def _cmd_config_validate(args: argparse.Namespace) -> int:
    config = load(args.config)
    sys.stdout.write(f"OK: {len(config.targets)} target(s) validated\n")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cloudprobe",
        description="Hybrid cloud network observability and QA pipeline.",
    )
    parser.add_argument("--version", action="version", version=f"cloudprobe {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the probe pipeline.")
    run.add_argument("--config", required=True, help="Path to a config file or directory.")
    mode = run.add_mutually_exclusive_group()
    mode.add_argument(
        "--once",
        dest="scheduler",
        action="store_false",
        help="Run one pass and exit (default).",
    )
    mode.add_argument(
        "--scheduler",
        dest="scheduler",
        action="store_true",
        help="Run on the configured cron cadence until signalled.",
    )
    run.set_defaults(scheduler=False, func=_cmd_run)

    health = sub.add_parser("healthcheck", help="Validate config (and AWS reachability).")
    health.add_argument("--config", required=True, help="Path to a config file or directory.")
    health.set_defaults(func=_cmd_healthcheck)

    config_parser = sub.add_parser("config", help="Configuration utilities.")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    validate = config_sub.add_parser("validate", help="Validate a configuration.")
    validate.add_argument("config", help="Path to a config file or directory.")
    validate.set_defaults(func=_cmd_config_validate)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch to the selected command.

    Returns a process exit code: 0 on a clean run, 2 on a configuration error.
    Configuration failures are the one class the architecture (§4 step 9)
    reserves a non-zero exit for, so they are caught here and reported without
    a traceback.
    """
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py
    raise SystemExit(main())
