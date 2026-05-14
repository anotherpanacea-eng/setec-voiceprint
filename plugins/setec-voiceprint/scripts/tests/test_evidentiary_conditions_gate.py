#!/usr/bin/env python3
"""Regression tests for evidentiary_conditions_gate.py (Release 6).

The gate is **not a classifier**. Its contract is the qualitative
posture ladder, the rules that cap each level, and the graceful
degradation when inputs are absent. Tests pin those.
"""

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

import evidentiary_conditions_gate as ecg  # type: ignore


# ---------- evaluate_evidentiary_posture ----------


class TestPostureCaps:
    def test_short_text_caps_at_revision_only(self):
        r = ecg.evaluate_evidentiary_posture(target_length=100)
        assert r["posture"] == "revision_only"

    def test_medium_text_caps_at_exploratory(self):
        r = ecg.evaluate_evidentiary_posture(target_length=300)
        assert r["posture"] == "exploratory_comparison"

    def test_long_text_can_reach_research_grade(self):
        r = ecg.evaluate_evidentiary_posture(
            target_length=1500,
            baseline_size=10,
            register_match_strength="strong",
            n_audit_surfaces=4,
        )
        assert r["posture"] == "research_grade_validation"

    def test_no_baseline_caps_at_exploratory(self):
        r = ecg.evaluate_evidentiary_posture(
            target_length=2000,
            baseline_size=0,
            register_match_strength=None,
        )
        assert r["posture"] == "exploratory_comparison"

    def test_small_baseline_caps_at_internal_triage(self):
        r = ecg.evaluate_evidentiary_posture(
            target_length=2000,
            baseline_size=3,
            register_match_strength="strong",
        )
        assert r["posture"] == "internal_triage"

    def test_register_mismatch_caps_at_revision_only(self):
        r = ecg.evaluate_evidentiary_posture(
            target_length=2500,
            baseline_size=25,
            register_match_strength="mismatch",
        )
        assert r["posture"] == "revision_only"

    def test_high_strip_ratio_caps_at_exploratory(self):
        r = ecg.evaluate_evidentiary_posture(
            target_length=2000,
            baseline_size=20,
            register_match_strength="strong",
            strip_ratio=0.40,
            has_confounder_diagnosis=True,
            n_audit_surfaces=5,
        )
        assert r["posture"] == "exploratory_comparison"

    def test_small_impostor_pool_caps_at_internal_triage(self):
        r = ecg.evaluate_evidentiary_posture(
            target_length=2000,
            baseline_size=10,
            register_match_strength="strong",
            impostor_pool_size=3,
            n_audit_surfaces=4,
        )
        assert r["posture"] == "internal_triage"


class TestPostureGates:
    def test_research_grade_requires_confounder_or_3_surfaces(self):
        # Long enough text + good baseline, but only 1 surface and
        # no confounder diagnosis → caps at internal_triage.
        r = ecg.evaluate_evidentiary_posture(
            target_length=2500,
            baseline_size=20,
            register_match_strength="strong",
            n_audit_surfaces=1,
            has_confounder_diagnosis=False,
        )
        assert r["posture"] == "internal_triage"

    def test_research_grade_with_confounder_only(self):
        # Even with just 1 surface, a confounder diagnosis lifts
        # the call to research_grade.
        r = ecg.evaluate_evidentiary_posture(
            target_length=2500,
            baseline_size=20,
            register_match_strength="strong",
            n_audit_surfaces=1,
            has_confounder_diagnosis=True,
        )
        # 2500 < 2000 cap... wait, 2500 >= 2000 so no length cap.
        assert r["posture"] == "research_grade_validation"

    def test_forensic_requires_pre_edit_or_known_author(self):
        # All conditions for research_grade, but no pre-edit / known
        # author → caps at research_grade_validation.
        r = ecg.evaluate_evidentiary_posture(
            target_length=2500,
            baseline_size=25,
            register_match_strength="strong",
            n_audit_surfaces=6,
            has_confounder_diagnosis=True,
            has_pre_edit_version=False,
            has_known_author=False,
        )
        assert r["posture"] == "research_grade_validation"

    def test_forensic_adjacent_with_pre_edit_and_5_surfaces(self):
        r = ecg.evaluate_evidentiary_posture(
            target_length=3000,
            baseline_size=25,
            register_match_strength="strong",
            n_audit_surfaces=5,
            has_confounder_diagnosis=True,
            has_pre_edit_version=True,
        )
        assert r["posture"] == "forensic_adjacent_nondispositive"

    def test_known_author_substitutes_for_pre_edit(self):
        r = ecg.evaluate_evidentiary_posture(
            target_length=3000,
            baseline_size=25,
            register_match_strength="strong",
            n_audit_surfaces=5,
            has_confounder_diagnosis=True,
            has_known_author=True,
        )
        assert r["posture"] == "forensic_adjacent_nondispositive"


