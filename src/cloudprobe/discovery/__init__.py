"""CloudProbe discovery layer — public surface.

Every other layer of the pipeline imports from this module, never from the
internal submodules directly.  The layer owns:

* Typed models describing a discovered inventory and its provenance
  (InventorySource, InventoryEntry, Inventory, DiscoveryResult, plus the
  TargetCollision and SourceFailure records a run can produce).
* Canonical inventory construction, which merges every source into one target
  set with static definitions taking precedence over discovered ones.
* EC2-backed discovery, which turns ``DescribeInstances`` responses into
  targets using a caller-supplied client.
* VPC inventory collection, which gathers the network topology (VPCs, subnets,
  route tables) that gives a target's AWS identifiers their meaning.

Discovery consumes and emits ``Target`` records owned by ``config/`` — it does
not define a target shape of its own.
"""

from cloudprobe.discovery.ec2 import EC2Client, discover_ec2_targets
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
from cloudprobe.discovery.vpc import (
    NetworkTopology,
    Route,
    RouteTableMetadata,
    SubnetMetadata,
    VpcMetadata,
    collect_network_topology,
)

__all__ = [
    "DiscoveryResult",
    "EC2Client",
    "Inventory",
    "InventoryEntry",
    "InventorySource",
    "NetworkTopology",
    "Route",
    "RouteTableMetadata",
    "SourceFailure",
    "SubnetMetadata",
    "TargetCollision",
    "TargetKey",
    "VpcMetadata",
    "build_inventory",
    "collect_network_topology",
    "discover_ec2_targets",
]
