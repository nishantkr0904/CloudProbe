"""Unit tests for the ICMP reachability probe.

``subprocess.run`` is patched, so these tests exercise the real command
construction, output parsing and classification with no process spawned and no
packet sent.

Covers:
    - successful reply -> success result with parsed RTT
    - timeout, destination unreachable, DNS failure, command execution failure
    - latency extraction from ping output
    - the configured timeout reaching both ping's flags and the subprocess
    - the command being a list, never a shell string
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from typing import Any

import pytest

from cloudprobe.config.models import ProbeType, Target
from cloudprobe.probes.base import ProbeErrorClass, ProbeResult
from cloudprobe.probes.icmp import IcmpProbe

_LINUX_SUCCESS = """PING 10.20.1.10 (10.20.1.10) 56(84) bytes of data.
64 bytes from 10.20.1.10: icmp_seq=1 ttl=64 time=12.4 ms

--- 10.20.1.10 ping statistics ---
1 packets transmitted, 1 received, 0% packet loss, time 0ms
rtt min/avg/max/mdev = 12.400/12.400/12.400/0.000 ms
"""

_SILENT_DROP = """PING 10.20.1.10 (10.20.1.10) 56(84) bytes of data.

--- 10.20.1.10 ping statistics ---
1 packets transmitted, 0 received, 100% packet loss, time 1001ms
"""

_UNREACHABLE = """PING 10.20.1.10 (10.20.1.10) 56(84) bytes of data.
From 10.20.1.1 icmp_seq=1 Destination Host Unreachable

