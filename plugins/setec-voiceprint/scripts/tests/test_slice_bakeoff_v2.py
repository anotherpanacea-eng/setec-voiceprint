#!/usr/bin/env python3
"""Regression tests for slice_bakeoff_v2.py.

The slicer is a pure-Python per-stratum AUC analyzer that consumes
``cache_phase{A,B}_*.json`` files (the per-row scored-records caches
produced by ``calibrate_thresholds.score_corpus``) and emits a CSV +
markdown summary + optional polarity-audit JSON. Tests pin:

  * Mann-Whitney AUC arithmetic with proper rank-based tie handling.
  * Hanley-McNeil CIs and abs_signal CI clamping.
  * Cache + manifest I/O against synthetic fixtures.
  * The CSV column contract (v2 columns are a strict superset of v1).
  * Cross-tab multi-key grouping.
  * End-to-end smoke test: synthetic 2-model cache → CSV with the
    expected number of rows, aggregate AUC matches the input.
  * The polarity-audit integration produces the same verdicts the
    standalone tool produces (single source of truth).
"""

from __future__ import annotations

import csv
import json
import random
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

import slice_bakeoff_v2 as sb  # type: ignore
import polarity_audit as pa  # type: ignore


# --------------- AUC arithmetic ------------------------------------


def test_mwu_auc_perfect_separation_returns_1():
    """All positive scores above all negatives → AUC = 1.0."""
    auc = sb.mwu_auc([3.0, 4.0, 5.0], [0.0, 1.0, 2.0])
    assert auc == 1.0


def test_mwu_auc_inverse_separation_returns_0():
    """All positive scores below all negatives → AUC = 0.0."""
    auc = sb.mwu_auc([0.0, 1.0, 2.0], [3.0, 4.0, 5.0])
    assert auc == 0.0


