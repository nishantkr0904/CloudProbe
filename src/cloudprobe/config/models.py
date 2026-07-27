"""Pydantic v2 models for CloudProbe configuration.

Every layer of the pipeline consumes these models — never raw dicts or YAML.
All models are frozen (immutable) after construction so that a loaded config
cannot be mutated at runtime.

Model hierarchy:
    CloudProbeConfig          ← root aggregate loaded by loader.py
    ├── list[Target]          ← what to probe
    ├── list[Threshold]       ← when to consider a result a breach
    ├── list[Schedule]        ← how often to run each probe type
    ├── list[AlertRule]       ← how to react to a breach
    └── ProbeConfig           ← global probe execution settings
"""

from __future__ import annotations

import ipaddress
import re
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Loose RFC 1123 hostname pattern: labels of letters/digits/hyphens separated
# by dots.  Length checked separately in the validator.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)"
    r"(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)


def _is_ip_shaped(value: str) -> bool:
    """Return True if the value looks like it is trying to be an IP address."""
    if ":" in value:
        return True
    return bool(value) and all(c.isdigit() or c == "." for c in value)


def _validate_host(value: str) -> str:
    """Accept either a valid IPv4/IPv6 address or a valid DNS hostname.

    Strings that clearly attempt an IP shape (all digits+dots, or contain a
    colon) are parsed with the ``ipaddress`` module; failure to parse raises.
    All other strings must match a loose RFC 1123 hostname pattern.
    """
    if _is_ip_shaped(value):
        try:
            ipaddress.ip_address(value)
        except ValueError as e:
            raise ValueError(f"invalid IP address: {value!r}") from e
        return value
    if not _HOSTNAME_RE.match(value):
        raise ValueError(f"invalid hostname: {value!r}")
    return value


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ProbeType(str, Enum):
    """Supported probe types.  String-valued so YAML values map directly."""

    TCP = "tcp"
    ICMP = "icmp"
    UDP = "udp"
    HTTP = "http"
    SSH = "ssh"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Shared field aliases
# ---------------------------------------------------------------------------

_PositiveInt = Annotated[int, Field(gt=0)]
_NonNegativeFloat = Annotated[float, Field(ge=0.0)]
_Port = Annotated[int, Field(ge=1, le=65535)]


# ---------------------------------------------------------------------------
# Target
# ---------------------------------------------------------------------------


class Target(BaseModel):
    """A single host/service that CloudProbe will probe.

    ``target_id`` is the stable identifier used in metrics dimensions and
    reports.  It must be unique across the entire inventory.
    """

    model_config = ConfigDict(frozen=True)

    target_id: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1)
    port: _Port | None = None
    probe_types: list[ProbeType] = Field(min_length=1)
    # Optional AWS dimensions — populated by discovery layer in Phase 4.
    vpc_id: str | None = None
    subnet_id: str | None = None
    instance_id: str | None = None
    # Human-readable label for reports.
    label: str | None = None
    # Arbitrary key/value tags forwarded to metrics dimensions.
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("target_id")
    @classmethod
    def target_id_no_whitespace(cls, v: str) -> str:
        if v != v.strip() or " " in v:
            raise ValueError("target_id must not contain whitespace")
        return v

    @field_validator("host")
    @classmethod
    def host_is_ip_or_hostname(cls, v: str) -> str:
        return _validate_host(v)

    @field_validator("probe_types")
    @classmethod
    def probe_types_unique(cls, v: list[ProbeType]) -> list[ProbeType]:
        if len(v) != len(set(v)):
            raise ValueError("probe_types must not contain duplicates")
        return v


# ---------------------------------------------------------------------------
# Threshold
# ---------------------------------------------------------------------------


class Threshold(BaseModel):
    """Acceptable performance bounds for a probe type.

    A ProbeResult that exceeds any of these values is a candidate breach.
    The alerting layer decides whether a breach triggers an alarm.
    """

    model_config = ConfigDict(frozen=True)

    probe_type: ProbeType
    # Maximum acceptable round-trip / connect latency in milliseconds.
    max_latency_ms: _PositiveInt = 1000
    # Minimum acceptable success ratio over the evaluation window (0.0–1.0).
    min_success_ratio: Annotated[float, Field(ge=0.0, le=1.0)] = 0.9
    # Number of consecutive failures before a breach is declared.
    consecutive_failures: _PositiveInt = 3


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


