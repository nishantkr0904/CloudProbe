"""Integration: configuration load → probe execution.

Exercises the boundary between the config loader and the probe engine
(architecture §10.2 "Probes → pytest-socket, Paramiko fake").  The probe's
timeout comes from the loaded ``ProbeConfig`` and the target from the loaded
inventory, so this verifies that a YAML document on disk drives a real
``TcpProbe`` call.

``TcpProbe`` reaches the network through exactly one seam —
``socket.create_connection`` — which is monkeypatched here.  Nothing is
dialled: no host is contacted, no DNS is resolved and no real socket is
opened, while the probe's own success and failure classification runs
unmodified.
"""

from __future__ import annotations

import socket

import pytest

from cloudprobe.config import load
from cloudprobe.probes import ProbeErrorClass, TcpProbe

_CONFIG = """\
targets:
  - target_id: web-1
    host: 10.20.1.10
    port: 443
    probe_types: [tcp]
thresholds:
  - probe_type: tcp
    warn_above_ms: 200
schedules:
  - probe_type: tcp
    cron_expression: "*/5 * * * *"
probe:
  default_timeout_seconds: 3
"""


class _FakeConnection:
    """A stand-in for the socket ``create_connection`` returns.

    Only the context-manager protocol is used by the probe, so only that is
    implemented; nothing is bound, connected or sent.
    """

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


@pytest.fixture
def config(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(_CONFIG, encoding="utf-8")
    return load(str(config_file))


@pytest.mark.integration
class TestConfigToProbe:
    def test_a_reachable_target_yields_a_successful_result(self, config, monkeypatch) -> None:
        dialled: list[tuple[tuple[str, int], float]] = []

        def fake_create_connection(address, timeout=None, **kwargs):
            dialled.append((address, timeout))
            return _FakeConnection()

        monkeypatch.setattr(socket, "create_connection", fake_create_connection)

        probe = TcpProbe(config.probe.default_timeout_seconds)
        result = probe.run(config.targets[0])

        assert result.success is True
        assert result.error_class is None
        # The address and timeout came from the loaded configuration.
        assert dialled == [(("10.20.1.10", 443), 3)]

    def test_a_refused_target_is_classified_as_refused(self, config, monkeypatch) -> None:
        def refuse(address, timeout=None, **kwargs):
            raise ConnectionRefusedError("connection refused")

        monkeypatch.setattr(socket, "create_connection", refuse)

        result = TcpProbe(config.probe.default_timeout_seconds).run(config.targets[0])

        assert result.success is False
        assert result.error_class is ProbeErrorClass.REFUSED

    def test_a_timing_out_target_is_classified_as_timeout(self, config, monkeypatch) -> None:
        def time_out(address, timeout=None, **kwargs):
            raise TimeoutError("timed out")

        monkeypatch.setattr(socket, "create_connection", time_out)

        result = TcpProbe(config.probe.default_timeout_seconds).run(config.targets[0])

        assert result.success is False
        assert result.error_class is ProbeErrorClass.TIMEOUT
