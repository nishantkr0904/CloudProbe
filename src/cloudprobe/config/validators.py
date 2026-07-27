"""Cross-inventory validators.

These functions inspect the full collection of models (targets, rules,
schedules, thresholds) after each individual model has been validated.
They are called from ``CloudProbeConfig.validate_inventory``.

Each function has a single responsibility and raises ``ValueError`` (which
Pydantic wraps into a ``ValidationError``) with a message identifying the
offending items.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from cloudprobe.config.models import AlertRule, ProbeType, Schedule, Target, Threshold


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted(item for item, count in Counter(values).items() if count > 1)


def validate_no_duplicate_target_ids(targets: list[Target]) -> None:
    dupes = _duplicates(t.target_id for t in targets)
    if dupes:
        raise ValueError(f"duplicate target_id(s): {', '.join(dupes)}")


def validate_no_duplicate_rule_ids(rules: list[AlertRule]) -> None:
    dupes = _duplicates(r.rule_id for r in rules)
    if dupes:
        raise ValueError(f"duplicate rule_id(s): {', '.join(dupes)}")


def _referenced_probe_types(targets: list[Target]) -> set[ProbeType]:
    return {pt for t in targets for pt in t.probe_types}


def validate_thresholds_cover_probe_types(
    targets: list[Target], thresholds: list[Threshold]
) -> None:
    """Every probe type used by any target must have a matching Threshold.

    Without this, a probe result could produce a metric that no rule can
    ever evaluate — a silent gap.
    """
    referenced = _referenced_probe_types(targets)
    defined = {t.probe_type for t in thresholds}
    missing = referenced - defined
    if missing:
        names = ", ".join(sorted(pt.value for pt in missing))
        raise ValueError(f"missing threshold(s) for probe type(s): {names}")


def validate_schedules_cover_probe_types(
    targets: list[Target], schedules: list[Schedule]
) -> None:
    """Every probe type used by any target must have a matching Schedule.

    Without this, a target would be declared but never executed.
    """
    referenced = _referenced_probe_types(targets)
    defined = {s.probe_type for s in schedules}
    missing = referenced - defined
    if missing:
        names = ", ".join(sorted(pt.value for pt in missing))
        raise ValueError(f"missing schedule(s) for probe type(s): {names}")