class Schedule(BaseModel):
    """Cron-style execution cadence for a probe type.

    ``cron_expression`` is a standard 5-field cron string
    (minute hour dom month dow).  Validation is intentionally lightweight
    here — APScheduler validates the expression at runtime when the scheduler
    is started (Phase 5).
    """

    model_config = ConfigDict(frozen=True)

    probe_type: ProbeType
    cron_expression: str = Field(min_length=9)  # shortest valid: "* * * * *"
    # Maximum seconds a single probe execution may run before being cancelled.
    timeout_seconds: _PositiveInt = 10
    # Maximum concurrent probe executions for this probe type.
    max_concurrency: _PositiveInt = 10

    @field_validator("cron_expression")
    @classmethod
    def cron_has_five_fields(cls, v: str) -> str:
        parts = v.strip().split()
        if len(parts) != 5:
            raise ValueError(
                f"cron_expression must have exactly 5 fields, got {len(parts)}: {v!r}"
            )
        return v


# ---------------------------------------------------------------------------
# AlertRule
# ---------------------------------------------------------------------------


class AlertRule(BaseModel):
    """Declares when a threshold breach should produce an alarm.

    ``rule_id`` is used as the CloudWatch alarm name prefix (Phase 4).
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str = Field(min_length=1, max_length=64)
    probe_type: ProbeType
    # If set, the rule applies only to targets with this tag key/value.
    target_tag_filter: dict[str, str] = Field(default_factory=dict)
    severity: AlertSeverity = AlertSeverity.WARNING
    # Evaluation window in seconds over which the success ratio is measured.
    evaluation_window_seconds: _PositiveInt = 300
    # Whether to publish to SNS when this rule fires.
    notify_sns: bool = True

    @field_validator("rule_id")
    @classmethod
    def rule_id_no_whitespace(cls, v: str) -> str:
        if v != v.strip() or " " in v:
            raise ValueError("rule_id must not contain whitespace")
        return v


# ---------------------------------------------------------------------------
# ProbeConfig
# ---------------------------------------------------------------------------


class ProbeConfig(BaseModel):
    """Global probe execution settings shared across all probe types."""

    model_config = ConfigDict(frozen=True)

    # Default connect / response timeout in seconds (overridden per Schedule).
    default_timeout_seconds: _PositiveInt = 10
    # Number of retry attempts before a probe records a failure result.
    retry_attempts: Annotated[int, Field(ge=0, le=5)] = 1
    # Base delay in seconds between retries (exponential backoff applied).
    retry_backoff_seconds: _NonNegativeFloat = 1.0
    # User-agent string sent by the HTTP probe.
    http_user_agent: str = "cloudprobe/0.0.1"


# ---------------------------------------------------------------------------
# CloudProbeConfig — root aggregate
# ---------------------------------------------------------------------------


class CloudProbeConfig(BaseModel):
    """Root configuration object.  Produced by loader.py; consumed by every
    other layer.  Immutable after construction.

    Cross-inventory validation (e.g. duplicate target_ids) lives here because
    it requires access to the full collection.  Field-level validation lives
    in the individual models above.
    """

    model_config = ConfigDict(frozen=True)

    targets: list[Target] = Field(min_length=1)
    thresholds: list[Threshold] = Field(default_factory=list)
    schedules: list[Schedule] = Field(default_factory=list)
    alert_rules: list[AlertRule] = Field(default_factory=list)
    probe: ProbeConfig = Field(default_factory=ProbeConfig)

    @model_validator(mode="after")
    def validate_inventory(self) -> "CloudProbeConfig":
        from cloudprobe.config.validators import (
            validate_no_duplicate_rule_ids,
            validate_no_duplicate_target_ids,
            validate_schedules_cover_probe_types,
            validate_thresholds_cover_probe_types,
        )

        validate_no_duplicate_target_ids(self.targets)
        validate_no_duplicate_rule_ids(self.alert_rules)
        validate_thresholds_cover_probe_types(self.targets, self.thresholds)
        validate_schedules_cover_probe_types(self.targets, self.schedules)
        return self
