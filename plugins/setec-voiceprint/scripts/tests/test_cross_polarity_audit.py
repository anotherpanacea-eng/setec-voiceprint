#!/usr/bin/env python3
"""Regression tests for cross_polarity_audit.py.

The module pivots polarity_audit's joint-evidence verdict into one
verdict per slice value of a chosen slicing dimension (default
``adversarial_class``). Tests pin:

  * Filtering: per-slice row filtering keeps the matching slice cells
    AND the (per-model, per-signal) "ALL" aggregate that
    polarity_audit.build_audit requires.
  * Distinct-slice-value enumeration: collects only the slice_value
    strings present for the chosen slicing dimension.
  * Cross-slice synthesis: per (model, signal), the per-slice verdicts
    are aggregated and ``robust_across_slices`` is set correctly.
  * Recommendation buckets: keep / flip / direction_by_comparator /
    inconclusive / partially-robust.
  * End-to-end build_cross_audit on a synthetic CSV-shaped rows list,
    covering the load-bearing direction_by_comparator case.
  * MAGE-shape edge: a CSV with only one observed slice value
    (e.g., adversarial_class=none for MAGE 5K) produces a clean
    single-slice cross-audit identical to the polarity_audit verdict.
"""

from __future__ import annotations

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

import cross_polarity_audit as cpa  # type: ignore
import polarity_audit as pa  # type: ignore


# --------------- Helpers ----------------------------------------


def _row(
    model: str, signal: str, slice_key: str, slice_value: str,
    n_pos: int, n_neg: int, auc: float,
) -> dict:
    """Build a slicer-CSV-shaped row with CI populated. Matches the
    helper in test_polarity_audit.py."""
    se = pa.hanley_mcneil_se(auc, n_pos, n_neg)
    lo, hi = pa.ci95(auc, se)
    return {
        "model": model, "signal": signal,
        "slice_key": slice_key, "slice_value": slice_value,
        "n_pos": n_pos, "n_neg": n_neg,
        "auc": auc, "da_auc": auc, "se": se,
        "auc_lo": lo, "auc_hi": hi,
    }


# --------------- Filtering --------------------------------------


def test_filter_rows_for_slice_keeps_only_matching_header_cells():
    """The per-slice filter returns ONLY the cells whose
    ``(slice_key, slice_value)`` matches the requested
    ``(slice_by, slice_value)``. The corpus-wide ``ALL`` aggregate
    and other slicing dimensions are dropped — the per-slice
    classifier consumes the within-slice header cell directly, not
    the overall aggregate."""
    rows = [
        _row("m1", "x", "ALL", "all", 1000, 1000, 0.62),
        _row("m1", "x", "adversarial_class", "none", 600, 600, 0.60),
        _row("m1", "x", "adversarial_class", "paraphrase", 400, 400, 0.65),
        _row("m1", "x", "length_bucket", "200_499", 300, 300, 0.61),
    ]
    out = cpa.filter_rows_for_slice(
        rows, slice_by="adversarial_class", slice_value="none",
    )
    keys_kept = [(r["slice_key"], r["slice_value"]) for r in out]
    assert keys_kept == [("adversarial_class", "none")]
    # No ALL aggregate, no length_bucket, no other adversarial_class value.
    assert ("ALL", "all") not in keys_kept
    assert ("length_bucket", "200_499") not in keys_kept
    assert ("adversarial_class", "paraphrase") not in keys_kept


def test_filter_rows_for_slice_with_no_match_returns_empty():
    """When no cell matches the requested slice value, the filter
    returns an empty list. The downstream loop simply produces no
    per-(model, signal) verdicts for that slice value."""
    rows = [
        _row("m1", "x", "ALL", "all", 1000, 1000, 0.62),
        _row("m1", "x", "adversarial_class", "none", 600, 600, 0.60),
    ]
    out = cpa.filter_rows_for_slice(
        rows, slice_by="adversarial_class", slice_value="paraphrase",
    )
    assert out == []


# --------------- Per-slice classifier --------------------------


def test_classify_slice_cell_gt_signal_inverted():
    """``gt``-registered signal with raw AUC well below 0.5 →
    globally_inverted in this slice. da_lo and da_hi are the raw
    bounds (identity for gt); both < 0.5 yields inverted."""
    cell = _row("m1", "adjacent_cosine_mean", "adversarial_class",
                "paraphrase", 1000, 1000, 0.41)
    verdict = cpa.classify_slice_cell(cell, "gt")
    assert verdict == "globally_inverted"


