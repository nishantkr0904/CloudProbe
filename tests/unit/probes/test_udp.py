"""Unit tests for the UDP connectivity probe.

``socket.socket`` is patched, so these tests exercise the real send/receive
sequencing, reply validation and classification with no DNS, no kernel and no
datagram leaving the process — pytest-socket would fail any test that opened a
real socket.

Covers:
    - reply received -> success result
    - optional reply validation: match, mismatch, no expectation
    - each distinguished failure class (timeout, refused, unreachable,
      dns_failure, socket_error)
    - latency is measured on a monotonic clock and reported in milliseconds
    - the configured timeout, payload and address reach the socket layer
    - the socket is always closed, and a target without a port is rejected
"""

from __future__ import annotations

import errno
import socket
from datetime import UTC, datetime
from typing import Any

import pytest

from cloudprobe.config.models import ProbeType, Target
from cloudprobe.probes.base import ProbeErrorClass, ProbeResult
from cloudprobe.probes.udp import UdpProbe


def _target(**overrides: Any) -> Target:
    base: dict[str, Any] = {
        "target_id": "dns-1",
        "host": "10.20.1.10",
        "port": 53,
        "probe_types": [ProbeType.UDP],
    }
    base.update(overrides)
    return Target(**base)


class _FakeSocket:
    """Stands in for a datagram socket.

    Records every interaction, then either returns a reply or raises the
    configured exception from the stage it was configured for.
    """

    def __init__(
        self,
        response: bytes = b"reply",
        connect_error: Exception | None = None,
        send_error: Exception | None = None,
        recv_error: Exception | None = None,
    ) -> None:
        self._response = response
        self._connect_error = connect_error
        self._send_error = send_error
        self._recv_error = recv_error
        self.timeout: float | None = None
        self.address: tuple[str, int] | None = None
        self.sent: bytes | None = None
        self.recv_bufsize: int | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, address: tuple[str, int]) -> None:
        self.address = address
        if self._connect_error is not None:
            raise self._connect_error

    def send(self, payload: bytes) -> int:
        self.sent = payload
        if self._send_error is not None:
            raise self._send_error
        return len(payload)

    def recv(self, bufsize: int) -> bytes:
        self.recv_bufsize = bufsize
        if self._recv_error is not None:
            raise self._recv_error
        return self._response

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True


class _RecordingFactory:
    """Stands in for ``socket.socket``, recording the address family used."""

    def __init__(self, sock: _FakeSocket) -> None:
        self._sock = sock
        self.family: int | None = None
        self.kind: int | None = None

    def __call__(self, family: int, kind: int) -> _FakeSocket:
        self.family = family
        self.kind = kind
        return self._sock


def _patch_socket(monkeypatch: pytest.MonkeyPatch, sock: _FakeSocket) -> _RecordingFactory:
    factory = _RecordingFactory(sock)
    monkeypatch.setattr("cloudprobe.probes.udp.socket.socket", factory)
    return factory


def _os_error(err: int) -> OSError:
    return OSError(err, "boom")


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuccess:
    def test_reply_is_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket())
        result = UdpProbe(timeout_seconds=5).run(_target())
        assert result.success is True
        assert result.error_class is None
        assert result.raw is None

    def test_empty_reply_is_still_a_reply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A zero-length datagram arrived, which is itself proof of reachability.
        _patch_socket(monkeypatch, _FakeSocket(response=b""))
        assert UdpProbe(timeout_seconds=5).run(_target()).success is True

    def test_result_carries_target_and_probe_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket())
        target = _target()
        result = UdpProbe(timeout_seconds=5).run(target)
        assert result.target is target
        assert result.probe_type is ProbeType.UDP

    def test_timestamp_is_timezone_aware_utc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket())
        result = UdpProbe(timeout_seconds=5).run(_target())
        assert isinstance(result.timestamp, datetime)
        assert result.timestamp.tzinfo is UTC


