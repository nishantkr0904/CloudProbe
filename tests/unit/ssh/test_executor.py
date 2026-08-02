"""Unit tests for the SSH diagnostic executor.

The Paramiko client is replaced by an injected fake, so these tests exercise
the real connection sequencing, command capture and error translation with no
transport, no key material and no socket — pytest-socket would fail any test
that opened a real connection.

Covers:
    - successful command execution and stdout/stderr capture
    - exit status propagation (including non-zero)
    - authentication failure, connection timeout, transport error
    - command dispatch failure over a healthy session
    - connection and command timeout forwarding
    - clean resource release on success and on failure
"""

from __future__ import annotations

import socket
from typing import Any

import paramiko
import pytest

from cloudprobe.ssh.executor import (
    CommandResult,
    SSHAuthenticationError,
    SSHCommandError,
    SSHConnectionError,
    SSHExecutor,
    SSHTimeoutError,
)


class _FakeChannelFile:
    """Stands in for a Paramiko channel file (stdout/stderr).

    Carries a fake ``channel`` exposing the exit status, mirroring Paramiko's
    ``stdout.channel.recv_exit_status()`` access path.
    """

    def __init__(self, data: bytes, exit_status: int = 0) -> None:
        self._data = data
        self.channel = _FakeChannel(exit_status)

    def read(self) -> bytes:
        return self._data


class _FakeChannel:
    def __init__(self, exit_status: int) -> None:
        self._exit_status = exit_status

    def recv_exit_status(self) -> int:
        return self._exit_status


class _FakeClient:
    """Stands in for ``paramiko.SSHClient``.

    Records how it was driven, then returns canned command output or raises the
    configured exception from the stage it was configured for.
    """

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        exit_status: int = 0,
        connect_error: Exception | None = None,
        exec_error: Exception | None = None,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._exit_status = exit_status
        self._connect_error = connect_error
        self._exec_error = exec_error
        self.connect_kwargs: dict[str, Any] = {}
        self.exec_command_calls: list[dict[str, Any]] = []
        self.system_host_keys_loaded = False
        self.close_count = 0

    def load_system_host_keys(self) -> None:
        self.system_host_keys_loaded = True

    def connect(self, **kwargs: Any) -> None:
        self.connect_kwargs = kwargs
        if self._connect_error is not None:
            raise self._connect_error

    def exec_command(self, command: str, **kwargs: Any) -> tuple[Any, Any, Any]:
        self.exec_command_calls.append({"command": command, **kwargs})
        if self._exec_error is not None:
            raise self._exec_error
        return (
            _FakeChannelFile(b""),
            _FakeChannelFile(self._stdout, self._exit_status),
            _FakeChannelFile(self._stderr),
        )

    def close(self) -> None:
        self.close_count += 1


def _executor(client: _FakeClient, **overrides: Any) -> SSHExecutor:
    base: dict[str, Any] = {
        "host": "10.20.1.10",
        "username": "ec2-user",
        "key_filename": "/keys/lab.pem",
        "timeout": 10.0,
        "client_factory": lambda: client,
    }
    base.update(overrides)
    return SSHExecutor(**base)


# ---------------------------------------------------------------------------
# Success and output capture
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuccess:
    def test_command_runs_and_returns_result(self) -> None:
        client = _FakeClient(stdout=b"ok\n")
        with _executor(client) as ssh:
            result = ssh.execute("uptime")
        assert isinstance(result, CommandResult)
        assert result.command == "uptime"

    def test_stdout_and_stderr_are_captured(self) -> None:
        client = _FakeClient(stdout=b"out-data", stderr=b"err-data")
        with _executor(client) as ssh:
            result = ssh.execute("do-thing")
        assert result.stdout == "out-data"
        assert result.stderr == "err-data"

    def test_zero_exit_status_is_propagated(self) -> None:
        client = _FakeClient(exit_status=0)
        with _executor(client) as ssh:
            assert ssh.execute("true").exit_status == 0

    def test_nonzero_exit_status_is_propagated_not_raised(self) -> None:
        # A non-zero exit is a faithful result, never an executor error.
        client = _FakeClient(stderr=b"boom", exit_status=7)
        with _executor(client) as ssh:
            result = ssh.execute("false")
        assert result.exit_status == 7
        assert result.stderr == "boom"

    def test_undecodable_output_does_not_raise(self) -> None:
        client = _FakeClient(stdout=b"\xff\xfe")
        with _executor(client) as ssh:
            result = ssh.execute("cat /bin/x")
        assert isinstance(result.stdout, str)


