#!/usr/bin/env python3
"""Regression tests for pov_voice_profile.py.

Uses the public-domain Federalist Papers fixture with synthetic
`pov` tags (Hamilton vs. Madison as POV characters) as a multi-POV
test corpus. With 3 docs per POV, the cross-POV voiceprint should
produce a measurable distance and surface distinguishing features
per POV.

Federalist isn't real multi-POV fiction — Hamilton and Madison are
different writers, not POV characters within a single novel. But
the code paths are identical: docs grouped by an explicit
character/POV label, pairwise voice distance computed in a shared
feature space. The test asserts the script runs end-to-end and
produces sensible output.
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

import pov_voice_profile as pvp  # type: ignore


MANIFEST = ROOT / "test_data" / "federalist_pov_manifest.jsonl"


def _args(**overrides) -> argparse.Namespace:
    base = {
        "manifest": str(MANIFEST),
        "use": "voice_profile",
        "min_docs_per_pov": 2,
        "top_distinguishing": 15,
        "collapse_threshold": 0.5,
        "out": None,
        "json_out": None,
        "allow_public_output": True,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


# ---- Manifest loading ---------------------------------------


def test_load_manifest_filters_by_use_and_requires_pov() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    entries = pvp._load_manifest_entries(MANIFEST, "voice_profile")
    assert len(entries) == 6
    povs = {e.pov for e in entries}
    assert povs == {"Hamilton", "Madison"}


def test_load_manifest_drops_other_use_tags() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    entries = pvp._load_manifest_entries(MANIFEST, "validation")
    assert entries == []


# ---- POV grouping ------------------------------------------


def test_grouping_produces_two_povs_with_three_docs_each() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    entries = pvp._load_manifest_entries(MANIFEST, "voice_profile")
    grouped, dropped = pvp.group_by_pov(entries, min_docs_per_pov=2)
    assert set(grouped.keys()) == {"Hamilton", "Madison"}
    assert len(grouped["Hamilton"]) == 3
    assert len(grouped["Madison"]) == 3
    assert dropped == []


def test_min_docs_filter_drops_thin_povs() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    entries = pvp._load_manifest_entries(MANIFEST, "voice_profile")
    # With min_docs_per_pov=4, both POVs (3 docs each) get dropped.
    grouped, dropped = pvp.group_by_pov(entries, min_docs_per_pov=4)
    assert grouped == {}
    assert set(dropped) == {"Hamilton", "Madison"}


# ---- End-to-end via run() ---------------------------------


def test_run_produces_two_povs_with_distance() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    result = pvp.run(_args())
    profiles = result["profiles"]
    assert set(profiles.keys()) == {"Hamilton", "Madison"}
    assert profiles["Hamilton"].n_docs == 3
    assert profiles["Madison"].n_docs == 3
    weighted = result["weighted_distances"]
    assert ("Hamilton", "Madison") in weighted
    pair = weighted[("Hamilton", "Madison")]
    # Hamilton vs. Madison should produce a measurable Burrows-Delta;
    # cosine should also be > 0 (different writers in function-word
    # space).
    assert pair["burrows_delta"] is not None
    assert pair["burrows_delta"] > 0.5
    assert pair["cosine_distance"] is not None
    assert pair["cosine_distance"] > 0


def test_distinguishing_features_per_pov() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    result = pvp.run(_args())
    distinguishing = result["distinguishing"]
    assert "Hamilton" in distinguishing
    assert "Madison" in distinguishing
    # Each POV must have function_words distinguishing features
    # (function-word distribution is the canonical authorship marker
    # and surfaces here as POV-distinguishing features).
    h_fw = distinguishing["Hamilton"].get("function_words", [])
    m_fw = distinguishing["Madison"].get("function_words", [])
    assert len(h_fw) > 0
    assert len(m_fw) > 0
    # Each feature row must carry the required fields.
    sample = h_fw[0]
    for key in ("feature", "this_pov_value", "others_mean", "delta", "log2_ratio"):
        assert key in sample


def test_pov_vs_corpus_mean() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    result = pvp.run(_args())
    pov_vs_mean = result["pov_vs_mean"]
    assert "Hamilton" in pov_vs_mean
    assert "Madison" in pov_vs_mean
    assert pov_vs_mean["Hamilton"]["burrows_delta"] is not None
    # With exactly 2 POVs, both are equidistant from the midpoint; the
    # values are equal by construction. With 3+ POVs they'll diverge.
    h_delta = pov_vs_mean["Hamilton"]["burrows_delta"]
    m_delta = pov_vs_mean["Madison"]["burrows_delta"]
    assert abs(h_delta - m_delta) < 1e-6


# ---- Voice-collapse verdict --------------------------------


def test_no_collapse_flag_for_distinct_povs() -> None:
    """Hamilton and Madison are different writers; their pairwise
    Burrows-Delta is well above the 0.5 collapse threshold. No
    collapse flag should fire."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    result = pvp.run(_args(collapse_threshold=0.5))
    collapse = result["collapse_verdict"]
    assert collapse == []


