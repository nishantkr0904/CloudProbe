"""VPC inventory collection — network topology metadata.

One responsibility: call ``DescribeVpcs``, ``DescribeSubnets`` and
``DescribeRouteTables`` and represent the results as typed metadata.

Why this exists separately from ``ec2.py``: an instance description carries only
the *identifiers* ``VpcId`` and ``SubnetId``.  Metrics dimension on those, and
the run report summarizes "unique VPCs/subnets", but neither can say what a
subnet's CIDR or availability zone is.  This module supplies that context, keyed
so a caller can look it up per target.

What this module deliberately does **not** do:

* Create an AWS client.  The client is injected, as in ``ec2.py``, so this is
  unit-testable against a fake with no SDK, credentials or network.
* Discover instances or produce ``Target`` records.  ``ec2.py`` owns that.
* Merge, deduplicate or apply precedence.  ``inventory.build_inventory`` owns
  canonicalization, and topology metadata is not part of the target set.
* Attach itself to targets.  It returns a lookup structure; the caller decides
  which dimensions a given target needs.
* Infer whether a subnet is "public".  That conclusion needs internet-gateway
  and NAT semantics, which are out of this module's scope, so routes are
  reported as AWS states them and interpretation is left to the consumer.
* Swallow AWS errors.  A failure propagates, letting the caller downgrade the
  run and report the failure as data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cloudprobe.discovery.ec2 import EC2Client

# The AWS tag whose value is conventionally a resource's display name.
_NAME_TAG = "Name"


class Route(BaseModel):
    """A single entry in a route table.

    Only the destination and the resolved next hop are kept: enough to explain
    which paths a subnet has, without modelling gateway resources this layer
    does not discover.
    """

    model_config = ConfigDict(frozen=True)

    destination_cidr_block: str | None = None
    gateway_id: str | None = None
    state: str | None = None


class VpcMetadata(BaseModel):
    """A VPC as reported by ``DescribeVpcs``."""

    model_config = ConfigDict(frozen=True)

    vpc_id: str
    cidr_block: str | None = None
    is_default: bool = False
    label: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class SubnetMetadata(BaseModel):
    """A subnet as reported by ``DescribeSubnets``.

    ``availability_zone`` is the field that makes a cross-AZ reachability
    signal interpretable: without it, two subnet IDs are indistinguishable.
    """

    model_config = ConfigDict(frozen=True)

    subnet_id: str
    vpc_id: str | None = None
    cidr_block: str | None = None
    availability_zone: str | None = None
    label: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class RouteTableMetadata(BaseModel):
    """A route table, its routes, and the subnets explicitly associated with it.

    ``is_main`` matters because a subnet with no explicit association uses its
    VPC's main table — the association is implicit in the API response and has
    to be resolved by the reader.
    """

    model_config = ConfigDict(frozen=True)

    route_table_id: str
    vpc_id: str | None = None
    is_main: bool = False
    subnet_ids: list[str] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)


class NetworkTopology(BaseModel):
    """Everything this module collected, indexed for per-target lookup."""

    model_config = ConfigDict(frozen=True)

    vpcs: list[VpcMetadata] = Field(default_factory=list)
    subnets: list[SubnetMetadata] = Field(default_factory=list)
    route_tables: list[RouteTableMetadata] = Field(default_factory=list)

    def vpc(self, vpc_id: str | None) -> VpcMetadata | None:
        """Return the VPC with this ID, or ``None`` if it was not collected."""
        return next((vpc for vpc in self.vpcs if vpc.vpc_id == vpc_id), None)

    def subnet(self, subnet_id: str | None) -> SubnetMetadata | None:
        """Return the subnet with this ID, or ``None`` if it was not collected."""
        return next((subnet for subnet in self.subnets if subnet.subnet_id == subnet_id), None)

    def route_table_for_subnet(self, subnet_id: str | None) -> RouteTableMetadata | None:
        """Return the route table governing this subnet.

        Resolves AWS's implicit association: an explicitly associated table
        wins, otherwise the subnet falls back to the main table of its VPC.
        Returns ``None`` when neither is known.
        """
        explicit = next(
            (table for table in self.route_tables if subnet_id in table.subnet_ids), None
        )
        if explicit is not None:
            return explicit

        subnet = self.subnet(subnet_id)
        if subnet is None:
            return None
        return next(
            (
                table
                for table in self.route_tables
                if table.is_main and table.vpc_id == subnet.vpc_id
            ),
            None,
        )


def collect_network_topology(
    ec2_client: EC2Client, vpc_ids: Sequence[str] | None = None
) -> NetworkTopology:
    """Return network metadata for every VPC matching the filter.

    Args:
        ec2_client: An object satisfying ``EC2Client`` — in production a Boto3
            EC2 client supplied by the caller.
        vpc_ids: Optional VPC identifier selector.  When ``None``, all VPCs in
            the region are collected.  When a sequence, only those VPCs,
            their subnets, and their route tables are returned.

    Returns:
        The discovered topology in API response order.  Subnets and route
        tables are filtered to those belonging to the collected VPC set, so
        a caller asking for one VPC receives only its resources.

    Raises:
        botocore.exceptions.ClientError: Propagated unchanged when AWS rejects
            or cannot serve the request.
    """
    filters = [{"Name": "vpc-id", "Values": list(vpc_ids)}] if vpc_ids else []

    vpcs = _collect_vpcs(ec2_client, filters)
    vpc_id_set = {vpc.vpc_id for vpc in vpcs}

    # DescribeSubnets also returns subnets shared into this account, whose VPC
    # DescribeVpcs never reports.  Dropping them keeps every subnet resolvable
    # back to a collected VPC.
    subnets = _collect_subnets(ec2_client, filters)
    subnets = [subnet for subnet in subnets if subnet.vpc_id in vpc_id_set]

    route_tables = _collect_route_tables(ec2_client, filters)
    route_tables = [table for table in route_tables if table.vpc_id in vpc_id_set]

    return NetworkTopology(vpcs=vpcs, subnets=subnets, route_tables=route_tables)


def _collect_vpcs(ec2_client: EC2Client, filters: list[dict[str, Any]]) -> list[VpcMetadata]:
    paginator = ec2_client.get_paginator("describe_vpcs")
    vpcs: list[VpcMetadata] = []
    for page in paginator.paginate(Filters=filters):
        for vpc in page.get("Vpcs", ()):
            vpc_id = vpc.get("VpcId")
            if not vpc_id:
                continue
            tags = _tags(vpc)
            vpcs.append(
                VpcMetadata(
                    vpc_id=vpc_id,
                    cidr_block=vpc.get("CidrBlock"),
                    is_default=vpc.get("IsDefault", False),
                    label=tags.get(_NAME_TAG),
                    tags=tags,
                )
            )
    return vpcs


def _collect_subnets(ec2_client: EC2Client, filters: list[dict[str, Any]]) -> list[SubnetMetadata]:
    paginator = ec2_client.get_paginator("describe_subnets")
    subnets: list[SubnetMetadata] = []
    for page in paginator.paginate(Filters=filters):
        for subnet in page.get("Subnets", ()):
            subnet_id = subnet.get("SubnetId")
            if not subnet_id:
                continue
            tags = _tags(subnet)
            subnets.append(
                SubnetMetadata(
                    subnet_id=subnet_id,
                    vpc_id=subnet.get("VpcId"),
                    cidr_block=subnet.get("CidrBlock"),
                    availability_zone=subnet.get("AvailabilityZone"),
                    label=tags.get(_NAME_TAG),
                    tags=tags,
                )
            )
    return subnets


def _collect_route_tables(
    ec2_client: EC2Client, filters: list[dict[str, Any]]
) -> list[RouteTableMetadata]:
    paginator = ec2_client.get_paginator("describe_route_tables")
    route_tables: list[RouteTableMetadata] = []
    for page in paginator.paginate(Filters=filters):
        for table in page.get("RouteTables", ()):
            route_table_id = table.get("RouteTableId")
            if not route_table_id:
                continue

            associations = table.get("Associations", ())
            is_main = any(assoc.get("Main", False) for assoc in associations)
            subnet_ids = [assoc["SubnetId"] for assoc in associations if assoc.get("SubnetId")]

            routes = [
                Route(
                    destination_cidr_block=route.get("DestinationCidrBlock"),
                    gateway_id=route.get("GatewayId"),
                    state=route.get("State"),
                )
                for route in table.get("Routes", ())
            ]

            route_tables.append(
                RouteTableMetadata(
                    route_table_id=route_table_id,
                    vpc_id=table.get("VpcId"),
                    is_main=is_main,
                    subnet_ids=subnet_ids,
                    routes=routes,
                )
            )
    return route_tables


def _tags(resource: Mapping[str, Any]) -> dict[str, str]:
    """Flatten AWS's ``[{"Key": k, "Value": v}]`` tag shape into a mapping."""
    return {tag["Key"]: tag.get("Value", "") for tag in resource.get("Tags", ()) if tag.get("Key")}
