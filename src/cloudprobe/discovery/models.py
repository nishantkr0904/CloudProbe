"""Pydantic v2 models for the CloudProbe discovery layer.

Discovery answers one question: *what should be probed on this run?*  It
answers it with a single canonical ``Inventory`` of ``Target`` records,
regardless of where those records came from.

``Target`` itself is owned by ``config/models.py`` and is deliberately
unaware of its own origin — the probe engine does not care how a target was
found.  Discovery therefore *wraps* targets with provenance rather than
extending them.

Model hierarchy:
    DiscoveryResult          ← what a discovery run produces
    ├── Inventory            ← the canonical target set
    │   └── list[InventoryEntry]
    │       ├── Target       ← owned by config/
    │       └── InventorySource
    ├── list[SourceFailure]  ← non-fatal per-source failures
    └── list[TargetCollision]← targets displaced by precedence
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from cloudprobe.config.models import ProbeType, Target

# A target's identity for deduplication purposes.  Two entries that resolve to
# the same (host, port, probe_type) triple are the same probe, whatever their
# target_id says.
TargetKey = tuple[str, int | None, ProbeType]


class InventorySource(str, Enum):
    """Where an inventory entry came from.  String-valued for report output."""

    STATIC = "static"
    AWS = "aws"


class InventoryEntry(BaseModel):
    """A single target paired with the source that contributed it."""

    model_config = ConfigDict(frozen=True)

    target: Target
    source: InventorySource

    def keys(self) -> tuple[TargetKey, ...]:
        """Return one identity key per probe type declared on the target."""
        return tuple(
            (self.target.host, self.target.port, probe_type)
            for probe_type in self.target.probe_types
        )


class TargetCollision(BaseModel):
    """Record of an entry dropped because a higher-precedence source won.

    Collisions are data rather than log lines so that the reporting layer can
    surface them and unit tests can assert on them.
    """

    model_config = ConfigDict(frozen=True)

    host: str
    port: int | None
    probe_type: ProbeType
    kept_source: InventorySource
    kept_target_id: str
    dropped_source: InventorySource
    dropped_target_id: str


class SourceFailure(BaseModel):
    """A source that could not be enumerated.

    Discovery failures never abort a run: the inventory degrades to whatever
    sources did succeed and the failure travels to the report as data.
    """

    model_config = ConfigDict(frozen=True)

    source: InventorySource
    reason: str = Field(min_length=1)


class Inventory(BaseModel):
    """The canonical set of targets for one run, with provenance retained."""

    model_config = ConfigDict(frozen=True)

    entries: list[InventoryEntry] = Field(default_factory=list)

    @property
    def targets(self) -> list[Target]:
        """Targets in inventory order, stripped of provenance."""
        return [entry.target for entry in self.entries]

    def by_source(self, source: InventorySource) -> list[Target]:
        """Targets contributed by a single source."""
        return [entry.target for entry in self.entries if entry.source is source]

    def source_counts(self) -> dict[InventorySource, int]:
        """Entry count per source, for the report's static-vs-dynamic split."""
        counts = dict.fromkeys(InventorySource, 0)
        for entry in self.entries:
            counts[entry.source] += 1
        return counts


class DiscoveryResult(BaseModel):
    """Everything one discovery run produced, including what went wrong."""

    model_config = ConfigDict(frozen=True)

    inventory: Inventory
    failures: list[SourceFailure] = Field(default_factory=list)
    collisions: list[TargetCollision] = Field(default_factory=list)

    @property
    def degraded(self) -> bool:
        """True when at least one source failed and was skipped."""
        return bool(self.failures)