--- 10.20.1.10 ping statistics ---
1 packets transmitted, 0 received, +1 errors, 100% packet loss
"""

_DNS_FAILURE = "ping: nope.invalid: Name or service not known\n"


def _target(**overrides: Any) -> Target:
    base: dict[str, Any] = {
        "target_id": "web-1",
        "host": "10.20.1.10",
        "probe_types": [ProbeType.ICMP],
    }
    base.update(overrides)
    return Target(**base)


class _RecordingRun:
    """Stands in for ``subprocess.run``.

    Records how it was invoked, then returns a completed process or raises the
    configured exception.
    """

    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        error: Exception | None = None,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode
        self._error = error
        self.command: list[str] | None = None
        self.kwargs: dict[str, Any] = {}

    def __call__(self, command: list[str], **kwargs: Any) -> Any:
        self.command = command
        self.kwargs = kwargs
        if self._error is not None:
            raise self._error
        return subprocess.CompletedProcess(
            args=command, returncode=self._returncode, stdout=self._stdout, stderr=self._stderr
        )


def _patch_run(monkeypatch: pytest.MonkeyPatch, run: _RecordingRun) -> None:
    monkeypatch.setattr("cloudprobe.probes.icmp.subprocess.run", run)


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuccess:
    def test_reply_is_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, _RecordingRun(stdout=_LINUX_SUCCESS))
        result = IcmpProbe(timeout_seconds=5).run(_target())
        assert result.success is True
        assert result.error_class is None

    def test_result_carries_target_and_probe_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_run(monkeypatch, _RecordingRun(stdout=_LINUX_SUCCESS))
        target = _target()
        result = IcmpProbe(timeout_seconds=5).run(target)
        assert result.target is target
        assert result.probe_type is ProbeType.ICMP

    def test_timestamp_is_timezone_aware_utc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, _RecordingRun(stdout=_LINUX_SUCCESS))
        result = IcmpProbe(timeout_seconds=5).run(_target())
        assert isinstance(result.timestamp, datetime)
        assert result.timestamp.tzinfo is timezone.utc

    def test_no_port_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # ICMP has no port concept; a portless target must probe fine.
        _patch_run(monkeypatch, _RecordingRun(stdout=_LINUX_SUCCESS))
        assert IcmpProbe(timeout_seconds=5).run(_target(port=None)).success is True


# ---------------------------------------------------------------------------
# Latency extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLatencyExtraction:
    def test_rtt_is_parsed_from_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, _RecordingRun(stdout=_LINUX_SUCCESS))
        assert IcmpProbe(timeout_seconds=5).run(_target()).latency_ms == 12.4

    def test_multiple_replies_are_averaged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        output = (
            "64 bytes from 10.20.1.10: icmp_seq=1 ttl=64 time=10.0 ms\n"
            "64 bytes from 10.20.1.10: icmp_seq=2 ttl=64 time=20.0 ms\n"
        )
        _patch_run(monkeypatch, _RecordingRun(stdout=output))
        assert IcmpProbe(timeout_seconds=5, count=2).run(_target()).latency_ms == 15.0

    def test_sub_millisecond_form_is_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Some platforms print "time<1 ms" for very fast local replies.
        output = "64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time<1 ms\n"
        _patch_run(monkeypatch, _RecordingRun(stdout=output))
        assert IcmpProbe(timeout_seconds=5).run(_target()).latency_ms == 1.0

    def test_success_without_rtt_is_a_command_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_run(monkeypatch, _RecordingRun(stdout="unparseable output"))
        result = IcmpProbe(timeout_seconds=5).run(_target())
        assert result.success is False
        assert result.error_class is ProbeErrorClass.COMMAND_ERROR

    def test_failure_reports_zero_latency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, _RecordingRun(stdout=_SILENT_DROP, returncode=1))
        assert IcmpProbe(timeout_seconds=5).run(_target()).latency_ms == 0.0


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailureClassification:
    def test_silent_drop_is_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, _RecordingRun(stdout=_SILENT_DROP, returncode=1))
        result = IcmpProbe(timeout_seconds=5).run(_target())
        assert result.success is False
        assert result.error_class is ProbeErrorClass.TIMEOUT

    def test_subprocess_timeout_is_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        error = subprocess.TimeoutExpired(cmd=["ping"], timeout=5.0)
        _patch_run(monkeypatch, _RecordingRun(error=error))
        result = IcmpProbe(timeout_seconds=5).run(_target())
        assert result.error_class is ProbeErrorClass.TIMEOUT

    def test_destination_unreachable_is_classified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_run(monkeypatch, _RecordingRun(stdout=_UNREACHABLE, returncode=1))
        result = IcmpProbe(timeout_seconds=5).run(_target())
        assert result.error_class is ProbeErrorClass.UNREACHABLE

    def test_no_route_to_host_is_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, _RecordingRun(stderr="connect: No route to host", returncode=1))
        result = IcmpProbe(timeout_seconds=5).run(_target())
        assert result.error_class is ProbeErrorClass.UNREACHABLE

    def test_dns_failure_is_classified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, _RecordingRun(stderr=_DNS_FAILURE, returncode=2))
        result = IcmpProbe(timeout_seconds=5).run(_target(host="nope.invalid"))
        assert result.error_class is ProbeErrorClass.DNS_FAILURE

    def test_dns_failure_wins_over_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Resolution failure is the more specific finding when both appear.
        output = "ping: unknown host; network is unreachable"
        _patch_run(monkeypatch, _RecordingRun(stderr=output, returncode=2))
        result = IcmpProbe(timeout_seconds=5).run(_target())
        assert result.error_class is ProbeErrorClass.DNS_FAILURE

    def test_missing_ping_binary_is_command_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_run(monkeypatch, _RecordingRun(error=FileNotFoundError("no ping")))
        result = IcmpProbe(timeout_seconds=5).run(_target())
        assert result.error_class is ProbeErrorClass.COMMAND_ERROR
        assert result.raw == "no ping"

    def test_generic_os_error_is_command_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, _RecordingRun(error=PermissionError("denied")))
        result = IcmpProbe(timeout_seconds=5).run(_target())
        assert result.error_class is ProbeErrorClass.COMMAND_ERROR

    def test_failure_output_is_kept_as_raw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_run(monkeypatch, _RecordingRun(stdout=_UNREACHABLE, returncode=1))
        result = IcmpProbe(timeout_seconds=5).run(_target())
        assert result.raw is not None
        assert "Unreachable" in result.raw

    def test_empty_failure_output_leaves_raw_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_run(monkeypatch, _RecordingRun(returncode=1))
        assert IcmpProbe(timeout_seconds=5).run(_target()).raw is None

    def test_every_failure_still_returns_a_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_run(monkeypatch, _RecordingRun(error=OSError()))
        assert isinstance(IcmpProbe(timeout_seconds=5).run(_target()), ProbeResult)


# ---------------------------------------------------------------------------
# Command construction and timeout forwarding
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCommandConstruction:
    def test_host_is_passed_as_final_argument(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = _RecordingRun(stdout=_LINUX_SUCCESS)
        _patch_run(monkeypatch, run)
        IcmpProbe(timeout_seconds=5).run(_target(host="10.0.0.5"))
        assert run.command is not None
        assert run.command[0] == "ping"
        assert run.command[-1] == "10.0.0.5"

    def test_command_is_a_list_and_no_shell_is_used(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run = _RecordingRun(stdout=_LINUX_SUCCESS)
        _patch_run(monkeypatch, run)
        IcmpProbe(timeout_seconds=5).run(_target())
        assert isinstance(run.command, list)
        assert run.kwargs.get("shell") is None

    def test_count_is_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = _RecordingRun(stdout=_LINUX_SUCCESS)
        _patch_run(monkeypatch, run)
        IcmpProbe(timeout_seconds=5, count=3).run(_target())
        assert run.command is not None
        assert run.command[run.command.index("-c") + 1] == "3"

    def test_default_count_is_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = _RecordingRun(stdout=_LINUX_SUCCESS)
        _patch_run(monkeypatch, run)
        IcmpProbe(timeout_seconds=5).run(_target())
        assert run.command is not None
        assert run.command[run.command.index("-c") + 1] == "1"

    def test_timeout_is_forwarded_to_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = _RecordingRun(stdout=_LINUX_SUCCESS)
        _patch_run(monkeypatch, run)
        IcmpProbe(timeout_seconds=4).run(_target())
        assert run.kwargs["timeout"] > 4

    def test_per_reply_timeout_flag_is_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = _RecordingRun(stdout=_LINUX_SUCCESS)
        _patch_run(monkeypatch, run)
        IcmpProbe(timeout_seconds=3).run(_target())
        assert run.command is not None
        assert "-W" in run.command

    def test_linux_per_reply_timeout_is_whole_seconds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("cloudprobe.probes.icmp.sys.platform", "linux")
        run = _RecordingRun(stdout=_LINUX_SUCCESS)
        _patch_run(monkeypatch, run)
        IcmpProbe(timeout_seconds=3).run(_target())
        assert run.command is not None
        assert run.command[run.command.index("-W") + 1] == "3"

    def test_linux_sub_second_timeout_floors_to_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Linux ping rejects -W 0, so a sub-second budget must still send 1.
        monkeypatch.setattr("cloudprobe.probes.icmp.sys.platform", "linux")
        run = _RecordingRun(stdout=_LINUX_SUCCESS)
        _patch_run(monkeypatch, run)
        IcmpProbe(timeout_seconds=0.4).run(_target())
        assert run.command is not None
        assert run.command[run.command.index("-W") + 1] == "1"

    def test_macos_per_reply_timeout_is_milliseconds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("cloudprobe.probes.icmp.sys.platform", "darwin")
        run = _RecordingRun(stdout=_LINUX_SUCCESS)
        _patch_run(monkeypatch, run)
        IcmpProbe(timeout_seconds=3).run(_target())
        assert run.command is not None
        assert run.command[run.command.index("-W") + 1] == "3000"

    def test_output_is_captured_as_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        run = _RecordingRun(stdout=_LINUX_SUCCESS)
        _patch_run(monkeypatch, run)
        IcmpProbe(timeout_seconds=5).run(_target())
        assert run.kwargs["capture_output"] is True
        assert run.kwargs["text"] is True

    def test_nonzero_exit_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # check=False: a failed ping is data, not an exception.
        run = _RecordingRun(stdout=_SILENT_DROP, returncode=1)
        _patch_run(monkeypatch, run)
        IcmpProbe(timeout_seconds=5).run(_target())
        assert run.kwargs["check"] is False
