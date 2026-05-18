#!/usr/bin/env python3
"""Regression tests for polarity_audit.py.

The module is a pure-Python analyzer that consumes slice_bakeoff CSV
output and produces a per (model × signal) verdict + registry-direction
recommendation. Tests pin:

  * Hanley-McNeil SE math (known reference values).
  * CI computation + clamping at [0, 1].
  * Cell classification (consistent / inverted / chance).
  * Verdict logic — all five cases from SPEC_polarity_audit.md.
  * Recommendation rationale text + structured fields.
  * CSV loading for v1 (no CIs) and v2 (with CIs) formats.
  * Registry-direction override parsing.
  * End-to-end build_audit against synthetic rows.
  * Integration end-to-end against the bundled MAGE 5K slicer CSV.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import polarity_audit as pa  # type: ignore


# --------------- Hanley-McNeil SE ---------------------------------


def test_hanley_mcneil_se_known_value():
    """AUC=0.8, n_p=50, n_n=50 → SE ≈ 0.0445 by direct computation
    of the Hanley-McNeil 1982 formula (Q1=0.667, Q2=0.711). Test
    accepts the formula's exact value within FP tolerance."""
    se = pa.hanley_mcneil_se(0.8, 50, 50)
    assert 0.044 <= se <= 0.046, (
        f"Expected SE ≈ 0.0445 for AUC=0.8 at n=50+50; got {se}"
    )


def test_hanley_mcneil_se_scales_inversely_with_sqrt_n():
    """SE should decrease as ~1/sqrt(n). At fixed AUC, the SE at
    n=200 should be ≈ half the SE at n=50 (since sqrt(4) = 2)."""
    import math
    se_small = pa.hanley_mcneil_se(0.7, 50, 50)
    se_large = pa.hanley_mcneil_se(0.7, 200, 200)
    ratio = se_small / se_large
    # ratio ≈ 2 (within ~20% — the Q1/Q2 terms don't scale exactly
    # but the 1/(n_p·n_n) prefactor dominates).
    assert 1.7 < ratio < 2.3, (
        f"SE should drop ~2x going from n=50+50 to n=200+200; "
        f"got ratio {ratio}"
    )


def test_hanley_mcneil_se_zero_n_returns_nan():
    """Degenerate case: n=0 produces NaN. Pin so downstream consumers
    can rely on isnan() as the failure-mode signal."""
    import math
    assert math.isnan(pa.hanley_mcneil_se(0.8, 0, 50))
    assert math.isnan(pa.hanley_mcneil_se(0.8, 50, 0))


def test_hanley_mcneil_se_at_boundary_auc_does_not_crash():
    """AUC=0 or AUC=1 are floating-point edge cases for the Q1/Q2
    formula. The SE should be computable (possibly 0) without
    raising or producing NaN."""
    se_lo = pa.hanley_mcneil_se(0.001, 50, 50)
    se_hi = pa.hanley_mcneil_se(0.999, 50, 50)
    assert se_lo >= 0.0
    assert se_hi >= 0.0


# --------------- CI clamping --------------------------------------


def test_ci95_clamps_at_zero_and_one():
    """The normal-approximation CI can extend past [0, 1] for extreme
    AUC values; the function clamps to the legal AUC range."""
    lo, hi = pa.ci95(0.05, 0.1)
    assert lo == 0.0  # 0.05 - 0.196 < 0; clamped
    lo, hi = pa.ci95(0.95, 0.1)
    assert hi == 1.0  # 0.95 + 0.196 > 1; clamped


def test_ci95_symmetric_in_normal_range():
    """At mid-range AUC the CI is symmetric around the point estimate
    (within rounding)."""
    auc = 0.7
    se = 0.02
    lo, hi = pa.ci95(auc, se)
    assert abs((auc - lo) - (hi - auc)) < 1e-9


# --------------- Direction-aware transform ------------------------


def test_to_direction_aware_gt_is_identity():
    """For ``gt`` signals, direction-aware bounds equal the raw bounds.
    Registry `gt` means AI > human expected; raw AUC > 0.5 already
    means the expected direction."""
    lo, hi = pa.to_direction_aware(0.40, 0.45, "gt")
    assert lo == 0.40
    assert hi == 0.45


