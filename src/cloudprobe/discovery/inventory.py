"""Canonical inventory construction.

One responsibility: given targets from every source, produce a single
``DiscoveryResult`` with duplicates resolved by source precedence.

This module is pure.  It performs no discovery, opens no sockets and knows
nothing about AWS — callers hand it the targets each source produced.  The
AWS-backed producer arrives in a later phase and feeds this same function.

Precedence: **static wins on collision.**  If a static target and a
discovered target resolve to the same ``(host, port, probe_type)`` triple, the
operator's explicit statement outranks discovery, and the displaced entry is
recorded as a ``TargetCollision``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from cloudprobe.config.models import Target
from cloudprobe.discovery.models import (
    DiscoveryResult,
    Inventory,
    InventoryEntry,
    InventorySource,
    SourceFailure,
    TargetCollision,
    TargetKey,
)

# Highest precedence first.  Entries from an earlier source displace later ones.
_SOURCE_PRECEDENCE: tuple[InventorySource, ...] = (
    InventorySource.STATIC,
    InventorySource.AWS,
)


def build_inventory(
    static_targets: Iterable[Target],
    discovered_targets: Iterable[Target] = (),
    failures: Sequence[SourceFailure] = (),
) -> DiscoveryResult:
    """Merge every source into one canonical inventory.

    Args:
        static_targets: Targets declared in the validated configuration.
        discovered_targets: Targets contributed by AWS discovery. Empty until
            the AWS producer lands.
        failures: Sources that could not be enumerated on this run.

    Returns:
        A ``DiscoveryResult`` whose inventory contains each distinct
        ``(host, port, probe_type)`` triple exactly once.
    """
    candidates = [
        *(InventoryEntry(target=t, source=InventorySource.STATIC) for t in static_targets),
        *(InventoryEntry(target=t, source=InventorySource.AWS) for t in discovered_targets),
    ]
    candidates.sort(key=lambda entry: _SOURCE_PRECEDENCE.index(entry.source))

    claimed: dict[TargetKey, InventoryEntry] = {}
    entries: list[InventoryEntry] = []
    collisions: list[TargetCollision] = []

    for entry in candidates:
        conflicts = [(key, claimed[key]) for key in entry.keys() if key in claimed]
        if conflicts:
            collisions.extend(
                _collision(key, kept=winner, dropped=entry) for key, winner in conflicts
            )
            continue
        for key in entry.keys():
            claimed[key] = entry
        entries.append(entry)

    return DiscoveryResult(
        inventory=Inventory(entries=entries),
        failures=list(failures),
        collisions=collisions,
    )


def _collision(key: TargetKey, kept: InventoryEntry, dropped: InventoryEntry) -> TargetCollision:
    host, port, probe_type = key
    return TargetCollision(
        host=host,
        port=port,
        probe_type=probe_type,
        kept_source=kept.source,
        kept_target_id=kept.target.target_id,
        dropped_source=dropped.source,
        dropped_target_id=dropped.target.target_id,
    )