class TestPostureCappingViaUseCase:
    def test_user_declared_use_case_caps_posture(self):
        # All evidence supports forensic_adjacent, but user says
        # they only want revision-level guidance.
        r = ecg.evaluate_evidentiary_posture(
            target_length=3000,
            baseline_size=25,
            register_match_strength="strong",
            n_audit_surfaces=5,
            has_confounder_diagnosis=True,
            has_pre_edit_version=True,
            declared_use_case="revision_only",
        )
        assert r["posture"] == "revision_only"

    def test_use_case_does_not_promote_posture(self):
        # User declares forensic_adjacent but evidence is weak.
        # The gate respects the actual evidence, not the wish.
        r = ecg.evaluate_evidentiary_posture(
            target_length=300,
            declared_use_case="forensic_adjacent_nondispositive",
        )
        assert r["posture"] == "exploratory_comparison"


class TestFindings:
    def test_caps_recorded_in_findings(self):
        r = ecg.evaluate_evidentiary_posture(target_length=100)
        # Short-text cap should produce a finding.
        indicators = [f["indicator"] for f in r["findings"]]
        assert "target_length" in indicators

    def test_findings_have_human_readable_reasons(self):
        r = ecg.evaluate_evidentiary_posture(
            target_length=100, baseline_size=0,
        )
        for finding in r["findings"]:
            assert finding["reason"]
            assert finding["effect"]


class TestIndicatorReadHelpers:
    def test_target_length_from_text(self):
        n = ecg._read_target_length("word " * 100, None, None)
        assert n == 100

    def test_target_length_from_variance_audit(self):
        variance = {"audit": {"summary": {"n_words": 1500}}}
        assert ecg._read_target_length(None, variance, None) == 1500

    def test_baseline_size_from_voice_distance(self):
        vd = {"baseline_summary": {"n_files": 25}}
        assert ecg._read_baseline_size(voice_distance=vd) == 25

    def test_baseline_size_from_paragraph(self):
        para = {"baseline_block": {"n_files": 10}}
        assert ecg._read_baseline_size(paragraph=para) == 10

    def test_register_match_strength_extraction(self):
        vd = {"register_match": {"match": {"strength": "strong"}}}
        assert ecg._read_register_match_strength(vd) == "strong"

    def test_strip_ratio_extraction(self):
        variance = {"preprocessing": {"strip_ratio": 0.25}}
        assert ecg._read_strip_ratio(variance) == 0.25


class TestGateE2E:
    def test_gate_with_no_inputs_gives_revision_only(self):
        report = ecg.gate()
        # No evidence → minimum posture.
        assert report["posture"] == "revision_only"

    def test_gate_records_inputs_used(self):
        report = ecg.gate(variance={"x": 1})
        assert report["inputs_used"]["variance"] is True
        assert report["inputs_used"]["voice_distance"] is False


class TestRender:
    def test_markdown_includes_claim_license(self):
        report = ecg.gate(target_text="word " * 100)
        md = ecg.render_report(report)
        assert "## What this result licenses" in md

    def test_markdown_includes_posture_label(self):
        report = ecg.gate(target_text="word " * 100)
        md = ecg.render_report(report)
        assert "## Posture:" in md
        assert "revision_only" in md

    def test_markdown_lists_findings(self):
        report = ecg.gate(target_text="word " * 100)
        md = ecg.render_report(report)
        assert "## Findings" in md