def test_to_direction_aware_lt_swaps_and_complements():
    """For ``lt`` signals, direction-aware bounds are (1-hi, 1-lo).
    Registry `lt` means AI < human expected; raw AUC < 0.5 (AI
    scored lower) is the expected direction, so 1-raw is the
    direction-aware AUC, and the lo/hi bounds swap under the
    complement transform.

    Concrete: raw bounds [0.38, 0.42] (AI scored lower, matches lt).
    Direction-aware bounds: [1 - 0.42, 1 - 0.38] = [0.58, 0.62]
    (above 0.5 → consistent). The swap is the key — without it the
    classifier sees [0.62, 0.58] which has lo > hi, an invalid CI."""
    lo, hi = pa.to_direction_aware(0.38, 0.42, "lt")
    assert abs(lo - 0.58) < 1e-9
    assert abs(hi - 0.62) < 1e-9
    # Ordering preserved: lo < hi.
    assert lo < hi


def test_to_direction_aware_lt_high_raw_auc_becomes_low_da():
    """Raw AUC 0.62 (AI scored higher) on a registered-`lt` signal:
    under the direction-aware transform the bounds drop below 0.5,
    surfacing the inverted classification. This is the regression-
    guard case — a classifier that compared raw bounds to 0.5
    without the direction-aware transform would call this consistent
    rather than inverted."""
    lo, hi = pa.to_direction_aware(0.60, 0.64, "lt")
    assert hi < 0.5
    assert lo < 0.5


# --------------- Cell classification -------------------------------


def test_classify_consistent_when_lower_bound_above_half():
    assert pa.classify_cell(0.55, 0.65) == "consistent"


def test_classify_inverted_when_upper_bound_below_half():
    assert pa.classify_cell(0.30, 0.40) == "inverted"


def test_classify_chance_when_ci_brackets_half():
    assert pa.classify_cell(0.45, 0.55) == "chance"
    assert pa.classify_cell(0.50, 0.50) == "chance"
    assert pa.classify_cell(0.49, 0.51) == "chance"


def test_classify_chance_when_lower_bound_exactly_half():
    """Edge case: lo == 0.5 is NOT consistent (must be strict gt)."""
    assert pa.classify_cell(0.5, 0.6) == "chance"


# --------------- Verdict logic ------------------------------------


def test_verdict_globally_consistent_via_aggregate_ci():
    """All cells consistent, aggregate CI strictly above 0.5 →
    globally_consistent."""
    verdict = pa.polarity_verdict(
        ["consistent", "consistent", "consistent", "chance"],
        da_aggregate_auc=0.65, da_aggregate_se=0.02,
    )
    assert verdict == "globally_consistent"


def test_verdict_globally_consistent_via_n_consistent_ge_3():
    """All cells consistent or chance, n_consistent ≥ 3 →
    globally_consistent even if aggregate CI is wide."""
    verdict = pa.polarity_verdict(
        ["consistent", "consistent", "consistent", "chance"],
        da_aggregate_auc=0.51, da_aggregate_se=0.10,  # CI brackets 0.5
    )
    assert verdict == "globally_consistent"


def test_verdict_globally_inverted_via_aggregate_ci():
    verdict = pa.polarity_verdict(
        ["inverted", "inverted", "inverted", "chance"],
        da_aggregate_auc=0.35, da_aggregate_se=0.02,
    )
    assert verdict == "globally_inverted"


def test_verdict_globally_inverted_via_n_inverted_ge_3():
    """Mirror of consistent-via-count. Three inverted cells override
    aggregate-CI wiggling."""
    verdict = pa.polarity_verdict(
        ["inverted", "inverted", "inverted", "chance"],
        da_aggregate_auc=0.49, da_aggregate_se=0.10,
    )
    assert verdict == "globally_inverted"