def test_collapse_flag_fires_with_aggressive_threshold() -> None:
    """With a deliberately-aggressive threshold (Hamilton vs. Madison
    delta ≈ 1.4, so threshold 2.0 forces a flag), the collapse
    verdict should produce one row."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    result = pvp.run(_args(collapse_threshold=2.0))
    collapse = result["collapse_verdict"]
    assert len(collapse) == 1
    row = collapse[0]
    assert {row["pov_a"], row["pov_b"]} == {"Hamilton", "Madison"}
    assert row["verdict"] == "potentially_collapsed"
    assert row["threshold"] == 2.0


# ---- Refusal paths -----------------------------------------


def test_refuses_when_only_one_pov_after_filtering() -> None:
    """If min_docs_per_pov drops every POV but one, the run should
    refuse with a clear message."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    args = _args(min_docs_per_pov=10)  # No POV has 10 docs
    if pytest is not None:
        with pytest.raises(SystemExit):
            pvp.run(args)


def test_refuses_when_no_pov_tagged_entries() -> None:
    """If --use selects no entries (because no entries match the
    tag), the run should refuse rather than computing on empty
    input."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    args = _args(use="validation")  # No entries with use=validation
    if pytest is not None:
        with pytest.raises(SystemExit):
            pvp.run(args)


# ---- Privacy guard -----------------------------------------


def test_privacy_guard_refuses_public_path_without_allow(tmp_path) -> None:
    out_path = tmp_path / "pov.md"
    if pytest is not None:
        with pytest.raises(SystemExit):
            pvp._check_output_privacy([out_path], allow_public=False)


def test_privacy_guard_allows_with_flag(tmp_path) -> None:
    out_path = tmp_path / "pov.md"
    pvp._check_output_privacy([out_path], allow_public=True)


# ---- JSON / Markdown output --------------------------------


def test_json_output_includes_required_fields() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    result = pvp.run(_args())
    json_str = pvp.render_json(
        profiles=result["profiles"],
        family_distances=result["family_distances"],
        weighted_distances=result["weighted_distances"],
        pov_vs_mean=result["pov_vs_mean"],
        distinguishing=result["distinguishing"],
        collapse_verdict=result["collapse_verdict"],
        dropped_povs=result["dropped_povs"],
        inputs=result["inputs"],
    )
    parsed = json.loads(json_str)
    assert parsed["task_surface"] == "voice_coherence"
    assert parsed["tool"] == "pov_voice_profile"
    assert parsed["n_povs"] == 2
    assert "claim_license" in parsed
    assert "cross_pov_distances_weighted" in parsed
    assert "distinguishing_features" in parsed
    assert "voice_collapse_verdict" in parsed
    assert "pov_vs_corpus_mean" in parsed


def test_markdown_output_includes_distance_table() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    result = pvp.run(_args())
    md = pvp.render_markdown(
        profiles=result["profiles"],
        weighted_distances=result["weighted_distances"],
        pov_vs_mean=result["pov_vs_mean"],
        distinguishing=result["distinguishing"],
        collapse_verdict=result["collapse_verdict"],
        dropped_povs=result["dropped_povs"],
        collapse_threshold=0.5,
    )
    assert "Per-POV Voiceprint Report" in md
    assert "Cross-POV voice distance" in md
    assert "Hamilton" in md and "Madison" in md
    # No collapse flag should appear in the default-threshold run.
    assert "Voice-collapse flag" not in md


def test_markdown_includes_collapse_section_when_flagged() -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    result = pvp.run(_args(collapse_threshold=2.0))
    md = pvp.render_markdown(
        profiles=result["profiles"],
        weighted_distances=result["weighted_distances"],
        pov_vs_mean=result["pov_vs_mean"],
        distinguishing=result["distinguishing"],
        collapse_verdict=result["collapse_verdict"],
        dropped_povs=result["dropped_povs"],
        collapse_threshold=2.0,
    )
    assert "Voice-collapse flag" in md
    assert "potentially_collapsed" in md


# ---- CLI smoke test ----------------------------------------


def test_cli_main_runs_end_to_end(tmp_path) -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    out_md = tmp_path / "pov.md"
    out_json = tmp_path / "pov.json"
    rc = pvp.main([
        "--manifest", str(MANIFEST),
        "--use", "voice_profile",
        "--out", str(out_md),
        "--json-out", str(out_json),
        "--allow-public-output",
    ])
    assert rc == 0
    assert out_md.is_file()
    assert out_json.is_file()
    parsed = json.loads(out_json.read_text(encoding="utf-8"))
    assert parsed["task_surface"] == "voice_coherence"
    assert parsed["n_povs"] == 2


def test_cli_refuses_public_output_without_allow_flag(tmp_path) -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    out_md = tmp_path / "pov.md"
    if pytest is not None:
        with pytest.raises(SystemExit) as exc:
            pvp.main([
                "--manifest", str(MANIFEST),
                "--out", str(out_md),
            ])
        assert exc.value.code == 2


def test_cli_refuses_stdout_without_allow_flag() -> None:
    """Reviewer catch: when no --out / --json-out is supplied, the
    report previously went to stdout without going through the
    privacy guard. POV voiceprints are voice-cloning input;
    default-private posture must hold for stdout too."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    if pytest is not None:
        rc = pvp.main(["--manifest", str(MANIFEST)])
        assert rc == 2


