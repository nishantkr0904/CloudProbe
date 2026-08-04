"""Regression: the shapes AWS discovery turns cloud responses into.

Discovery reads ``DescribeInstances``, ``DescribeVpcs``, ``DescribeSubnets``
and ``DescribeRouteTables`` and produces two user-visible shapes:

* a ``Target`` per running instance — the record the probe engine and metrics
  layer consume, whose ``vpc_id``/``subnet_id``/``tags`` become metric
  dimensions and whose unset ``port`` is a deliberate contract (discovery
  cannot know a service's port);
* a ``NetworkTopology`` — the VPC/subnet/route-table metadata a report
  summarizes and a per-target lookup resolves.

A change to how an AWS response field maps onto these shapes (a dropped tag, a
renamed field, a port that suddenly defaults to something) changes what every
downstream layer sees, so the mapped shapes are frozen here.

Why regression, not unit: ``tests/unit/discovery`` already asserts each
individual mapping rule (private IP becomes host, ``IsDefault`` becomes a bool,
a subnet without an id is skipped).  This freezes the *whole assembled shape*
of a realistic multi-resource response — the serialized ``Target`` and
``NetworkTopology`` a caller actually receives — so a refactor that preserves
every field-level rule but reorders or reshapes the aggregate still trips here.

No network and no SDK: the EC2 client is a hand-built fake returning canned
paginated pages, exactly the injected-client seam discovery documents.  No
production code changes are required — this photographs the existing mapping.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from cloudprobe.config.models import ProbeType
from cloudprobe.discovery import (
    collect_network_topology,
    discover_ec2_targets,
)
from tests.regression.golden import assert_against_golden


class _FakePaginator:
    """Returns a fixed list of pages, ignoring the filters it is handed.

    Filtering is the AWS API's job; discovery only walks the pages.  Freezing
    the mapped output means the input pages must be fixed, so this yields
    canned pages verbatim.
    """

    def __init__(self, pages: list[Mapping[str, Any]]) -> None:
        self._pages = pages

    def paginate(self, **_kwargs: Any) -> Iterator[Mapping[str, Any]]:
        yield from self._pages


class _FakeEC2Client:
    """A structural ``EC2Client`` backed by canned pages per operation."""

    def __init__(self, pages_by_operation: Mapping[str, list[Mapping[str, Any]]]) -> None:
        self._pages_by_operation = pages_by_operation

    def get_paginator(self, operation_name: str) -> _FakePaginator:
        return _FakePaginator(self._pages_by_operation.get(operation_name, []))


_DESCRIBE_INSTANCES_PAGES: list[Mapping[str, Any]] = [
    {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-web1",
                        "PrivateIpAddress": "10.20.1.10",
                        "VpcId": "vpc-aaa",
                        "SubnetId": "subnet-aaa",
                        "Tags": [
                            {"Key": "Name", "Value": "web"},
                            {"Key": "Environment", "Value": "lab"},
                        ],
                    },
                    {
                        "InstanceId": "i-api1",
                        "PrivateIpAddress": "10.20.2.20",
                        "VpcId": "vpc-aaa",
                        "SubnetId": "subnet-bbb",
                        "Tags": [{"Key": "Name", "Value": "api"}],
                    },
                ]
            }
        ]
    }
]

_TOPOLOGY_PAGES: dict[str, list[Mapping[str, Any]]] = {
    "describe_vpcs": [
        {
            "Vpcs": [
                {
                    "VpcId": "vpc-aaa",
                    "CidrBlock": "10.20.0.0/16",
                    "IsDefault": False,
                    "Tags": [{"Key": "Name", "Value": "lab-vpc"}],
                }
            ]
        }
    ],
    "describe_subnets": [
        {
            "Subnets": [
                {
                    "SubnetId": "subnet-aaa",
                    "VpcId": "vpc-aaa",
                    "CidrBlock": "10.20.1.0/24",
                    "AvailabilityZone": "us-east-1a",
                    "Tags": [{"Key": "Name", "Value": "web-subnet"}],
                },
                {
                    "SubnetId": "subnet-bbb",
                    "VpcId": "vpc-aaa",
                    "CidrBlock": "10.20.2.0/24",
                    "AvailabilityZone": "us-east-1b",
                    "Tags": [],
                },
            ]
        }
    ],
    "describe_route_tables": [
        {
            "RouteTables": [
                {
                    "RouteTableId": "rtb-aaa",
                    "VpcId": "vpc-aaa",
                    "Associations": [
                        {"Main": True},
                        {"SubnetId": "subnet-aaa"},
                    ],
                    "Routes": [
                        {
                            "DestinationCidrBlock": "10.20.0.0/16",
                            "GatewayId": "local",
                            "State": "active",
                        },
                        {
                            "DestinationCidrBlock": "0.0.0.0/0",
                            "GatewayId": "igw-aaa",
                            "State": "active",
                        },
                    ],
                }
            ]
        }
    ],
}


@pytest.mark.regression
class TestEC2TargetShape:
    def test_discovered_target_shape_is_frozen(self, update_goldens: bool) -> None:
        client = _FakeEC2Client({"describe_instances": _DESCRIBE_INSTANCES_PAGES})

        targets = discover_ec2_targets(client, probe_types=[ProbeType.TCP])

        assert_against_golden(
            targets,
            "discovery_ec2_targets.json",
            update=update_goldens,
        )


@pytest.mark.regression
class TestVpcTopologyShape:
    def test_collected_topology_shape_is_frozen(self, update_goldens: bool) -> None:
        client = _FakeEC2Client(_TOPOLOGY_PAGES)

        topology = collect_network_topology(client, vpc_ids=["vpc-aaa"])

        assert_against_golden(
            topology,
            "discovery_vpc_topology.json",
            update=update_goldens,
        )
