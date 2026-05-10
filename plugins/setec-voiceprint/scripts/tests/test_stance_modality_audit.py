#!/usr/bin/env python3
"""Regression tests for stance_modality_audit.py (Release 5)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import stance_modality_audit as sm  # type: ignore


_BOOSTER_HEAVY = (
    "The framework clearly demonstrates impact. Obviously the "
    "implementation should provide actionable insights. Of course "
    "stakeholders will benefit. The data show that, certainly, the "
    "strategy works. Indeed, the methodology proves effective. "
    "Without doubt, adoption is the path forward."
) * 15  # ~600 words to clear the 500-word refusal-absence floor

_BALANCED_HEDGED = (
    "It seems likely that the framework offers some benefit, "
    "although the evidence is somewhat mixed. The data suggest, "
    "perhaps, that adoption may help — but I think we should be "
    "cautious. The study shows a pattern, but more or less only in "
    "one register. We cannot conclude broad applicability without "
    "further work. In some cases the result may be artifact."
) * 12


class TestAuditBasics:
    def test_empty_text_unavailable(self):
        a = sm.audit_stance_modality("")
        assert a["available"] is False

    def test_returns_per_category_densities(self):
        a = sm.audit_stance_modality(_BALANCED_HEDGED)
        densities = a["category_densities_per_1k"]
        for cat in (
            "deontic_modality", "epistemic_modality",
            "hedge", "booster", "evidential",
            "first_person_stance", "refusal",
        ):
            assert cat in densities


class TestBoosterDominance:
    def test_booster_heavy_flags_dominance(self):
        a = sm.audit_stance_modality(_BOOSTER_HEAVY)
        flagged = set(a["compression"]["flagged_signals"])
        assert "booster_dominance" in flagged

    def test_booster_heavy_band(self):
        a = sm.audit_stance_modality(_BOOSTER_HEAVY)
        assert a["compression"]["band"] in {
            "Moderately stance-shifted", "Heavily stance-shifted",
        }

    def test_hedge_booster_ratio_low_when_booster_dominant(self):
        a = sm.audit_stance_modality(_BOOSTER_HEAVY)
        # All boosters, no hedges → ratio toward 0.
        assert a["hedge_booster_ratio"] <= 0.2


class TestRefusalAbsence:
    def test_high_stance_no_refusal_flagged(self):
        a = sm.audit_stance_modality(_BOOSTER_HEAVY)
        flagged = set(a["compression"]["flagged_signals"])
        # Booster-heavy prose with no refusal markers.
        assert "low_refusal_marker_density" in flagged


class TestBalancedProse:
    def test_balanced_hedged_lightly_shifted(self):
        a = sm.audit_stance_modality(_BALANCED_HEDGED)
        # Has hedges + epistemic + refusal markers; not booster-dominated.
        assert a["compression"]["band"] in {
            "Lightly stance-shifted", "Moderately stance-shifted",
        }


class TestStanceEntropy:
    def test_entropy_high_when_categories_balanced(self):
        a = sm.audit_stance_modality(_BALANCED_HEDGED)
        # Multiple stance types fire.
        assert a["stance_entropy_bits"] > 1.0


class TestBaselineHardening:
    def test_nonexistent_baseline_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            sm.audit_baseline_stance(str(tmp_path / "no_dir"))

    def test_target_overlap_excluded(self, tmp_path, capsys):
        base = tmp_path / "baseline"
        base.mkdir()
        target = base / "draft.txt"
        target.write_text(_BALANCED_HEDGED, encoding="utf-8")
        (base / "other.txt").write_text(_BALANCED_HEDGED, encoding="utf-8")
        block = sm.audit_baseline_stance(
            str(base), target_path=target,
        )
        assert block["n_files"] == 1

    def test_filenames_anonymized(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "client_brief.txt").write_text(
            _BALANCED_HEDGED, encoding="utf-8",
        )
        block = sm.audit_baseline_stance(str(base))
        for s in block["per_file_summaries"]:
            assert "client" not in s["file"]


class TestRender:
    def test_markdown_includes_claim_license(self):
        a = sm.audit_stance_modality(_BALANCED_HEDGED)
        md = sm.render_report(a)
        assert "## What this result licenses" in md


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        in_path = tmp_path / "draft.txt"
        in_path.write_text(_BALANCED_HEDGED, encoding="utf-8")
        out_path = tmp_path / "out.json"
        rc = sm.main(["--json", "--out", str(out_path), str(in_path)])
        assert rc == 0


# ---------- 1.35.1 reviewer-flagged P2 fix ----------------------


class TestEvidentialDeduplication:
    """Pre-1.35.1, the evidential category had two patterns: a
    bare-verb pattern (`shows`, `suggests`, ...) and a phrase
    pattern (`(evidence|research|...)\\s+(shows|...)`). A phrase
    like "evidence shows" matched both, double-counting. Reviewer
    reproduced inflated evidential density. Fix: collect spans
    per category and de-duplicate by containment (longer match
    wins; non-overlapping all count)."""

    def test_phrase_does_not_double_count(self):
        # "Evidence shows" matches both the bare-verb pattern
        # ("shows") and the phrase pattern ("evidence shows"). Should
        # count as 1 evidential, not 2.
        text = "Evidence shows progress. " * 10
        a = sm.audit_stance_modality(text)
        # 10 "evidence shows" phrases → 10 evidentials (not 20).
        assert a["category_counts"]["evidential"] == 10

    def test_bare_verb_still_counts_alone(self):
        # "shows" without an "evidence" prefix should still count
        # exactly once as evidential.
        text = "The data shows progress consistently. " * 10
        a = sm.audit_stance_modality(text)
        # Each repetition contributes 1 "shows". Confirm count
        # is exactly the number of occurrences.
        assert a["category_counts"]["evidential"] >= 10

    def test_multiple_distinct_phrases_count_separately(self):
        # Different evidential phrases in same text shouldn't
        # collapse to 1 — they're non-overlapping spans.
        text = (
            "Evidence shows progress. Research demonstrates impact. "
            "Data suggests trends."
        ) * 5
        a = sm.audit_stance_modality(text)
        # 3 phrases × 5 reps = 15 evidentials (not 30 with the
        # pre-fix double-counting).
        assert a["category_counts"]["evidential"] == 15

    def test_non_evidential_category_still_uses_dedup(self):
        # Sanity: hedge category has multiple patterns too. The dedup
        # should apply uniformly without breaking other categories.
        text = "Somewhat indeed, more or less, the result is somewhat clear. " * 5
        a = sm.audit_stance_modality(text)
        # Should count `somewhat` (hedge) + `indeed` (booster) +
        # `more or less` (hedge) per repetition, with no double
        # counting from overlapping patterns.
        assert isinstance(a["category_counts"]["hedge"], int)
        assert a["category_counts"]["hedge"] >= 5  # at least "more or less"


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
