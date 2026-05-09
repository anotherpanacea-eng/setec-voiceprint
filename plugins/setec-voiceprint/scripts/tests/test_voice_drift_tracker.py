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


def test_parse_iso_date_rejects_trailing_garbage() -> None:
    """Reviewer catch: prefix-only regex previously accepted
    '2020-01-foo' as January 2020. Anchored regex must reject."""
    assert vdt._parse_iso_date("2020-01-foo") is None
    assert vdt._parse_iso_date("2020-01-15-extra") is None
    assert vdt._parse_iso_date("2020 January") is None
    assert vdt._parse_iso_date("2020-1") is None  # single-digit month
    assert vdt._parse_iso_date("2020-01-1") is None  # single-digit day


def test_parse_iso_date_rejects_impossible_calendar_dates() -> None:
    """Reviewer catch: '2020-02-31' previously parsed as a real
    date because day-of-month wasn't validated against the month.
    datetime.date validation now catches Feb 30/31, Apr 31, etc."""
    assert vdt._parse_iso_date("2020-02-31") is None  # Feb 31
    assert vdt._parse_iso_date("2020-02-30") is None  # Feb 30
    assert vdt._parse_iso_date("2020-04-31") is None  # Apr 31
    assert vdt._parse_iso_date("2020-06-31") is None  # Jun 31
    # Leap year edge cases:
    assert vdt._parse_iso_date("2020-02-29") == (2020, 2, 29)  # 2020 is leap
    assert vdt._parse_iso_date("2021-02-29") is None  # 2021 isn't


def test_parse_iso_date_accepts_valid_partials_and_fulls() -> None:
    """Sanity: the strictness fix doesn't break legitimate dates."""
    assert vdt._parse_iso_date("2022") == (2022, 0, 0)
    assert vdt._parse_iso_date("2022-04") == (2022, 4, 0)
    assert vdt._parse_iso_date("2022-04-15") == (2022, 4, 15)
    assert vdt._parse_iso_date("1787-10-27") == (1787, 10, 27)
    assert vdt._parse_iso_date("1788-01-16") == (1788, 1, 16)


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


def test_privacy_guard_accepts_sibling_private_path(tmp_path) -> None:
    """Reviewer catch: the documented standard layout uses a
    SIBLING `../ai-prose-baselines-private/` directory next to the
    repo, not a repo-internal path. The pre-fix guard rooted the
    allowlist at <repo>/ai-prose-baselines-private/ and refused
    sibling paths, training users to bypass the guard with
    --allow-public-output. The marker-based check accepts any path
    under any directory named `ai-prose-baselines-private`,
    matching `voice_profile.py`'s convention."""
    sibling = tmp_path / "ai-prose-baselines-private"
    sibling.mkdir()
    out_path = sibling / "drift.md"
    # No exception expected.
    vdt._check_output_privacy([out_path], allow_public=False)


def test_privacy_guard_accepts_nested_private_path(tmp_path) -> None:
    """Marker check accepts the private directory anywhere in the
    path's components, not just at a fixed root. Reflects real-
    world layouts where users may have private/ at varying depths."""
    nested = tmp_path / "some" / "intermediate" / "ai-prose-baselines-private" / "subdir"
    nested.mkdir(parents=True)
    out_path = nested / "drift.md"
    vdt._check_output_privacy([out_path], allow_public=False)


def test_privacy_guard_refuses_path_without_marker(tmp_path) -> None:
    """Marker check still refuses paths that lack the
    `ai-prose-baselines-private` component."""
    out_path = tmp_path / "innocent_folder" / "drift.md"
    out_path.parent.mkdir(parents=True)
    if pytest is not None:
        with pytest.raises(SystemExit):
            vdt._check_output_privacy([out_path], allow_public=False)


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


def test_cli_refuses_stdout_without_allow_flag() -> None:
    """Reviewer catch: when no --out / --json-out is supplied, the
    report previously went to stdout without going through the
    privacy guard. Voice-drift output is voice-cloning input;
    stdout is also default-private. Without --allow-public-output,
    the script must refuse stdout output (exit 2)."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    if pytest is not None:
        rc = vdt.main([
            "--manifest", str(MANIFEST),
            "--min-docs-per-period", "1",
        ])
        assert rc == 2


def test_cli_allows_stdout_with_allow_flag(capsys) -> None:
    """With --allow-public-output, stdout works as before."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    rc = vdt.main([
        "--manifest", str(MANIFEST),
        "--min-docs-per-period", "1",
        "--allow-public-output",
    ])
    assert rc == 0
    captured = capsys.readouterr() if pytest is not None else None
    if captured:
        assert "Voice Drift Report" in captured.out


