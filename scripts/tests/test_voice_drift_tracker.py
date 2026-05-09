#!/usr/bin/env python3
"""Regression tests for voice_drift_tracker.py.

Uses the public-domain Federalist Papers fixture with synthetic
date_written tags (1787-10-27 through 1788-01-16) as a cross-period
test corpus. Year granularity yields two periods (1787, 1788); the
script computes cross-period Burrows-Delta + cosine distance and
identifies drifting / stable features.

Federalist isn't real time drift — Hamilton (1787) and Madison
(1788) are different writers, so the "drift" is authorship change
in disguise. That's fine for exercising the code paths: the tracker
should produce a non-trivial cross-period distance, surface
drifting features (which would be authorship markers in a real
multi-author corpus, or genuinely-drifting habits in a single-
writer corpus), and respect the privacy guard.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import voice_drift_tracker as vdt  # type: ignore


MANIFEST = ROOT / "test_data" / "federalist_drift_manifest.jsonl"


def _args(**overrides) -> argparse.Namespace:
    base = {
        "manifest": str(MANIFEST),
        "baseline_dir": None,
        "periods_json": None,
        "date_pattern": r"(\d{4}-\d{2}|\d{4})",
        "use": "voice_profile",
        "period_granularity": "year",
        "period_boundaries": None,
        "min_docs_per_period": 1,  # 1788 has only one doc; tests
                                    # the partial-period path.
        "top_drifting": 15,
        "top_stable": 15,
        "out": None,
        "json_out": None,
        "allow_public_output": True,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


# ---- Date parsing ------------------------------------------


def test_parse_iso_date_handles_partial_dates() -> None:
    assert vdt._parse_iso_date("2022") == (2022, 0, 0)
    assert vdt._parse_iso_date("2022-04") == (2022, 4, 0)
    assert vdt._parse_iso_date("2022-04-15") == (2022, 4, 15)


def test_parse_iso_date_rejects_garbage() -> None:
    assert vdt._parse_iso_date("not a date") is None
    assert vdt._parse_iso_date("9999-13-99") is None  # bad month
    assert vdt._parse_iso_date("1500") == (1500, 0, 0)
    assert vdt._parse_iso_date("3001") is None  # year out of range


def test_period_key_year_granularity() -> None:
    assert vdt._period_key((2022, 4, 15), "year") == "2022"
    assert vdt._period_key((2023, 0, 0), "year") == "2023"


def test_period_key_quarter_granularity() -> None:
    assert vdt._period_key((2022, 1, 1), "quarter") == "2022-Q1"
    assert vdt._period_key((2022, 6, 15), "quarter") == "2022-Q2"
    assert vdt._period_key((2022, 9, 30), "quarter") == "2022-Q3"
    assert vdt._period_key((2022, 12, 1), "quarter") == "2022-Q4"


def test_period_key_month_granularity() -> None:
    assert vdt._period_key((2022, 4, 15), "month") == "2022-04"
    # Year-only date with month granularity gets the unknown sentinel
    assert vdt._period_key((2022, 0, 0), "month") == "2022-??"


def test_period_key_custom_boundaries() -> None:
    bounds = [(2023, 1, 1)]  # split into "before 2023" and "after"
    assert vdt._period_key((2022, 5, 1), "custom", bounds) == "before_2023-01-01"
    assert vdt._period_key((2024, 5, 1), "custom", bounds) == "after_2023-01-01"
    # Boundary-exact date goes to the "after" interval (lower bound
    # is inclusive)
    assert vdt._period_key((2023, 1, 1), "custom", bounds) == "after_2023-01-01"


# ---- Manifest loading ---------------------------------------


def test_load_manifest_filters_by_use() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    entries = vdt._load_manifest_entries(MANIFEST, "voice_profile")
    assert len(entries) == 6
    # All entries should have parsed dates.
    for e in entries:
        assert e.date_tuple[0] in (1787, 1788)


def test_load_manifest_drops_other_use_tags() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    # No entries with use=validation, so should be empty.
    entries = vdt._load_manifest_entries(MANIFEST, "validation")
    assert entries == []


# ---- Period grouping ---------------------------------------


def test_grouping_by_year_produces_two_periods() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    entries = vdt._load_manifest_entries(MANIFEST, "voice_profile")
    grouped, dropped = vdt.group_by_period(
        entries, "year", min_docs_per_period=1,
    )
    assert set(grouped.keys()) == {"1787", "1788"}
    assert len(grouped["1787"]) == 5  # Hamilton's three + Madison's two
    assert len(grouped["1788"]) == 1  # Madison's late entry
    assert dropped == []


def test_min_docs_filter_drops_thin_periods() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    entries = vdt._load_manifest_entries(MANIFEST, "voice_profile")
    grouped, dropped = vdt.group_by_period(
        entries, "year", min_docs_per_period=2,
    )
    # 1788 has only 1 doc; should be dropped.
    assert "1788" in dropped
    assert "1787" in grouped


# ---- End-to-end via run() ---------------------------------


def test_run_produces_two_periods_with_distance() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    result = vdt.run(_args())
    profiles = result["profiles"]
    assert set(profiles.keys()) == {"1787", "1788"}
    assert profiles["1787"].n_docs == 5
    assert profiles["1788"].n_docs == 1
    # Cross-period weighted distance must exist for the (1787, 1788)
    # pair.
    weighted = result["weighted_distances"]
    assert ("1787", "1788") in weighted
    pair = weighted[("1787", "1788")]
    # Hamilton vs. Madison should produce a measurable Burrows-Delta;
    # cosine should also be > 0 (they're different writers in
    # function-word space).
    assert pair["burrows_delta"] is not None
    assert pair["burrows_delta"] > 0.5
    assert pair["cosine_distance"] is not None
    assert pair["cosine_distance"] > 0


def test_run_produces_drifting_features_per_family() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    result = vdt.run(_args())
    drift = result["drift"]
    # At least the function_words family must have drifting features
    # (function-word distribution is the canonical authorship marker).
    assert "function_words" in drift
    fw_drift = drift["function_words"]
    assert "drifting" in fw_drift
    assert "stable" in fw_drift
    # Top drifting feature should have a positive CV (some drift).
    assert fw_drift["drifting"][0]["cv"] > 0


def test_run_refuses_when_only_one_period() -> None:
    """If min_docs_per_period drops every period but one, the run
    should refuse with a clear message (drift requires ≥ 2
    periods)."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    args = _args(min_docs_per_period=10)  # No period has 10 docs
    if pytest is not None:
        with pytest.raises(SystemExit):
            vdt.run(args)


