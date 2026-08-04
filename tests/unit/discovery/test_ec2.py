"""Unit tests for EC2-backed target discovery.

The EC2 client is a hand-written fake rather than ``moto``: the module takes an
injected client, so a fake exercises the real code path with no SDK, no
credentials and no network.

Covers:
    - DescribeInstances response shape -> Target mapping
    - pagination across multiple pages and reservations
    - running-state and tag filters sent to the API
    - instances that cannot be probed
    - AWS errors propagating to the caller
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import pytest

from cloudprobe.config.models import ProbeType, Target
from cloudprobe.discovery.ec2 import discover_ec2_targets

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
        pages: Sequence[Mapping[str, Any]] = (),
        error: Exception | None = None,
    ) -> None:
        self.paginator = _FakePaginator(pages, error)
        self.operation_name: str | None = None

    def get_paginator(self, operation_name: str) -> _FakePaginator:
        self.operation_name = operation_name
        return self.paginator


def _instance(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "InstanceId": "i-0abc123",
        "PrivateIpAddress": "10.20.1.10",
        "VpcId": "vpc-123",
        "SubnetId": "subnet-abc",
        "Tags": [{"Key": "Name", "Value": "lab-web-1"}],
    }
    base.update(overrides)
    return base


def _page(*instances: Mapping[str, Any]) -> dict[str, Any]:
    return {"Reservations": [{"Instances": list(instances)}]}


def _discover(client: _FakeEC2Client, **kwargs: Any) -> list[Target]:
    kwargs.setdefault("probe_types", [ProbeType.TCP])
    return discover_ec2_targets(client, **kwargs)


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInstanceMapping:
    def test_instance_id_becomes_target_id_and_instance_id(self) -> None:
        [target] = _discover(_FakeEC2Client([_page(_instance())]))
        assert target.target_id == "i-0abc123"
        assert target.instance_id == "i-0abc123"

    def test_private_ip_becomes_host(self) -> None:
        [target] = _discover(_FakeEC2Client([_page(_instance())]))
        assert target.host == "10.20.1.10"

    def test_aws_dimensions_are_carried_over(self) -> None:
        [target] = _discover(_FakeEC2Client([_page(_instance())]))
        assert target.vpc_id == "vpc-123"
        assert target.subnet_id == "subnet-abc"

    def test_probe_types_come_from_the_caller(self) -> None:
        [target] = _discover(
            _FakeEC2Client([_page(_instance())]),
            probe_types=[ProbeType.TCP, ProbeType.SSH],
        )
        assert target.probe_types == [ProbeType.TCP, ProbeType.SSH]

    def test_port_is_left_unset(self) -> None:
        # Discovery cannot know which port a service listens on.
        [target] = _discover(_FakeEC2Client([_page(_instance())]))
        assert target.port is None

    def test_tags_are_flattened(self) -> None:
        instance = _instance(
            Tags=[{"Key": "Environment", "Value": "lab"}, {"Key": "tier", "Value": "web"}]
        )
        [target] = _discover(_FakeEC2Client([_page(instance)]))
        assert target.tags == {"Environment": "lab", "tier": "web"}

    def test_name_tag_becomes_label(self) -> None:
        [target] = _discover(_FakeEC2Client([_page(_instance())]))
        assert target.label == "lab-web-1"

    def test_missing_name_tag_leaves_label_unset(self) -> None:
        [target] = _discover(_FakeEC2Client([_page(_instance(Tags=[]))]))
        assert target.label is None

    def test_untagged_instance_yields_empty_tags(self) -> None:
        instance = _instance()
        del instance["Tags"]
        [target] = _discover(_FakeEC2Client([_page(instance)]))
        assert target.tags == {}

    def test_missing_vpc_and_subnet_are_unset(self) -> None:
        instance = _instance()
        del instance["VpcId"]
        del instance["SubnetId"]
        [target] = _discover(_FakeEC2Client([_page(instance)]))
        assert target.vpc_id is None
        assert target.subnet_id is None


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPagination:
    def test_describe_instances_paginator_is_used(self) -> None:
        client = _FakeEC2Client([_page(_instance())])
        _discover(client)
        assert client.operation_name == "describe_instances"

    def test_no_instances_yields_no_targets(self) -> None:
        assert _discover(_FakeEC2Client([{"Reservations": []}])) == []

    def test_no_pages_yields_no_targets(self) -> None:
        assert _discover(_FakeEC2Client([])) == []

    def test_targets_collected_across_pages(self) -> None:
        client = _FakeEC2Client(
            [
                _page(_instance(InstanceId="i-1", PrivateIpAddress="10.20.1.1")),
                _page(_instance(InstanceId="i-2", PrivateIpAddress="10.20.1.2")),
            ]
        )
        assert [t.target_id for t in _discover(client)] == ["i-1", "i-2"]

    def test_multiple_reservations_are_flattened(self) -> None:
        page = {
            "Reservations": [
                {"Instances": [_instance(InstanceId="i-1", PrivateIpAddress="10.20.1.1")]},
                {"Instances": [_instance(InstanceId="i-2", PrivateIpAddress="10.20.1.2")]},
            ]
        }
        assert [t.target_id for t in _discover(_FakeEC2Client([page]))] == ["i-1", "i-2"]

    def test_reservation_without_instances_key_is_skipped(self) -> None:
        assert _discover(_FakeEC2Client([{"Reservations": [{}]}])) == []


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFilters:
    def test_only_running_instances_are_requested(self) -> None:
        client = _FakeEC2Client([_page(_instance())])
        _discover(client)
        assert client.paginator.paginate_kwargs == {
            "Filters": [{"Name": "instance-state-name", "Values": ["running"]}]
        }

    def test_tag_filters_are_sent_to_the_api(self) -> None:
        client = _FakeEC2Client([_page(_instance())])
        _discover(client, tag_filters={"Environment": "lab"})
        assert client.paginator.paginate_kwargs is not None
        filters = client.paginator.paginate_kwargs["Filters"]
        assert {"Name": "tag:Environment", "Values": ["lab"]} in filters

    def test_every_tag_filter_is_sent(self) -> None:
        client = _FakeEC2Client([_page(_instance())])
        _discover(client, tag_filters={"Environment": "lab", "tier": "web"})
        assert client.paginator.paginate_kwargs is not None
        names = [f["Name"] for f in client.paginator.paginate_kwargs["Filters"]]
        assert names == ["instance-state-name", "tag:Environment", "tag:tier"]


# ---------------------------------------------------------------------------
# Unprobeable instances
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnprobeableInstances:
    def test_instance_without_private_ip_is_skipped(self) -> None:
        instance = _instance()
        del instance["PrivateIpAddress"]
        assert _discover(_FakeEC2Client([_page(instance)])) == []

    def test_instance_without_instance_id_is_skipped(self) -> None:
        instance = _instance()
        del instance["InstanceId"]
        assert _discover(_FakeEC2Client([_page(instance)])) == []

    def test_probeable_siblings_still_returned(self) -> None:
        client = _FakeEC2Client(
            [_page(_instance(InstanceId="i-1", PrivateIpAddress=None), _instance(InstanceId="i-2"))]
        )
        assert [t.target_id for t in _discover(client)] == ["i-2"]


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestErrorPropagation:
    def test_aws_errors_reach_the_caller(self) -> None:
        # Discovery does not decide what a failed source means; the caller
        # downgrades the run and records the failure.
        client = _FakeEC2Client(error=RuntimeError("DescribeInstances denied"))
        with pytest.raises(RuntimeError, match="denied"):
            _discover(client)
