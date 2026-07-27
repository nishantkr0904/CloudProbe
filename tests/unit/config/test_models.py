"""Unit tests for CloudProbe configuration models.

Covers:
    - valid configurations (round-trip)
    - missing required keys
    - invalid IPs and hostnames
    - duplicate targets / rules
    - invalid schedules
    - invalid thresholds
    - immutability
    - cross-collection validation (thresholds/schedules must cover probe types)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cloudprobe.config.models import (
    AlertRule,
    AlertSeverity,
    CloudProbeConfig,
    ProbeConfig,
    ProbeType,
    Schedule,
    Target,
    Threshold,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _valid_target(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "target_id": "t-1",
        "host": "10.0.0.1",
        "port": 22,
        "probe_types": ["tcp"],
    }
    base.update(overrides)
    return base


def _valid_threshold(probe_type: str = "tcp") -> dict[str, object]:
    return {"probe_type": probe_type}


def _valid_schedule(probe_type: str = "tcp") -> dict[str, object]:
    return {"probe_type": probe_type, "cron_expression": "*/1 * * * *"}


def _valid_rule() -> dict[str, object]:
    return {"rule_id": "r-1", "probe_type": "tcp"}


def _valid_config(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "targets": [_valid_target()],
        "thresholds": [_valid_threshold()],
        "schedules": [_valid_schedule()],
        "alert_rules": [_valid_rule()],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidConfig:
    def test_minimal_valid_config(self) -> None:
        cfg = CloudProbeConfig.model_validate(_valid_config())
        assert len(cfg.targets) == 1
        assert cfg.targets[0].probe_types == [ProbeType.TCP]
        assert cfg.probe.retry_attempts == 1  # default from ProbeConfig

    def test_target_accepts_ipv4(self) -> None:
        t = Target.model_validate(_valid_target(host="192.0.2.10"))
        assert t.host == "192.0.2.10"

    def test_target_accepts_ipv6(self) -> None:
        t = Target.model_validate(_valid_target(host="2001:db8::1"))
        assert t.host == "2001:db8::1"

    def test_target_accepts_hostname(self) -> None:
        t = Target.model_validate(_valid_target(host="api.example.com"))
        assert t.host == "api.example.com"

    def test_target_accepts_single_label_hostname(self) -> None:
        t = Target.model_validate(_valid_target(host="localhost"))
        assert t.host == "localhost"

    def test_target_with_all_optional_fields(self) -> None:
        t = Target.model_validate(
            _valid_target(
                label="Web-01",
                vpc_id="vpc-abc",
                subnet_id="subnet-abc",
                instance_id="i-abc",
                tags={"tier": "web"},
            )
        )
        assert t.label == "Web-01"
        assert t.tags == {"tier": "web"}

    def test_all_probe_types_accepted(self) -> None:
        for pt in ProbeType:
            t = Target.model_validate(_valid_target(probe_types=[pt.value]))
            assert pt in t.probe_types

    def test_probe_config_defaults(self) -> None:
        pc = ProbeConfig()
        assert pc.retry_attempts == 1
        assert pc.retry_backoff_seconds == 1.0
        assert pc.default_timeout_seconds == 10

    def test_alert_rule_defaults(self) -> None:
        r = AlertRule.model_validate(_valid_rule())
        assert r.severity == AlertSeverity.WARNING
        assert r.notify_sns is True


# ---------------------------------------------------------------------------
# Missing keys
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMissingKeys:
    def test_target_missing_id(self) -> None:
        data = _valid_target()
        del data["target_id"]
        with pytest.raises(ValidationError, match="target_id"):
            Target.model_validate(data)

    def test_target_missing_host(self) -> None:
        data = _valid_target()
        del data["host"]
        with pytest.raises(ValidationError, match="host"):
            Target.model_validate(data)

    def test_target_missing_probe_types(self) -> None:
        data = _valid_target()
        del data["probe_types"]
        with pytest.raises(ValidationError, match="probe_types"):
            Target.model_validate(data)

    def test_target_empty_probe_types(self) -> None:
        with pytest.raises(ValidationError, match="probe_types"):
            Target.model_validate(_valid_target(probe_types=[]))

    def test_config_missing_targets(self) -> None:
        data = _valid_config()
        del data["targets"]
        with pytest.raises(ValidationError, match="targets"):
            CloudProbeConfig.model_validate(data)

    def test_config_empty_targets(self) -> None:
        with pytest.raises(ValidationError, match="targets"):
            CloudProbeConfig.model_validate(_valid_config(targets=[]))

    def test_rule_missing_id(self) -> None:
        data = _valid_rule()
        del data["rule_id"]
        with pytest.raises(ValidationError, match="rule_id"):
            AlertRule.model_validate(data)


# ---------------------------------------------------------------------------
# Invalid IPs / hostnames
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvalidHost:
    @pytest.mark.parametrize(
        "bad_ip",
        [
            "999.999.999.999",
            "10.0.0",
            "10.0.0.256",
            "10.0.0.1.5",
            "1.2.3.4.5",
            "2001:db8::gggg",
        ],
    )
    def test_invalid_ip_rejected(self, bad_ip: str) -> None:
        with pytest.raises(ValidationError, match="invalid IP"):
            Target.model_validate(_valid_target(host=bad_ip))

    @pytest.mark.parametrize(
        "bad_host",
        [
            "-startswithdash.example.com",
            "endswithdash-.example.com",
            "has space.example.com",
            "double..dot.example.com",
            "under_score.example.com",
        ],
    )
    def test_invalid_hostname_rejected(self, bad_host: str) -> None:
        with pytest.raises(ValidationError, match="invalid hostname"):
            Target.model_validate(_valid_target(host=bad_host))

    def test_empty_host_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Target.model_validate(_valid_target(host=""))


# ---------------------------------------------------------------------------
# Duplicate targets / rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDuplicates:
    def test_duplicate_target_ids_rejected(self) -> None:
        cfg = _valid_config(
            targets=[_valid_target(target_id="dup"), _valid_target(target_id="dup")]
        )
        with pytest.raises(ValidationError, match="duplicate target_id"):
            CloudProbeConfig.model_validate(cfg)

    def test_duplicate_rule_ids_rejected(self) -> None:
        cfg = _valid_config(
            alert_rules=[
                {"rule_id": "dup", "probe_type": "tcp"},
                {"rule_id": "dup", "probe_type": "tcp"},
            ]
        )
        with pytest.raises(ValidationError, match="duplicate rule_id"):
            CloudProbeConfig.model_validate(cfg)

    def test_duplicate_probe_types_on_single_target_rejected(self) -> None:
        with pytest.raises(ValidationError, match="probe_types must not contain duplicates"):
            Target.model_validate(_valid_target(probe_types=["tcp", "tcp"]))

    def test_distinct_target_ids_pass(self) -> None:
        cfg = _valid_config(
            targets=[_valid_target(target_id="a"), _valid_target(target_id="b")]
        )
        CloudProbeConfig.model_validate(cfg)


# ---------------------------------------------------------------------------
# Invalid schedules
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvalidSchedule:
    @pytest.mark.parametrize(
        "expr",
        [
            "* * * *",           # 4 fields
            "* * * * * *",       # 6 fields
            "everyminute",       # single token
            "",                  # empty
        ],
    )
    def test_bad_cron_expression_rejected(self, expr: str) -> None:
        with pytest.raises(ValidationError):
            Schedule.model_validate({"probe_type": "tcp", "cron_expression": expr})

    def test_zero_timeout_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Schedule.model_validate(
                {
                    "probe_type": "tcp",
                    "cron_expression": "*/1 * * * *",
                    "timeout_seconds": 0,
                }
            )

    def test_negative_concurrency_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Schedule.model_validate(
                {
                    "probe_type": "tcp",
                    "cron_expression": "*/1 * * * *",
                    "max_concurrency": 0,
                }
            )

    def test_missing_schedule_for_referenced_probe_type_rejected(self) -> None:
        # Target references ssh but no schedule declared for it.
        cfg = _valid_config(
            targets=[_valid_target(probe_types=["tcp", "ssh"])],
            schedules=[_valid_schedule("tcp")],
            thresholds=[_valid_threshold("tcp"), _valid_threshold("ssh")],
        )
        with pytest.raises(ValidationError, match="missing schedule.*ssh"):
            CloudProbeConfig.model_validate(cfg)


# ---------------------------------------------------------------------------
# Invalid thresholds
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInvalidThreshold:
    @pytest.mark.parametrize("bad_latency", [0, -1, -1000])
    def test_bad_latency_rejected(self, bad_latency: int) -> None:
        with pytest.raises(ValidationError):
            Threshold.model_validate(
                {"probe_type": "tcp", "max_latency_ms": bad_latency}
            )

    @pytest.mark.parametrize("bad_ratio", [-0.1, 1.01, 2.0])
    def test_bad_success_ratio_rejected(self, bad_ratio: float) -> None:
        with pytest.raises(ValidationError):
            Threshold.model_validate(
                {"probe_type": "tcp", "min_success_ratio": bad_ratio}
            )

    def test_zero_consecutive_failures_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Threshold.model_validate(
                {"probe_type": "tcp", "consecutive_failures": 0}
            )

    def test_missing_threshold_for_referenced_probe_type_rejected(self) -> None:
        cfg = _valid_config(
            targets=[_valid_target(probe_types=["tcp", "http"])],
            thresholds=[_valid_threshold("tcp")],
            schedules=[_valid_schedule("tcp"), _valid_schedule("http")],
        )
        with pytest.raises(ValidationError, match="missing threshold.*http"):
            CloudProbeConfig.model_validate(cfg)

    def test_unknown_probe_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Threshold.model_validate(
                {"probe_type": "quic", "max_latency_ms": 100}
            )


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestImmutability:
    def test_target_is_frozen(self) -> None:
        t = Target.model_validate(_valid_target())
        with pytest.raises(ValidationError):
            t.target_id = "other"  # type: ignore[misc]

    def test_config_is_frozen(self) -> None:
        cfg = CloudProbeConfig.model_validate(_valid_config())
        with pytest.raises(ValidationError):
            cfg.targets = []  # type: ignore[misc]


# ---------------------------------------------------------------------------
# target_id / rule_id whitespace rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIdWhitespace:
    @pytest.mark.parametrize("bad_id", [" t1", "t 1", "t1 ", ""])
    def test_target_id_whitespace_rejected(self, bad_id: str) -> None:
        with pytest.raises(ValidationError):
            Target.model_validate(_valid_target(target_id=bad_id))

    @pytest.mark.parametrize("bad_id", [" r1", "r 1", "r1 "])
    def test_rule_id_whitespace_rejected(self, bad_id: str) -> None:
        with pytest.raises(ValidationError):
            AlertRule.model_validate({"rule_id": bad_id, "probe_type": "tcp"})
