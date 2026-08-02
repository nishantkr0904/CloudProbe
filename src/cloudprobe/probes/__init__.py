"""CloudProbe probe engine — public surface.

Every probe executes a reachability check against a ``Target`` and returns a
``ProbeResult`` carrying an enumerated ``error_class`` on failure.  Downstream
layers import that shared contract and the concrete probes from here, never
from the internal submodules.

The layer reports facts and never judges them: it holds no threshold logic, no
metric emission and no report formatting.
"""

from cloudprobe.probes.base import ProbeErrorClass, ProbeResult
from cloudprobe.probes.tcp import TcpProbe

__all__ = [
    "ProbeErrorClass",
    "ProbeResult",
    "TcpProbe",
]
