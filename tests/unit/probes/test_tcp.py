"""Unit tests for the TCP connectivity probe.

The socket layer is patched, so these tests exercise the real classification
code path with no DNS, no kernel and no network — pytest-socket would fail any
test that opened a real connection.

Covers:
    - successful handshake -> success result
    - each distinguished failure class (timeout, refused, dns_failure, socket_error)
    - latency is measured and reported in milliseconds
    - the configured timeout is forwarded to the socket layer
    - a target without a port is rejected before any connection attempt
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from typing import Any

import pytest

from cloudprobe.config.models import ProbeType, Target
from cloudprobe.probes.base import ProbeErrorClass, ProbeResult
from cloudprobe.probes.tcp import TcpProbe


def _target(**overrides: Any) -> Target:
    base: dict[str, Any] = {
        "target_id": "web-1",
        "host": "10.20.1.10",
        "port": 443,
        "probe_types": [ProbeType.TCP],
    }
    base.update(overrides)
    return Target(**base)


class _RecordingConnect:
    """Stands in for ``socket.create_connection``.

    Records the arguments it was called with, then either returns a context
    manager (success) or raises the configured exception (failure).
    """

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.address: tuple[str, int] | None = None
        self.timeout: float | None = None

    def __call__(self, address: tuple[str, int], timeout: float) -> Any:
        self.address = address
        self.timeout = timeout
        if self._error is not None:
            raise self._error
        return _FakeSocket()


class _FakeSocket:
    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _patch_connect(monkeypatch: pytest.MonkeyPatch, connect: _RecordingConnect) -> None:
    monkeypatch.setattr("cloudprobe.probes.tcp.socket.create_connection", connect)


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuccess:
    def test_completed_handshake_is_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connect(monkeypatch, _RecordingConnect())
        result = TcpProbe(timeout_seconds=5).run(_target())
        assert result.success is True
        assert result.error_class is None
        assert result.raw is None

    def test_result_carries_target_and_probe_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connect(monkeypatch, _RecordingConnect())
        target = _target()
        result = TcpProbe(timeout_seconds=5).run(target)
        assert result.target is target
        assert result.probe_type is ProbeType.TCP

    def test_timestamp_is_timezone_aware_utc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connect(monkeypatch, _RecordingConnect())
        result = TcpProbe(timeout_seconds=5).run(_target())
        assert isinstance(result.timestamp, datetime)
        assert result.timestamp.tzinfo is UTC


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailureClassification:
    def test_timeout_is_classified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connect(monkeypatch, _RecordingConnect(TimeoutError("timed out")))
        result = TcpProbe(timeout_seconds=1).run(_target())
        assert result.success is False
        assert result.error_class is ProbeErrorClass.TIMEOUT
        assert result.raw == "timed out"

    def test_connection_refused_is_classified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connect(monkeypatch, _RecordingConnect(ConnectionRefusedError("refused")))
        result = TcpProbe(timeout_seconds=1).run(_target())
        assert result.success is False
        assert result.error_class is ProbeErrorClass.REFUSED

    def test_dns_failure_is_classified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connect(monkeypatch, _RecordingConnect(socket.gaierror("name resolution")))
        result = TcpProbe(timeout_seconds=1).run(_target(host="nope.invalid"))
        assert result.success is False
        assert result.error_class is ProbeErrorClass.DNS_FAILURE

    def test_generic_socket_error_is_classified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connect(monkeypatch, _RecordingConnect(OSError("network unreachable")))
        result = TcpProbe(timeout_seconds=1).run(_target())
        assert result.success is False
        assert result.error_class is ProbeErrorClass.SOCKET_ERROR

    def test_refused_not_collapsed_into_socket_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # ConnectionRefusedError is an OSError subclass; the specific class must
        # win over the generic catch-all.
        _patch_connect(monkeypatch, _RecordingConnect(ConnectionRefusedError()))
        result = TcpProbe(timeout_seconds=1).run(_target())
        assert result.error_class is ProbeErrorClass.REFUSED

    def test_every_failure_still_returns_a_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connect(monkeypatch, _RecordingConnect(OSError()))
        result = TcpProbe(timeout_seconds=1).run(_target())
        assert isinstance(result, ProbeResult)


# ---------------------------------------------------------------------------
# Latency and timeout forwarding
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLatencyAndTimeout:
    def test_latency_is_non_negative_float(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connect(monkeypatch, _RecordingConnect())
        result = TcpProbe(timeout_seconds=5).run(_target())
        assert isinstance(result.latency_ms, float)
        assert result.latency_ms >= 0.0

    def test_latency_measured_on_failure_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_connect(monkeypatch, _RecordingConnect(TimeoutError()))
        result = TcpProbe(timeout_seconds=1).run(_target())
        assert result.latency_ms >= 0.0

    def test_timeout_is_forwarded_to_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        connect = _RecordingConnect()
        _patch_connect(monkeypatch, connect)
        TcpProbe(timeout_seconds=3.5).run(_target())
        assert connect.timeout == 3.5

    def test_host_and_port_forwarded_to_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        connect = _RecordingConnect()
        _patch_connect(monkeypatch, connect)
        TcpProbe(timeout_seconds=5).run(_target(host="10.0.0.5", port=22))
        assert connect.address == ("10.0.0.5", 22)


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPortGuard:
    def test_missing_port_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        connect = _RecordingConnect()
        _patch_connect(monkeypatch, connect)
        with pytest.raises(ValueError, match="port"):
            TcpProbe(timeout_seconds=5).run(_target(port=None))
        assert connect.address is None