def test_classify_slice_cell_lt_signal_inverted():
    """``lt``-registered signal with raw AUC well above 0.5 (AI
    scored HIGHER than the registry expected) → globally_inverted.
    Pins the direction-aware classification for lt slices — the
    case PR #93's review surfaced as load-bearing."""
    cell = _row("m1", "surprisal_mean", "adversarial_class", "none",
                1000, 1000, 0.62)
    verdict = cpa.classify_slice_cell(cell, "lt")
    assert verdict == "globally_inverted"


def test_classify_slice_cell_lt_signal_consistent():
    """``lt``-registered signal with raw AUC well below 0.5 (AI
    scored lower, matching the registry direction) →
    globally_consistent in this slice."""
    cell = _row("m1", "surprisal_mean", "adversarial_class",
                "paraphrase", 1000, 1000, 0.38)
    verdict = cpa.classify_slice_cell(cell, "lt")
    assert verdict == "globally_consistent"


def test_classify_slice_cell_chance_when_ci_brackets_half():
    """When the per-slice cell's direction-aware CI contains 0.5
    (small n or AUC near 0.5), the within-slice verdict is
    ``chance`` — the slice can't decide the polarity question at
    the available sample size."""
    cell = _row("m1", "x", "adversarial_class", "humanizer", 30, 30, 0.52)
    verdict = cpa.classify_slice_cell(cell, "gt")
    assert verdict == "chance"


def test_distinct_slice_values_enumerates_only_chosen_dimension():
    """``distinct_slice_values`` returns the slice_value strings for
    the chosen slicing dimension only; other slice_keys are ignored.
    Sorted output for deterministic per-slice ordering downstream."""
    rows = [
        _row("m1", "x", "ALL", "all", 1, 1, 0.5),
        _row("m1", "x", "adversarial_class", "none", 1, 1, 0.5),
        _row("m1", "x", "adversarial_class", "paraphrase", 1, 1, 0.5),
        _row("m1", "x", "adversarial_class", "humanizer", 1, 1, 0.5),
        _row("m1", "x", "length_bucket", "200_499", 1, 1, 0.5),
        _row("m1", "x", "length_bucket", "500_999", 1, 1, 0.5),
    ]
    out = cpa.distinct_slice_values(rows, slice_by="adversarial_class")
    assert out == ["humanizer", "none", "paraphrase"]
    out_length = cpa.distinct_slice_values(rows, slice_by="length_bucket")
    assert out_length == ["200_499", "500_999"]


# --------------- Cross-slice synthesis --------------------------


def _make_slice_block(slice_value, model, signal, direction, verdict):
    """Helper: build a per_slice block matching the new shape
    (slice_value + results list of dicts with verdict + registry
    direction)."""
    return {
        "slice_value": slice_value,
        "results": [{
            "model": model, "signal": signal,
            "registry_direction": direction,
            "verdict": verdict,
        }],
    }


def test_summarise_cross_slice_robust_consistent():
    """All slice values produce ``globally_consistent`` → robust;
    recommendation: keep the registry direction."""
    per_slice = [
        _make_slice_block("none", "m1", "x", "gt", "globally_consistent"),
        _make_slice_block(
            "paraphrase", "m1", "x", "gt", "globally_consistent",
        ),
    ]
    cross = cpa.summarise_cross_slice(per_slice)
    assert len(cross) == 1
    r = cross[0]
    assert r["robust_across_slices"] is True
    assert "keep" in r["registry_recommendation"]
    assert "'gt'" in r["registry_recommendation"]


def test_summarise_cross_slice_robust_inverted():
    """All slices produce ``globally_inverted`` → robust;
    recommendation: flip the registry direction."""
    per_slice = [
        _make_slice_block(s, "m1", "x", "gt", "globally_inverted")
        for s in ("none", "paraphrase", "humanizer")
    ]
    cross = cpa.summarise_cross_slice(per_slice)
    r = cross[0]
    assert r["robust_across_slices"] is True
    assert "flip" in r["registry_recommendation"]
    assert "'gt' → 'lt'" in r["registry_recommendation"]


def test_summarise_cross_slice_direction_by_comparator():
    """Mix of consistent + inverted across slice values → non-robust;
    recommendation: direction_by_comparator with the specific slice
    values that hold each direction."""
    per_slice = [
        _make_slice_block(
            "none", "m1", "surprisal_mean", "lt", "globally_inverted",
        ),
        _make_slice_block(
            "paraphrase", "m1", "surprisal_mean", "lt",
            "globally_consistent",
        ),
    ]
    cross = cpa.summarise_cross_slice(per_slice)
    r = cross[0]
    assert r["robust_across_slices"] is False
    assert "direction_by_comparator" in r["registry_recommendation"]
    # The flipped direction for an `lt` signal is `gt`.
    assert "'gt'" in r["registry_recommendation"]
    # Each slice should be named in the recommendation.
    assert "paraphrase" in r["registry_recommendation"]
    assert "none" in r["registry_recommendation"]


