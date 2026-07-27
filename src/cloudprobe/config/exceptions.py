"""Exceptions raised by the configuration layer.

These are the only exceptions this package raises to callers.  They form a
small, stable contract so downstream layers can catch config problems without
depending on Pydantic or PyYAML internals.
"""

from __future__ import annotations

from pathlib import Path


class ConfigError(Exception):
    """Base class for all configuration-layer errors."""


class ConfigNotFoundError(ConfigError):
    """A configuration file that was expected to exist was not found."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Configuration file not found: {path}")
        self.path = path


class ConfigParseError(ConfigError):
    """A configuration file exists but is not valid YAML."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"Failed to parse YAML from {path}: {reason}")
        self.path = path
        self.reason = reason


class ConfigValidationError(ConfigError):
    """A configuration file parsed as YAML but violates the schema."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"Configuration failed validation ({path}): {reason}")
        self.path = path
        self.reason = reason
