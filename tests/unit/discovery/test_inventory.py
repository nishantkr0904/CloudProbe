"""Unit tests for canonical inventory construction.

Covers:
    - static-only inventory (the only populated source in this phase)
    - union of static and discovered targets
    - static-wins-on-collision precedence
    - dedup keyed on (host, port, probe_type)
    - collision records
    - non-fatal source failures carried through
"""

from __future__ import annotations

import pytest

from cloudprobe.config.models import ProbeType, Target
from cloudprobe.discovery.inventory import build_inventory
from cloudprobe.discovery.models import InventorySource, SourceFailure


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _target(**overrides: object) -> Target:
    base: dict[str, object] = {
        "target_id": "t-1",
        "host": "10.0.0.1",
        "port": 22,
        "probe_types": ["tcp"],
    }
    base.update(overrides)
    return Target.model_validate(base)


# ---------------------------------------------------------------------------
# Static-only
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStaticOnly:
    def test_empty_sources_produce_empty_inventory(self) -> None:
        result = build_inventory([])
        assert result.inventory.targets == []
        assert result.collisions == []
        assert result.degraded is False

    def test_static_targets_are_tagged_static(self) -> None:
        result = build_inventory([_target()])
        assert result.inventory.source_counts() == {
            InventorySource.STATIC: 1,
            InventorySource.AWS: 0,
        }

    def test_static_order_preserved(self) -> None:
        result = build_inventory(
            [_target(target_id="a", host="10.0.0.1"), _target(target_id="b", host="10.0.0.2")]
        )
        assert [t.target_id for t in result.inventory.targets] == ["a", "b"]

    def test_distinct_probe_types_on_same_host_both_kept(self) -> None:
        result = build_inventory(
            [
                _target(target_id="a", probe_types=["tcp"]),
                _target(target_id="b", probe_types=["http"]),
            ]
        )
        assert len(result.inventory.targets) == 2
        assert result.collisions == []

    def test_duplicate_static_triples_deduped(self) -> None:
        result = build_inventory([_target(target_id="a"), _target(target_id="b")])
        assert [t.target_id for t in result.inventory.targets] == ["a"]
        assert len(result.collisions) == 1
        collision = result.collisions[0]
        assert collision.kept_source is InventorySource.STATIC
        assert collision.dropped_source is InventorySource.STATIC
        assert collision.dropped_target_id == "b"


# ---------------------------------------------------------------------------
# Union of sources
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnion:
    def test_non_overlapping_sources_are_unioned(self) -> None:
        result = build_inventory(
            [_target(target_id="s1", host="10.0.0.1")],
            [_target(target_id="a1", host="10.0.0.2")],
        )
        assert result.inventory.source_counts() == {
            InventorySource.STATIC: 1,
            InventorySource.AWS: 1,
        }
        assert result.collisions == []

    def test_discovered_only_inventory(self) -> None:
        result = build_inventory([], [_target(target_id="a1")])
        assert [t.target_id for t in result.inventory.by_source(InventorySource.AWS)] == ["a1"]

    def test_same_host_different_port_is_not_a_collision(self) -> None:
        result = build_inventory(
            [_target(target_id="s1", port=22)],
            [_target(target_id="a1", port=443)],
        )
        assert len(result.inventory.targets) == 2
        assert result.collisions == []

    def test_unset_port_does_not_collide_with_set_port(self) -> None:
        result = build_inventory(
            [_target(target_id="s1", port=None, probe_types=["icmp"])],
            [_target(target_id="a1", port=22, probe_types=["icmp"])],
        )
        assert len(result.inventory.targets) == 2


# ---------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPrecedence:
    def test_static_wins_on_collision(self) -> None:
        result = build_inventory(
            [_target(target_id="static-1")],
            [_target(target_id="aws-1")],
        )
        assert [t.target_id for t in result.inventory.targets] == ["static-1"]

    def test_static_wins_regardless_of_argument_content_order(self) -> None:
        # Two discovered targets, one static, all on the same triple.
        result = build_inventory(
            [_target(target_id="static-1")],
            [_target(target_id="aws-1"), _target(target_id="aws-2")],
        )
        assert [t.target_id for t in result.inventory.targets] == ["static-1"]
        assert len(result.collisions) == 2

    def test_collision_names_winner_and_loser(self) -> None:
        result = build_inventory(
            [_target(target_id="static-1")],
            [_target(target_id="aws-1")],
        )
        collision = result.collisions[0]
        assert collision.host == "10.0.0.1"
        assert collision.port == 22
        assert collision.probe_type is ProbeType.TCP
        assert collision.kept_source is InventorySource.STATIC
        assert collision.kept_target_id == "static-1"
        assert collision.dropped_source is InventorySource.AWS
        assert collision.dropped_target_id == "aws-1"

    def test_partial_overlap_drops_the_whole_entry(self) -> None:
        # A discovered target sharing only one of its probe types with a static
        # target is dropped entirely: a Target is indivisible.
        result = build_inventory(
            [_target(target_id="static-1", probe_types=["tcp"])],
            [_target(target_id="aws-1", probe_types=["tcp", "http"])],
        )
        assert [t.target_id for t in result.inventory.targets] == ["static-1"]
        assert len(result.collisions) == 1
        assert result.collisions[0].probe_type is ProbeType.TCP


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailures:
    def test_failures_are_carried_through(self) -> None:
        failure = SourceFailure(source=InventorySource.AWS, reason="DescribeInstances denied")
        result = build_inventory([_target()], failures=[failure])
        assert result.failures == [failure]
        assert result.degraded is True

    def test_failed_source_still_yields_static_inventory(self) -> None:
        result = build_inventory(
            [_target(target_id="s1")],
            failures=[SourceFailure(source=InventorySource.AWS, reason="timeout")],
        )
        assert [t.target_id for t in result.inventory.targets] == ["s1"]

    def test_no_failures_means_not_degraded(self) -> None:
        assert build_inventory([_target()]).degraded is False