def test_verdict_comparator_dependent():
    """≥2 inverted AND ≥2 consistent → comparator_dependent."""
    verdict = pa.polarity_verdict(
        ["consistent", "consistent", "inverted", "inverted", "chance"],
        da_aggregate_auc=0.50, da_aggregate_se=0.01,
    )
    assert verdict == "comparator_dependent"


def test_verdict_chance_when_almost_all_cells_chance():
    """n_consistent + n_inverted < 2 → chance."""
    verdict = pa.polarity_verdict(
        ["chance", "chance", "chance", "consistent"],
        da_aggregate_auc=0.51, da_aggregate_se=0.05,
    )
    assert verdict == "chance"


def test_verdict_mixed_noisy_otherwise():
    """One inverted + one consistent = some signal in both
    directions but neither side strong enough → mixed_noisy."""
    verdict = pa.polarity_verdict(
        ["consistent", "inverted", "chance", "chance"],
        da_aggregate_auc=0.50, da_aggregate_se=0.05,
    )
    assert verdict == "mixed_noisy"


# --------------- Recommendation logic ------------------------------


def test_recommendation_globally_consistent_keeps_direction():
    rec = pa.polarity_recommendation(
        "globally_consistent", "gt", 0.65, 0.61, 0.69,
    )
    assert rec["default"] == "gt"
    assert "consistent" in rec["rationale"].lower()


def test_recommendation_globally_inverted_flips_direction():
    rec = pa.polarity_recommendation(
        "globally_inverted", "gt", 0.40, 0.36, 0.44,
    )
    assert rec["default"] == "lt"
    assert "flip" in rec["rationale"].lower()


def test_recommendation_globally_inverted_flips_lt_to_gt():
    """The flip is symmetric: lt-registered signal that's inverted
    recommends gt."""
    rec = pa.polarity_recommendation(
        "globally_inverted", "lt", 0.65, 0.61, 0.69,
    )
    assert rec["default"] == "gt"


def test_recommendation_comparator_dependent_returns_none_default():
    rec = pa.polarity_recommendation(
        "comparator_dependent", "gt", 0.50, 0.48, 0.52,
    )
    assert rec["default"] is None
    assert "direction_by_comparator" in rec["rationale"]


# --------------- CSV loading --------------------------------------


def _write_csv(tmp_path: Path, rows: list[dict], columns: list[str]) -> Path:
    path = tmp_path / "slice_analysis.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def test_load_v1_csv_computes_cis_on_the_fly(tmp_path: Path):
    """v1 CSVs lack the se / auc_lo / auc_hi columns; the loader
    computes Hanley-McNeil CIs from (auc, n_pos, n_neg) on load."""
    v1_columns = [
        "corpus", "model", "signal", "slice_key", "slice_value",
        "n_pos", "n_neg", "auc", "da_auc", "abs_signal",
    ]
    rows = [{
        "corpus": "mage", "model": "mxbai", "signal": "adjacent_cosine_mean",
        "slice_key": "ALL", "slice_value": "all",
        "n_pos": "1000", "n_neg": "1500",
        "auc": "0.4151", "da_auc": "0.4151", "abs_signal": "0.0849",
    }]
    csv_path = _write_csv(tmp_path, rows, v1_columns)
    loaded = pa.load_slicer_csv(csv_path)
    assert len(loaded) == 1
    row = loaded[0]
    # CIs computed; non-NaN; auc_lo < auc < auc_hi.
    assert row["se"] > 0.0
    assert row["auc_lo"] < row["auc"] < row["auc_hi"]
    # CI bounds are bounded in [0, 1].
    assert 0.0 <= row["auc_lo"] <= 1.0
    assert 0.0 <= row["auc_hi"] <= 1.0