def test_summarise_cross_slice_inconclusive_when_all_chance():
    """When every slice produces ``chance`` or ``mixed_noisy``, no
    real-signal slice exists. Recommendation: inconclusive / mark
    experimental rather than guessing a direction."""
    per_slice = [
        _make_slice_block("none", "m1", "x", "gt", "chance"),
        _make_slice_block(
            "paraphrase", "m1", "x", "gt", "mixed_noisy",
        ),
    ]
    cross = cpa.summarise_cross_slice(per_slice)
    r = cross[0]
    assert r["robust_across_slices"] is False
    assert "inconclusive" in r["registry_recommendation"]


def test_summarise_cross_slice_partially_robust():
    """One slice produces a real verdict; others produce chance /
    mixed_noisy. Not enough to recommend a registry change without
    human review."""
    per_slice = [
        _make_slice_block("none", "m1", "x", "gt", "globally_inverted"),
        _make_slice_block("paraphrase", "m1", "x", "gt", "chance"),
    ]
    cross = cpa.summarise_cross_slice(per_slice)
    r = cross[0]
    assert r["robust_across_slices"] is False
    assert "partially robust" in r["registry_recommendation"]
    assert "Human review" in r["registry_recommendation"]


def test_summarise_cross_slice_missing_in_one_slice_is_not_robust():
    """Reviewer P1 on PR #96: a ``(model, signal)`` present in one
    slice but absent in another previously reported as
    ``robust_across_slices: True`` because ``verdict_set`` only had
    one entry. Real bug — the slice with no row had no verdict but
    contributed nothing to the robustness check.

    Fix: every observed slice value must have a verdict (or be marked
    ``"missing"``) for robustness to be True. A missing-in-one-slice
    cell is now reported as non-robust with a data-missing
    recommendation rather than a false flip/keep recommendation."""
    # Slice "none" has the (m1, x) row; slice "paraphrase" does not.
    per_slice = [
        _make_slice_block("none", "m1", "x", "gt", "globally_inverted"),
        {"slice_value": "paraphrase", "results": []},
    ]
    cross = cpa.summarise_cross_slice(per_slice)
    assert len(cross) == 1
    r = cross[0]
    assert r["robust_across_slices"] is False, (
        "A signal missing from a slice cannot be 'robust across "
        "slices'; the missing slice contributes no evidence."
    )
    # The missing slice is recorded explicitly so consumers can see
    # which slices need re-running.
    assert r["verdicts_per_slice"]["paraphrase"] == "missing"
    assert r["verdicts_per_slice"]["none"] == "globally_inverted"
    # The recommendation steers the operator toward re-running the
    # slicer rather than acting on an incomplete picture.
    assert "data missing" in r["registry_recommendation"]
    assert "paraphrase" in r["registry_recommendation"]


def test_summarise_cross_slice_missing_in_multiple_slices_reported():
    """When multiple slices are missing the (model, signal), all of
    them get listed in the recommendation so the operator knows the
    full re-run scope."""
    per_slice = [
        _make_slice_block(
            "none", "m1", "x", "lt", "globally_consistent",
        ),
        {"slice_value": "paraphrase", "results": []},
        {"slice_value": "humanizer", "results": []},
    ]
    cross = cpa.summarise_cross_slice(per_slice)
    r = cross[0]
    assert r["robust_across_slices"] is False
    assert r["verdicts_per_slice"]["paraphrase"] == "missing"
    assert r["verdicts_per_slice"]["humanizer"] == "missing"
    assert "data missing" in r["registry_recommendation"]
    # Both missing slices listed.
    assert "paraphrase" in r["registry_recommendation"]
    assert "humanizer" in r["registry_recommendation"]


# --------------- End-to-end build_cross_audit -------------------


