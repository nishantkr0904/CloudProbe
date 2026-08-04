"""Unit tests for CloudProbe discovery models.

Covers:
    - InventorySource values
    - InventoryEntry identity keys (one per probe type)
    - Inventory target/provenance accessors
    - source_counts static-vs-dynamic split
    - SourceFailure / TargetCollision validation
    - DiscoveryResult.degraded
    - immutability
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cloudprobe.config.models import ProbeType, Target
from cloudprobe.discovery.models import (
    DiscoveryResult,
    Inventory,
    InventoryEntry,
    InventorySource,
    SourceFailure,
    TargetCollision,
)

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


def _entry(source: InventorySource = InventorySource.STATIC, **overrides: object) -> InventoryEntry:
    return InventoryEntry(target=_target(**overrides), source=source)


# ---------------------------------------------------------------------------
# InventorySource
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInventorySource:
    def test_values_are_report_friendly_strings(self) -> None:
        assert InventorySource.STATIC.value == "static"
        assert InventorySource.AWS.value == "aws"

    def test_only_two_sources_defined(self) -> None:
        assert set(InventorySource) == {InventorySource.STATIC, InventorySource.AWS}

    def test_unknown_source_rejected(self) -> None:
        with pytest.raises(ValidationError):
            InventoryEntry(target=_target(), source="gcp")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# InventoryEntry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInventoryEntry:
    def test_single_probe_type_yields_one_key(self) -> None:
        entry = _entry()
        assert entry.keys() == (("10.0.0.1", 22, ProbeType.TCP),)

    def test_key_per_probe_type(self) -> None:
        entry = _entry(probe_types=["tcp", "http"])
        assert entry.keys() == (
            ("10.0.0.1", 22, ProbeType.TCP),
            ("10.0.0.1", 22, ProbeType.HTTP),
        )

    def test_unset_port_is_part_of_the_key(self) -> None:
        entry = _entry(port=None, probe_types=["icmp"])
        assert entry.keys() == (("10.0.0.1", None, ProbeType.ICMP),)

    def test_target_id_is_not_part_of_identity(self) -> None:
        a = _entry(target_id="a")
        b = _entry(target_id="b")
        assert a.keys() == b.keys()


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInventory:
    def test_empty_inventory_is_valid(self) -> None:
        inv = Inventory()
        assert inv.targets == []
        assert inv.source_counts() == {InventorySource.STATIC: 0, InventorySource.AWS: 0}

    def test_targets_preserves_order_and_strips_provenance(self) -> None:
        inv = Inventory(
            entries=[
                _entry(target_id="a"),
                _entry(InventorySource.AWS, target_id="b"),
            ]
        )
        assert [t.target_id for t in inv.targets] == ["a", "b"]

    def test_by_source_filters(self) -> None:
        inv = Inventory(
            entries=[
                _entry(target_id="s1"),
                _entry(InventorySource.AWS, target_id="a1"),
                _entry(target_id="s2"),
            ]
        )
        assert [t.target_id for t in inv.by_source(InventorySource.STATIC)] == ["s1", "s2"]
        assert [t.target_id for t in inv.by_source(InventorySource.AWS)] == ["a1"]

    def test_source_counts_gives_static_vs_dynamic_split(self) -> None:
        inv = Inventory(
            entries=[
                _entry(target_id="s1"),
                _entry(target_id="s2"),
                _entry(InventorySource.AWS, target_id="a1"),
            ]
        )
        assert inv.source_counts() == {InventorySource.STATIC: 2, InventorySource.AWS: 1}


# ---------------------------------------------------------------------------
# SourceFailure / TargetCollision
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailureAndCollision:
    def test_source_failure_requires_reason(self) -> None:
        with pytest.raises(ValidationError):
            SourceFailure(source=InventorySource.AWS, reason="")

    def test_source_failure_round_trip(self) -> None:
        f = SourceFailure(source=InventorySource.AWS, reason="DescribeInstances denied")
        assert f.source is InventorySource.AWS
        assert "denied" in f.reason

    def test_collision_records_both_sides(self) -> None:
        c = TargetCollision(
            host="10.0.0.1",
            port=22,
            probe_type=ProbeType.TCP,
            kept_source=InventorySource.STATIC,
            kept_target_id="static-1",
            dropped_source=InventorySource.AWS,
            dropped_target_id="aws-1",
        )
        assert c.kept_target_id == "static-1"
        assert c.dropped_target_id == "aws-1"


# ---------------------------------------------------------------------------
# DiscoveryResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiscoveryResult:
    def test_defaults_are_empty(self) -> None:
        result = DiscoveryResult(inventory=Inventory())
        assert result.failures == []
        assert result.collisions == []

    def test_not_degraded_without_failures(self) -> None:
        assert DiscoveryResult(inventory=Inventory()).degraded is False

    def test_degraded_when_a_source_failed(self) -> None:
        result = DiscoveryResult(
            inventory=Inventory(),
            failures=[SourceFailure(source=InventorySource.AWS, reason="timeout")],
        )
        assert result.degraded is True

    def test_inventory_is_required(self) -> None:
        with pytest.raises(ValidationError, match="inventory"):
            DiscoveryResult()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImmutability:
    def test_entry_is_frozen(self) -> None:
        entry = _entry()
        with pytest.raises(ValidationError):
            entry.source = InventorySource.AWS  # type: ignore[misc]

    def test_inventory_is_frozen(self) -> None:
        inv = Inventory()
        with pytest.raises(ValidationError):
            inv.entries = []  # type: ignore[misc]

    def test_result_is_frozen(self) -> None:
        result = DiscoveryResult(inventory=Inventory())
        with pytest.raises(ValidationError):
            result.failures = []  # type: ignore[misc]
