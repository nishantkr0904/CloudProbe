"""Threshold evaluation — the point where a result becomes a decision.

``AlertManager.evaluate`` takes one ``ProbeResult`` and the declared rules and
returns one ``Alert`` per applicable rule.  It decides *whether* an alert
should exist and nothing more: it creates no CloudWatch alarm, publishes to no
SNS topic, talks to no AWS service, and keeps no state between calls.  That
purity is what makes it deterministic — the same result and rules always yield
the same alerts (architecture §8.1; §6.6 "Alerting decides").

Windowed predicates (success ratio over a ring buffer of recent results) are a
later addition to this same module; today's evaluation reads a single result,
which is all the single-result metrics below require.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from cloudprobe.alerting.models import (
    Alert,
    AlertRuleSpec,
    ComparisonOperator,
    InvalidRuleError,
    MetricKind,
    MissingThresholdError,
)
from cloudprobe.probes.base import ProbeResult

# Each operator as a pure two-argument comparison.  A table, not a chain of
# ``if``s, so adding an operator is adding one entry — and an unknown operator
# is a missing key, caught explicitly rather than silently returning False.
_COMPARISONS: dict[ComparisonOperator, Callable[[float, float], bool]] = {
    ComparisonOperator.GT: lambda observed, threshold: observed > threshold,
    ComparisonOperator.GE: lambda observed, threshold: observed >= threshold,
    ComparisonOperator.LT: lambda observed, threshold: observed < threshold,
    ComparisonOperator.LE: lambda observed, threshold: observed <= threshold,
    ComparisonOperator.EQ: lambda observed, threshold: observed == threshold,
}


class AlertManager:
    """Evaluate probe results against declared rules, statelessly.

    Holds no configuration and no history; every input arrives as an argument
    so the manager is trivially unit-testable and safe to share.
    """

    def evaluate(self, result: ProbeResult, rules: Iterable[AlertRuleSpec]) -> list[Alert]:
        """Return one ``Alert`` per rule that applies to ``result``.

        A rule applies when its probe type matches the result's and its
        target-tag filter is a subset of the target's tags (the §8.1
        selector).  Rules that do not apply are skipped silently — not every
        rule concerns every result.

        Args:
            result: The single probe outcome to judge.
            rules: The declared rules to judge it against.

        Returns:
            One ``Alert`` per applicable rule, breached or not, in rule order.

        Raises:
            MissingThresholdError: An applicable rule has no threshold value.
            InvalidRuleError: An applicable rule names a metric with no
                reading, or an operator with no comparison.
        """
        alerts: list[Alert] = []
        for spec in rules:
            if not self._applies(spec, result):
                continue
            alerts.append(self._evaluate_one(spec, result))
        return alerts

    def _applies(self, spec: AlertRuleSpec, result: ProbeResult) -> bool:
        """True when the rule's selector matches this result."""
        if spec.rule.probe_type != result.probe_type:
            return False
        return _tags_match(spec.rule.target_tag_filter, result.target.tags)

    def _evaluate_one(self, spec: AlertRuleSpec, result: ProbeResult) -> Alert:
        """Apply one applicable rule's predicate to the result."""
        if spec.threshold_value is None:
            raise MissingThresholdError(
                f"rule {spec.rule.rule_id!r} has no threshold value configured"
            )
        observed = _observe(spec.metric, result)
        compare = _COMPARISONS.get(spec.operator)
        if compare is None:  # pragma: no cover - guarded by AlertRuleSpec
            raise InvalidRuleError(
                f"rule {spec.rule.rule_id!r} uses unsupported comparison "
                f"operator {spec.operator!r}"
            )
        breached = compare(observed, spec.threshold_value)
        return Alert(
            rule_id=spec.rule.rule_id,
            target=result.target,
            probe_type=result.probe_type,
            severity=spec.rule.severity,
            metric=spec.metric,
            operator=spec.operator,
            threshold_value=spec.threshold_value,
            observed_value=observed,
            breached=breached,
            timestamp=result.timestamp,
        )


def _observe(metric: MetricKind, result: ProbeResult) -> float:
    """Read the value a rule's metric refers to from the result.

    Availability and packet loss are derived from ``success`` — a single
    result is fully available (loss 0.0) or fully unavailable (loss 1.0);
    ratios over a window belong to the future windowed evaluator.
    """
    if metric is MetricKind.LATENCY_MS:
        return result.latency_ms
    if metric is MetricKind.AVAILABILITY:
        return 1.0 if result.success else 0.0
    if metric is MetricKind.PACKET_LOSS:
        return 0.0 if result.success else 1.0
    raise InvalidRuleError(  # pragma: no cover - guarded by AlertRuleSpec
        f"no reading for metric {metric!r}"
    )


def _tags_match(required: Mapping[str, str], actual: Mapping[str, str]) -> bool:
    """True when ``actual`` contains every key/value pair in ``required``."""
    return all(actual.get(key) == value for key, value in required.items())
