#!/usr/bin/env python3
"""Regression tests for fairness_dialect_guardrails.py (Release 9)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import fairness_dialect_guardrails as fdg  # type: ignore


# ---------- Conditions registry ----------


class TestConditionsRegistry:
    def test_eight_conditions(self):
        assert len(fdg.CONDITION_KEYS) == 8

    def test_six_declaration_only(self):
        # nonnative_english and code_switching can be detected /
        # heuristically inferred; the other six are decl-only.
        assert len(fdg.DECLARATION_ONLY_CONDITIONS) == 6
        assert "dialect_features" in fdg.DECLARATION_ONLY_CONDITIONS
        assert "translation_influenced" in fdg.DECLARATION_ONLY_CONDITIONS
        assert "speech_to_text" in fdg.DECLARATION_ONLY_CONDITIONS
        assert "neurodivergent_patterns" in fdg.DECLARATION_ONLY_CONDITIONS
        assert "educational_genre" in fdg.DECLARATION_ONLY_CONDITIONS
        assert "institutional_template" in fdg.DECLARATION_ONLY_CONDITIONS


# ---------- Code-switching detection ----------


class TestCodeSwitchingDetection:
    def test_pure_english_not_flagged(self):
        text = (
            "She walked to the library and read for an hour. "
            "The afternoon was quiet. She returned home at five."
        )
        detected, evidence = fdg.detect_code_switching(text)
        assert detected is False
        assert evidence["n_non_english"] == 0

    def test_accented_loanword_below_threshold(self):
        # A few accented words shouldn't trigger.
        text = "She enjoyed a café. The naïve approach failed."
        detected, _ = fdg.detect_code_switching(text)
        # Two accented chars in a long enough text → below threshold.
        assert detected is False

    def test_code_switching_detected_with_substantial_non_english(self):
        text = (
            "Привет мир. Здравствуйте. Это длинный текст на "
            "русском языке. Some English here. Больше русского "
            "языка чтобы превысить порог."
        )
        detected, evidence = fdg.detect_code_switching(text)
        assert detected is True
        assert evidence["n_non_english"] > 0

    def test_chinese_characters_not_flagged_by_letter_test(self):
        # CJK characters are not isalpha() === False under
        # Python's Unicode but actually they are isalpha. So
        # they should be flagged. This test verifies that.
        text = "Hello 你好世界 hello world. " * 10
        detected, evidence = fdg.detect_code_switching(text)
        assert detected is True or evidence["n_non_english"] > 0


# ---------- Manifest reading ----------


class TestManifestReading:
    def test_tsv_manifest(self, tmp_path):
        manifest = tmp_path / "manifest.tsv"
        manifest.write_text(
            "id\tpath\tuse\tlanguage_status\n"
            "doc1\ta.txt\tbaseline\tnative\n"
            "doc2\tb.txt\tbaseline\tnative\n"
            "doc3\tc.txt\tbaseline\tnon_native_advanced\n"
            "doc4\td.txt\ttarget\tnative\n",
            encoding="utf-8",
        )
        counts = fdg._read_manifest_language_backgrounds(manifest)
        # 2 native baselines, 1 non_native_advanced; target excluded.
        assert counts["native"] == 2
        assert counts["non_native_advanced"] == 1
        assert "doc4" not in str(counts)

    def test_json_manifest(self, tmp_path):
        manifest = tmp_path / "manifest.json"
        manifest.write_text(
            json.dumps([
                {"use": "baseline", "language_status": "native"},
                {"use": "baseline", "language_status": "non_native_advanced"},
                {"use": "target"},
            ]),
            encoding="utf-8",
        )
        counts = fdg._read_manifest_language_backgrounds(manifest)
        assert counts["native"] == 1
        assert counts["non_native_advanced"] == 1

    def test_missing_manifest_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            fdg._read_manifest_language_backgrounds(
                tmp_path / "nope.tsv"
            )


# ---------- Baseline coverage ----------


class TestBaselineCoverage:
    def test_nonnative_with_l2_baseline_covered(self):
        backgrounds = {"non_native_advanced": 5}
        assert fdg.baseline_covers_condition(
            "nonnative_english", backgrounds,
        ) is True

    def test_nonnative_with_only_native_baseline_uncovered(self):
        backgrounds = {"native": 10}
        assert fdg.baseline_covers_condition(
            "nonnative_english", backgrounds,
        ) is False

    def test_dialect_uncovered_always(self):
        # The manifest doesn't track dialect, so coverage is
        # always False (conservative).
        backgrounds = {"native": 30, "non_native_advanced": 5}
        assert fdg.baseline_covers_condition(
            "dialect_features", backgrounds,
        ) is False

    def test_speech_to_text_uncovered_always(self):
        backgrounds = {"native": 30}
        assert fdg.baseline_covers_condition(
            "speech_to_text", backgrounds,
        ) is False


# ---------- Caution report ----------


class TestCautionReport:
    def test_no_conditions_no_caution(self):
        report = fdg.build_caution_report(
            target_text=None,
            declared_conditions=[],
            baseline_backgrounds={"native": 30},
        )
        rec = report["recommendation"]
        assert rec["overall"] == "no_conditions_flagged"
        assert rec["posture_cap"] is None
        assert rec["refuses_evaluative_use"] is False

    def test_declared_condition_creates_flag(self):
        report = fdg.build_caution_report(
            target_text=None,
            declared_conditions=["nonnative_english"],
            baseline_backgrounds={"native": 30},
        )
        flags = report["condition_flags"]
        assert "nonnative_english" in flags
        assert flags["nonnative_english"]["source"] == "declared"
        assert flags["nonnative_english"]["baseline_covered"] is False

    def test_baseline_unmatched_caps_at_revision_only(self):
        report = fdg.build_caution_report(
            target_text=None,
            declared_conditions=["nonnative_english"],
            baseline_backgrounds={"native": 30},
        )
        rec = report["recommendation"]
        assert rec["overall"] == (
            "conditions_present_baseline_unmatched"
        )
        assert rec["posture_cap"] == "revision_only"
        assert rec["refuses_evaluative_use"] is True

    def test_baseline_matched_does_not_cap_at_revision_only(self):
        report = fdg.build_caution_report(
            target_text=None,
            declared_conditions=["nonnative_english"],
            baseline_backgrounds={"non_native_advanced": 30},
        )
        rec = report["recommendation"]
        assert rec["overall"] == (
            "conditions_present_baseline_matched"
        )
        assert rec["posture_cap"] != "revision_only"
        assert rec["refuses_evaluative_use"] is False

    def test_dialect_always_caps(self):
        # No manifest mapping for dialect → always uncovered.
        report = fdg.build_caution_report(
            target_text=None,
            declared_conditions=["dialect_features"],
            baseline_backgrounds={"native": 30, "non_native_advanced": 30},
        )
        rec = report["recommendation"]
        assert rec["refuses_evaluative_use"] is True
        assert rec["posture_cap"] == "revision_only"

    def test_code_switching_detected_from_text(self):
        text = (
            "Привет мир. Здравствуйте. Это длинный текст на "
            "русском языке. Some English here. Больше русского "
            "языка чтобы превысить порог."
        )
        report = fdg.build_caution_report(
            target_text=text,
            declared_conditions=[],
            baseline_backgrounds={"native": 30},
        )
        flags = report["condition_flags"]
        assert "code_switching" in flags
        assert flags["code_switching"]["source"] == "detected"

    def test_unknown_declared_condition_recorded_separately(self):
        report = fdg.build_caution_report(
            target_text=None,
            declared_conditions=["nonexistent_condition"],
            baseline_backgrounds={"native": 30},
        )
        assert "nonexistent_condition" in (
            report["unknown_declared_conditions"]
        )
        # It should NOT appear in condition_flags.
        assert "nonexistent_condition" not in report["condition_flags"]


# ---------- Render ----------


class TestRender:
    def test_no_conditions_renders_clean_report(self):
        report = fdg.build_caution_report(
            target_text=None,
            declared_conditions=[],
            baseline_backgrounds={"native": 30},
        )
        md = fdg.render_report(report)
        assert "Fairness / dialect / multilingual guardrails" in md
        assert "no_conditions_flagged" in md
        assert "## What this result licenses" in md

    def test_flagged_condition_appears_in_render(self):
        report = fdg.build_caution_report(
            target_text=None,
            declared_conditions=["nonnative_english"],
            baseline_backgrounds={"native": 30},
        )
        md = fdg.render_report(report)
        assert "## Flagged conditions" in md
        assert "nonnative_english" in md
        assert "**Refuses evaluative" in md

    def test_unknown_declared_section_when_present(self):
        report = fdg.build_caution_report(
            target_text=None,
            declared_conditions=["nonexistent"],
            baseline_backgrounds={"native": 30},
        )
        md = fdg.render_report(report)
        assert "## Unknown declared conditions" in md


# ---------- CLI ----------


class TestCli:
    def test_cli_no_conditions(self, tmp_path):
        out_path = tmp_path / "report.json"
        rc = fdg.main([
            "--baseline-language-backgrounds", "native:30",
            "--json", "--out", str(out_path),
        ])
        assert rc == 0
        report = json.loads(out_path.read_text(encoding="utf-8"))
        assert (
            report["recommendation"]["overall"]
            == "no_conditions_flagged"
        )

    def test_cli_with_declared_condition(self, tmp_path):
        out_path = tmp_path / "report.json"
        rc = fdg.main([
            "--declare", "nonnative_english",
            "--baseline-language-backgrounds", "native:30",
            "--json", "--out", str(out_path),
        ])
        assert rc == 0
        report = json.loads(out_path.read_text(encoding="utf-8"))
        assert "nonnative_english" in report["condition_flags"]
        assert (
            report["recommendation"]["refuses_evaluative_use"]
            is True
        )

    def test_cli_with_manifest(self, tmp_path):
        manifest = tmp_path / "manifest.tsv"
        manifest.write_text(
            "id\tpath\tuse\tlanguage_status\n"
            "d1\ta.txt\tbaseline\tnon_native_advanced\n"
            "d2\tb.txt\tbaseline\tnon_native_advanced\n",
            encoding="utf-8",
        )
        out_path = tmp_path / "report.json"
        rc = fdg.main([
            "--declare", "nonnative_english",
            "--manifest", str(manifest),
            "--json", "--out", str(out_path),
        ])
        assert rc == 0
        report = json.loads(out_path.read_text(encoding="utf-8"))
        assert (
            report["recommendation"]["refuses_evaluative_use"]
            is False
        )

    def test_cli_with_target_detects_code_switching(self, tmp_path):
        target = tmp_path / "target.md"
        target.write_text(
            "Привет мир. Здравствуйте. Это длинный текст на "
            "русском языке. Some English here. Больше русского "
            "языка чтобы превысить порог.",
            encoding="utf-8",
        )
        out_path = tmp_path / "report.json"
        rc = fdg.main([
            "--target", str(target),
            "--baseline-language-backgrounds", "native:30",
            "--json", "--out", str(out_path),
        ])
        assert rc == 0
        report = json.loads(out_path.read_text(encoding="utf-8"))
        assert "code_switching" in report["condition_flags"]

    def test_cli_missing_target_returns_2(self, tmp_path):
        rc = fdg.main([
            "--target", str(tmp_path / "missing.md"),
        ])
        assert rc == 2

    def test_cli_missing_manifest_returns_2(self, tmp_path):
        rc = fdg.main([
            "--manifest", str(tmp_path / "missing.tsv"),
        ])
        assert rc == 2

    def test_cli_unknown_declare_choice_rejected_by_argparse(self):
        # argparse `choices` rejects unknown declarations at parse
        # time. Returns SystemExit code 2 from argparse.
        with pytest.raises(SystemExit) as exc_info:
            fdg.main([
                "--declare", "completely-unknown-condition",
            ])
        assert exc_info.value.code == 2


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