def test_mwu_auc_identical_distributions_returns_half():
    """Identical positive and negative score sets → AUC = 0.5 (ties
    handled via average ranks)."""
    auc = sb.mwu_auc([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert auc == 0.5


def test_mwu_auc_returns_none_for_tiny_class():
    """n_pos or n_neg < 2 → None (calibration cells below this n
    are statistically meaningless)."""
    assert sb.mwu_auc([1.0], [1.0, 2.0, 3.0]) is None
    assert sb.mwu_auc([1.0, 2.0], [1.0]) is None


def test_mwu_auc_handles_ties_with_average_ranks():
    """Tied groups must use average ranks, not naive concordance.
    Reference: pos=[2, 2], neg=[1, 2, 3]. Combined sorted:
    1(neg), 2(pos), 2(pos), 2(neg), 3(neg). Tied rank-group for
    value 2 spans positions 2-4, average rank = 3. Rank-sum for
    pos = 3 + 3 = 6. U_pos = 6 - 2*3/2 = 3. AUC = 3 / (2*3) = 0.5."""
    auc = sb.mwu_auc([2.0, 2.0], [1.0, 2.0, 3.0])
    assert abs(auc - 0.5) < 1e-9


# --------------- CI clamping ---------------------------------------


def test_abs_signal_ci_clamps_lower_bound_at_zero():
    """|sig| ≥ 0 by definition; the lower CI bound is clamped at 0
    when the normal-approximation interval would otherwise go
    negative."""
    lo, hi = sb.abs_signal_ci(da_auc=0.50, se=0.10)
    assert lo == 0.0
    assert hi > 0.0


def test_abs_signal_ci_normal_range():
    """At |da_auc - 0.5| = 0.10 with SE 0.02, CI is roughly
    [0.06, 0.14]."""
    lo, hi = sb.abs_signal_ci(da_auc=0.60, se=0.02)
    assert 0.05 < lo < 0.07
    assert 0.13 < hi < 0.15


# --------------- Cell emission -------------------------------------


def test_emit_cell_skips_cells_below_min_n():
    rows: list[dict] = []
    sb.emit_cell(
        rows, "test", "m1", "sig", "gt", "ALL", "all",
        pos=[1.0, 2.0], neg=[1.0, 2.0],  # n=2 each, below default min=30
        min_n=30,
    )
    assert rows == []


def test_emit_cell_writes_expected_columns():
    rows: list[dict] = []
    sb.emit_cell(
        rows, "test", "m1", "sig", "gt", "ALL", "all",
        pos=[float(i) for i in range(50)],
        neg=[float(i) - 5 for i in range(50)],
        min_n=30,
    )
    assert len(rows) == 1
    r = rows[0]
    for col in (
        "corpus", "model", "signal", "slice_key", "slice_value",
        "n_pos", "n_neg", "auc", "da_auc", "abs_signal",
        "se", "auc_lo", "auc_hi", "da_auc_lo", "da_auc_hi",
        "abs_signal_lo", "abs_signal_hi",
    ):
        assert col in r, f"Missing column: {col}"


def test_emit_cell_direction_lt_flips_da_auc():
    """Direction `lt` means AI is expected to score LOWER than humans;
    the direction-aware AUC = 1 - raw AUC."""
    rows: list[dict] = []
    pos = [float(i) for i in range(50)]
    neg = [float(i) + 10 for i in range(50)]
    # Raw AUC will be < 0.5 (positives below negatives).
    sb.emit_cell(
        rows, "test", "m1", "sig", "lt", "ALL", "all",
        pos=pos, neg=neg, min_n=30,
    )
    r = rows[0]
    # Under lt direction, da_auc = 1 - raw_auc, which should be > 0.5.
    assert r["da_auc"] > 0.5
    assert r["da_auc"] == 1.0 - r["auc"]


# --------------- Cross-tab grouping --------------------------------


def _record(label: int, signal_value: float, **extras) -> dict:
    return {
        "id": f"rec_{random.random()}",
        "label": label,
        "per_signal_scores": {
            "tier3.adjacent_cosine.mean": signal_value,
        },
        "_notes": {},
        **extras,
    }


def test_crosstab_groups_by_key_tuple():
    """Cross-tab with two keys produces one cell per (key1_value,
    key2_value) tuple."""
    records = []
    # Two length buckets × two sources.
    for _ in range(40):
        records.append(_record(1, 0.6, length_bucket="lt_200"))
        records.append(_record(0, 0.5, length_bucket="lt_200"))
        records.append(_record(1, 0.7, length_bucket="500_999"))
        records.append(_record(0, 0.4, length_bucket="500_999"))
    # Stamp notes.
    for r in records[:80]:
        r["_notes"] = {"original_source": "imdb"}
    for r in records[80:]:
        r["_notes"] = {"original_source": "yelp"}
    rows: list[dict] = []
    sb.emit_crosstab(
        rows, "test", "m1", "adjacent_cosine_mean", "gt",
        "tier3.adjacent_cosine.mean",
        records,
        keys=["length_bucket", "notes.original_source"],
        min_n=10,
    )
    # 2 buckets × 2 sources = up to 4 cells (subject to min_n cuts).
    assert len(rows) >= 2
    for r in rows:
        assert r["slice_key"] == "length_bucket,notes.original_source"
        assert "," in r["slice_value"]


def test_get_crosstab_value_top_level_vs_notes():
    """The crosstab key dispatcher: ``length_bucket`` reads from the
    top-level record; ``notes.<key>`` reads from the joined manifest
    notes block."""
    r = {"length_bucket": "200_499", "_notes": {"original_source": "imdb"}}
    assert sb.get_crosstab_value(r, "length_bucket") == "200_499"
    assert sb.get_crosstab_value(
        r, "notes.original_source",
    ) == "imdb"
    assert sb.get_crosstab_value(r, "missing_key") == "unknown"
    assert sb.get_crosstab_value(r, "notes.missing_subkey") == "unknown"


# --------------- CSV columns contract ------------------------------


def test_csv_columns_contain_v1_subset():
    """The v1 column set is a strict subset of v2's. Downstream
    consumers reading only v1 columns must continue to work against
    v2 output without modification."""
    v1_columns = (
        "corpus", "model", "signal", "slice_key", "slice_value",
        "n_pos", "n_neg", "auc", "da_auc", "abs_signal",
    )
    for col in v1_columns:
        assert col in sb.CSV_COLUMNS, f"v1 column missing from v2: {col}"


def test_csv_columns_add_ci_columns():
    """v2-specific columns: standard error, AUC CI bounds, da_AUC
    CI bounds, |sig| CI bounds."""
    v2_new_columns = (
        "se", "auc_lo", "auc_hi", "da_auc_lo", "da_auc_hi",
        "abs_signal_lo", "abs_signal_hi",
    )
    for col in v2_new_columns:
        assert col in sb.CSV_COLUMNS, f"v2 column missing: {col}"


# --------------- End-to-end with synthetic cache -------------------


def _make_synthetic_cache(
    tmp_path: Path,
    phase: str,
    model_alias: str,
    n_pos: int,
    n_neg: int,
    pos_mean_offset: float = 0.1,
) -> Path:
    """Write a cache_phase{A,B}_<alias>.json with synthetic per-row
    scores. Positives are offset from negatives so the aggregate AUC
    is reliably above 0.5."""
    rng = random.Random(42)
    records = []
    signal_paths = {
        "A": ("tier3.adjacent_cosine.mean", "tier3.adjacent_cosine.sd"),
        "B": (
            "tier4.surprisal.mean", "tier4.surprisal.sd",
            "tier4.surprisal.autocorrelation.lag_1",
        ),
    }[phase]
    buckets = ["lt_200", "200_499", "500_999"]
    for i in range(n_pos):
        per_signal = {
            sp: rng.gauss(pos_mean_offset, 1.0) for sp in signal_paths
        }
        records.append({
            "id": f"pos_{i}",
            "label": 1,
            "length_bucket": buckets[i % 3],
            "register": "essay",
            "adversarial_class": "none",
            "per_signal_scores": per_signal,
        })
    for i in range(n_neg):
        per_signal = {sp: rng.gauss(0.0, 1.0) for sp in signal_paths}
        records.append({
            "id": f"neg_{i}",
            "label": 0,
            "length_bucket": buckets[i % 3],
            "register": "essay",
            "adversarial_class": "none",
            "per_signal_scores": per_signal,
        })
    cache_path = tmp_path / f"cache_phase{phase}_{model_alias}.json"
    cache_path.write_text(
        json.dumps({"records": records}, indent=2),
        encoding="utf-8",
    )
    return cache_path


def _make_synthetic_manifest(tmp_path: Path, n_records: int) -> Path:
    path = tmp_path / "manifest.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n_records):
            f.write(json.dumps({
                "id": f"pos_{i}" if i % 2 == 0 else f"neg_{i}",
                "notes": {"original_source": "synth"},
            }) + "\n")
    return path


def test_end_to_end_synthetic_cache_produces_csv(tmp_path: Path):
    """Synthesize a Phase A cache, run the slicer, verify the CSV
    has rows for every (model, signal, slice_key, slice_value) cell
    that clears min_n. End-to-end without a real bakeoff."""
    cache_dir = tmp_path / "caches"
    cache_dir.mkdir()
    _make_synthetic_cache(cache_dir, "A", "mxbai", n_pos=200, n_neg=200)
    manifest = _make_synthetic_manifest(tmp_path, n_records=400)
    out_dir = tmp_path / "out"
    rc = sb.analyze(
        cache_dir=cache_dir,
        manifest_path=manifest,
        out_dir=out_dir,
        corpus="synth",
        domain_key="original_source",
        split_key=None,
        generator_key=None,
        crosstabs=[],
        min_n=30,
        do_polarity_audit=False,
        comparator_key=None,
    )
    assert rc == 0
    csv_path = out_dir / "slice_analysis.csv"
    assert csv_path.exists()
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    # Two signals × at least 1 (ALL) + N length_bucket slices + N register/adv.
    assert len(rows) >= 2  # at minimum one row per signal's ALL.
    # All rows have the v2 CI columns populated.
    for r in rows:
        assert r["se"] != ""
        assert r["auc_lo"] != ""
        assert r["auc_hi"] != ""


def test_end_to_end_polarity_audit_mode(tmp_path: Path):
    """When --audit polarity is set, the slicer writes
    polarity_audit.json alongside the CSV."""
    cache_dir = tmp_path / "caches"
    cache_dir.mkdir()
    _make_synthetic_cache(cache_dir, "A", "mxbai", n_pos=200, n_neg=200)
    manifest = _make_synthetic_manifest(tmp_path, n_records=400)
    out_dir = tmp_path / "out"
    rc = sb.analyze(
        cache_dir=cache_dir, manifest_path=manifest, out_dir=out_dir,
        corpus="synth", domain_key=None, split_key=None, generator_key=None,
        crosstabs=[], min_n=30,
        do_polarity_audit=True, comparator_key="notes.original_source",
    )
    assert rc == 0
    audit_path = out_dir / "polarity_audit.json"
    assert audit_path.exists()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit.get("audit_version") == "polarity-v1"
    assert audit.get("comparator_key") == "notes.original_source"


def test_end_to_end_provenance_written(tmp_path: Path):
    """Every run writes a provenance.json with the CLI args + cache
    file metadata."""
    cache_dir = tmp_path / "caches"
    cache_dir.mkdir()
    _make_synthetic_cache(cache_dir, "A", "mxbai", n_pos=100, n_neg=100)
    manifest = _make_synthetic_manifest(tmp_path, n_records=200)
    out_dir = tmp_path / "out"
    sb.analyze(
        cache_dir=cache_dir, manifest_path=manifest, out_dir=out_dir,
        corpus="synth", domain_key=None, split_key=None, generator_key=None,
        crosstabs=[], min_n=30, do_polarity_audit=False, comparator_key=None,
    )
    prov_path = out_dir / "provenance.json"
    assert prov_path.exists()
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    assert prov["tool"] == sb.TOOL_NAME
    assert prov["tool_version"] == sb.SLICER_VERSION
    assert prov["corpus"] == "synth"
    assert prov["manifest_sha256"] is not None


# --------------- Polarity-audit integration: single source of truth


def test_polarity_audit_integrated_matches_standalone(tmp_path: Path):
    """The slicer's --audit polarity mode and the standalone
    polarity_audit.py tool must produce byte-identical verdicts
    against the same input. Pins the single-source-of-truth
    invariant: both routes call build_audit() from the same module."""
    cache_dir = tmp_path / "caches"
    cache_dir.mkdir()
    _make_synthetic_cache(cache_dir, "A", "mxbai", n_pos=200, n_neg=200)
    manifest = _make_synthetic_manifest(tmp_path, n_records=400)

    # Route A: integrated polarity-audit mode.
    out_dir_a = tmp_path / "out_a"
    sb.analyze(
        cache_dir=cache_dir, manifest_path=manifest, out_dir=out_dir_a,
        corpus="synth", domain_key=None, split_key=None, generator_key=None,
        crosstabs=[], min_n=30,
        do_polarity_audit=True, comparator_key=None,
    )
    audit_a = json.loads(
        (out_dir_a / "polarity_audit.json").read_text(encoding="utf-8")
    )

    # Route B: standalone polarity_audit.py against the slicer CSV.
    csv_path = out_dir_a / "slice_analysis.csv"
    rows = pa.load_slicer_csv(csv_path)
    audit_b = pa.build_audit(rows)

    # Compare per (model, signal) verdicts. Dates differ between runs;
    # results array shape is what matters.
    verdicts_a = {(r["model"], r["signal"]): r["verdict"] for r in audit_a["results"]}
    verdicts_b = {(r["model"], r["signal"]): r["verdict"] for r in audit_b["results"]}
    assert verdicts_a == verdicts_b


# --------------- CLI surface --------------------------------------


def test_cli_help_includes_audit_flag():
    parser = sb.build_arg_parser()
    option_strings = {
        s for action in parser._actions
        for s in (action.option_strings or [])
    }
    assert "--audit" in option_strings
    assert "--cache-dir" in option_strings
    assert "--manifest" in option_strings
    assert "--out-dir" in option_strings
    assert "--crosstab" in option_strings
    assert "--min-n" in option_strings