# ---- Privacy guard -----------------------------------------


def test_privacy_guard_refuses_public_path_without_allow(tmp_path) -> None:
    """Without --allow-public-output, the script must refuse to write
    outside ai-prose-baselines-private/. tmp_path is outside that
    directory."""
    out_path = tmp_path / "drift.md"
    json_path = tmp_path / "drift.json"
    if pytest is not None:
        with pytest.raises(SystemExit):
            vdt._check_output_privacy(
                [out_path, json_path], allow_public=False,
            )


def test_privacy_guard_allows_with_flag(tmp_path) -> None:
    """With --allow-public-output, the guard waives the check."""
    out_path = tmp_path / "drift.md"
    # No exception expected.
    vdt._check_output_privacy([out_path], allow_public=True)


# ---- JSON / Markdown output --------------------------------


def test_json_output_includes_required_fields() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    result = vdt.run(_args())
    json_str = vdt.render_json(
        profiles=result["profiles"],
        family_distances=result["family_distances"],
        weighted_distances=result["weighted_distances"],
        drift=result["drift"],
        dropped_periods=result["dropped_periods"],
        inputs=result["inputs"],
        granularity=result["granularity"],
    )
    parsed = json.loads(json_str)
    assert parsed["task_surface"] == "voice_coherence"
    assert parsed["tool"] == "voice_drift_tracker"
    assert parsed["n_periods"] == 2
    assert "claim_license" in parsed
    assert "cross_period_distances_weighted" in parsed
    assert "drift_scores" in parsed


def test_markdown_output_includes_distance_table() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    result = vdt.run(_args())
    md = vdt.render_markdown(
        profiles=result["profiles"],
        family_distances=result["family_distances"],
        weighted_distances=result["weighted_distances"],
        drift=result["drift"],
        dropped_periods=result["dropped_periods"],
        granularity=result["granularity"],
    )
    assert "Voice Drift Report" in md
    assert "Cross-period voice distance" in md
    assert "1787" in md and "1788" in md
    # Drift sections must surface for at least the function_words
    # family.
    assert "function_words" in md.lower()


# ---- CLI smoke test ----------------------------------------


def test_cli_main_runs_end_to_end(tmp_path) -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    out_md = tmp_path / "drift.md"
    out_json = tmp_path / "drift.json"
    rc = vdt.main([
        "--manifest", str(MANIFEST),
        "--use", "voice_profile",
        "--period-granularity", "year",
        "--min-docs-per-period", "1",
        "--out", str(out_md),
        "--json-out", str(out_json),
        "--allow-public-output",
    ])
    assert rc == 0
    assert out_md.is_file()
    assert out_json.is_file()
    parsed = json.loads(out_json.read_text(encoding="utf-8"))
    assert parsed["task_surface"] == "voice_coherence"
    assert parsed["n_periods"] == 2


def test_cli_refuses_zero_inputs() -> None:
    if pytest is not None:
        with pytest.raises(SystemExit):
            vdt.main([])


def test_cli_refuses_public_output_without_allow_flag(tmp_path) -> None:
    """End-to-end privacy check: without --allow-public-output, the
    script exits 2 when the output path is outside
    ai-prose-baselines-private/."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    out_md = tmp_path / "drift.md"
    if pytest is not None:
        with pytest.raises(SystemExit) as exc:
            vdt.main([
                "--manifest", str(MANIFEST),
                "--out", str(out_md),
            ])
        assert exc.value.code == 2