# ---------------------------------------------------------------------------
# Failure translation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailureTranslation:
    def test_authentication_failure_is_translated(self) -> None:
        client = _FakeClient(connect_error=paramiko.AuthenticationException("bad key"))
        with pytest.raises(SSHAuthenticationError):
            _executor(client).connect()

    def test_connection_timeout_is_translated(self) -> None:
        client = _FakeClient(connect_error=socket.timeout("timed out"))
        with pytest.raises(SSHTimeoutError):
            _executor(client).connect()

    def test_builtin_timeout_error_is_translated(self) -> None:
        client = _FakeClient(connect_error=TimeoutError("timed out"))
        with pytest.raises(SSHTimeoutError):
            _executor(client).connect()

    def test_transport_error_is_translated(self) -> None:
        client = _FakeClient(connect_error=paramiko.SSHException("protocol banner"))
        with pytest.raises(SSHConnectionError):
            _executor(client).connect()

    def test_socket_error_on_connect_is_translated(self) -> None:
        client = _FakeClient(connect_error=ConnectionRefusedError("refused"))
        with pytest.raises(SSHConnectionError):
            _executor(client).connect()

    def test_auth_failure_is_not_a_generic_connection_error(self) -> None:
        # AuthenticationException subclasses SSHException; the specific class wins.
        client = _FakeClient(connect_error=paramiko.AuthenticationException())
        with pytest.raises(SSHAuthenticationError):
            _executor(client).connect()

    def test_command_dispatch_failure_is_translated(self) -> None:
        client = _FakeClient(exec_error=paramiko.SSHException("session refused"))
        ssh = _executor(client)
        ssh.connect()
        with pytest.raises(SSHCommandError):
            ssh.execute("uptime")

    def test_command_timeout_is_translated(self) -> None:
        client = _FakeClient(exec_error=socket.timeout("timed out"))
        ssh = _executor(client)
        ssh.connect()
        with pytest.raises(SSHTimeoutError):
            ssh.execute("sleep 999")

    def test_execute_before_connect_is_rejected(self) -> None:
        with pytest.raises(SSHConnectionError):
            _executor(_FakeClient()).execute("uptime")


# ---------------------------------------------------------------------------
# Parameter forwarding
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParameterForwarding:
    def test_connection_parameters_are_forwarded(self) -> None:
        client = _FakeClient()
        _executor(
            client, host="host.example", port=2222, username="admin"
        ).connect()
        assert client.connect_kwargs["hostname"] == "host.example"
        assert client.connect_kwargs["port"] == 2222
        assert client.connect_kwargs["username"] == "admin"

    def test_connection_timeout_is_forwarded(self) -> None:
        client = _FakeClient()
        _executor(client, timeout=4.5).connect()
        assert client.connect_kwargs["timeout"] == 4.5

    def test_command_timeout_is_forwarded(self) -> None:
        client = _FakeClient()
        ssh = _executor(client, timeout=4.5)
        ssh.connect()
        ssh.execute("uptime")
        assert client.exec_command_calls[0]["timeout"] == 4.5

    def test_key_authentication_is_forwarded(self) -> None:
        client = _FakeClient()
        _executor(client, key_filename="/keys/id.pem", password=None).connect()
        assert client.connect_kwargs["key_filename"] == "/keys/id.pem"
        assert client.connect_kwargs["password"] is None

    def test_password_authentication_is_forwarded(self) -> None:
        client = _FakeClient()
        _executor(client, key_filename=None, password="s3cr3t").connect()
        assert client.connect_kwargs["password"] == "s3cr3t"
        assert client.connect_kwargs["key_filename"] is None

    def test_system_host_keys_are_loaded(self) -> None:
        # No auto-add policy: unknown hosts rely on known_hosts (architecture §13.2).
        client = _FakeClient()
        _executor(client).connect()
        assert client.system_host_keys_loaded is True


# ---------------------------------------------------------------------------
# Resource cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResourceCleanup:
    def test_context_manager_closes_session(self) -> None:
        client = _FakeClient()
        with _executor(client) as ssh:
            ssh.execute("uptime")
        assert client.close_count == 1

    def test_close_is_idempotent(self) -> None:
        client = _FakeClient()
        ssh = _executor(client)
        ssh.connect()
        ssh.close()
        ssh.close()
        assert client.close_count == 1

    def test_failed_connection_closes_client(self) -> None:
        client = _FakeClient(connect_error=paramiko.SSHException("reset"))
        with pytest.raises(SSHConnectionError):
            _executor(client).connect()
        assert client.close_count == 1

    def test_context_manager_closes_even_when_command_raises(self) -> None:
        client = _FakeClient(exec_error=paramiko.SSHException("dispatch failed"))
        with pytest.raises(SSHCommandError):
            with _executor(client) as ssh:
                ssh.execute("uptime")
        assert client.close_count == 1