def test_load_v2_csv_uses_existing_cis(tmp_path: Path):
    """v2 CSVs have explicit CI columns; the loader trusts them
    rather than recomputing."""
    v2_columns = [
        "corpus", "model", "signal", "slice_key", "slice_value",
        "n_pos", "n_neg", "auc", "da_auc", "abs_signal",
        "se", "auc_lo", "auc_hi", "da_auc_lo", "da_auc_hi",
        "abs_signal_lo", "abs_signal_hi",
    ]
    rows = [{
        "corpus": "mage", "model": "mxbai", "signal": "adjacent_cosine_mean",
        "slice_key": "ALL", "slice_value": "all",
        "n_pos": "1000", "n_neg": "1500",
        "auc": "0.4151", "da_auc": "0.4151", "abs_signal": "0.0849",
        "se": "0.0099", "auc_lo": "0.3957", "auc_hi": "0.4345",
        "da_auc_lo": "0.3957", "da_auc_hi": "0.4345",
        "abs_signal_lo": "0.0655", "abs_signal_hi": "0.1043",
    }]
    csv_path = _write_csv(tmp_path, rows, v2_columns)
    loaded = pa.load_slicer_csv(csv_path)
    assert len(loaded) == 1
    assert abs(loaded[0]["se"] - 0.0099) < 1e-6
    assert abs(loaded[0]["auc_lo"] - 0.3957) < 1e-6


def test_load_csv_skips_malformed_rows(tmp_path: Path):
    """Rows with missing n_pos / n_neg / auc are dropped, not faulted."""
    columns = [
        "corpus", "model", "signal", "slice_key", "slice_value",
        "n_pos", "n_neg", "auc", "da_auc", "abs_signal",
    ]
    rows = [
        {"corpus": "mage", "model": "mxbai", "signal": "x",
         "slice_key": "ALL", "slice_value": "all",
         "n_pos": "100", "n_neg": "100",
         "auc": "0.55", "da_auc": "0.55", "abs_signal": "0.05"},
        {"corpus": "mage", "model": "mxbai", "signal": "x",
         "slice_key": "ALL", "slice_value": "all",
         "n_pos": "", "n_neg": "100",
         "auc": "0.55", "da_auc": "0.55", "abs_signal": "0.05"},
    ]
    csv_path = _write_csv(tmp_path, rows, columns)
    loaded = pa.load_slicer_csv(csv_path)
    assert len(loaded) == 1  # Bad row dropped.


# --------------- Registry-direction override parsing ---------------


def test_registry_override_parses_signal_equals_direction():
    """Overriding a registry direction works for any signal; pinning
    a few specific override targets demonstrates the parser. The
    1.95.0 registry has all surprisal signals as ``gt`` and
    cosine signals as ``lt``; passing explicit overrides flips them
    individually while non-overridden signals keep their 1.95.0
    defaults."""
    overrides = pa.parse_registry_overrides([
        "adjacent_cosine_mean=gt",  # override the 1.95.0 lt
        "surprisal_acf_lag1=gt",    # override the 1.95.0 lt
    ])
    assert overrides["adjacent_cosine_mean"] == "gt"
    assert overrides["surprisal_acf_lag1"] == "gt"
    # Non-overridden signals retain their 1.95.0 defaults.
    assert overrides["surprisal_mean"] == "gt"
    assert overrides["adjacent_cosine_sd"] == "lt"


def test_registry_override_rejects_invalid_direction(capsys):
    """Direction must be 'gt' or 'lt'; anything else logs a warning
    and is silently dropped from the overrides."""
    overrides = pa.parse_registry_overrides([
        "adjacent_cosine_mean=bogus",
    ])
    captured = capsys.readouterr()
    assert "bogus" in captured.err
    # The 1.95.0 default for adjacent_cosine_mean is `lt`; an invalid
    # override leaves the default in place.
    assert overrides["adjacent_cosine_mean"] == "lt"


def test_registry_override_rejects_malformed_item(capsys):
    """Items without '=' log a warning and are ignored."""
    overrides = pa.parse_registry_overrides(["bare_signal_name"])
    captured = capsys.readouterr()
    assert "bare_signal_name" in captured.err


# --------------- End-to-end build_audit ----------------------------


def _row(
    model: str, signal: str, slice_key: str, slice_value: str,
    n_pos: int, n_neg: int, auc: float,
) -> dict:
    se = pa.hanley_mcneil_se(auc, n_pos, n_neg)
    lo, hi = pa.ci95(auc, se)
    return {
        "model": model, "signal": signal,
        "slice_key": slice_key, "slice_value": slice_value,
        "n_pos": n_pos, "n_neg": n_neg,
        "auc": auc, "da_auc": auc, "se": se,
        "auc_lo": lo, "auc_hi": hi,
    }


