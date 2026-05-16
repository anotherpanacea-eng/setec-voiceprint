#!/usr/bin/env python3
"""Regression tests for the ThresholdSpec registry contract.

ThresholdSpec replaces the previous tuple-based COMPRESSION_HEURISTICS
shape with a dataclass carrying calibration metadata. The v1.66.0
retier (per `internal/SPEC_calibration_status_retier.md`) replaced
the binary `provisional: bool` with a four-tier `status` enum +
structural_only marker. The per-tier provenance invariants are the
load-bearing contract: calibrated / literature_anchored /
empirically_oriented all require a provenance slug; heuristic must
have provenance=None. These tests guard against regression in that
contract.
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


def test_default_threshold_is_heuristic_with_no_provenance() -> None:
    """v1.66.0 retier: default status is 'heuristic', which requires
    provenance=None."""
    spec = ThresholdSpec(
        signal_path="test.foo",
        value=0.5,
        direction="lt",
        weight=1.0,
        length_floor=200,
    )
    assert spec.status == "heuristic"
    assert spec.provenance is None
    # Backward-compat: derived property still works
    assert spec.provisional is True


def test_calibrated_threshold_must_declare_provenance() -> None:
    """status='calibrated' requires a provenance slug citing the
    labeled corpus + version + reported metrics."""
    spec = ThresholdSpec(
        signal_path="test.foo",
        value=0.5,
        direction="lt",
        weight=1.0,
        length_floor=200,
        provenance="some_corpus_slug",
        status="calibrated",
    )
    assert spec.status == "calibrated"
    assert spec.provenance == "some_corpus_slug"
    # Backward-compat: calibrated → provisional False
    assert spec.provisional is False


def test_literature_anchored_must_declare_provenance() -> None:
    """status='literature_anchored' requires a provenance slug
    citing the publication."""
    spec = ThresholdSpec(
        signal_path="test.foo",
        value=0.5,
        direction="lt",
        weight=1.0,
        length_floor=200,
        provenance="some_paper_slug",
        status="literature_anchored",
    )
    assert spec.status == "literature_anchored"
    assert spec.provenance == "some_paper_slug"
    # Backward-compat: not calibrated → provisional True
    assert spec.provisional is True


def test_empirically_oriented_must_declare_provenance() -> None:
    """status='empirically_oriented' requires a provenance slug
    citing the local source."""
    spec = ThresholdSpec(
        signal_path="test.foo",
        value=0.5,
        direction="lt",
        weight=1.0,
        length_floor=200,
        provenance="some_local_source",
        status="empirically_oriented",
    )
    assert spec.status == "empirically_oriented"
    assert spec.provenance == "some_local_source"


def test_heuristic_must_have_null_provenance() -> None:
    """status='heuristic' (default) must have provenance=None.
    Promote to one of the three lower tiers to attach a slug."""
    if pytest is not None:
        with pytest.raises(ValueError) as exc:
            ThresholdSpec(
                signal_path="test.foo",
                value=0.5,
                direction="lt",
                weight=1.0,
                length_floor=200,
                provenance="some_slug",
                status="heuristic",
            )
        assert "must have provenance=None" in str(exc.value)


def test_calibrated_without_provenance_raises() -> None:
    """The per-tier provenance invariant: calibrated requires a
    slug. A non-None provenance is required."""
    if pytest is not None:
        with pytest.raises(ValueError) as exc:
            ThresholdSpec(
                signal_path="test.foo",
                value=0.5,
                direction="lt",
                weight=1.0,
                length_floor=200,
                provenance=None,
                status="calibrated",
            )
        assert "requires a" in str(exc.value)


def test_literature_anchored_without_provenance_raises() -> None:
    if pytest is not None:
        with pytest.raises(ValueError) as exc:
            ThresholdSpec(
                signal_path="test.foo",
                value=0.5,
                direction="lt",
                weight=1.0,
                length_floor=200,
                provenance=None,
                status="literature_anchored",
            )
        assert "requires" in str(exc.value)


def test_empirically_oriented_without_provenance_raises() -> None:
    if pytest is not None:
        with pytest.raises(ValueError) as exc:
            ThresholdSpec(
                signal_path="test.foo",
                value=0.5,
                direction="lt",
                weight=1.0,
                length_floor=200,
                provenance=None,
                status="empirically_oriented",
            )
        assert "requires" in str(exc.value)


def test_invalid_status_raises() -> None:
    """The status enum is closed: typos and forks of the four-tier
    taxonomy are rejected at construction time."""
    if pytest is not None:
        with pytest.raises(ValueError) as exc:
            ThresholdSpec(
                signal_path="test.foo",
                value=0.5,
                direction="lt",
                weight=1.0,
                length_floor=200,
                status="not_a_real_status",
            )
        assert "status must be one of" in str(exc.value)


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
    """Under the v1.66.0 retier, the registry's status field
    partitions cleanly across five tiers. Verify provisional_signals
    + calibrated_signals + structural_only signals cover every
    registry key with no overlap."""
    p = set(provisional_signals())
    c = set(calibrated_signals())
    s = {
        name for name, spec in COMPRESSION_HEURISTICS.items()
        if spec.status == "structural_only"
    }
    assert p & c == set(), f"overlap between provisional and calibrated: {p & c}"
    assert p | c | s == set(COMPRESSION_HEURISTICS), (
        f"missing: {set(COMPRESSION_HEURISTICS) - (p | c | s)}"
    )


def test_per_tier_provenance_invariants_hold_across_registry() -> None:
    """v1.66.0 retier contract:
      * calibrated / literature_anchored / empirically_oriented all
        require a non-None provenance slug.
      * heuristic must have provenance=None.

    The `provisional` derived property keeps working: True for any
    status other than calibrated / structural_only.
    """
    for name, spec in COMPRESSION_HEURISTICS.items():
        if spec.status == "calibrated":
            assert spec.provenance, (
                f"calibrated signal {name!r} must carry a provenance slug"
            )
            assert spec.provisional is False
        elif spec.status == "literature_anchored":
            assert spec.provenance, (
                f"literature_anchored signal {name!r} must carry a "
                "provenance slug citing the publication"
            )
            assert spec.provisional is True
        elif spec.status == "empirically_oriented":
            assert spec.provenance, (
                f"empirically_oriented signal {name!r} must carry a "
                "provenance slug citing the local source"
            )
            assert spec.provisional is True
        elif spec.status == "heuristic":
            assert spec.provenance is None, (
                f"heuristic signal {name!r} must have provenance=None "
                "(promote to a non-heuristic tier to attach a slug)"
            )
            assert spec.provisional is True
        elif spec.status == "structural_only":
            assert spec.provisional is False  # not provisional, not calibrated


def test_provisional_signals_partitions_correctly_against_calibrated() -> None:
    """v1.66.0 backward-compat: `provisional_signals()` returns
    everything that isn't calibrated and isn't structural_only.
    Union with `calibrated_signals()` covers the registry except
    structural_only entries; intersection is empty."""
    p = set(provisional_signals())
    c = set(calibrated_signals())
    assert p & c == set(), f"overlap: {p & c}"
    # `provisional` = not calibrated and not structural; `calibrated`
    # is its own thing. Union should cover everything except
    # structural_only entries.
    structural = {
        name for name, spec in COMPRESSION_HEURISTICS.items()
        if spec.status == "structural_only"
    }
    assert p | c | structural == set(COMPRESSION_HEURISTICS), (
        f"missing: {set(COMPRESSION_HEURISTICS) - (p | c | structural)}"
    )