def test_build_cross_audit_single_slice_value_mage_shape():
    """The MAGE 5K bundle case: every row has
    ``adversarial_class=none``. The cross-audit collapses to a single
    slice and produces a verdict driven by the within-slice AUC. The
    cross_summary's recommendation should match the single-slice
    classification."""
    rows = [
        # Aggregate (not consumed by the per-slice classifier).
        _row("m1", "surprisal_mean", "ALL", "all", 1000, 1000, 0.62),
        # The adversarial_class=none cell is the per-slice header.
        _row("m1", "surprisal_mean", "adversarial_class", "none",
             1000, 1000, 0.62),
    ]
    cross_audit = cpa.build_cross_audit(rows, slice_by="adversarial_class")
    assert cross_audit["slice_values"] == ["none"]
    cross = cross_audit["cross_summary"]
    assert len(cross) == 1
    r = cross[0]
    assert r["robust_across_slices"] is True
    # surprisal_mean is registered `gt` post-1.95 (the registry was
    # flipped from `lt` based on the same MAGE 5K audit that this
    # tool was built to surface). Raw 0.62 on gt → consistent → keep
    # direction. Same finding as polarity_audit's integration test
    # against MAGE 5K: 'globally_consistent' + "keep" recommendation.
    assert "keep" in r["registry_recommendation"]
    assert "gt" in r["registry_recommendation"]


def test_build_cross_audit_direction_by_comparator_case():
    """Synthetic RAID-shape: two adversarial classes produce opposite
    verdicts for the same (model, signal). The headline finding the
    tool exists to surface — a registry flip recommendation that
    holds on one attack class but not on another."""
    # Use a generic test signal name with an explicit
    # ``registry_directions`` override so this test pins the
    # classification logic ("does cross_polarity_audit synthesise a
    # direction_by_comparator recommendation when verdicts disagree
    # across slices?") without depending on the framework's current
    # registry encoding. The polarity-flips work (1.95.1) flipped
    # several signal directions in the live registry; the synthetic
    # scenario here would otherwise need to be re-authored every
    # time the registry changes. Generic-signal-with-override is
    # the same pattern test_polarity_audit's classification-logic
    # guards use.
    rows = [
        # Aggregate (informational; not used by the per-slice classifier).
        _row("m1", "test_signal_lt", "ALL", "all", 2000, 2000, 0.55),
        # Attack class A: raw AUC 0.65 (lt registry) → inverted in this slice.
        _row("m1", "test_signal_lt", "adversarial_class", "none",
             1000, 1000, 0.65),
        # Attack class B: raw AUC 0.40 (lt registry) → consistent in this slice.
        _row("m1", "test_signal_lt", "adversarial_class", "paraphrase",
             1000, 1000, 0.40),
    ]
    cross_audit = cpa.build_cross_audit(
        rows, slice_by="adversarial_class",
        registry_directions={"test_signal_lt": "lt"},
    )
    assert sorted(cross_audit["slice_values"]) == ["none", "paraphrase"]
    cross = cross_audit["cross_summary"]
    assert len(cross) == 1
    r = cross[0]
    assert r["model"] == "m1"
    assert r["signal"] == "test_signal_lt"
    # Verdicts differ across slices → non-robust.
    assert r["robust_across_slices"] is False
    # Under direction-aware classification: raw 0.65 on lt is
    # inverted; raw 0.40 on lt is consistent.
    assert r["verdicts_per_slice"]["none"] == "globally_inverted"
    assert r["verdicts_per_slice"]["paraphrase"] == "globally_consistent"
    assert "direction_by_comparator" in r["registry_recommendation"]


def test_build_cross_audit_preserves_per_slice_raw_auc():
    """The output preserves each slice's raw_auc + CI + n under
    ``per_slice`` so consumers can drill into the underlying numbers
    rather than just the synthesised verdict labels."""
    rows = [
        _row("m1", "x", "adversarial_class", "none", 1000, 1000, 0.62),
    ]
    cross_audit = cpa.build_cross_audit(rows, slice_by="adversarial_class")
    per_slice = cross_audit["per_slice"]
    assert len(per_slice) == 1
    slice_block = per_slice[0]
    assert slice_block["slice_value"] == "none"
    assert len(slice_block["results"]) == 1
    cell = slice_block["results"][0]
    assert cell["model"] == "m1"
    assert cell["signal"] == "x"
    assert cell["raw_auc"] == 0.62
    assert cell["n_pos"] == 1000
    assert cell["n_neg"] == 1000
    assert len(cell["raw_auc_ci"]) == 2


# --------------- Markdown rendering -----------------------------


def test_render_markdown_includes_all_observed_slices_as_columns():
    """The markdown table has one column per observed slice value
    plus the standard model / signal / registry / robust /
    recommendation columns. Pins the column ordering."""
    cross_audit = {
        "date": "2026-05-18T00:00:00+00:00",
        "slice_by": "adversarial_class",
        "slice_values": ["none", "paraphrase"],
        "comparator_key": None,
        "cross_summary": [{
            "model": "m1",
            "signal": "surprisal_mean",
            "registry_direction": "lt",
            "verdicts_per_slice": {
                "none": "globally_inverted",
                "paraphrase": "globally_consistent",
            },
            "robust_across_slices": False,
            "registry_recommendation": "direction_by_comparator: ...",
        }],
    }
    md = cpa.render_cross_audit_markdown(cross_audit)
    # Each slice value is a column header.
    assert "| none |" in md or "| none " in md
    assert "paraphrase" in md
    # The non-robust signal is called out below the table.
    assert "Non-robust signals" in md
    assert "m1 × surprisal_mean" in md


