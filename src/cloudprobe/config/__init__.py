"""CloudProbe configuration layer — public surface.

Every other layer of the pipeline imports from this module, never from the
internal submodules directly.  The layer owns:

* Pydantic models for every configuration concept (Target, Threshold,
  Schedule, AlertRule, ProbeConfig, and the CloudProbeConfig aggregate).
* A YAML loader that produces a validated, immutable CloudProbeConfig.
* A small set of exceptions describing config failure modes.
"""

from cloudprobe.config.exceptions import (
    ConfigError,
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)
from cloudprobe.config.loader import load
from cloudprobe.config.models import (
    AlertRule,
    AlertSeverity,
    CloudProbeConfig,
    ProbeConfig,
    ProbeType,
    Schedule,
    Target,
    Threshold,
)

__all__ = [
    "AlertRule",
    "AlertSeverity",
    "CloudProbeConfig",
    "ConfigError",
    "ConfigNotFoundError",
    "ConfigParseError",
    "ConfigValidationError",
    "ProbeConfig",
    "ProbeType",
    "Schedule",
    "Target",
    "Threshold",
    "load",
]
