"""Golden-file regression harness.

Freezes user-visible output shapes — the exact keys, order and formatting of
what CloudProbe emits — so that a change to any public surface breaks a test
loudly instead of drifting silently.  Golden artifacts live under
``tests/fixtures/golden/`` (project-structure §tests/regression) and are
regenerated only with an explicit ``--update-goldens`` flag, which exists to
make silent regeneration impossible (a reviewer must sign off on the diff).

The helper is deliberately small: one function to compare a produced artifact
against its committed golden file, and one to rewrite the golden files.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

# Golden files are committed reference artifacts (project-structure §14).
_GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden"


class GoldenMismatchError(AssertionError):
    """The produced artifact no longer matches its committed golden file."""


def _golden_path(name: str) -> Path:
    """Resolve a golden file name (relative or absolute) under the golden dir."""
    path = Path(name)
    if not path.is_absolute():
        path = _GOLDEN_DIR / path
    return path


def assert_against_golden(
    produced: Any,
    name: str,
    *,
    update: bool = False,
) -> None:
    """Assert ``produced`` matches the committed golden artifact ``name``.

    With ``update=True`` the golden file is rewritten instead of compared —
    the explicit opt-in a golden-file change requires (project-structure
    §tests/regression).  The comparison hides nothing: the rendered form must
    match the committed file byte for byte, so a reordered list, a renamed key
    or a changed value all fail.

    Args:
        produced: The artifact to freeze, or the artifact to store when
            ``update`` is set.
        name: Golden file name; resolved under ``tests/fixtures/golden/``
            unless it is already absolute.
        update: When True, write ``produced`` over the golden file instead
            of comparing.  When False (default), compare.

    Raises:
        GoldenMismatchError: The produced artifact differs from the committed
            golden file, or the golden file does not exist yet (when
            ``update`` is False).
    """
    path = _golden_path(name)
    if update:
        _write_golden(path, produced)
        return

    if not path.exists():
        raise GoldenMismatchError(
            f"golden file {path} does not exist; create it with --update-goldens"
        )
    expected = path.read_text(encoding="utf-8")
    produced_text = _render(produced)
    if produced_text != expected:
        _raise_mismatch(path, expected, produced_text)


def write_golden(produced: Any, name: str) -> None:
    """Write ``produced`` to the golden file ``name`` (explicit update path)."""
    _write_golden(_golden_path(name), produced)


def _write_golden(path: Path, produced: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render(produced), encoding="utf-8")


def _render(produced: Any) -> str:
    """Render an artifact into the exact text a golden file holds.

    Both the writer and the comparer go through this one function, so a
    freshly written golden always compares equal to the artifact that wrote
    it.  Exactly one trailing newline is enforced, which keeps the files
    POSIX-clean and diff-friendly regardless of whether the artifact itself
    ended in a newline.
    """
    return _serialize(produced).rstrip("\n") + "\n"


def _serialize(produced: Any) -> str:
    """Render an artifact for comparison.

    Strings and bytes are frozen verbatim — a rendered HTML document is
    compared exactly as an operator would see it.  Everything else is
    normalized to JSON-safe values and dumped with sorted keys, so that a
    dictionary's insertion order cannot make a golden file churn while the
    order of *sequences*, which is a real part of the contract, is preserved.
    """
    if isinstance(produced, str):
        return produced
    if isinstance(produced, bytes):
        return produced.decode("utf-8")
    return json.dumps(_jsonable(produced), sort_keys=True, indent=2)


def _jsonable(value: Any) -> Any:
    """Convert a CloudProbe artifact into JSON-safe primitives.

    Handles the four shapes the project's public surfaces are built from:
    frozen dataclasses (reports, probe results, alarm definitions), Pydantic
    models (targets, inventories, topology), enums (whose ``value`` is the
    stable wire form) and datetimes (frozen as ISO-8601).  Anything else falls
    back to ``str`` so an unexpected type is visible in the diff rather than
    raising during serialization.
    """
    if isinstance(value, Enum):
        # The enum's value is the wire form; its member name is not.
        return _jsonable(value.value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        # Pydantic v2 models render themselves in JSON mode.
        return model_dump(mode="json")
    if not isinstance(value, type) and dataclasses.is_dataclass(value):
        return {
            field.name: _jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        # Keys are normalized too: enum-keyed dicts are common in reports.
        return {str(_jsonable(key)): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence):
        return [_jsonable(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted(_jsonable(item) for item in value)
    return str(value)


def _raise_mismatch(path: Path, expected: str, produced_text: str) -> None:
    """Raise a GoldenMismatchError showing the first differing region."""
    expected_lines = expected.splitlines()
    produced_lines = produced_text.splitlines()
    # Truncating to the shorter side is deliberate: a pure length difference
    # falls through to the length-mismatch raise below.
    for index, (expected_line, produced_line) in enumerate(
        zip(expected_lines, produced_lines, strict=False), start=1
    ):
        if expected_line != produced_line:
            raise GoldenMismatchError(
                f"golden file {path} differs at line {index}:\n"
                f"  expected: {expected_line!r}\n"
                f"  produced: {produced_line!r}"
            )
    raise GoldenMismatchError(
        f"golden file {path} differs in length "
        f"({len(expected_lines)} vs {len(produced_lines)} lines)"
    )
