"""YAML configuration loader.

The only module in this package that touches disk or PyYAML.  All other
layers of CloudProbe receive a validated ``CloudProbeConfig`` object and
have no knowledge of how it was produced.

The loader supports two shapes:

1. **Single file** — one YAML document containing every section (targets,
   thresholds, schedules, alert_rules, probe).  Used by tests and small
   deployments.

2. **Directory** — a directory containing per-section YAML files.  Each
   file must be a YAML mapping whose top-level key matches the section it
   provides.  Missing files are treated as an absent section.

Recognized section files:

    inventory.yaml      → key: targets
    thresholds.yaml     → key: thresholds
    schedules.yaml      → key: schedules
    alert_rules.yaml    → key: alert_rules
    probe.yaml          → key: probe

Requiring the wrapping key in every file removes ambiguity: a file that
accidentally puts a value under the wrong key produces a clear error
instead of a silent misinterpretation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from cloudprobe.config.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
)
from cloudprobe.config.models import CloudProbeConfig

_SECTION_FILES: dict[str, str] = {
    "inventory.yaml": "targets",
    "thresholds.yaml": "thresholds",
    "schedules.yaml": "schedules",
    "alert_rules.yaml": "alert_rules",
    "probe.yaml": "probe",
}


def load(path: str | Path) -> CloudProbeConfig:
    """Load and validate CloudProbe configuration.

    Args:
        path: Path to a YAML file or a directory of YAML files.

    Returns:
        A validated, immutable ``CloudProbeConfig``.

    Raises:
        ConfigNotFoundError: The path does not exist.
        ConfigParseError:    The file(s) contain invalid YAML.
        ConfigValidationError: The parsed data does not satisfy the schema.
    """
    p = Path(path)
    if not p.exists():
        raise ConfigNotFoundError(p)

    raw = _read_directory(p) if p.is_dir() else _read_single_file(p)

    try:
        return CloudProbeConfig.model_validate(raw)
    except ValidationError as e:
        raise ConfigValidationError(p, str(e)) from e


def _read_single_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as e:
        raise ConfigParseError(path, str(e)) from e

    if data is None:
        raise ConfigValidationError(path, "file is empty")
    if not isinstance(data, dict):
        raise ConfigValidationError(
            path, f"top-level YAML must be a mapping, got {type(data).__name__}"
        )
    return data


def _read_directory(directory: Path) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for filename, section in _SECTION_FILES.items():
        file_path = directory / filename
        if not file_path.exists():
            continue
        content = _read_single_file(file_path)
        if section not in content:
            raise ConfigValidationError(
                file_path,
                f"expected top-level key {section!r} in {filename}, got keys: "
                f"{sorted(content)}",
            )
        merged[section] = content[section]
    if not merged:
        raise ConfigValidationError(
            directory,
            f"no recognized configuration files in {directory}. "
            f"Expected one of: {', '.join(_SECTION_FILES)}",
        )
    return merged