def test_render_markdown_robust_case_says_no_routing_needed():
    """When every signal is robust, the markdown declares that no
    direction_by_comparator routing is needed. Pins the positive-case
    summary line so operators reading the report don't have to
    eyeball the empty Non-robust section."""
    cross_audit = {
        "date": "2026-05-18T00:00:00+00:00",
        "slice_by": "adversarial_class",
        "slice_values": ["none"],
        "comparator_key": None,
        "cross_summary": [{
            "model": "m1",
            "signal": "surprisal_mean",
            "registry_direction": "lt",
            "verdicts_per_slice": {"none": "globally_inverted"},
            "robust_across_slices": True,
            "registry_recommendation": "flip registry: 'lt' → 'gt'",
        }],
    }
    md = cpa.render_cross_audit_markdown(cross_audit)
    assert "All signals were robust" in md
    assert "no comparator-class routing needed" in md


# --------------- CLI smoke --------------------------------------


def test_cli_rejects_missing_input_csv(tmp_path, capsys):
    """The CLI surfaces a clean error for a missing input CSV path
    rather than tracebacking. Pins the rc=2 convention."""
    out_json = tmp_path / "out.json"
    rc = cpa.main([
        "--input-csv", str(tmp_path / "nope.csv"),
        "--out-json", str(out_json),
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert "not found" in captured.err.lower()


def test_cli_rejects_unknown_slice_by(tmp_path, capsys):
    """The CLI errors cleanly when --slice-by names a slicing
    dimension that's absent from the input CSV. Lists the available
    slice keys to help the operator fix the command."""
    csv_path = tmp_path / "slice.csv"
    csv_path.write_text(
        "corpus,model,signal,slice_key,slice_value,n_pos,n_neg,auc,da_auc,abs_signal\n"
        "mage,m1,x,ALL,all,1000,1000,0.62,0.62,0.12\n"
        "mage,m1,x,length_bucket,200_499,400,400,0.65,0.65,0.15\n",
        encoding="utf-8",
    )
    out_json = tmp_path / "out.json"
    rc = cpa.main([
        "--input-csv", str(csv_path),
        "--slice-by", "adversarial_class",  # not in the CSV
        "--out-json", str(out_json),
    ])
    assert rc == 3
    captured = capsys.readouterr()
    assert "no rows with slice_key='adversarial_class'" in captured.err.lower()
    assert "length_bucket" in captured.err.lower()


def test_cli_end_to_end_writes_json_and_markdown(tmp_path):
    """Full smoke: build a synthetic slicer CSV with adversarial-
    class diversity, run cross_polarity_audit, verify JSON + markdown
    outputs are written and contain the expected shape."""
    import csv as _csv
    csv_path = tmp_path / "slice.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fp:
        writer = _csv.writer(fp)
        writer.writerow([
            "corpus", "model", "signal", "slice_key", "slice_value",
            "n_pos", "n_neg", "auc", "da_auc", "abs_signal",
        ])
        # Aggregate.
        writer.writerow([
            "synth", "m1", "surprisal_mean", "ALL", "all",
            2000, 2000, 0.55, 0.45, 0.10,
        ])
        # Two adversarial classes with opposing signals.
        writer.writerow([
            "synth", "m1", "surprisal_mean",
            "adversarial_class", "none",
            1000, 1000, 0.65, 0.35, 0.15,
        ])
        writer.writerow([
            "synth", "m1", "surprisal_mean",
            "adversarial_class", "paraphrase",
            1000, 1000, 0.40, 0.60, 0.10,
        ])

    out_json = tmp_path / "cross.json"
    out_md = tmp_path / "cross.md"
    rc = cpa.main([
        "--input-csv", str(csv_path),
        "--slice-by", "adversarial_class",
        "--out-json", str(out_json),
        "--out-markdown", str(out_md),
    ])
    assert rc == 0
    assert out_json.exists()
    assert out_md.exists()
    import json as _json
    payload = _json.loads(out_json.read_text())
    assert payload["tool"] == "cross_polarity_audit"
    assert sorted(payload["slice_values"]) == ["none", "paraphrase"]
    assert len(payload["cross_summary"]) == 1
    # Non-robust signal → flagged in the markdown.
    md = out_md.read_text()
    assert "Non-robust signals" in md