class TestCli:
    def test_cli_no_inputs_succeeds_with_revision_only(self, tmp_path):
        out_path = tmp_path / "out.json"
        rc = ecg.main(["--json", "--out", str(out_path)])
        assert rc == 0
        import json
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["posture"] == "revision_only"

    def test_cli_missing_input_path_returns_2(self, tmp_path):
        rc = ecg.main([
            "--variance-json", str(tmp_path / "missing.json"),
        ])
        assert rc == 2

    def test_cli_invalid_json_returns_2(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{ malformed", encoding="utf-8")
        rc = ecg.main(["--variance-json", str(bad)])
        assert rc == 2


# ---------- 1.37.1 reviewer-flagged P2 fixes ----------------------


class TestUsableAuditValidators:
    """Pre-1.37.1 `_count_audit_inputs` counted every non-None
    dict, including outputs with `available: false` or unrelated
    JSON. Reviewer reproduced forensic_adjacent posture using
    five failed/empty payloads. Fix: per-surface usability
    validator gates the count."""

    def test_unavailable_paragraph_does_not_count(self):
        assert ecg._is_usable_paragraph({"available": False}) is False

    def test_available_paragraph_with_compression_counts(self):
        assert ecg._is_usable_paragraph({
            "available": True,
            "compression": {"band": "Lightly smoothed"},
        }) is True

    def test_empty_dict_does_not_count_as_paragraph(self):
        assert ecg._is_usable_paragraph({}) is False

    def test_variance_with_compression_counts(self):
        assert ecg._is_usable_variance({
            "compression": {"band": "Lightly smoothed"},
        }) is True

    def test_voice_distance_with_overall_or_families(self):
        assert ecg._is_usable_voice_distance({"overall": {}}) is True
        assert ecg._is_usable_voice_distance({"families": {}}) is True
        assert ecg._is_usable_voice_distance({"x": "y"}) is False

    def test_confounder_requires_ranked_list(self):
        assert ecg._is_usable_confounder({
            "ranked_confounders": [],
        }) is True
        assert ecg._is_usable_confounder({"x": "y"}) is False

    def test_gi_requires_decision(self):
        assert ecg._is_usable_gi({
            "decision": "consistent_with_candidate",
        }) is True
        assert ecg._is_usable_gi({"decision": "junk"}) is False

    def test_failed_payloads_dont_promote_posture(self):
        """The reviewer-reproduced bug: 5 failed payloads + a
        confounder stub should NOT reach forensic_adjacent."""
        report = ecg.gate(
            target_text="word " * 2500,
            variance={"available": False},
            voice_distance={"available": False},
            paragraph={"available": False},
            discourse={"available": False},
            agency={"available": False},
            confounder={"x": "stub"},
            has_pre_edit_version=True,
        )
        # All 5 surface inputs failed, and the confounder isn't
        # in the ranked-list shape. n_surfaces should be 0,
        # has_confounder_diagnosis should be False. Posture
        # should NOT be forensic_adjacent.
        assert report["posture"] != "forensic_adjacent_nondispositive"

    def test_count_only_usable_surfaces(self):
        n = ecg._count_audit_surfaces(
            variance={"compression": {"band": "Lightly smoothed"}},
            voice_distance={"overall": {}},
            paragraph={"available": False, "compression": {}},
            discourse={"available": True, "compression": {}},
            agency=None,
            punctuation={"compression": {}, "available": True},
            stance=None,
            function_grammar=None,
        )
        # variance + voice_distance + discourse + punctuation = 4.
        # paragraph fails because available=False; agency / stance /
        # function_grammar are None.
        assert n == 4


class TestVarianceBaselineRead:
    """Pre-1.37.1 the gate read baseline size only from
    voice_distance and paragraph. A variance-only run with 25
    baseline files reported `baseline_size: 0`. Fix: read from
    every audit shape that surfaces a baseline-size count."""

    def test_variance_baseline_size_extraction(self):
        variance = {"baseline": {"n_files": 25}}
        assert ecg._read_baseline_size(variance=variance) == 25

    def test_tier2_audit_baseline_extraction(self):
        # Tier-2 audits use baseline_block.n_files (matches
        # paragraph audit's shape).
        for kwarg in (
            "discourse", "agency", "punctuation", "stance",
            "function_grammar",
        ):
            block = {"baseline_block": {"n_files": 12}}
            n = ecg._read_baseline_size(**{kwarg: block})
            assert n == 12, f"failed for {kwarg}"

    def test_max_across_audits(self):
        n = ecg._read_baseline_size(
            variance={"baseline": {"n_files": 5}},
            voice_distance={"baseline_summary": {"n_files": 25}},
            paragraph={"baseline_block": {"n_files": 12}},
        )
        # Max across the three.
        assert n == 25

    def test_variance_only_run_no_longer_caps_at_exploratory(self):
        """End-to-end: a variance-only run with a real baseline
        size of 25 should now reach research-grade (not capped
        at exploratory_comparison by `baseline_size: 0`)."""
        variance = {
            "compression": {"band": "Lightly smoothed"},
            "baseline": {"n_files": 25},
        }
        confounder = {"ranked_confounders": [
            {"confounder": "professional_copyediting", "compatibility_score": 0.7},
        ]}
        report = ecg.gate(
            target_text="word " * 1500,
            variance=variance,
            confounder=confounder,
        )
        # Pre-1.37.1: would have been capped at
        # exploratory_comparison because baseline_size=0. Post-fix:
        # baseline_size=25 + 1500 words + confounder = research-grade
        # eligible (the length cap at 2000 keeps it at
        # research_grade rather than forensic).
        assert report["posture"] in {
            "research_grade_validation",
            "internal_triage",  # if other caps apply
        }
        # And the indicator value should reflect the actual size.
        assert report["indicators"]["baseline_size"] == 25


# ---------- Reviewer P2 (2026-05-14 retroactive R6 audit) ----------


class TestUnavailablePayloadsAreRefused:
    """Reviewer P2: the structural usability checks (variance,
    voice_distance, confounder, gi) didn't honor
    ``available: False``. Reviewer reproduced
    research_grade_validation from a variance payload marked
    unavailable plus a confounder, because:

      * ``_read_baseline_size`` pulled n_files without checking
        availability → spuriously high baseline indicator.
      * ``_is_usable_variance`` counted the variance surface
        → "two surfaces present."
      * Combined: posture promoted to research_grade against
        an audit that produced no actual evidence.

    Post-fix: every usability check + metadata read honors
    ``available is False`` as "not usable, not present."
    """

    def test_is_usable_variance_refuses_available_false(self):
        unavail = {
            "available": False,
            "reason": "spaCy missing",
            "compression": {"band": "Heavily smoothed"},
        }
        assert ecg._is_usable_variance(unavail) is False

    def test_is_usable_variance_accepts_available_true(self):
        ok = {
            "available": True,
            "compression": {"band": "Heavily smoothed"},
        }
        assert ecg._is_usable_variance(ok) is True

    def test_is_usable_variance_accepts_missing_available_key(self):
        """Backwards compat: older payloads (R1-R6 era) without
        the available key are still treated as usable."""
        old_shape = {"compression": {"band": "Heavily smoothed"}}
        assert ecg._is_usable_variance(old_shape) is True

    def test_is_usable_voice_distance_refuses_available_false(self):
        unavail = {
            "available": False,
            "overall": {"burrows_delta": 1.5},
        }
        assert ecg._is_usable_voice_distance(unavail) is False

    def test_is_usable_voice_distance_accepts_available_true(self):
        ok = {
            "available": True,
            "overall": {"burrows_delta": 1.5},
        }
        assert ecg._is_usable_voice_distance(ok) is True

    def test_is_usable_confounder_refuses_available_false(self):
        unavail = {
            "available": False,
            "ranked_confounders": [{"confounder": "ai_smoothing"}],
        }
        assert ecg._is_usable_confounder(unavail) is False

    def test_is_usable_gi_refuses_available_false(self):
        unavail = {
            "available": False,
            "decision": "consistent_with_candidate",
        }
        assert ecg._is_usable_gi(unavail) is False


class TestBaselineSizeIgnoresUnavailable:
    """Reviewer P2: ``_read_baseline_size`` pulled n_files from
    any payload that had the field, without honoring
    ``available: False``. Unavailable payloads must be skipped —
    the baseline-size indicator only reflects audits that actually
    produced evidence."""

    def test_unavailable_variance_baseline_ignored(self):
        unavail = {
            "available": False,
            "baseline": {"n_files": 50},
        }
        n = ecg._read_baseline_size(variance=unavail)
        assert n == 0

    def test_unavailable_voice_distance_baseline_ignored(self):
        unavail = {
            "available": False,
            "baseline_summary": {"n_files": 30},
        }
        n = ecg._read_baseline_size(voice_distance=unavail)
        assert n == 0

    def test_unavailable_paragraph_baseline_ignored(self):
        unavail = {
            "available": False,
            "baseline_block": {"n_files": 12},
        }
        n = ecg._read_baseline_size(paragraph=unavail)
        assert n == 0

    def test_available_variance_baseline_counted(self):
        ok = {
            "available": True,
            "baseline": {"n_files": 25},
        }
        n = ecg._read_baseline_size(variance=ok)
        assert n == 25

    def test_missing_available_key_still_counts(self):
        """Older payload shape (no available key) still
        contributes."""
        old_shape = {"baseline": {"n_files": 25}}
        n = ecg._read_baseline_size(variance=old_shape)
        assert n == 25

    def test_unavailable_takes_max_of_available_only(self):
        """When some payloads are available and others aren't,
        the max only sees the available ones."""
        unavail_variance = {
            "available": False, "baseline": {"n_files": 50},
        }
        avail_voice = {
            "available": True, "baseline_summary": {"n_files": 10},
        }
        n = ecg._read_baseline_size(
            variance=unavail_variance, voice_distance=avail_voice,
        )
        assert n == 10


class TestReadHelpersIgnoreUnavailable:
    """Reviewer P2 third leg: the read-helpers
    ``_read_register_match_strength`` and ``_read_strip_ratio``
    also pulled from unavailable payloads. Pin them too."""

    def test_register_match_strength_ignores_unavailable(self):
        unavail = {
            "available": False,
            "register_match": {"match": {"strength": "strong"}},
        }
        assert ecg._read_register_match_strength(unavail) is None

    def test_register_match_strength_reads_available(self):
        ok = {
            "available": True,
            "register_match": {"match": {"strength": "strong"}},
        }
        assert ecg._read_register_match_strength(ok) == "strong"

    def test_strip_ratio_ignores_unavailable(self):
        unavail = {
            "available": False,
            "preprocessing": {"strip_ratio": 0.45},
        }
        assert ecg._read_strip_ratio(unavail) is None

    def test_strip_ratio_reads_available(self):
        ok = {
            "available": True,
            "preprocessing": {"strip_ratio": 0.45},
        }
        assert ecg._read_strip_ratio(ok) == 0.45


class TestReviewerReproducerScenario:
    """End-to-end reproducer for the reviewer's exact scenario:
    a variance payload marked unavailable plus a confounder
    used to produce research_grade_validation. Post-fix, the
    posture should NOT be promoted to research_grade."""

    def test_unavailable_variance_does_not_promote_to_research_grade(
        self,
    ):
        unavail_variance = {
            "available": False,
            "reason": "spaCy missing",
            "compression": {"band": "Heavily smoothed"},
            "baseline": {"n_files": 50},
        }
        confounder = {
            "available": True,
            "ranked_confounders": [
                {"confounder": "ai_smoothing", "score": 0.6},
            ],
        }
        # 2500 words of target text so the length cap is at the
        # research-grade tier (≥ 2000 words allows the top posture
        # IF other indicators support it).
        target_text = "word " * 2500
        report = ecg.gate(
            variance=unavail_variance,
            confounder=confounder,
            target_text=target_text,
        )
        # Pre-fix: research_grade_validation. Post-fix: lower
        # posture (the variance surface doesn't count + baseline
        # size reads as 0).
        assert report["posture"] != "research_grade_validation"
