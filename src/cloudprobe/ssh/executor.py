"""SSH diagnostic executor — the single place Paramiko is used.

One responsibility: establish an SSH connection, run one remote command, and
return its captured output as a structured result.  Every consumer that needs
on-host diagnostics talks to this class, never to Paramiko directly, so the
transport library stays behind one small, testable surface.

The Paramiko client is *injected* (``client_factory``) rather than constructed
inline, so the executor can be unit-tested with a fake client and never opens a
real connection under test.

This module reports facts about command execution.  It holds no probe logic,
builds no ``ProbeResult``, performs no retry, scheduling, metrics or reporting,
and — for now — implements neither connection pooling nor command whitelisting.
Those concerns live in other layers or later commits.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import paramiko


class SSHExecutorError(Exception):
    """Base class for every error this executor raises.

    Callers can catch this one type to handle any SSH failure without importing
    Paramiko, which keeps the transport library an implementation detail.
    """


class SSHAuthenticationError(SSHExecutorError):
    """Credentials were rejected by the server."""


class SSHTimeoutError(SSHExecutorError):
    """The connection or command did not complete within the timeout."""


class SSHConnectionError(SSHExecutorError):
    """The transport could not be established (refused, reset, protocol error)."""


class SSHCommandError(SSHExecutorError):
    """A command could not be dispatched over an otherwise healthy session."""


@dataclass(frozen=True)
class CommandResult:
    """The captured outcome of one remote command.

    ``exit_status`` is the command's own return code; a non-zero value is a
    faithfully reported result, not an executor error.  Frozen because a result
    is a record of something that already happened and must not be edited.
    """

    command: str
    stdout: str
    stderr: str
    exit_status: int


class SSHExecutor:
    """Run a single remote command over one SSH session.

    Supports key-based or password authentication and a configurable timeout
    applied to both the connection and the command.  Intended to be used as a
    context manager so the session is always closed:

        with SSHExecutor(host="10.20.1.10", username="ec2-user",
                         key_filename="/path/key.pem") as ssh:
            result = ssh.execute("uptime")
    """

    def __init__(
        self,
        host: str,
        username: str,
        *,
        port: int = 22,
        password: str | None = None,
        key_filename: str | None = None,
        timeout: float = 10.0,
        client_factory: Callable[[], paramiko.SSHClient] = paramiko.SSHClient,
    ) -> None:
        self._host = host
        self._username = username
        self._port = port
        self._password = password
        self._key_filename = key_filename
        self._timeout = timeout
        self._client_factory = client_factory
        self._client: paramiko.SSHClient | None = None

    def __enter__(self) -> SSHExecutor:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        """Open the SSH session, translating transport failures.

        Unknown host keys are rejected: the client keeps Paramiko's default
        ``RejectPolicy`` and loads the system ``known_hosts``, so there is no
        auto-accept path (see architecture §13.2).
        """
        client = self._client_factory()
        client.load_system_host_keys()
        try:
            client.connect(
                hostname=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
                key_filename=self._key_filename,
                timeout=self._timeout,
            )
        except paramiko.AuthenticationException as exc:
            client.close()
            raise SSHAuthenticationError(str(exc)) from exc
        except TimeoutError as exc:
            client.close()
            raise SSHTimeoutError(str(exc)) from exc
        except (paramiko.SSHException, OSError) as exc:
            client.close()
            raise SSHConnectionError(str(exc)) from exc
        self._client = client

    def execute(self, command: str) -> CommandResult:
        """Run ``command`` and capture stdout, stderr and the exit status.

        The same timeout that bounds the connection also bounds the command, so
        a hung command cannot block indefinitely.
        """
        if self._client is None:
            raise SSHConnectionError("execute() called before connect()")
        try:
            _, stdout, stderr = self._client.exec_command(command, timeout=self._timeout)
            exit_status = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
        except TimeoutError as exc:
            raise SSHTimeoutError(str(exc)) from exc
        except paramiko.SSHException as exc:
            raise SSHCommandError(str(exc)) from exc
        return CommandResult(
            command=command,
            stdout=out,
            stderr=err,
            exit_status=exit_status,
        )

    def close(self) -> None:
        """Close the session if one is open.  Safe to call more than once."""
        if self._client is not None:
            self._client.close()
            self._client = None