# ---- Burrows-Delta magnitude regression --------------------


def test_burrows_delta_is_not_the_two_period_constant() -> None:
    """Reviewer catch: with stats computed over only K=2 period
    centroids, every informative feature gets symmetric z-scores
    ±sqrt(2)/2, so |z_a - z_b| collapses to a constant sqrt(2) ≈
    1.414 regardless of actual drift magnitude. The fix uses per-
    document stats. On the Federalist fixture (Hamilton 1787 vs.
    Madison 1788), the result must NOT equal sqrt(2)."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist drift manifest not available")
        return
    result = vdt.run(_args())
    pair = result["weighted_distances"][("1787", "1788")]
    delta = pair["burrows_delta"]
    assert delta is not None
    # The pre-fix degenerate value was exactly sqrt(2) ≈ 1.4142135.
    # If the fix regresses, this value will return.
    SQRT_2 = 2 ** 0.5  # 1.4142135623730951
    assert abs(delta - SQRT_2) > 0.01, (
        f"Burrows-Delta {delta!r} is suspiciously close to sqrt(2). "
        f"The 2-period centroid-stats degeneracy may have returned."
    )


def test_burrows_delta_varies_with_drift_magnitude() -> None:
    """Direct regression test on cross_period_distances: build two
    synthetic period-profile scenarios, one with small drift and one
    with large drift, and assert the Burrows-Delta values differ.
    Pre-fix, both would return ~sqrt(2)."""
    from voice_drift_tracker import cross_period_distances, PeriodProfile

    selected_features = {"function_words": ["the", "and", "of", "to"]}

    def _build_profile(label: str, per_doc_freqs: list[dict[str, float]]) -> PeriodProfile:
        items = [
            {"id": f"{label}_doc{i}", "features": {"function_words": doc}}
            for i, doc in enumerate(per_doc_freqs)
        ]
        names = ["the", "and", "of", "to"]
        centroid = {
            n: sum(d.get(n, 0.0) for d in per_doc_freqs) / len(per_doc_freqs)
            for n in names
        }
        return PeriodProfile(
            label=label,
            n_docs=len(per_doc_freqs),
            n_words=sum(1000 for _ in per_doc_freqs),
            feature_items=items,
            period_centroids={"function_words": centroid},
        )

    # Small drift: period A and period B have very similar per-doc
    # frequencies. The within-period dispersion is comparable to the
    # tiny cross-period shift.
    small_a = _build_profile("A", [
        {"the": 0.10, "and": 0.05, "of": 0.04, "to": 0.03},
        {"the": 0.11, "and": 0.06, "of": 0.04, "to": 0.03},
        {"the": 0.10, "and": 0.05, "of": 0.05, "to": 0.03},
    ])
    small_b = _build_profile("B", [
        {"the": 0.11, "and": 0.06, "of": 0.04, "to": 0.04},
        {"the": 0.12, "and": 0.05, "of": 0.05, "to": 0.03},
        {"the": 0.11, "and": 0.06, "of": 0.04, "to": 0.03},
    ])
    small_distances = cross_period_distances(
        {"A": small_a, "B": small_b}, selected_features,
    )
    small_delta = small_distances["function_words"][("A", "B")]["burrows_delta"]

    # Large drift: cross-period shift far exceeds within-period
    # dispersion. The same algorithm should produce a much bigger
    # Burrows-Delta.
    large_a = _build_profile("A", [
        {"the": 0.05, "and": 0.02, "of": 0.02, "to": 0.01},
        {"the": 0.05, "and": 0.02, "of": 0.02, "to": 0.01},
        {"the": 0.06, "and": 0.03, "of": 0.02, "to": 0.01},
    ])
    large_b = _build_profile("B", [
        {"the": 0.20, "and": 0.10, "of": 0.08, "to": 0.06},
        {"the": 0.21, "and": 0.11, "of": 0.09, "to": 0.06},
        {"the": 0.20, "and": 0.10, "of": 0.08, "to": 0.07},
    ])
    large_distances = cross_period_distances(
        {"A": large_a, "B": large_b}, selected_features,
    )
    large_delta = large_distances["function_words"][("A", "B")]["burrows_delta"]

    assert small_delta is not None
    assert large_delta is not None
    # The old degenerate value would force both to exactly sqrt(2).
    # The fix gives them different values; large drift > small drift.
    assert large_delta > small_delta, (
        f"Large drift Burrows-Delta {large_delta!r} should exceed "
        f"small drift {small_delta!r}. If they're equal (or both ≈ "
        f"sqrt(2)), the centroid-stats degeneracy has returned."
    )
