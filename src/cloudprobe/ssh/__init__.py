"""CloudProbe SSH adapter — public surface.

All Paramiko usage is isolated behind this package.  The SSH probe and any
future on-host diagnostic collector import the executor and its result and
error types from here, never from Paramiko or the internal submodule directly.

This layer runs remote commands and reports their outcome.  It builds no
``ProbeResult`` and holds no probe, metric, or reporting logic — those belong
to the layers above it.
"""

from cloudprobe.ssh.executor import (
    CommandResult,
    SSHAuthenticationError,
    SSHCommandError,
    SSHConnectionError,
    SSHExecutor,
    SSHExecutorError,
    SSHTimeoutError,
)

__all__ = [
    "CommandResult",
    "SSHAuthenticationError",
    "SSHCommandError",
    "SSHConnectionError",
    "SSHExecutor",
    "SSHExecutorError",
    "SSHTimeoutError",
]
