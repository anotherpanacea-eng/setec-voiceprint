#!/usr/bin/env python3
"""Regression tests for the ThresholdSpec registry refactor.

ThresholdSpec replaces the previous tuple-based COMPRESSION_HEURISTICS
shape with a dataclass that carries calibration metadata
(`provenance` + `provisional`). The mutual-exclusion contract is the
load-bearing addition: a calibrated threshold must declare a
provenance slug; a heuristic threshold must not. These tests guard
against regressions in that contract.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

from variance_audit import (  # type: ignore
    COMPRESSION_HEURISTICS,
    POS_BIGRAM_KL_HEURISTIC,
    ThresholdSpec,
    calibrated_signals,
    provisional_signals,
)


def test_default_threshold_is_provisional_with_no_provenance() -> None:
    spec = ThresholdSpec(
        signal_path="test.foo",
        value=0.5,
        direction="lt",
        weight=1.0,
        length_floor=200,
    )
    assert spec.provisional is True
    assert spec.provenance is None


def test_calibrated_threshold_must_declare_provenance() -> None:
    spec = ThresholdSpec(
        signal_path="test.foo",
        value=0.5,
        direction="lt",
        weight=1.0,
        length_floor=200,
        provenance="some_calibration_slug",
        provisional=False,
    )
    assert spec.provisional is False
    assert spec.provenance == "some_calibration_slug"


def test_provenance_and_provisional_are_mutually_exclusive() -> None:
    """A threshold cannot be both provisional and have a provenance
    slug. Setting provenance to a non-None value must clear
    provisional."""
    if pytest is not None:
        with pytest.raises(ValueError) as exc:
            ThresholdSpec(
                signal_path="test.foo",
                value=0.5,
                direction="lt",
                weight=1.0,
                length_floor=200,
                provenance="slug",
                provisional=True,
            )
        assert "mutually exclusive" in str(exc.value)
    else:
        try:
            ThresholdSpec(
                signal_path="test.foo",
                value=0.5,
                direction="lt",
                weight=1.0,
                length_floor=200,
                provenance="slug",
                provisional=True,
            )
            raise AssertionError("expected ValueError")
        except ValueError:
            pass


def test_non_provisional_must_have_provenance() -> None:
    """A non-provisional threshold without a provenance slug is a
    contract violation: how was it calibrated, and against what?"""
    if pytest is not None:
        with pytest.raises(ValueError) as exc:
            ThresholdSpec(
                signal_path="test.foo",
                value=0.5,
                direction="lt",
                weight=1.0,
                length_floor=200,
                provenance=None,
                provisional=False,
            )
        assert "must declare a provenance" in str(exc.value)


def test_direction_must_be_gt_or_lt() -> None:
    if pytest is not None:
        with pytest.raises(ValueError) as exc:
            ThresholdSpec(
                signal_path="test.foo",
                value=0.5,
                direction="ne",
                weight=1.0,
                length_floor=200,
            )
        assert "'gt' or 'lt'" in str(exc.value)


def test_compression_heuristics_registry_is_well_formed() -> None:
    """Every entry must be a ThresholdSpec with a non-empty signal_path,
    a valid direction, and a positive length_floor. Guards against the
    registry shape regressing back to tuples."""
    assert COMPRESSION_HEURISTICS, "registry is empty"
    for key, spec in COMPRESSION_HEURISTICS.items():
        assert isinstance(spec, ThresholdSpec), (
            f"{key}: expected ThresholdSpec, got {type(spec).__name__}"
        )
        assert spec.signal_path, f"{key}: empty signal_path"
        assert spec.direction in ("gt", "lt"), (
            f"{key}: invalid direction {spec.direction!r}"
        )
        assert spec.length_floor > 0, f"{key}: non-positive length_floor"
        assert spec.weight > 0, f"{key}: non-positive weight"


def test_pos_bigram_kl_is_threshold_spec() -> None:
    assert isinstance(POS_BIGRAM_KL_HEURISTIC, ThresholdSpec)
    assert POS_BIGRAM_KL_HEURISTIC.direction == "gt"
    assert POS_BIGRAM_KL_HEURISTIC.value > 0


def test_signal_helpers_partition_the_registry() -> None:
    """provisional_signals + calibrated_signals must cover every
    registry key with no overlap."""
    p = set(provisional_signals())
    c = set(calibrated_signals())
    assert p & c == set(), f"overlap: {p & c}"
    assert p | c == set(COMPRESSION_HEURISTICS), (
        f"missing: {set(COMPRESSION_HEURISTICS) - (p | c)}"
    )


def test_calibrated_signals_carry_provenance_slugs() -> None:
    """The first calibrated threshold landed in 1.27.0 (`burstiness_B`,
    derived from EditLens val split). Pre-1.27.0 this test asserted
    every signal was provisional; the v1.27.0 commit flipped it to
    require that **calibrated** signals carry a non-None provenance
    slug pointing at `scripts/calibration/thresholds_calibrated.json`.

    Provisional signals (still-uncalibrated) must continue to carry
    provenance=None — the ThresholdSpec dataclass enforces the
    mutex via __post_init__.
    """
    provisional = provisional_signals()
    calibrated = calibrated_signals()
    # Together they account for every signal in the registry.
    assert len(provisional) + len(calibrated) == len(COMPRESSION_HEURISTICS)
    # Every calibrated signal carries a non-None provenance slug.
    for name in calibrated:
        spec = COMPRESSION_HEURISTICS[name]
        assert spec.provenance is not None and spec.provenance != "", (
            f"Calibrated signal {name!r} must carry a provenance slug"
        )
        assert spec.provisional is False, (
            f"Calibrated signal {name!r} must have provisional=False"
        )
    # Every provisional signal carries provenance=None.
    for name in provisional:
        spec = COMPRESSION_HEURISTICS[name]
        assert spec.provenance is None, (
            f"Provisional signal {name!r} must have provenance=None"
        )
        assert spec.provisional is True, (
            f"Provisional signal {name!r} must have provisional=True"
        )
