"""Integration: configuration load → AWS discovery → canonical inventory.

Exercises the boundary between the config loader and the discovery layer
(architecture §10.2 "Config → moto EC2 describe", "Discovery → moto EC2
describe").  A real ``boto3`` EC2 client backed by ``moto`` satisfies the
discovery layer's ``EC2Client`` protocol, so this test verifies the full path
a run follows: YAML on disk becomes a validated ``CloudProbeConfig``; moto's
``describe_instances`` payload becomes discovery ``Target``s; and
``build_inventory`` merges the static targets with the discovered ones into one
canonical inventory.

No real AWS is touched: ``moto`` patches the ``botocore`` endpoint so every
EC2 call stays in memory, and no credentials are needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import boto3
import pytest
from moto import mock_aws

from cloudprobe.config import load
from cloudprobe.config.models import ProbeType
from cloudprobe.discovery import (
    InventorySource,
    build_inventory,
    discover_ec2_targets,
)
from cloudprobe.discovery.ec2 import EC2Client

_CONFIG = """\
targets:
  - target_id: web-1
    host: 10.20.1.10
    port: 443
    probe_types: [tcp]
thresholds:
  - probe_type: tcp
    warn_above_ms: 200
schedules:
  - probe_type: tcp
    cron_expression: "*/5 * * * *"
"""


@pytest.mark.integration
class TestConfigToDiscovery:
    def test_discovered_targets_merge_with_static_targets(self, tmp_path: Path) -> None:
        with mock_aws():
            ec2 = boto3.client("ec2", region_name="us-east-1")
            ec2.run_instances(
                ImageId="ami-12345678",
                MinCount=1,
                MaxCount=1,
                InstanceType="t3.micro",
                PrivateIpAddress="10.20.2.20",
                TagSpecifications=[
                    {
                        "ResourceType": "instance",
                        "Tags": [{"Key": "Name", "Value": "api-1"}],
                    }
                ],
            )

            config_file = tmp_path / "config.yaml"
            config_file.write_text(_CONFIG, encoding="utf-8")
            config = load(str(config_file))

            discovered = discover_ec2_targets(
                cast(EC2Client, ec2),
                probe_types=[ProbeType.TCP],
                tag_filters={"Name": "api-1"},
            )
            result = build_inventory(config.targets, discovered)

            target_ids = {target.target_id for target in result.inventory.targets}
            assert "web-1" in target_ids
            assert len(result.inventory.by_source(InventorySource.AWS)) == 1
