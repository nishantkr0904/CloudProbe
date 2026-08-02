"""Alerting contracts: the inputs and outputs of threshold evaluation.

The alerting layer decides whether a ``ProbeResult`` breaches a declared rule
(architecture §8.1).  That decision needs two things the configuration
``AlertRule`` does not yet carry — which metric to measure and what value to
compare it against — so this module composes the config rule (selector +
severity, reused as-is) with an explicit predicate: a ``MetricKind``, a
``ComparisonOperator`` and a threshold value.  The composed form is
``AlertRuleSpec``; a successful (or failed) evaluation produces an ``Alert``.

This module holds no evaluation logic, opens no sockets and touches no AWS:
it is the static contract the manager and its callers share.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from cloudprobe.config.models import AlertRule, AlertSeverity, ProbeType, Target


class AlertingError(Exception):
    """Base class for every error the alerting layer raises.

    Callers catch this one type to handle any alerting failure without
    importing anything beyond the package's public surface.
    """


class InvalidRuleError(AlertingError):
    """A rule was built with a predicate CloudProbe cannot evaluate."""


class MissingThresholdError(AlertingError):
    """A rule reached evaluation without a configured threshold value.

    A rule with no threshold is a configuration error, never a silent
    "no fire" — the caller must know the rule is broken.
    """


class MetricKind(str, Enum):
    """Which measured quantity a rule's predicate inspects.

    String-valued because the value is used verbatim in alerts and (later)
    in CloudWatch metric names.
    """

    LATENCY_MS = "latency_ms"
    AVAILABILITY = "availability"
    PACKET_LOSS = "packet_loss"


class ComparisonOperator(str, Enum):
    """How a rule compares the observed value against its threshold.

    String-valued so the symbolic form survives into alert payloads.
    """

    GT = ">"
    GE = ">="
    LT = "<"
    LE = "<="
    EQ = "=="


@dataclass(frozen=True)
class AlertRuleSpec:
    """An ``AlertRule`` composed with the predicate fields it lacks.

    ``rule`` supplies the selector and severity; ``metric``, ``operator`` and
    ``threshold_value`` supply the predicate.  The predicate is validated at
    construction so the manager only ever evaluates rules it can decide.
    """

    rule: AlertRule
    metric: MetricKind
    operator: ComparisonOperator
    threshold_value: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.metric, MetricKind):
            raise InvalidRuleError(
                f"rule {self.rule.rule_id!r} uses unsupported metric "
                f"{self.metric!r}; expected one of "
                f"{[kind.value for kind in MetricKind]}"
            )
        if not isinstance(self.operator, ComparisonOperator):
            raise InvalidRuleError(
                f"rule {self.rule.rule_id!r} uses unsupported comparison "
                f"operator {self.operator!r}; expected one of "
                f"{[op.value for op in ComparisonOperator]}"
            )
        if self.threshold_value is not None and not math.isfinite(self.threshold_value):
            raise InvalidRuleError(
                f"rule {self.rule.rule_id!r} threshold must be finite, "
                f"got {self.threshold_value!r}"
            )


@dataclass(frozen=True)
class Alert:
    """The outcome of evaluating one rule against one probe result.

    ``breached`` records whether the predicate held.  Every applicable rule
    produces one ``Alert`` — breached or not — so a caller can see a rule's
    OK/ALARM state exactly like the CloudWatch lifecycle (architecture §8.4),
    not just the moments it fired.  Frozen because a decision about a result
    that already happened must not be edited.
    """

    rule_id: str
    target: Target
    probe_type: ProbeType
    severity: AlertSeverity
    metric: MetricKind
    operator: ComparisonOperator
    threshold_value: float
    observed_value: float
    breached: bool
    timestamp: datetime
