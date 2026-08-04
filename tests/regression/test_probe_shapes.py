"""Regression: the ``ProbeResult`` shape every probe emits.

Each probe (TCP, ICMP, UDP) reports its outcome through one shared contract —
``ProbeResult`` — and the metrics, alerting and reporting layers all read that
contract by field name and by the string *values* of ``probe_type`` and
``error_class``.  Those strings are used verbatim as CloudWatch metric
dimensions and in rendered reports, so renaming ``ProbeErrorClass.TIMEOUT``
from ``"timeout"`` to anything else, or dropping a field, silently breaks a
downstream surface without breaking the probe's own logic.

This test freezes:

* the field set of ``ProbeResult`` and the order it serializes in,
* the wire value of every ``ProbeType`` a probe emits (``tcp``/``icmp``/``udp``),
* the wire value of every ``ProbeErrorClass`` the taxonomy defines,
* the invariant that a success carries ``error_class = null`` and a failure
  carries a class plus ``raw`` diagnostic text.

Why regression, not unit: ``tests/unit/probes`` already drives each probe's
socket/subprocess logic against fakes and asserts the *right* class is chosen
for a given failure.  This pins the *serialized shape and vocabulary* the rest
of the pipeline consumes — a different guarantee that must survive any probe
refactor.  Results are constructed with a frozen timestamp rather than by
running a probe, because a real probe would open a socket or spawn ``ping``
(forbidden here) and would produce a non-deterministic latency and clock.

No production code changes are required: this photographs the existing
``ProbeResult`` and ``ProbeErrorClass`` definitions.
"""

from __future__ import annotations

import pytest

from cloudprobe.config.models import ProbeType, Target
from cloudprobe.probes import ProbeErrorClass, ProbeResult
from tests.regression.conftest import FROZEN_TIME
from tests.regression.golden import assert_against_golden

_TARGET = Target(
    target_id="web-1",
    host="10.20.1.10",
    port=443,
    probe_types=[ProbeType.TCP],
    vpc_id="vpc-aaa",
    subnet_id="subnet-aaa",
    instance_id="i-web1",
    label="web",
    tags={"Environment": "lab", "Name": "web"},
)


def _result(
    probe_type: ProbeType,
    *,
    success: bool,
    latency_ms: float,
    error_class: ProbeErrorClass | None = None,
    raw: str | None = None,
) -> ProbeResult:
    return ProbeResult(
        target=_TARGET,
        probe_type=probe_type,
        success=success,
        latency_ms=latency_ms,
        timestamp=FROZEN_TIME,
        error_class=error_class,
        raw=raw,
    )


@pytest.mark.regression
class TestProbeResultShape:
    def test_tcp_success_shape_is_frozen(self, update_goldens: bool) -> None:
        result = _result(ProbeType.TCP, success=True, latency_ms=12.5)
        assert_against_golden(result, "probe_tcp_success.json", update=update_goldens)

    def test_icmp_success_shape_is_frozen(self, update_goldens: bool) -> None:
        result = _result(ProbeType.ICMP, success=True, latency_ms=1.25)
        assert_against_golden(result, "probe_icmp_success.json", update=update_goldens)

    def test_udp_success_shape_is_frozen(self, update_goldens: bool) -> None:
        result = _result(ProbeType.UDP, success=True, latency_ms=8.0)
        assert_against_golden(result, "probe_udp_success.json", update=update_goldens)

    def test_tcp_timeout_failure_shape_is_frozen(self, update_goldens: bool) -> None:
        result = _result(
            ProbeType.TCP,
            success=False,
            latency_ms=250.0,
            error_class=ProbeErrorClass.TIMEOUT,
            raw="timed out",
        )
        assert_against_golden(result, "probe_tcp_timeout.json", update=update_goldens)


@pytest.mark.regression
class TestProbeErrorTaxonomy:
    def test_error_class_vocabulary_is_frozen(self, update_goldens: bool) -> None:
        # The complete set of wire values the failure taxonomy exposes.  Frozen
        # so that adding, removing or renaming a class is a deliberate, visible
        # change — these strings are metric dimensions downstream.
        vocabulary = {member.name: member.value for member in ProbeErrorClass}
        assert_against_golden(
            vocabulary,
            "probe_error_class_vocabulary.json",
            update=update_goldens,
        )

    def test_probe_type_vocabulary_is_frozen(self, update_goldens: bool) -> None:
        vocabulary = {member.name: member.value for member in ProbeType}
        assert_against_golden(
            vocabulary,
            "probe_type_vocabulary.json",
            update=update_goldens,
        )
