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
        assert ecg._read_baseline_size(vd, None) == 25

    def test_baseline_size_from_paragraph(self):
        para = {"baseline_block": {"n_files": 10}}
        assert ecg._read_baseline_size(None, para) == 10

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


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
