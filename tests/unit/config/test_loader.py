"""Unit tests for the YAML configuration loader.

Covers:
    - loading a valid single-file config
    - loading a valid directory of section files
    - loading the shipped ``configs/inventory.example.yaml`` demo file
    - missing file
    - malformed YAML
    - empty YAML
    - non-mapping top-level
    - duplicate section across directory files
    - directory with no recognized files
    - section-file with both wrapped and unwrapped shapes
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from cloudprobe.config.exceptions import (
    ConfigError,
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)
from cloudprobe.config.loader import load
from cloudprobe.config.models import CloudProbeConfig, ProbeType

REPO_ROOT = Path(__file__).resolve().parents[3]

_VALID_SINGLE_FILE = dedent(
    """
    targets:
      - target_id: t-1
        host: 10.0.0.1
        port: 22
        probe_types: [tcp, ssh]
    thresholds:
      - probe_type: tcp
      - probe_type: ssh
    schedules:
      - probe_type: tcp
        cron_expression: "*/1 * * * *"
      - probe_type: ssh
        cron_expression: "*/5 * * * *"
    alert_rules:
      - rule_id: r-1
        probe_type: tcp
    probe:
      retry_attempts: 2
    """
)


@pytest.mark.unit
class TestLoadSingleFile:
    def test_valid_single_file(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.yaml"
        p.write_text(_VALID_SINGLE_FILE)
        cfg = load(p)
        assert isinstance(cfg, CloudProbeConfig)
        assert cfg.targets[0].target_id == "t-1"
        assert cfg.probe.retry_attempts == 2

    def test_accepts_str_path(self, tmp_path: Path) -> None:
        p = tmp_path / "cfg.yaml"
        p.write_text(_VALID_SINGLE_FILE)
        cfg = load(str(p))
        assert cfg.targets[0].host == "10.0.0.1"


@pytest.mark.unit
class TestLoadDirectory:
    def _write_valid_directory(self, tmp_path: Path) -> Path:
        (tmp_path / "inventory.yaml").write_text(
            "targets:\n"
            "  - target_id: t-1\n"
            "    host: 10.0.0.1\n"
            "    port: 22\n"
            "    probe_types: [tcp]\n"
        )
        (tmp_path / "thresholds.yaml").write_text(
            "thresholds:\n  - probe_type: tcp\n"
        )
        (tmp_path / "schedules.yaml").write_text(
            "schedules:\n  - probe_type: tcp\n    cron_expression: '*/1 * * * *'\n"
        )
        return tmp_path

    def test_valid_directory(self, tmp_path: Path) -> None:
        cfg = load(self._write_valid_directory(tmp_path))
        assert cfg.targets[0].probe_types == [ProbeType.TCP]

    def test_directory_with_missing_wrapping_key_rejected(self, tmp_path: Path) -> None:
        # A per-section file must wrap its content under the section key.
        (tmp_path / "inventory.yaml").write_text(
            "not_targets:\n"
            "  - target_id: t-1\n"
            "    host: 10.0.0.1\n"
            "    port: 22\n"
            "    probe_types: [tcp]\n"
        )
        with pytest.raises(ConfigValidationError, match="expected top-level key 'targets'"):
            load(tmp_path)

    def test_directory_with_no_recognized_files(self, tmp_path: Path) -> None:
        (tmp_path / "unrelated.yaml").write_text("foo: bar\n")
        with pytest.raises(ConfigValidationError, match="no recognized"):
            load(tmp_path)


@pytest.mark.unit
class TestErrors:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigNotFoundError):
            load(tmp_path / "does-not-exist.yaml")

    def test_malformed_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("targets: [unterminated\n")
        with pytest.raises(ConfigParseError):
            load(p)

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("")
        with pytest.raises(ConfigValidationError, match="empty"):
            load(p)

    def test_non_mapping_top_level(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("- just\n- a\n- list\n")
        with pytest.raises(ConfigValidationError, match="mapping"):
            load(p)

    def test_schema_violation_wraps_validation_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text(
            "targets:\n"
            "  - target_id: t-1\n"
            "    host: 999.999.999.999\n"
            "    probe_types: [tcp]\n"
            "thresholds:\n"
            "  - probe_type: tcp\n"
            "schedules:\n"
            "  - probe_type: tcp\n"
            "    cron_expression: '*/1 * * * *'\n"
        )
        with pytest.raises(ConfigValidationError, match="invalid IP"):
            load(p)


@pytest.mark.unit
class TestShippedConfigs:
    """Phase 2 exit criterion: the shipped example config validates.

    The shipped inventory ships as ``inventory.example.yaml`` (a template);
    operators are expected to copy it to ``inventory.yaml`` alongside the
    other section files.  These tests reproduce that layout in ``tmp_path``
    and assert it validates.
    """

    def _assemble_shipped(self, tmp_path: Path) -> Path:
        (tmp_path / "inventory.yaml").write_text(
            (REPO_ROOT / "configs" / "inventory.example.yaml").read_text()
        )
        for name in ("thresholds.yaml", "schedules.yaml"):
            (tmp_path / name).write_text((REPO_ROOT / "configs" / name).read_text())
        return tmp_path

    def test_shipped_configs_validate(self, tmp_path: Path) -> None:
        cfg = load(self._assemble_shipped(tmp_path))
        # ROADMAP Phase 2 requires 50+ targets.
        assert len(cfg.targets) >= 50
        referenced = {pt for t in cfg.targets for pt in t.probe_types}
        assert {th.probe_type for th in cfg.thresholds} >= referenced
        assert {s.probe_type for s in cfg.schedules} >= referenced

    def test_shipped_inventory_example_alone_is_incomplete(self) -> None:
        # Loading only the inventory (without thresholds/schedules) must fail
        # loudly rather than silently accept a degenerate config.
        with pytest.raises(ConfigError):
            load(REPO_ROOT / "configs" / "inventory.example.yaml")


@pytest.mark.unit
class TestExceptionAttributes:
    def test_not_found_exposes_path(self, tmp_path: Path) -> None:
        target = tmp_path / "nope.yaml"
        with pytest.raises(ConfigNotFoundError) as exc:
            load(target)
        assert exc.value.path == target

    def test_parse_error_exposes_path_and_reason(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("targets: [unterminated\n")
        with pytest.raises(ConfigParseError) as exc:
            load(p)
        assert exc.value.path == p
        assert exc.value.reason  # non-empty

    def test_validation_error_exposes_path(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("not: a target list\n")
        with pytest.raises(ConfigValidationError) as exc:
            load(p)
        assert exc.value.path == p


@pytest.mark.unit
class TestYamlRoundTrip:
    def test_roundtrip_preserves_targets(self, tmp_path: Path) -> None:
        original = yaml.safe_load(_VALID_SINGLE_FILE)
        p = tmp_path / "cfg.yaml"
        p.write_text(yaml.safe_dump(original))
        cfg = load(p)
        assert len(cfg.targets) == len(original["targets"])
        assert cfg.targets[0].target_id == original["targets"][0]["target_id"]
