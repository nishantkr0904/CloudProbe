"""CloudProbe discovery layer — public surface.

Every other layer of the pipeline imports from this module, never from the
internal submodules directly.  The layer owns:

* Typed models describing a discovered inventory and its provenance
  (InventorySource, InventoryEntry, Inventory, DiscoveryResult, plus the
  TargetCollision and SourceFailure records a run can produce).
* Canonical inventory construction, which merges every source into one target
  set with static definitions taking precedence over discovered ones.

Discovery consumes and emits ``Target`` records owned by ``config/`` — it does
not define a target shape of its own.
"""

from cloudprobe.discovery.inventory import build_inventory
from cloudprobe.discovery.models import (
    DiscoveryResult,
    Inventory,
    InventoryEntry,
    InventorySource,
    SourceFailure,
    TargetCollision,
    TargetKey,
)

__all__ = [
    "DiscoveryResult",
    "Inventory",
    "InventoryEntry",
    "InventorySource",
    "SourceFailure",
    "TargetCollision",
    "TargetKey",
    "build_inventory",
]