def test_build_audit_globally_consistent_for_lt_signal_with_low_raw_auc():
    """End-to-end: rows for a signal whose passed-in registry
    direction is ``lt``, with raw AUC < 0.5 (AI scoring lower than
    humans). Produces ``globally_consistent`` — the passed-in
    direction matches the empirical sign. Pins the direction-aware
    classification on an arbitrary signal name to decouple the test
    from the current registry encoding."""
    rows = [
        _row("m1", "test_signal_lt", "ALL", "all", 1000, 1000, 0.38),
        _row("m1", "test_signal_lt", "length_bucket", "lt_200", 300, 300, 0.40),
        _row("m1", "test_signal_lt", "length_bucket", "200_499", 400, 400, 0.35),
        _row("m1", "test_signal_lt", "length_bucket", "500_999", 200, 200, 0.39),
    ]
    audit = pa.build_audit(
        rows, registry_directions={"test_signal_lt": "lt"},
    )
    assert len(audit["results"]) == 1
    r = audit["results"][0]
    assert r["model"] == "m1"
    assert r["signal"] == "test_signal_lt"
    assert r["verdict"] == "globally_consistent"
    assert r["recommended_direction"]["default"] == "lt"  # registry kept
    assert r["aggregate_raw_auc"] == 0.38


def test_build_audit_globally_inverted_for_lt_signal_with_high_raw_auc():
    """Mirror of the above: passed-in ``lt`` registry direction with
    AI scoring HIGHER (raw AUC > 0.5) → ``globally_inverted``. Pins
    that direction-aware classification flips for an ``lt`` signal
    when the data contradicts the direction, regardless of which
    specific signal name we use."""
    rows = [
        _row("m1", "test_signal_lt", "ALL", "all", 1000, 1000, 0.62),
        _row("m1", "test_signal_lt", "length_bucket", "lt_200", 300, 300, 0.60),
        _row("m1", "test_signal_lt", "length_bucket", "200_499", 400, 400, 0.65),
        _row("m1", "test_signal_lt", "length_bucket", "500_999", 200, 200, 0.61),
    ]
    audit = pa.build_audit(
        rows, registry_directions={"test_signal_lt": "lt"},
    )
    assert len(audit["results"]) == 1
    r = audit["results"][0]
    assert r["verdict"] == "globally_inverted"
    assert r["recommended_direction"]["default"] == "gt"  # flipped from lt


def test_build_audit_globally_inverted_recommends_flip():
    """Inverted aggregate + uniformly inverted cells → flip
    recommendation."""
    rows = [
        _row("m1", "test_signal_gt", "ALL", "all", 1000, 1500, 0.41),
        _row("m1", "test_signal_gt", "length_bucket", "lt_200", 400, 500, 0.42),
        _row("m1", "test_signal_gt", "length_bucket", "200_499", 300, 500, 0.40),
        _row("m1", "test_signal_gt", "length_bucket", "500_999", 300, 500, 0.39),
    ]
    audit = pa.build_audit(
        rows, registry_directions={"test_signal_gt": "gt"},
    )
    r = audit["results"][0]
    assert r["verdict"] == "globally_inverted"
    assert r["recommended_direction"]["default"] == "lt"  # gt → lt


def test_build_audit_drops_model_signal_without_aggregate():
    """A (model, signal) with no ALL row is dropped silently; the
    audit can't compute a verdict without the aggregate CI."""
    rows = [
        _row("m1", "x", "length_bucket", "200_499", 100, 100, 0.6),
    ]
    audit = pa.build_audit(rows)
    assert audit["results"] == []


def test_build_audit_uses_comparator_key_in_output():
    rows = [_row("m1", "surprisal_mean", "ALL", "all", 1000, 1000, 0.62)]
    audit = pa.build_audit(
        rows, comparator_key="notes.original_source",
    )
    assert audit.get("comparator_key") == "notes.original_source"