def test_cli_allows_stdout_with_allow_flag(capsys) -> None:
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    rc = pvp.main([
        "--manifest", str(MANIFEST),
        "--allow-public-output",
    ])
    assert rc == 0
    captured = capsys.readouterr() if pytest is not None else None
    if captured:
        assert "Per-POV Voiceprint Report" in captured.out


# ---- Burrows-Delta magnitude regression --------------------


def test_burrows_delta_is_not_the_two_pov_constant() -> None:
    """Reviewer catch: with stats computed over only K=2 POV
    centroids, every informative feature gets symmetric z-scores
    ±sqrt(2)/2, so |z_a - z_b| collapses to a constant sqrt(2)
    regardless of actual drift magnitude. The fix uses per-document
    stats. On the Federalist POV fixture, the result must NOT equal
    sqrt(2)."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    result = pvp.run(_args())
    pair = result["weighted_distances"][("Hamilton", "Madison")]
    delta = pair["burrows_delta"]
    assert delta is not None
    SQRT_2 = 2 ** 0.5
    assert abs(delta - SQRT_2) > 0.01, (
        f"POV Burrows-Delta {delta!r} is suspiciously close to "
        f"sqrt(2). The 2-POV centroid-stats degeneracy may have "
        f"returned."
    )


def test_pov_vs_corpus_mean_is_not_the_two_pov_constant() -> None:
    """Same check on pov_vs_corpus_mean_distances: the per-POV-vs-
    midpoint computation also previously used K-small centroid
    stats and would degenerate when K=2."""
    if not MANIFEST.exists():
        if pytest is not None:
            pytest.skip("Federalist POV manifest not available")
        return
    result = pvp.run(_args())
    pov_vs_mean = result["pov_vs_mean"]
    h_delta = pov_vs_mean["Hamilton"]["burrows_delta"]
    SQRT_2 = 2 ** 0.5
    assert h_delta is not None
    # The pre-fix value was exactly sqrt(2). With the fix using
    # per-doc stats, this won't be sqrt(2).
    assert abs(h_delta - SQRT_2) > 0.01, (
        f"POV-vs-corpus-mean Burrows-Delta {h_delta!r} is "
        f"suspiciously close to sqrt(2)."
    )


def test_pov_burrows_delta_varies_with_voice_distinctness() -> None:
    """Synthetic regression: two POV configurations, one where the
    POVs are voice-distinct (large cross-POV shift) and one where
    they're nearly-collapsed (small shift). Burrows-Delta values
    must differ; pre-fix both would have been sqrt(2)."""
    from pov_voice_profile import cross_pov_distances, POVProfile

    selected_features = {"function_words": ["the", "and", "of", "to"]}

    def _build_profile(label: str, per_doc_freqs: list[dict[str, float]]) -> POVProfile:
        items = [
            {"id": f"{label}_doc{i}", "features": {"function_words": doc}}
            for i, doc in enumerate(per_doc_freqs)
        ]
        names = ["the", "and", "of", "to"]
        centroid = {
            n: sum(d.get(n, 0.0) for d in per_doc_freqs) / len(per_doc_freqs)
            for n in names
        }
        return POVProfile(
            label=label,
            n_docs=len(per_doc_freqs),
            n_words=sum(1000 for _ in per_doc_freqs),
            feature_items=items,
            pov_centroids={"function_words": centroid},
        )

    # Voice-collapsed POVs: nearly identical per-doc frequencies.
    collapsed_a = _build_profile("A", [
        {"the": 0.10, "and": 0.05, "of": 0.04, "to": 0.03},
        {"the": 0.11, "and": 0.05, "of": 0.04, "to": 0.03},
    ])
    collapsed_b = _build_profile("B", [
        {"the": 0.10, "and": 0.06, "of": 0.04, "to": 0.03},
        {"the": 0.11, "and": 0.05, "of": 0.05, "to": 0.03},
    ])
    collapsed_dist = cross_pov_distances(
        {"A": collapsed_a, "B": collapsed_b}, selected_features,
    )
    collapsed_delta = collapsed_dist["function_words"][("A", "B")]["burrows_delta"]

    # Voice-distinct POVs.
    distinct_a = _build_profile("A", [
        {"the": 0.05, "and": 0.02, "of": 0.02, "to": 0.01},
        {"the": 0.06, "and": 0.02, "of": 0.02, "to": 0.01},
    ])
    distinct_b = _build_profile("B", [
        {"the": 0.20, "and": 0.10, "of": 0.08, "to": 0.06},
        {"the": 0.21, "and": 0.10, "of": 0.09, "to": 0.06},
    ])
    distinct_dist = cross_pov_distances(
        {"A": distinct_a, "B": distinct_b}, selected_features,
    )
    distinct_delta = distinct_dist["function_words"][("A", "B")]["burrows_delta"]

    assert collapsed_delta is not None
    assert distinct_delta is not None
    assert distinct_delta > collapsed_delta, (
        f"Voice-distinct POVs Burrows-Delta {distinct_delta!r} "
        f"should exceed voice-collapsed {collapsed_delta!r}."
    )
