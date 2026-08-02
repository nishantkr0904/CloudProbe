"""UDP connectivity probe.

One responsibility: send a UDP datagram to a target's ``host:port``, wait for a
reply within a timeout, and report the outcome as a ``ProbeResult``.

UDP is connectionless, so there is no handshake to observe: the only evidence
that a service is answering is a datagram coming back.  The socket is therefore
*connected* before sending — not to establish a session, but because a
connected datagram socket is what makes the kernel surface an ICMP port- or
host-unreachable as an error on the next call instead of discarding it.  That
is what allows a refusal and an unreachable network to be distinguished from
silence.

Validation stays deliberately protocol-agnostic: an optional ``expected``
byte string must appear in the reply.  This is enough to tell a real DNS or NTP
responder from a black hole without teaching this module either wire format —
protocol decoding belongs to a probe that owns that protocol, not here.

Standard library only, synchronous, single attempt: retry, scheduling, metrics
and reporting live in other layers.
"""

from __future__ import annotations

import errno
import socket
from datetime import datetime, timezone
from time import perf_counter

from cloudprobe.config.models import ProbeType, Target
from cloudprobe.probes.base import ProbeErrorClass, ProbeResult

# Upper bound on a single received datagram.  Larger than the 512-byte classic
# DNS limit and the 1500-byte Ethernet MTU, so a legitimate reply is never
# truncated by this probe's own buffer.
_MAX_RESPONSE_BYTES = 4096

# Errnos that mean the datagram could not reach the destination, as opposed to
# reaching it and being refused.
_UNREACHABLE_ERRNOS = frozenset(
    {
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.EHOSTDOWN,
        errno.ENETDOWN,
    }
)


class UdpProbe:
    """Probe a target's UDP port with a configurable timeout.

    ``payload`` is the datagram sent; it defaults to empty because a zero-length
    datagram is sufficient to elicit an ICMP port-unreachable from a closed
    port.  ``expected`` opts into reply validation: when set, the reply must
    contain those bytes to count as a success.
    """

    def __init__(
        self,
        timeout_seconds: float,
        payload: bytes = b"",
        expected: bytes | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._payload = payload
        self._expected = expected

    def run(self, target: Target) -> ProbeResult:
        """Send one datagram to ``target`` and return the outcome.

        The target must carry a ``port``; UDP has no meaning without one.
        Latency is the monotonic-clock delta spanning send and reply, so it is
        unaffected by wall-clock adjustments.
        """
        if target.port is None:
            raise ValueError("UDP probe requires target.port to be set")

        start = perf_counter()
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(self._timeout_seconds)
                sock.connect((target.host, target.port))
                sock.send(self._payload)
                response = sock.recv(_MAX_RESPONSE_BYTES)
        except socket.gaierror as exc:
            return self._result(target, start, error=ProbeErrorClass.DNS_FAILURE, exc=exc)
        except TimeoutError as exc:
            # No reply and no ICMP error: the datagram vanished, or the service
            # is silent.  Either way this probe observed nothing come back.
            return self._result(target, start, error=ProbeErrorClass.TIMEOUT, exc=exc)
        except ConnectionRefusedError as exc:
            # ICMP port unreachable: the host answered, the port is closed.
            return self._result(target, start, error=ProbeErrorClass.REFUSED, exc=exc)
        except OSError as exc:
            error = (
                ProbeErrorClass.UNREACHABLE
                if exc.errno in _UNREACHABLE_ERRNOS
                else ProbeErrorClass.SOCKET_ERROR
            )
            return self._result(target, start, error=error, exc=exc)

        return self._validate(target, start, response)

    def _validate(self, target: Target, start: float, response: bytes) -> ProbeResult:
        """Judge a received datagram against the configured expectation.

        With no expectation, any reply is the success signal — the datagram
        arriving is itself the proof of reachability.  With one, a reply that
        does not contain the expected bytes is a ``protocol_error``: the host
        is reachable but is not the service being checked.
        """
        if self._expected is not None and self._expected not in response:
            return self._result(
                target,
                start,
                error=ProbeErrorClass.PROTOCOL_ERROR,
                raw=f"reply of {len(response)} bytes did not contain the expected bytes",
            )
        return self._result(target, start, success=True)

    def _result(
        self,
        target: Target,
        start: float,
        *,
        success: bool = False,
        error: ProbeErrorClass | None = None,
        exc: Exception | None = None,
        raw: str | None = None,
    ) -> ProbeResult:
        if raw is None and exc is not None:
            raw = str(exc)
        return ProbeResult(
            target=target,
            probe_type=ProbeType.UDP,
            success=success,
            latency_ms=(perf_counter() - start) * 1000.0,
            timestamp=datetime.now(timezone.utc),
            error_class=error,
            raw=raw,
        )