# ---------------------------------------------------------------------------
# Optional reply validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReplyValidation:
    def test_expected_bytes_present_is_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket(response=b"\x12\x34\x81\x80rest"))
        probe = UdpProbe(timeout_seconds=5, expected=b"\x12\x34")
        assert probe.run(_target()).success is True

    def test_expected_bytes_absent_is_protocol_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket(response=b"garbage"))
        result = UdpProbe(timeout_seconds=5, expected=b"\x12\x34").run(_target())
        assert result.success is False
        assert result.error_class is ProbeErrorClass.PROTOCOL_ERROR

    def test_protocol_error_explains_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket(response=b"garbage"))
        result = UdpProbe(timeout_seconds=5, expected=b"nope").run(_target())
        assert result.raw is not None
        assert "7 bytes" in result.raw

    def test_no_expectation_accepts_any_reply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket(response=b"anything at all"))
        result = UdpProbe(timeout_seconds=5).run(_target())
        assert result.success is True
        assert result.error_class is None


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailureClassification:
    def test_no_reply_is_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket(recv_error=TimeoutError("timed out")))
        result = UdpProbe(timeout_seconds=1).run(_target())
        assert result.success is False
        assert result.error_class is ProbeErrorClass.TIMEOUT
        assert result.raw == "timed out"

    def test_port_unreachable_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # ICMP port unreachable surfaces on the connected socket's next call.
        _patch_socket(monkeypatch, _FakeSocket(recv_error=ConnectionRefusedError("refused")))
        result = UdpProbe(timeout_seconds=1).run(_target())
        assert result.error_class is ProbeErrorClass.REFUSED

    def test_refused_on_send_is_also_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket(send_error=ConnectionRefusedError()))
        result = UdpProbe(timeout_seconds=1).run(_target())
        assert result.error_class is ProbeErrorClass.REFUSED

    @pytest.mark.parametrize(
        "err",
        [errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN, errno.ENETDOWN],
    )
    def test_unreachable_errnos_are_classified(
        self, monkeypatch: pytest.MonkeyPatch, err: int
    ) -> None:
        _patch_socket(monkeypatch, _FakeSocket(send_error=_os_error(err)))
        result = UdpProbe(timeout_seconds=1).run(_target())
        assert result.error_class is ProbeErrorClass.UNREACHABLE

    def test_dns_failure_is_classified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        error = socket.gaierror("name or service not known")
        _patch_socket(monkeypatch, _FakeSocket(connect_error=error))
        result = UdpProbe(timeout_seconds=1).run(_target(host="nope.invalid"))
        assert result.error_class is ProbeErrorClass.DNS_FAILURE

    def test_other_os_error_is_socket_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket(send_error=_os_error(errno.EACCES)))
        result = UdpProbe(timeout_seconds=1).run(_target())
        assert result.error_class is ProbeErrorClass.SOCKET_ERROR

    def test_errorless_os_error_is_socket_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # An OSError with no errno must not be mistaken for an unreachable host.
        _patch_socket(monkeypatch, _FakeSocket(send_error=OSError("no errno")))
        result = UdpProbe(timeout_seconds=1).run(_target())
        assert result.error_class is ProbeErrorClass.SOCKET_ERROR

    def test_refused_not_collapsed_into_socket_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # ConnectionRefusedError is an OSError subclass; the specific class wins.
        _patch_socket(monkeypatch, _FakeSocket(recv_error=ConnectionRefusedError()))
        result = UdpProbe(timeout_seconds=1).run(_target())
        assert result.error_class is ProbeErrorClass.REFUSED

    def test_every_failure_still_returns_a_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket(recv_error=OSError()))
        assert isinstance(UdpProbe(timeout_seconds=1).run(_target()), ProbeResult)


# ---------------------------------------------------------------------------
# Latency and socket-layer forwarding
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLatencyAndForwarding:
    def test_latency_is_non_negative_float(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket())
        result = UdpProbe(timeout_seconds=5).run(_target())
        assert isinstance(result.latency_ms, float)
        assert result.latency_ms >= 0.0

    def test_latency_measured_on_failure_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_socket(monkeypatch, _FakeSocket(recv_error=TimeoutError()))
        assert UdpProbe(timeout_seconds=1).run(_target()).latency_ms >= 0.0

    def test_latency_uses_a_monotonic_clock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ticks = iter([1.0, 1.25])
        monkeypatch.setattr("cloudprobe.probes.udp.perf_counter", lambda: next(ticks))
        _patch_socket(monkeypatch, _FakeSocket())
        assert UdpProbe(timeout_seconds=5).run(_target()).latency_ms == 250.0

    def test_timeout_is_forwarded_to_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sock = _FakeSocket()
        _patch_socket(monkeypatch, sock)
        UdpProbe(timeout_seconds=3.5).run(_target())
        assert sock.timeout == 3.5

    def test_host_and_port_forwarded_to_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sock = _FakeSocket()
        _patch_socket(monkeypatch, sock)
        UdpProbe(timeout_seconds=5).run(_target(host="10.0.0.5", port=123))
        assert sock.address == ("10.0.0.5", 123)

    def test_payload_is_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sock = _FakeSocket()
        _patch_socket(monkeypatch, sock)
        UdpProbe(timeout_seconds=5, payload=b"\x1b" + b"\x00" * 47).run(_target())
        assert sock.sent == b"\x1b" + b"\x00" * 47

    def test_default_payload_is_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sock = _FakeSocket()
        _patch_socket(monkeypatch, sock)
        UdpProbe(timeout_seconds=5).run(_target())
        assert sock.sent == b""

    def test_datagram_socket_is_requested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        factory = _patch_socket(monkeypatch, _FakeSocket())
        UdpProbe(timeout_seconds=5).run(_target())
        assert factory.family == socket.AF_INET
        assert factory.kind == socket.SOCK_DGRAM

    def test_receive_buffer_fits_a_full_datagram(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sock = _FakeSocket()
        _patch_socket(monkeypatch, sock)
        UdpProbe(timeout_seconds=5).run(_target())
        assert sock.recv_bufsize is not None
        assert sock.recv_bufsize >= 1500

    def test_socket_is_closed_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sock = _FakeSocket()
        _patch_socket(monkeypatch, sock)
        UdpProbe(timeout_seconds=5).run(_target())
        assert sock.closed is True

    def test_socket_is_closed_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sock = _FakeSocket(recv_error=TimeoutError())
        _patch_socket(monkeypatch, sock)
        UdpProbe(timeout_seconds=1).run(_target())
        assert sock.closed is True


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPortGuard:
    def test_missing_port_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sock = _FakeSocket()
        _patch_socket(monkeypatch, sock)
        with pytest.raises(ValueError, match="port"):
            UdpProbe(timeout_seconds=5).run(_target(port=None))
        assert sock.address is None