# --------------- Integration: bundled MAGE 5K ---------------------


_BUNDLE_CSV = Path(
    "/home/user/setec-voiceprint/internal/polarity_audit_results/"
    "mage_5k_slice_analysis.csv"
)


_skip_no_bundle = pytest.mark.skipif(
    not _BUNDLE_CSV.exists(),
    reason="MAGE 5K slice CSV from the 2026-05-18 desktop bundle not "
           "present in internal/polarity_audit_results/. Integration "
           "test will skip on a fresh clone.",
)


@_skip_no_bundle
def test_integration_mage_5k_polarity_audit_findings():
    """Pin the 2026-05-18 MAGE 5K polarity-audit verdicts. These are
    the load-bearing findings the polarity_audit tool was built to
    surface; a regression in classification or verdict logic that
    flipped any of these would be a load-bearing test failure."""
    rows = pa.load_slicer_csv(_BUNDLE_CSV)
    assert len(rows) >= 200, f"Expected ≥200 rows; got {len(rows)}"

    audit = pa.build_audit(
        rows, comparator_key="notes.original_source",
    )
    by_ms = {
        (r["model"], r["signal"]): r for r in audit["results"]
    }

    # Post-1.95.0: the framework's COMPRESSION_HEURISTICS now encodes
    # the four direction flips the 2026-05-18 MAGE 5K audit recommended
    # (adjacent_cosine_mean lt, surprisal_mean gt, surprisal_sd gt,
    # surprisal_acf_lag1 lt). Re-running the audit against the same
    # bundle data produces ``globally_consistent`` for those 22 cells
    # — the empirical sign now matches the registered direction.
    # adjacent_cosine_sd is unchanged (it was mixed/chance and not
    # part of the flip recommendation).

    # Tier-3: all four Phase A models on adjacent_cosine_mean are
    # globally_consistent with the post-flip ``lt`` registry direction.
    for model in ("mxbai", "minilm", "harrier", "gemma"):
        verdict = by_ms[(model, "adjacent_cosine_mean")]["verdict"]
        assert verdict == "globally_consistent", (
            f"Expected adjacent_cosine_mean on {model} to be "
            f"globally_consistent against the post-1.95.0 registry "
            f"(direction `lt`); got {verdict}. If this regresses to "
            f"`globally_inverted`, either the registry was reverted "
            f"in COMPRESSION_HEURISTICS or the direction-aware "
            f"classification is bypassing the registry direction."
        )

    # Tier-4: all three surprisal signals on all six Phase B models
    # are globally_consistent with the post-flip directions
    # (surprisal_mean → gt, surprisal_sd → gt, surprisal_acf_lag1 → lt).
    PHASE_B_MODELS = (
        "tinyllama", "llama32_1b", "olmo2_1b",
        "qwen25_1_5b", "qwen3_1_7b", "smollm2_1_7b",
    )
    TIER4_SIGNALS = (
        "surprisal_mean", "surprisal_sd", "surprisal_acf_lag1",
    )
    for model in PHASE_B_MODELS:
        for signal in TIER4_SIGNALS:
            verdict = by_ms[(model, signal)]["verdict"]
            assert verdict == "globally_consistent", (
                f"Expected {signal} on {model} to be "
                f"globally_consistent on MAGE 5K against the post-"
                f"1.95.0 registry; got {verdict}. The registry "
                f"correction (PR encoding the 22 flip recommendations) "
                f"may have been reverted."
            )

    # Recommendation: with the registry now matching empirical
    # direction, the recommendation is to keep the registry direction
    # rather than flip it.
    expected_directions = {
        "surprisal_mean": "gt",
        "surprisal_sd": "gt",
        "surprisal_acf_lag1": "lt",
    }
    for model in PHASE_B_MODELS:
        for signal, expected_dir in expected_directions.items():
            rec = by_ms[(model, signal)]["recommended_direction"]
            assert rec["default"] == expected_dir, (
                f"Expected {signal} on {model} to recommend keeping "
                f"the post-1.95.0 registry direction "
                f"{expected_dir!r}; got {rec['default']!r}"
            )
