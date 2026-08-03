"""Regression: the configuration surface an operator sees.

Two things about configuration loading are user-visible and must not drift:

* The **error messages** the loader raises.  Operators read these on a broken
  deploy; a reworded or reshaped message is a change to the tool's contract,
  not an implementation detail.  They are frozen as golden strings.
* The **defaults** a minimal config expands into.  Every key an operator does
  *not* write is a promise the tool makes on their behalf (timeouts, retry
  counts, severities, success ratios).  A silently changed default changes what
  a deployed probe does without any config edit, so the fully-defaulted
  ``CloudProbeConfig`` is frozen as a golden document.

These belong in the regression tier, not unit: ``tests/unit/config`` already
asserts *that* each field validates and *that* each default has a value.  This
pins the exact serialized shape and wording of the whole aggregate — the thing
a renderer, an API response or an operator's eyes actually depend on — so a
refactor that keeps every unit test green still trips here if the surface moves.

No production code changes are required: the loader and models already emit
these strings and defaults; this only photographs them.
"""

from __future__ import annotations

import pytest

from cloudprobe.config import ConfigValidationError, load
from tests.regression.golden import assert_against_golden

# A config that sets only the required keys, so every other value in the frozen
# artifact is a default the code chose.  One target, one probe type, and the
# threshold/schedule the aggregate validator requires that probe type to have.
_MINIMAL_CONFIG = """\
targets:
  - target_id: web-1
    host: 10.20.1.10
    port: 443
    probe_types: [tcp]
thresholds:
  - probe_type: tcp
schedules:
  - probe_type: tcp
    cron_expression: "*/5 * * * *"
"""

# A document that parses as YAML but violates the schema: the port is out of
# range.  The message the loader raises for this is the operator-facing
# contract frozen below.
_INVALID_CONFIG = """\
targets:
  - target_id: web-1
    host: 10.20.1.10
    port: 70000
    probe_types: [tcp]
thresholds:
  - probe_type: tcp
schedules:
  - probe_type: tcp
    cron_expression: "*/5 * * * *"
"""


@pytest.fixture
def minimal_config_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_MINIMAL_CONFIG, encoding="utf-8")
    return path


@pytest.mark.regression
class TestConfigDefaults:
    def test_defaulted_config_shape_is_frozen(self, minimal_config_file, update_goldens) -> None:
        # Loading a minimal document must always expand to the same fully
        # defaulted aggregate; the golden pins every default the code supplies.
        config = load(str(minimal_config_file))

        assert_against_golden(
            config,
            "config_minimal_defaults.json",
            update=update_goldens,
        )


@pytest.mark.regression
class TestConfigErrorMessages:
    def test_validation_error_message_is_frozen(self, tmp_path, update_goldens) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(_INVALID_CONFIG, encoding="utf-8")

        with pytest.raises(ConfigValidationError) as excinfo:
            load(str(path))

        # The full path is machine-specific (it lives under tmp_path); only the
        # stable prefix and the Pydantic reason are the operator contract, so
        # the volatile path segment is normalized out before freezing.
        message = str(excinfo.value).replace(str(path), "<config-path>")
        assert_against_golden(
            message,
            "config_validation_error.txt",
            update=update_goldens,
        )

    def test_missing_file_message_is_frozen(self, tmp_path, update_goldens) -> None:
        missing = tmp_path / "does-not-exist.yaml"

        # Broad on purpose: the frozen message, not the class, is the assertion.
        with pytest.raises(Exception) as excinfo:
            load(str(missing))

        message = str(excinfo.value).replace(str(missing), "<config-path>")
        assert_against_golden(
            message,
            "config_not_found_error.txt",
            update=update_goldens,
        )
