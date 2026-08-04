"""Regression: canonical inventory construction.

``build_inventory`` merges static and discovered targets into the one
``DiscoveryResult`` the rest of a run consumes.  Two guarantees it makes are
externally observable and must never regress:

* **Static wins on collision.**  When an operator's explicit target and a
  discovered target resolve to the same ``(host, port, probe_type)`` triple,
  the operator's statement outranks discovery and the displaced entry is
  recorded — not dropped silently.  Operators rely on this precedence to pin a
  target discovery would otherwise define differently.
* **Provenance and degradation survive the merge.**  The static-vs-dynamic
  split a report shows, and the "a source failed" degraded flag, are both read
  off the ``DiscoveryResult`` — a merge that lost either would misreport the
  run.

This test pins both as behaviour (the precedence decision, the recorded
collision, the source split, the degraded flag) and pins the *serialized
``DiscoveryResult`` shape* as a golden, because that aggregate is what a report
renderer walks.

Why regression, not unit: ``tests/unit/discovery/test_inventory.py`` already
asserts each merge rule in isolation.  This exercises a realistic mixed run —
overlap, a survivor, a genuine second source, and a failure — end to end, and
freezes the resulting aggregate shape, guarding the contract a refactor of the
merge internals could otherwise quietly change.

No production code changes are required.
"""

from __future__ import annotations

import pytest

from cloudprobe.config.models import ProbeType, Target
from cloudprobe.discovery import (
    InventorySource,
    SourceFailure,
    build_inventory,
)
from tests.regression.golden import assert_against_golden


def _target(target_id: str, host: str, port: int | None) -> Target:
    return Target(
        target_id=target_id,
        host=host,
        port=port,
        probe_types=[ProbeType.TCP],
    )


# A static target and a discovered target that collide on (host, port, tcp),
# plus a discovered target that does not collide, plus a failed source.  One
# run that touches every guarantee at once.
_STATIC = [_target("web-static", "10.20.1.10", 443)]
_DISCOVERED = [
    _target("i-web1", "10.20.1.10", 443),  # collides with web-static
    _target("i-api1", "10.20.2.20", 8080),  # survives
]
_FAILURES = [SourceFailure(source=InventorySource.AWS, reason="throttled")]


@pytest.mark.regression
class TestInventoryPrecedence:
    def test_static_wins_and_collision_is_recorded(self) -> None:
        result = build_inventory(_STATIC, _DISCOVERED, _FAILURES)

        kept_ids = [target.target_id for target in result.inventory.targets]
        # The static target survives the collision; the discovered survivor is
        # kept; the colliding discovered target is dropped.
        assert kept_ids == ["web-static", "i-api1"]

        assert len(result.collisions) == 1
        collision = result.collisions[0]
        assert collision.kept_source is InventorySource.STATIC
        assert collision.kept_target_id == "web-static"
        assert collision.dropped_source is InventorySource.AWS
        assert collision.dropped_target_id == "i-web1"

    def test_source_split_and_degraded_flag_are_preserved(self) -> None:
        result = build_inventory(_STATIC, _DISCOVERED, _FAILURES)

        counts = result.inventory.source_counts()
        assert counts[InventorySource.STATIC] == 1
        assert counts[InventorySource.AWS] == 1

        # A failed source degrades the run but never aborts it: the static
        # inventory is still returned.
        assert result.degraded is True
        assert result.inventory.by_source(InventorySource.STATIC)[0].target_id == "web-static"


@pytest.mark.regression
class TestInventoryShape:
    def test_discovery_result_shape_is_frozen(self, update_goldens: bool) -> None:
        result = build_inventory(_STATIC, _DISCOVERED, _FAILURES)

        assert_against_golden(
            result,
            "discovery_result.json",
            update=update_goldens,
        )
