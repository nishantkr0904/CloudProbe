"""TCP connectivity probe.

One responsibility: attempt a TCP three-way handshake to a target's
``host:port`` within a timeout and report the outcome as a ``ProbeResult``.

Success is a completed handshake; the recorded latency is the connect-time
delta.  Failures are classified by the wire-level cause — a refusal, a
timeout, a DNS failure and a generic socket error are four distinct outcomes,
never collapsed into one.  No failure escapes as an exception: the probe
reports facts, and a failed probe is one of those facts.

Standard library only, synchronous, single attempt: retry, scheduling, metrics
and reporting live in other layers.
"""

from __future__ import annotations

import socket
from datetime import datetime, timezone
from time import perf_counter

from cloudprobe.config.models import ProbeType, Target
from cloudprobe.probes.base import ProbeErrorClass, ProbeResult


class TcpProbe:
    """Probe a target's TCP port with a configurable connect timeout.

    The timeout is held on the instance so the probe satisfies the engine's
    ``run(target) -> ProbeResult`` interface without per-call parameters.
    """

    def __init__(self, timeout_seconds: float) -> None:
        self._timeout_seconds = timeout_seconds

    def run(self, target: Target) -> ProbeResult:
        """Attempt a TCP handshake to ``target`` and return the outcome.

        The target must carry a ``port``; TCP has no meaning without one.  The
        connection is closed immediately once established — the probe measures
        reachability, not a session.
        """
        if target.port is None:
            raise ValueError("TCP probe requires target.port to be set")

        start = perf_counter()
        try:
            with socket.create_connection(
                (target.host, target.port), timeout=self._timeout_seconds
            ):
                return self._result(target, start, success=True)
        except socket.gaierror as exc:
            return self._result(target, start, error=ProbeErrorClass.DNS_FAILURE, exc=exc)
        except TimeoutError as exc:
            return self._result(target, start, error=ProbeErrorClass.TIMEOUT, exc=exc)
        except ConnectionRefusedError as exc:
            return self._result(target, start, error=ProbeErrorClass.REFUSED, exc=exc)
        except OSError as exc:
            return self._result(target, start, error=ProbeErrorClass.SOCKET_ERROR, exc=exc)

    def _result(
        self,
        target: Target,
        start: float,
        *,
        success: bool = False,
        error: ProbeErrorClass | None = None,
        exc: Exception | None = None,
    ) -> ProbeResult:
        return ProbeResult(
            target=target,
            probe_type=ProbeType.TCP,
            success=success,
            latency_ms=(perf_counter() - start) * 1000.0,
            timestamp=datetime.now(timezone.utc),
            error_class=error,
            raw=str(exc) if exc is not None else None,
        )
