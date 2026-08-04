"""Behavioural regression: the SSH executor's observable contract.

The SSH executor has no byte-for-byte output to freeze as a golden file — it
returns a structured ``CommandResult`` and raises a translated exception per
failure mode (architecture §10.2 gives it no regression golden surface).  What
must stay stable is the *externally observable contract*: the field set of a
returned result, that a non-zero exit is data rather than an error, and that
each transport failure maps to the same executor exception class it maps to
today.

These tests therefore assert the contract as a whole, from one place, so a
refactor of the executor's internals that quietly changes the result shape or
re-buckets a failure is caught.  They deliberately do not re-assert every
individual rule — ``tests/unit/ssh/test_executor.py`` owns the fine-grained
per-rule coverage.  The distinction is intent: the unit suite proves each
translation is *correct*; this suite pins the assembled contract *stable* so
downstream callers (the future SSH probe) can depend on it not shifting.

The Paramiko client is an injected fake, so no transport, key material or
socket is used — pytest-socket would fail any test that dialled out.  No
production code changes are required.
"""

from __future__ import annotations

import dataclasses
from typing import Any, ClassVar, cast

import paramiko
import pytest

from cloudprobe.ssh import (
    CommandResult,
    SSHAuthenticationError,
    SSHCommandError,
    SSHConnectionError,
    SSHExecutor,
    SSHExecutorError,
    SSHTimeoutError,
)


class _FakeChannel:
    def __init__(self, exit_status: int) -> None:
        self._exit_status = exit_status

    def recv_exit_status(self) -> int:
        return self._exit_status


class _FakeChannelFile:
    """Mirrors Paramiko's ``stdout.channel.recv_exit_status()`` access path."""

    def __init__(self, data: bytes, exit_status: int = 0) -> None:
        self._data = data
        self.channel = _FakeChannel(exit_status)

    def read(self) -> bytes:
        return self._data


class _FakeClient:
    """A structural ``paramiko.SSHClient`` that raises where configured."""

    def __init__(
        self,
        *,
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

    def load_system_host_keys(self) -> None:
        return None

    def connect(self, **_kwargs: Any) -> None:
        if self._connect_error is not None:
            raise self._connect_error

    def exec_command(self, command: str, **_kwargs: Any) -> tuple[Any, Any, Any]:
        if self._exec_error is not None:
            raise self._exec_error
        return (
            _FakeChannelFile(b""),
            _FakeChannelFile(self._stdout, self._exit_status),
            _FakeChannelFile(self._stderr),
        )

    def close(self) -> None:
        return None


def _executor(client: _FakeClient) -> SSHExecutor:
    return SSHExecutor(
        host="10.20.1.10",
        username="ec2-user",
        key_filename="/keys/lab.pem",
        timeout=10.0,
        client_factory=lambda: cast(paramiko.SSHClient, client),
    )


@pytest.mark.regression
class TestCommandResultContract:
    def test_result_record_shape_is_stable(self) -> None:
        # The whole record a caller receives — field set and values — pinned in
        # one place so a reshape of CommandResult is a visible break.
        client = _FakeClient(stdout=b"load: 0.1\n", stderr=b"warn\n", exit_status=0)
        with _executor(client) as ssh:
            result = ssh.execute("uptime")

        assert dataclasses.asdict(result) == {
            "command": "uptime",
            "stdout": "load: 0.1\n",
            "stderr": "warn\n",
            "exit_status": 0,
        }

    def test_nonzero_exit_is_a_result_not_an_error(self) -> None:
        # The contract that a failed *command* is data, not an executor
        # exception, is what lets the caller distinguish "ran and failed" from
        # "could not run".
        client = _FakeClient(stderr=b"missing\n", exit_status=127)
        with _executor(client) as ssh:
            result = ssh.execute("run-thing")

        assert isinstance(result, CommandResult)
        assert result.exit_status == 127
        assert result.stderr == "missing\n"


@pytest.mark.regression
class TestFailureClassificationContract:
    # Each transport failure and the executor exception it must translate to.
    # Frozen as a table so re-bucketing any failure mode is one visible edit.
    _CONNECT_CASES: ClassVar[list[tuple[Exception, type[SSHExecutorError]]]] = [
        (paramiko.AuthenticationException("bad key"), SSHAuthenticationError),
        (TimeoutError("timed out"), SSHTimeoutError),
        (paramiko.SSHException("bad banner"), SSHConnectionError),
        (ConnectionRefusedError("refused"), SSHConnectionError),
    ]

    @pytest.mark.parametrize(("raised", "expected"), _CONNECT_CASES)
    def test_connect_failures_map_to_stable_classes(
        self, raised: Exception, expected: type[SSHExecutorError]
    ) -> None:
        with pytest.raises(expected):
            _executor(_FakeClient(connect_error=raised)).connect()

    @pytest.mark.parametrize(
        ("raised", "expected"),
        [
            (TimeoutError("timed out"), SSHTimeoutError),
            (paramiko.SSHException("session refused"), SSHCommandError),
        ],
    )
    def test_execute_failures_map_to_stable_classes(
        self, raised: Exception, expected: type[SSHExecutorError]
    ) -> None:
        ssh = _executor(_FakeClient(exec_error=raised))
        ssh.connect()
        with pytest.raises(expected):
            ssh.execute("uptime")

    def test_every_executor_error_is_catchable_as_the_base(self) -> None:
        # Downstream layers catch the one base class to stay free of Paramiko;
        # that umbrella guarantee is part of the contract.
        for _raised, expected in self._CONNECT_CASES:
            assert issubclass(expected, SSHExecutorError)
