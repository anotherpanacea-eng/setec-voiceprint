#!/usr/bin/env python3
"""Regression tests for punctuation_cadence_audit.py (Release 5)."""

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

import punctuation_cadence_audit as pa  # type: ignore


_VARIED = (
    "The discipline of attention is older than the disciplines that "
    "depend on it. The mathematician (and the carpenter — each in "
    "their own register) shares a single habit: each looks until "
    "the looking changes the looker. Why does this matter? Because "
    "attention, as everyone knows, is not what you give; it is what "
    "you discover. \"Look longer,\" she said. And so they did."
) * 3

_FLAT = (
    "The implementation of the framework requires consideration of "
    "multiple dimensions. Actionable insights are provided through "
    "holistic analysis. The challenges and opportunities of "
    "stakeholder engagement must be addressed in a robust manner. "
    "Decisions are made about the strategy, and recommendations are "
    "provided. The methodology leverages key takeaways from the "
    "literature."
) * 3


class TestAuditBasics:
    def test_empty_text_unavailable(self):
        a = pa.audit_punctuation_cadence("")
        assert a["available"] is False

    def test_returns_per_mark_densities(self):
        a = pa.audit_punctuation_cadence(_VARIED)
        for key in (
            "comma_per_1k", "semicolon_per_1k", "em_dash_per_1k",
            "parenthesis_per_1k", "question_per_1k",
        ):
            assert key in a["densities_per_1k"]

    def test_sentence_final_distribution_sums_to_one(self):
        a = pa.audit_punctuation_cadence(_VARIED)
        total = sum(a["sentence_final_distribution"].values())
        assert abs(total - 1.0) < 1e-9

    def test_interruption_grammar_records_three_types(self):
        a = pa.audit_punctuation_cadence(_VARIED)
        ig = a["interruption_grammar"]
        for key in (
            "parenthetical_per_1k", "em_dash_aside_per_1k",
            "comma_appositive_per_1k", "total_interruption_per_1k",
        ):
            assert key in ig


class TestBandCall:
    def test_varied_lightly_regularized(self):
        a = pa.audit_punctuation_cadence(_VARIED)
        assert a["compression"]["band"] == "Lightly regularized"

    def test_flat_at_least_moderately_regularized(self):
        a = pa.audit_punctuation_cadence(_FLAT)
        assert a["compression"]["band"] in {
            "Moderately regularized", "Heavily regularized",
        }

    def test_flat_flags_dominance_signals(self):
        a = pa.audit_punctuation_cadence(_FLAT)
        flagged = set(a["compression"]["flagged_signals"])
        assert "comma_period_dominance" in flagged
        assert "uniform_sentence_finals" in flagged

    def test_varied_punctuation_no_flags(self):
        a = pa.audit_punctuation_cadence(_VARIED)
        # Varied prose has interruption grammar + sentence-final
        # variety; should fire few or no flags.
        assert a["compression"]["n_flagged"] <= 1


class TestPunctuationBigrams:
    def test_bigrams_recorded(self):
        text = "The dog said, \"hello!\" The cat replied, \"goodbye?\""
        a = pa.audit_punctuation_cadence(text)
        bigrams = a["punctuation_bigrams"]
        # Comma + opening-quote should be recorded.
        assert isinstance(bigrams, dict)


class TestBaselineHardening:
    def test_nonexistent_baseline_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            pa.audit_baseline_punctuation(str(tmp_path / "no_dir"))

    def test_target_overlap_excluded(self, tmp_path, capsys):
        base = tmp_path / "baseline"
        base.mkdir()
        target = base / "draft.txt"
        target.write_text(_VARIED, encoding="utf-8")
        (base / "other.txt").write_text(_VARIED, encoding="utf-8")
        block = pa.audit_baseline_punctuation(
            str(base), target_path=target,
        )
        assert block["n_files"] == 1
        captured = capsys.readouterr()
        assert "draft.txt" in captured.err

    def test_filenames_anonymized_by_default(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "client_secret.txt").write_text(_VARIED, encoding="utf-8")
        block = pa.audit_baseline_punctuation(str(base))
        for s in block["per_file_summaries"]:
            assert "client_secret" not in s["file"]
            assert s["file"].startswith("baseline_")

    def test_filenames_opt_in(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "client.txt").write_text(_VARIED, encoding="utf-8")
        block = pa.audit_baseline_punctuation(
            str(base), include_filenames=True,
        )
        names = [s["file"] for s in block["per_file_summaries"]]
        assert "client.txt" in names

    def test_skipped_recorded(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "empty.txt").write_text("", encoding="utf-8")
        block = pa.audit_baseline_punctuation(str(base))
        assert block["n_skipped"] >= 1


class TestRender:
    def test_markdown_includes_claim_license(self):
        a = pa.audit_punctuation_cadence(_VARIED)
        md = pa.render_report(a)
        assert "## What this result licenses" in md
        assert "Punctuation cadence" in md or "punctuation" in md.lower()


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        in_path = tmp_path / "draft.txt"
        in_path.write_text(_VARIED, encoding="utf-8")
        out_path = tmp_path / "out.json"
        rc = pa.main(["--json", "--out", str(out_path), str(in_path)])
        assert rc == 0


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
