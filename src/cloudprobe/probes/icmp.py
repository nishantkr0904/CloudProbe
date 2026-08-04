"""ICMP reachability probe.

One responsibility: send ICMP echo requests to a target and report whether a
reply came back, as a ``ProbeResult``.

Executed by invoking the operating system's ``ping`` through ``subprocess``
rather than opening a raw socket, because raw sockets require elevated
privilege on Linux and macOS.  Portability is bought at the cost of parsing
human-readable output, so the parsing is kept narrow: the round-trip time and
the failure markers, nothing else.

An ICMP failure is *not* evidence that a host is down — many cloud security
groups drop ICMP by default.  It is evidence about ICMP reachability only.
This probe therefore reports what it observed and classifies why; the judgment
belongs to layers above it.

Standard library only, synchronous, one execution per call: retry, scheduling,
metrics and reporting live elsewhere.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import UTC, datetime

from cloudprobe.config.models import ProbeType, Target
from cloudprobe.probes.base import ProbeErrorClass, ProbeResult

# Per-reply round-trip time, e.g. "time=12.3 ms" (Linux) or "time=12.345 ms"
# (macOS).  Both platforms use this form for individual replies.
_RTT_PATTERN = re.compile(r"time[=<]\s*(\d+(?:\.\d+)?)\s*ms", re.IGNORECASE)

# Markers that identify *why* ping failed.  Wording differs across platforms,
# so each classification matches any of several substrings.
_DNS_MARKERS = (
    "name or service not known",
    "unknown host",
    "cannot resolve",
    "name resolution",
    "temporary failure in name resolution",
    "no address associated with hostname",
)
_UNREACHABLE_MARKERS = (
    "unreachable",
    "no route to host",
)

# Grace added to the subprocess deadline so ping reaches its own per-reply
# timeout first.  Ping's diagnostic text is what makes a failure classifiable,
# and killing the process would discard it.
_SUBPROCESS_GRACE_SECONDS = 0.5


class IcmpProbe:
    """Probe a target's ICMP reachability with a configurable timeout.

    ``count`` echo requests are sent per execution.  It defaults to 1 because
    sending several requests and accepting any reply is a retry, and this probe
    performs exactly one attempt; operators who want packet-loss measurement
    can raise it deliberately.
    """

    def __init__(self, timeout_seconds: float, count: int = 1) -> None:
        self._timeout_seconds = timeout_seconds
        self._count = count

    def run(self, target: Target) -> ProbeResult:
        """Send echo requests to ``target`` and return the outcome.

        No port is required: ICMP has no port concept.  Reported latency is the
        round-trip time parsed from ping's output, so it is ``0.0`` on failure
        — no round trip completed, and a wall-clock duration would be a
        different measurement wearing the same name.
        """
        command = self._build_command(target.host)
        try:
            # S603: argument list, never a shell; host comes from validated config.
            completed = subprocess.run(  # noqa: S603
                command,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds + _SUBPROCESS_GRACE_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self._result(target, error=ProbeErrorClass.TIMEOUT, raw="ping timed out")
        except OSError as exc:
            # ping absent or not executable — the transport never ran.
            return self._result(target, error=ProbeErrorClass.COMMAND_ERROR, raw=str(exc))

        output = f"{completed.stdout}\n{completed.stderr}"
        if completed.returncode == 0:
            return self._success(target, output)
        return self._result(target, error=self._classify(output), raw=output.strip() or None)

    def _build_command(self, host: str) -> list[str]:
        """Build the ping argument list for the running platform.

        Passed as a list and never through a shell, so a hostname can never be
        interpreted as a command.  ``-W`` is per-reply timeout: milliseconds on
        macOS, whole seconds on Linux.
        """
        if sys.platform == "darwin":
            per_reply = str(int(self._timeout_seconds * 1000))
        else:
            per_reply = str(max(1, int(self._timeout_seconds)))
        return ["ping", "-c", str(self._count), "-W", per_reply, host]

    def _success(self, target: Target, output: str) -> ProbeResult:
        """Build a success result, or a command error if no RTT was reported.

        Exit status 0 means a reply arrived, but this probe's contract includes
        reporting the round-trip time.  Output that cannot yield one is a
        contract this probe cannot satisfy, not a silent zero.
        """
        latency_ms = _parse_rtt_ms(output)
        if latency_ms is None:
            return self._result(
                target,
                error=ProbeErrorClass.COMMAND_ERROR,
                raw="ping reported success but no round-trip time was found",
            )
        return self._result(target, success=True, latency_ms=latency_ms)

    def _classify(self, output: str) -> ProbeErrorClass:
        """Map ping's diagnostic text to a failure class.

        DNS is checked before unreachability because a resolution failure is
        the more specific finding.  Absent any marker, ping exited non-zero
        having heard nothing back — a silent drop, which is a timeout.
        """
        haystack = output.lower()
        if any(marker in haystack for marker in _DNS_MARKERS):
            return ProbeErrorClass.DNS_FAILURE
        if any(marker in haystack for marker in _UNREACHABLE_MARKERS):
            return ProbeErrorClass.UNREACHABLE
        return ProbeErrorClass.TIMEOUT

    def _result(
        self,
        target: Target,
        *,
        success: bool = False,
        latency_ms: float = 0.0,
        error: ProbeErrorClass | None = None,
        raw: str | None = None,
    ) -> ProbeResult:
        return ProbeResult(
            target=target,
            probe_type=ProbeType.ICMP,
            success=success,
            latency_ms=latency_ms,
            timestamp=datetime.now(UTC),
            error_class=error,
            raw=raw,
        )


def _parse_rtt_ms(output: str) -> float | None:
    """Return the mean round-trip time in ms across replies, or ``None``.

    Averaging the per-reply times keeps one code path for any ``count``: with a
    single request the mean is that request's time.
    """
    times = [float(match) for match in _RTT_PATTERN.findall(output)]
    if not times:
        return None
    return sum(times) / len(times)
