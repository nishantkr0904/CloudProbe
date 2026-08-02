"""Unit tests for VPC inventory collection.

Hand-written fakes exercise the real code path with no SDK, no credentials, and
no network — the same strategy as ``test_ec2.py``.

Covers:
    - DescribeVpcs/Subnets/RouteTables response shapes → metadata mapping
    - pagination across multiple pages
    - VPC filter sent to the API
    - tags flattened and Name becomes label
    - route table association (explicit and implicit main)
    - topology accessors (vpc, subnet, route_table_for_subnet)
    - resources without required IDs are skipped
    - AWS errors propagating to the caller
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import pytest
from pydantic import ValidationError

from cloudprobe.discovery.vpc import (
    NetworkTopology,
    Route,
    RouteTableMetadata,
    SubnetMetadata,
    VpcMetadata,
    collect_network_topology,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakePaginator:
    def __init__(self, pages: Sequence[Mapping[str, Any]], error: Exception | None) -> None:
        self._pages = pages
        self._error = error
        self.paginate_kwargs: dict[str, Any] | None = None

    def paginate(self, **kwargs: Any) -> Iterator[Mapping[str, Any]]:
        self.paginate_kwargs = kwargs
        if self._error is not None:
            raise self._error
        yield from self._pages


class _FakeEC2Client:
    """Records how it was called so tests can assert on the request."""

    def __init__(
        self,
        responses: dict[str, list[Mapping[str, Any]]] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._errors = errors or {}
        self.paginators: dict[str, _FakePaginator] = {}

    def get_paginator(self, operation_name: str) -> _FakePaginator:
        paginator = _FakePaginator(
            self._responses.get(operation_name, []), self._errors.get(operation_name)
        )
        self.paginators[operation_name] = paginator
        return paginator


def _vpc(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "VpcId": "vpc-123",
        "CidrBlock": "10.20.0.0/16",
        "IsDefault": False,
        "Tags": [{"Key": "Name", "Value": "lab"}],
    }
    base.update(overrides)
    return base


def _subnet(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "SubnetId": "subnet-abc",
        "VpcId": "vpc-123",
        "CidrBlock": "10.20.1.0/24",
        "AvailabilityZone": "us-east-1a",
        "Tags": [{"Key": "Name", "Value": "lab-public-1"}],
    }
    base.update(overrides)
    return base


def _route_table(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "RouteTableId": "rtb-xyz",
        "VpcId": "vpc-123",
        "Associations": [],
        "Routes": [
            {"DestinationCidrBlock": "10.20.0.0/16", "GatewayId": "local", "State": "active"}
        ],
    }
    base.update(overrides)
    return base


def _route(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "DestinationCidrBlock": "0.0.0.0/0",
        "GatewayId": "igw-123",
        "State": "active",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# VPC mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVpcMapping:
    def test_vpc_id_and_cidr_are_carried_over(self) -> None:
        client = _FakeEC2Client({"describe_vpcs": [{"Vpcs": [_vpc()]}]})
        topology = collect_network_topology(client)
        assert len(topology.vpcs) == 1
        assert topology.vpcs[0].vpc_id == "vpc-123"
        assert topology.vpcs[0].cidr_block == "10.20.0.0/16"

    def test_is_default_becomes_boolean(self) -> None:
        client = _FakeEC2Client({"describe_vpcs": [{"Vpcs": [_vpc(IsDefault=True)]}]})
        topology = collect_network_topology(client)
        assert topology.vpcs[0].is_default is True

    def test_name_tag_becomes_label(self) -> None:
        client = _FakeEC2Client({"describe_vpcs": [{"Vpcs": [_vpc()]}]})
        topology = collect_network_topology(client)
        assert topology.vpcs[0].label == "lab"

    def test_missing_name_tag_leaves_label_unset(self) -> None:
        client = _FakeEC2Client({"describe_vpcs": [{"Vpcs": [_vpc(Tags=[])]}]})
        topology = collect_network_topology(client)
        assert topology.vpcs[0].label is None

    def test_tags_are_flattened(self) -> None:
        vpc = _vpc(Tags=[{"Key": "Environment", "Value": "lab"}, {"Key": "tier", "Value": "net"}])
        client = _FakeEC2Client({"describe_vpcs": [{"Vpcs": [vpc]}]})
        topology = collect_network_topology(client)
        assert topology.vpcs[0].tags == {"Environment": "lab", "tier": "net"}

    def test_untagged_vpc_yields_empty_tags(self) -> None:
        vpc = _vpc()
        del vpc["Tags"]
        client = _FakeEC2Client({"describe_vpcs": [{"Vpcs": [vpc]}]})
        topology = collect_network_topology(client)
        assert topology.vpcs[0].tags == {}

    def test_vpc_without_vpc_id_is_skipped(self) -> None:
        vpc = _vpc()
        del vpc["VpcId"]
        client = _FakeEC2Client({"describe_vpcs": [{"Vpcs": [vpc]}]})
        topology = collect_network_topology(client)
        assert topology.vpcs == []


# ---------------------------------------------------------------------------
# Subnet mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSubnetMapping:
    def test_subnet_id_vpc_id_cidr_and_az_are_carried_over(self) -> None:
        client = _FakeEC2Client(
            {"describe_vpcs": [{"Vpcs": [_vpc()]}], "describe_subnets": [{"Subnets": [_subnet()]}]}
        )
        topology = collect_network_topology(client)
        assert len(topology.subnets) == 1
        assert topology.subnets[0].subnet_id == "subnet-abc"
        assert topology.subnets[0].vpc_id == "vpc-123"
        assert topology.subnets[0].cidr_block == "10.20.1.0/24"
        assert topology.subnets[0].availability_zone == "us-east-1a"

    def test_name_tag_becomes_label(self) -> None:
        client = _FakeEC2Client(
            {"describe_vpcs": [{"Vpcs": [_vpc()]}], "describe_subnets": [{"Subnets": [_subnet()]}]}
        )
        topology = collect_network_topology(client)
        assert topology.subnets[0].label == "lab-public-1"

    def test_tags_are_flattened(self) -> None:
        subnet = _subnet(Tags=[{"Key": "tier", "Value": "public"}])
        client = _FakeEC2Client(
            {"describe_vpcs": [{"Vpcs": [_vpc()]}], "describe_subnets": [{"Subnets": [subnet]}]}
        )
        topology = collect_network_topology(client)
        assert topology.subnets[0].tags == {"tier": "public"}

    def test_subnet_without_subnet_id_is_skipped(self) -> None:
        subnet = _subnet()
        del subnet["SubnetId"]
        client = _FakeEC2Client(
            {"describe_vpcs": [{"Vpcs": [_vpc()]}], "describe_subnets": [{"Subnets": [subnet]}]}
        )
        topology = collect_network_topology(client)
        assert topology.subnets == []


# ---------------------------------------------------------------------------
# Route table mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRouteTableMapping:
    def test_route_table_id_and_vpc_id_are_carried_over(self) -> None:
        client = _FakeEC2Client(
            {
                "describe_vpcs": [{"Vpcs": [_vpc()]}],
                "describe_route_tables": [{"RouteTables": [_route_table()]}],
            }
        )
        topology = collect_network_topology(client)
        assert len(topology.route_tables) == 1
        assert topology.route_tables[0].route_table_id == "rtb-xyz"
        assert topology.route_tables[0].vpc_id == "vpc-123"

    def test_main_association_sets_is_main(self) -> None:
        table = _route_table(Associations=[{"Main": True}])
        client = _FakeEC2Client(
            {
                "describe_vpcs": [{"Vpcs": [_vpc()]}],
                "describe_route_tables": [{"RouteTables": [table]}],
            }
        )
        topology = collect_network_topology(client)
        assert topology.route_tables[0].is_main is True

    def test_subnet_associations_are_collected(self) -> None:
        table = _route_table(Associations=[{"SubnetId": "subnet-a"}, {"SubnetId": "subnet-b"}])
        client = _FakeEC2Client(
            {
                "describe_vpcs": [{"Vpcs": [_vpc()]}],
                "describe_route_tables": [{"RouteTables": [table]}],
            }
        )
        topology = collect_network_topology(client)
        assert topology.route_tables[0].subnet_ids == ["subnet-a", "subnet-b"]

    def test_routes_are_mapped(self) -> None:
        table = _route_table(
            Routes=[_route(), {"DestinationCidrBlock": "10.0.0.0/8", "GatewayId": "local"}]
        )
        client = _FakeEC2Client(
            {
                "describe_vpcs": [{"Vpcs": [_vpc()]}],
                "describe_route_tables": [{"RouteTables": [table]}],
            }
        )
        topology = collect_network_topology(client)
        assert len(topology.route_tables[0].routes) == 2
        assert topology.route_tables[0].routes[0].destination_cidr_block == "0.0.0.0/0"
        assert topology.route_tables[0].routes[0].gateway_id == "igw-123"

    def test_route_table_without_route_table_id_is_skipped(self) -> None:
        table = _route_table()
        del table["RouteTableId"]
        client = _FakeEC2Client(
            {
                "describe_vpcs": [{"Vpcs": [_vpc()]}],
                "describe_route_tables": [{"RouteTables": [table]}],
            }
        )
        topology = collect_network_topology(client)
        assert topology.route_tables == []


# ---------------------------------------------------------------------------
# Pagination and filters
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPaginationAndFilters:
    def test_no_resources_yields_empty_topology(self) -> None:
        client = _FakeEC2Client(
            {
                "describe_vpcs": [{"Vpcs": []}],
                "describe_subnets": [{"Subnets": []}],
                "describe_route_tables": [{"RouteTables": []}],
            }
        )
        topology = collect_network_topology(client)
        assert topology.vpcs == []
        assert topology.subnets == []
        assert topology.route_tables == []

    def test_resources_collected_across_pages(self) -> None:
        client = _FakeEC2Client(
            {
                "describe_vpcs": [
                    {"Vpcs": [_vpc(VpcId="vpc-1")]},
                    {"Vpcs": [_vpc(VpcId="vpc-2")]},
                ]
            }
        )
        topology = collect_network_topology(client)
        assert [vpc.vpc_id for vpc in topology.vpcs] == ["vpc-1", "vpc-2"]

    def test_vpc_id_filter_sent_when_provided(self) -> None:
        client = _FakeEC2Client({"describe_vpcs": [{"Vpcs": [_vpc()]}]})
        collect_network_topology(client, vpc_ids=["vpc-123", "vpc-456"])
        assert client.paginators["describe_vpcs"].paginate_kwargs == {
            "Filters": [{"Name": "vpc-id", "Values": ["vpc-123", "vpc-456"]}]
        }

    def test_vpc_id_filter_applied_to_every_describe(self) -> None:
        client = _FakeEC2Client({"describe_vpcs": [{"Vpcs": [_vpc()]}]})
        collect_network_topology(client, vpc_ids=["vpc-123"])
        expected = {"Filters": [{"Name": "vpc-id", "Values": ["vpc-123"]}]}
        assert client.paginators["describe_subnets"].paginate_kwargs == expected
        assert client.paginators["describe_route_tables"].paginate_kwargs == expected

    def test_no_filter_when_vpc_ids_is_none(self) -> None:
        client = _FakeEC2Client({"describe_vpcs": [{"Vpcs": [_vpc()]}]})
        collect_network_topology(client, vpc_ids=None)
        assert client.paginators["describe_vpcs"].paginate_kwargs == {"Filters": []}

    def test_subnets_filtered_to_collected_vpcs(self) -> None:
        client = _FakeEC2Client(
            {
                "describe_vpcs": [{"Vpcs": [_vpc(VpcId="vpc-123")]}],
                "describe_subnets": [
                    {
                        "Subnets": [
                            _subnet(SubnetId="subnet-a", VpcId="vpc-123"),
                            _subnet(SubnetId="subnet-b", VpcId="vpc-999"),
                        ]
                    }
                ],
            }
        )
        topology = collect_network_topology(client)
        assert [subnet.subnet_id for subnet in topology.subnets] == ["subnet-a"]

    def test_route_tables_filtered_to_collected_vpcs(self) -> None:
        client = _FakeEC2Client(
            {
                "describe_vpcs": [{"Vpcs": [_vpc(VpcId="vpc-123")]}],
                "describe_route_tables": [
                    {
                        "RouteTables": [
                            _route_table(RouteTableId="rtb-a", VpcId="vpc-123"),
                            _route_table(RouteTableId="rtb-b", VpcId="vpc-999"),
                        ]
                    }
                ],
            }
        )
        topology = collect_network_topology(client)
        assert [table.route_table_id for table in topology.route_tables] == ["rtb-a"]


# ---------------------------------------------------------------------------
# Topology accessors
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTopologyAccessors:
    def test_vpc_lookup_by_id(self) -> None:
        topology = NetworkTopology(vpcs=[VpcMetadata(vpc_id="vpc-123", cidr_block="10.0.0.0/16")])
        assert topology.vpc("vpc-123") is not None
        assert topology.vpc("vpc-123").vpc_id == "vpc-123"

    def test_vpc_lookup_returns_none_when_not_found(self) -> None:
        topology = NetworkTopology(vpcs=[VpcMetadata(vpc_id="vpc-123")])
        assert topology.vpc("vpc-999") is None

    def test_subnet_lookup_by_id(self) -> None:
        topology = NetworkTopology(
            subnets=[SubnetMetadata(subnet_id="subnet-abc", vpc_id="vpc-123")]
        )
        assert topology.subnet("subnet-abc") is not None
        assert topology.subnet("subnet-abc").subnet_id == "subnet-abc"

    def test_subnet_lookup_returns_none_when_not_found(self) -> None:
        topology = NetworkTopology(subnets=[SubnetMetadata(subnet_id="subnet-abc")])
        assert topology.subnet("subnet-999") is None

    def test_route_table_for_subnet_explicit_association(self) -> None:
        topology = NetworkTopology(
            subnets=[SubnetMetadata(subnet_id="subnet-abc", vpc_id="vpc-123")],
            route_tables=[
                RouteTableMetadata(
                    route_table_id="rtb-explicit", vpc_id="vpc-123", subnet_ids=["subnet-abc"]
                )
            ],
        )
        table = topology.route_table_for_subnet("subnet-abc")
        assert table is not None
        assert table.route_table_id == "rtb-explicit"

    def test_route_table_for_subnet_implicit_main(self) -> None:
        topology = NetworkTopology(
            subnets=[SubnetMetadata(subnet_id="subnet-abc", vpc_id="vpc-123")],
            route_tables=[
                RouteTableMetadata(route_table_id="rtb-main", vpc_id="vpc-123", is_main=True)
            ],
        )
        table = topology.route_table_for_subnet("subnet-abc")
        assert table is not None
        assert table.route_table_id == "rtb-main"

    def test_route_table_for_subnet_explicit_wins_over_main(self) -> None:
        topology = NetworkTopology(
            subnets=[SubnetMetadata(subnet_id="subnet-abc", vpc_id="vpc-123")],
            route_tables=[
                RouteTableMetadata(route_table_id="rtb-main", vpc_id="vpc-123", is_main=True),
                RouteTableMetadata(
                    route_table_id="rtb-explicit", vpc_id="vpc-123", subnet_ids=["subnet-abc"]
                ),
            ],
        )
        table = topology.route_table_for_subnet("subnet-abc")
        assert table.route_table_id == "rtb-explicit"

    def test_route_table_for_subnet_returns_none_when_subnet_unknown(self) -> None:
        topology = NetworkTopology(
            route_tables=[
                RouteTableMetadata(route_table_id="rtb-main", vpc_id="vpc-123", is_main=True)
            ]
        )
        assert topology.route_table_for_subnet("subnet-999") is None

    def test_route_table_for_subnet_returns_none_when_no_table_matches(self) -> None:
        topology = NetworkTopology(
            subnets=[SubnetMetadata(subnet_id="subnet-abc", vpc_id="vpc-123")],
            route_tables=[
                RouteTableMetadata(route_table_id="rtb-other", vpc_id="vpc-999", is_main=True)
            ],
        )
        assert topology.route_table_for_subnet("subnet-abc") is None


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImmutability:
    def test_vpc_metadata_is_frozen(self) -> None:
        vpc = VpcMetadata(vpc_id="vpc-123")
        with pytest.raises(ValidationError):
            vpc.vpc_id = "vpc-456"  # type: ignore[misc]

    def test_subnet_metadata_is_frozen(self) -> None:
        subnet = SubnetMetadata(subnet_id="subnet-abc")
        with pytest.raises(ValidationError):
            subnet.subnet_id = "subnet-xyz"  # type: ignore[misc]

    def test_route_table_metadata_is_frozen(self) -> None:
        table = RouteTableMetadata(route_table_id="rtb-xyz")
        with pytest.raises(ValidationError):
            table.route_table_id = "rtb-123"  # type: ignore[misc]

    def test_route_is_frozen(self) -> None:
        route = Route(destination_cidr_block="0.0.0.0/0")
        with pytest.raises(ValidationError):
            route.destination_cidr_block = "10.0.0.0/8"  # type: ignore[misc]

    def test_network_topology_is_frozen(self) -> None:
        topology = NetworkTopology()
        with pytest.raises(ValidationError):
            topology.vpcs = []  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestErrorPropagation:
    def test_describe_vpcs_error_reaches_the_caller(self) -> None:
        client = _FakeEC2Client(errors={"describe_vpcs": RuntimeError("DescribeVpcs denied")})
        with pytest.raises(RuntimeError, match="denied"):
            collect_network_topology(client)

    def test_describe_subnets_error_reaches_the_caller(self) -> None:
        client = _FakeEC2Client(
            responses={"describe_vpcs": [{"Vpcs": [_vpc()]}]},
            errors={"describe_subnets": RuntimeError("DescribeSubnets denied")},
        )
        with pytest.raises(RuntimeError, match="denied"):
            collect_network_topology(client)

    def test_describe_route_tables_error_reaches_the_caller(self) -> None:
        client = _FakeEC2Client(
            responses={"describe_vpcs": [{"Vpcs": [_vpc()]}]},
            errors={"describe_route_tables": RuntimeError("DescribeRouteTables denied")},
        )
        with pytest.raises(RuntimeError, match="denied"):
            collect_network_topology(client)
