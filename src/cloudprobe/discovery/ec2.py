"""EC2-backed target discovery.

One responsibility: call ``DescribeInstances`` and convert each matching
instance into a ``Target``.

What this module deliberately does **not** do:

* Create an AWS client.  The client is injected, per the discovery layer's
  documented interface, so that this module is unit-testable against a fake and
  callers control credentials, region and retry policy.
* Merge, deduplicate or apply precedence.  It returns discovered targets only;
  ``inventory.build_inventory`` owns canonicalization.
* Discover VPCs, subnets, route tables or security groups.  Instance responses
  already carry the ``VpcId`` and ``SubnetId`` that downstream metrics need as
  dimensions, so no additional call is warranted here.
* Swallow AWS errors.  A failure propagates to the caller, which is what allows
  a run to be downgraded to static-inventory-only and the failure reported as
  data rather than crashing the pipeline.

``Target`` is owned by ``config/models.py``; this module produces that shape and
invents no shape of its own.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Protocol

from cloudprobe.config.models import ProbeType, Target

# Only running instances are probe candidates: a stopped instance has no
# reachability to measure and typically no address to reach it at.
_RUNNING = "running"

# The AWS tag whose value is conventionally the instance's display name.
_NAME_TAG = "Name"


class _Paginator(Protocol):
    """The subset of a Boto3 paginator this module uses."""

    def paginate(self, **kwargs: Any) -> Iterator[Mapping[str, Any]]:
        ...  # pragma: no cover - structural type declaration


class EC2Client(Protocol):
    """The subset of a Boto3 EC2 client this module uses.

    Declared structurally so discovery depends on Boto3's interface rather than
    importing Boto3, keeping this module importable and testable without the
    SDK present.  A real ``boto3.client("ec2")`` satisfies it.
    """

    def get_paginator(self, operation_name: str) -> _Paginator:
        ...  # pragma: no cover - structural type declaration


def discover_ec2_targets(
    ec2_client: EC2Client,
    probe_types: Sequence[ProbeType],
    tag_filters: Mapping[str, str] | None = None,
) -> list[Target]:
    """Return a ``Target`` for every running EC2 instance that matches.

    Args:
        ec2_client: An object satisfying ``EC2Client`` — in production a Boto3
            EC2 client supplied by the caller.
        probe_types: Probe types to assign to every discovered target.  An EC2
            instance does not declare what should be probed against it, so the
            caller states it; ``Target`` requires at least one.
        tag_filters: Optional tag selector, e.g. ``{"Environment": "lab"}``.
            An instance must carry every key/value pair to be returned.
            Applied server-side by the AWS API.

    Returns:
        Discovered targets in API response order.  Instances without a private
        IPv4 address are skipped: there is no address to probe.  The list is
        neither deduplicated nor merged with any other source.

    Raises:
        botocore.exceptions.ClientError: Propagated unchanged when AWS rejects
            or cannot serve the request.
    """
    filters = _build_filters(tag_filters)
    paginator = ec2_client.get_paginator("describe_instances")

    targets: list[Target] = []
    for page in paginator.paginate(Filters=filters):
        for reservation in page.get("Reservations", ()):
            for instance in reservation.get("Instances", ()):
                target = _target_from_instance(instance, probe_types)
                if target is not None:
                    targets.append(target)
    return targets


def _build_filters(tag_filters: Mapping[str, str] | None) -> list[dict[str, Any]]:
    """Build the ``Filters`` argument, restricting to running instances."""
    filters: list[dict[str, Any]] = [{"Name": "instance-state-name", "Values": [_RUNNING]}]
    for key, value in (tag_filters or {}).items():
        filters.append({"Name": f"tag:{key}", "Values": [value]})
    return filters


def _target_from_instance(
    instance: Mapping[str, Any], probe_types: Sequence[ProbeType]
) -> Target | None:
    """Convert one instance description into a ``Target``.

    Returns ``None`` when the instance cannot be probed, which is the case
    when it has no instance ID or no private IPv4 address.
    """
    instance_id = instance.get("InstanceId")
    host = instance.get("PrivateIpAddress")
    if not instance_id or not host:
        return None

    tags = _tags(instance)
    return Target(
        target_id=instance_id,
        host=host,
        # Discovery cannot know which port a service listens on; port stays unset.
        probe_types=list(probe_types),
        vpc_id=instance.get("VpcId"),
        subnet_id=instance.get("SubnetId"),
        instance_id=instance_id,
        label=tags.get(_NAME_TAG),
        tags=tags,
    )


def _tags(instance: Mapping[str, Any]) -> dict[str, str]:
    """Flatten AWS's ``[{"Key": k, "Value": v}]`` tag shape into a mapping."""
    return {tag["Key"]: tag.get("Value", "") for tag in instance.get("Tags", ()) if tag.get("Key")}
