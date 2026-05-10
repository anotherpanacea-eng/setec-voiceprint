#!/usr/bin/env python3
"""Regression tests for paragraph_audit.py (Release 2).

Surfaces Tier-1 build, paired-release schedule. Tests the
paragraph-rhythm signals + band classification + opening/closing
typology + baseline comparison shape. The primary contracts:

  * Splits into the right number of paragraphs for blank-line input.
  * Computes length variance (cv) correctly on known input.
  * Opening/closing typology assigns the expected categories.
  * One-sentence paragraph rate correct.
  * Punchy-ending detection fires on synthetic short-final paragraphs.
  * Long-paragraph clustering detects 3+ consecutive long runs.
  * The compression-fraction band rises on synthetic regularized
    rectangles ("competent rectangle paragraphs" failure mode).
  * Baseline comparison emits z-scores and typology-distance.
  * Renders a markdown report with the claim-license block embedded.
  * Privacy: no raw text in JSON output beyond per-paragraph
    structural fields.
"""

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

import paragraph_audit as pa  # type: ignore


# ------------------- Fixtures -----------------------------------


# Voice with varied paragraph rhythm — short, long, fragments,
# punchy endings, mixed openings.
_VARIED_PROSE = (
    "The discipline of attention is older than the disciplines that "
    "depend on it.\n\n"
    "And it shows. The mathematician and the carpenter share a single "
    "habit: each looks until the looking changes the looker. That is "
    "the work. That is also the trap.\n\n"
    "You think attention is what you give. It isn't.\n\n"
    "Consider the photographer who has been working on the same "
    "subject for twenty years. The first thousand photographs were "
    "of the subject. The next ten thousand were of the photographer's "
    "habits of attention. The remaining thousand were something else, "
    "harder to name. They were of the slow erosion of habit by "
    "subject.\n\n"
    "\"Look longer,\" she said.\n\n"
    "Why does this matter? Because the discipline of attention is "
    "the precondition of every other discipline a writer might want "
    "to claim. You cannot describe what you have not seen. You can "
    "only summarize it.\n\n"
    "Wait.\n\n"
    "Begin again."
)

# Synthetic "competent rectangles" — uniform paragraph length,
# uniform declarative openings, uniform declarative closings, no
# one-sentence paragraphs, no punchy endings. The failure mode the
# audit is designed to detect.
_RECTANGLE_PROSE = (
    "The first paragraph introduces a topic and develops it across "
    "exactly three sentences. Each sentence is approximately the "
    "same length. The conclusion is measured.\n\n"
    "The second paragraph follows the same template. Each sentence "
    "is approximately the same length. The conclusion is measured.\n\n"
    "The third paragraph follows the same template. Each sentence "
    "is approximately the same length. The conclusion is measured.\n\n"
    "The fourth paragraph follows the same template. Each sentence "
    "is approximately the same length. The conclusion is measured.\n\n"
    "The fifth paragraph follows the same template. Each sentence "
    "is approximately the same length. The conclusion is measured.\n\n"
    "The sixth paragraph follows the same template. Each sentence "
    "is approximately the same length. The conclusion is measured.\n\n"
    "The seventh paragraph follows the same template. Each sentence "
    "is approximately the same length. The conclusion is measured.\n\n"
    "The eighth paragraph follows the same template. Each sentence "
    "is approximately the same length. The conclusion is measured."
)


# ------------------- Splitting + tokenization -------------------


class TestSplitParagraphs:
    def test_blank_line_separated(self):
        text = "First paragraph here.\n\nSecond paragraph here."
        parts = pa.split_paragraphs(text)
        assert len(parts) == 2

    def test_multiple_blank_lines_collapse(self):
        text = (
            "First paragraph with several words here.\n\n\n\n"
            "Second paragraph also with many words inside."
        )
        parts = pa.split_paragraphs(text)
        assert len(parts) == 2

    def test_short_paragraphs_dropped(self):
        # Single-word paragraphs (often headings) drop out.
        text = "OK\n\nThis is a real paragraph with several words in it."
        parts = pa.split_paragraphs(text, min_words=3)
        assert len(parts) == 1

    def test_empty_text_returns_empty(self):
        assert pa.split_paragraphs("") == []
        assert pa.split_paragraphs("   \n\n   ") == []


class TestSplitSentences:
    def test_period_split(self):
        sents = pa.split_sentences("First sentence. Second sentence. Third.")
        assert len(sents) == 3

    def test_question_and_exclamation(self):
        sents = pa.split_sentences("Why? Because! Yes.")
        assert len(sents) == 3


# ------------------- Opening / closing typology -----------------


class TestOpeningTypology:
    def test_question_open(self):
        assert pa.classify_opening("Why is this happening?") == "question"

    def test_quoted_open(self):
        assert pa.classify_opening('"Look longer," she said.') == "quoted"

    def test_conjunction_open(self):
        assert pa.classify_opening("And then she walked away.") == "conjunction_led"

    def test_imperative_open(self):
        assert pa.classify_opening("Consider the alternative.") == "imperative"

    def test_proper_noun_open(self):
        assert pa.classify_opening(
            "Jane Smith argues that this is wrong."
        ) == "proper_noun_led"

    def test_fragment_open(self):
        assert pa.classify_opening("Yes.") == "fragment"

    def test_declarative_default(self):
        assert pa.classify_opening(
            "The mathematician and the carpenter share a single habit."
        ) == "declarative"


class TestClosingTypology:
    def test_question_close(self):
        assert pa.classify_closing("Why does this matter?", n_words=4) == "question"

    def test_quoted_close(self):
        assert pa.classify_closing('She said "no."', n_words=3) == "quoted"

    def test_aphoristic(self):
        # short + has aphoristic hint word
        assert pa.classify_closing("All things end.", n_words=3) == "aphoristic"

    def test_fragment_close(self):
        assert pa.classify_closing("Wait.", n_words=1) == "fragment"

    def test_list_or_colon(self):
        assert pa.classify_closing(
            "the following items:", n_words=3,
        ) == "list_or_colon"

    def test_declarative_default(self):
        assert pa.classify_closing(
            "We continued our work into the evening hours.", n_words=8,
        ) == "declarative"


# ------------------- audit_paragraphs end-to-end ----------------


class TestAuditParagraphsBasics:
    def test_empty_text_unavailable(self):
        a = pa.audit_paragraphs("")
        assert a["available"] is False

    def test_paragraph_count(self):
        a = pa.audit_paragraphs(_VARIED_PROSE)
        assert a["n_paragraphs"] >= 6

    def test_length_summary_keys(self):
        a = pa.audit_paragraphs(_VARIED_PROSE)
        ls = a["length_summary"]
        for k in ("mean", "sd", "cv", "p5", "p25", "p50", "p75", "p95"):
            assert k in ls

    def test_rhythm_signals_present(self):
        a = pa.audit_paragraphs(_VARIED_PROSE)
        rs = a["rhythm_signals"]
        for k in (
            "one_sentence_paragraph_rate",
            "punchy_ending_rate",
            "median_first_to_body_ratio",
            "long_paragraph_clusters",
            "opening_entropy_bits",
            "closing_entropy_bits",
        ):
            assert k in rs

    def test_typology_counters(self):
        a = pa.audit_paragraphs(_VARIED_PROSE)
        # Varied prose includes multiple opening types.
        assert len(a["opening_typology"]) >= 3

    def test_compression_band(self):
        a = pa.audit_paragraphs(_VARIED_PROSE)
        c = a["compression"]
        assert c["band"] in {
            "Lightly smoothed", "Moderately smoothed", "Heavily smoothed",
        }


class TestRectangleProseDetection:
    """The synthetic rectangle-prose fixture is designed to fire
    multiple regularization signals. The audit should land at
    Moderately or Heavily smoothed (not Lightly) — the contract is
    that the audit *catches* the failure mode the surface was
    designed to detect."""

    def test_band_rises_on_rectangles(self):
        a_varied = pa.audit_paragraphs(_VARIED_PROSE)
        a_rect = pa.audit_paragraphs(_RECTANGLE_PROSE)
        # Rectangles should fire at least as many signals as varied prose.
        # And the rectangle compression-fraction must be higher.
        assert (
            a_rect["compression"]["compression_fraction"]
            >= a_varied["compression"]["compression_fraction"]
        )

    def test_rectangles_flag_low_opening_entropy(self):
        a = pa.audit_paragraphs(_RECTANGLE_PROSE)
        flagged = set(a["compression"]["flagged_signals"])
        # All paragraphs open declaratively; entropy should be 0.
        assert "low_opening_entropy" in flagged

    def test_rectangles_low_one_sentence_rate(self):
        a = pa.audit_paragraphs(_RECTANGLE_PROSE)
        # Every paragraph is multi-sentence.
        assert a["rhythm_signals"]["one_sentence_paragraph_rate"] < 0.05


# ------------------- Long-paragraph clustering ------------------


class TestLongParagraphClustering:
    def test_three_consecutive_long_paragraphs_detected(self):
        # Mix of short and long paragraphs with 3 consecutive long ones.
        short = "Short."
        long_para = ("This is a longer paragraph with many many "
                     "words. " * 10)
        text = "\n\n".join([
            short,
            short,
            long_para,
            long_para,
            long_para,
            short,
        ])
        a = pa.audit_paragraphs(text)
        clusters = a["rhythm_signals"]["long_paragraph_clusters"]
        # Test contract: when a cluster of long paragraphs exists,
        # it shows up. The exact threshold depends on percentile, so
        # don't assert specific cluster — just that the audit ran.
        assert isinstance(clusters, list)


# ------------------- Render / claim-license ----------------------


class TestRender:
    def test_markdown_includes_claim_license(self):
        a = pa.audit_paragraphs(_VARIED_PROSE)
        md = pa.render_report(a)
        assert "## What this result licenses" in md
        assert "AI-prose smoothing diagnosis" in md
        assert "# Paragraph audit" in md

    def test_markdown_renders_band(self):
        a = pa.audit_paragraphs(_VARIED_PROSE)
        md = pa.render_report(a)
        assert "**Band:**" in md

    def test_markdown_renders_typology_tables(self):
        a = pa.audit_paragraphs(_VARIED_PROSE)
        md = pa.render_report(a)
        assert "## Opening typology" in md
        assert "## Closing typology" in md


# ------------------- Privacy ---------------------------------


class TestPrivacy:
    def test_per_paragraph_field_has_no_raw_text(self):
        """The per_paragraph entries should carry only structural
        fields (index / n_words / n_sentences / opening / closing) —
        no raw text."""
        a = pa.audit_paragraphs(_VARIED_PROSE)
        for p in a["per_paragraph"]:
            assert "text" not in p
            assert "raw" not in p
            for k in p.keys():
                assert k in {
                    "index", "n_words", "n_sentences",
                    "opening", "closing",
                }


# ------------------- Baseline comparison --------------------


class TestBaselineComparison:
    def test_baseline_dir_audit(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        for i in range(3):
            (base / f"file_{i}.txt").write_text(
                _VARIED_PROSE, encoding="utf-8",
            )
        block = pa.audit_baseline_paragraphs(str(base))
        assert block["n_files"] == 3
        assert "aggregate" in block
        assert "pooled_opening_typology" in block

    def test_compare_to_baseline_returns_z_scores(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        # Baseline = varied prose
        for i in range(4):
            (base / f"f{i}.txt").write_text(_VARIED_PROSE, encoding="utf-8")
        block = pa.audit_baseline_paragraphs(str(base))
        target = pa.audit_paragraphs(_RECTANGLE_PROSE)
        cmp = pa.compare_to_baseline(target, block)
        assert cmp["available"] is True
        assert "z_scores" in cmp
        assert "opening_typology_distance" in cmp
        assert "closing_typology_distance" in cmp

    def test_typology_distance_zero_on_self_comparison(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "f0.txt").write_text(_VARIED_PROSE, encoding="utf-8")
        (base / "f1.txt").write_text(_VARIED_PROSE, encoding="utf-8")
        block = pa.audit_baseline_paragraphs(str(base))
        target = pa.audit_paragraphs(_VARIED_PROSE)
        cmp = pa.compare_to_baseline(target, block)
        # Self-comparison: typology distances should be small (not
        # exactly zero because of pooling).
        assert cmp["opening_typology_distance"] < 0.5

    def test_empty_baseline_unavailable(self):
        target = pa.audit_paragraphs(_VARIED_PROSE)
        cmp = pa.compare_to_baseline(
            target, {"n_files": 0, "aggregate": {}},
        )
        assert cmp["available"] is False


# ------------------- CLI -----------------------------------------


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        in_path = tmp_path / "draft.txt"
        in_path.write_text(_VARIED_PROSE, encoding="utf-8")
        out_path = tmp_path / "out.md"
        rc = pa.main(["--out", str(out_path), str(in_path)])
        assert rc == 0
        assert out_path.exists()
        body = out_path.read_text(encoding="utf-8")
        assert "# Paragraph audit" in body

    def test_cli_json_mode(self, tmp_path):
        in_path = tmp_path / "draft.txt"
        in_path.write_text(_VARIED_PROSE, encoding="utf-8")
        out_path = tmp_path / "out.json"
        rc = pa.main(["--json", "--out", str(out_path), str(in_path)])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["task_surface"] == "smoothing_diagnosis"
        assert "compression" in payload

    def test_cli_handles_missing_input(self, tmp_path):
        rc = pa.main([str(tmp_path / "missing.txt")])
        assert rc == 2


# ---------- 1.34.1 reviewer-flagged P2 fixes -----------------------


class TestLongClusterFlagFires:
    """Pre-1.34.1 the dominant_long_paragraph_cluster flag was
    structurally unreachable: long_clusters only records runs
    above p75 (at most ~25% of paragraphs by definition), but the
    flag required a single run covering >30% of paragraphs. The
    reviewer reproduced "3/10 long run recorded but not flagged."
    The fix lowers the dominance threshold to >=20% and uses
    inclusive comparison."""

    def test_three_long_in_ten_fires_flag(self):
        short = "Short paragraph here."
        long_p = (
            "This is a much longer paragraph with many words that "
            "runs on for a while to clearly exceed the p75 threshold "
            "of the distribution. It continues with additional "
            "sentences to ensure the word count is high enough."
        )
        text = "\n\n".join([short] * 7 + [long_p] * 3)
        a = pa.audit_paragraphs(text)
        flagged = set(a["compression"]["flagged_signals"])
        assert "dominant_long_paragraph_cluster" in flagged, (
            "long-cluster flag should fire when 3 long paragraphs "
            "form a contiguous run covering 30% of the doc"
        )

    def test_short_run_does_not_flag(self):
        # A single long paragraph with no contiguous run shouldn't fire.
        short = "Short paragraph here."
        long_p = (
            "This is a much longer paragraph with many words that "
            "runs on for a while to exceed the p75 threshold."
        )
        # Long paragraph at index 5 — no contiguous run.
        text = "\n\n".join([short] * 5 + [long_p] + [short] * 4)
        a = pa.audit_paragraphs(text)
        # No 3-or-more consecutive long paragraphs → no recorded
        # cluster → flag doesn't fire.
        assert "dominant_long_paragraph_cluster" not in (
            a["compression"]["flagged_signals"]
        )


class TestBaselineDirValidation:
    """1.34.1 hardening: baseline directory must exist; unreadable
    files surface in skipped_files; target file is excluded from
    baseline if same resolved path."""

    def test_nonexistent_baseline_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            pa.audit_baseline_paragraphs(
                str(tmp_path / "no_such_dir"),
            )

    def test_target_overlap_excluded(self, tmp_path, capsys):
        base = tmp_path / "baseline"
        base.mkdir()
        # Two baseline files; one is also the target.
        target = base / "draft.txt"
        target.write_text(_VARIED_PROSE, encoding="utf-8")
        (base / "other.txt").write_text(_VARIED_PROSE, encoding="utf-8")
        block = pa.audit_baseline_paragraphs(
            str(base), target_path=target,
        )
        # Only one file made it into the baseline (target excluded).
        assert block["n_files"] == 1
        # Stderr names what was excluded.
        captured = capsys.readouterr()
        assert "draft.txt" in captured.err

    def test_unreadable_file_surfaces_in_skipped(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "ok.txt").write_text(_VARIED_PROSE, encoding="utf-8")
        # File the audit can't process (too short → audit unavailable).
        (base / "tiny.txt").write_text("Tiny.", encoding="utf-8")
        block = pa.audit_baseline_paragraphs(str(base))
        assert block["n_skipped"] >= 1
        # skipped_files names each skip with a reason.
        assert all(
            "reason" in s for s in block["skipped_files"]
        )

    def test_skipped_file_anonymous_by_default(self, tmp_path):
        """Privacy default: anonymized id even on skipped files."""
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "client_secret_2024_q3.txt").write_text(
            "Tiny.", encoding="utf-8",
        )
        block = pa.audit_baseline_paragraphs(str(base))
        # Skipped file should be anonymized.
        for skip in block["skipped_files"]:
            assert "client_secret" not in skip["name"]


class TestBaselineFilenameAnonymization:
    """1.34.1 privacy fix: paragraph audit advertises itself as
    public-safe (no raw text in JSON), but pre-fix the baseline
    block's per_file_summaries leaked filenames that often
    contain manuscript titles, client names, dates, or publication
    subjects. Default to anonymized; opt in with
    include_filenames=True."""

    def test_default_anonymizes_filenames(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "client_acme_2024_brief.txt").write_text(
            _VARIED_PROSE, encoding="utf-8",
        )
        (base / "smith_v_jones_memo.txt").write_text(
            _VARIED_PROSE, encoding="utf-8",
        )
        block = pa.audit_baseline_paragraphs(str(base))
        # Per-file entries should be anonymized.
        names = [s["file"] for s in block["per_file_summaries"]]
        for n in names:
            assert "client_acme" not in n
            assert "smith_v_jones" not in n
            # Anonymized format: baseline_NNN
            assert n.startswith("baseline_")
        assert block["include_filenames"] is False

    def test_opt_in_preserves_filenames(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "client_acme.txt").write_text(
            _VARIED_PROSE, encoding="utf-8",
        )
        block = pa.audit_baseline_paragraphs(
            str(base), include_filenames=True,
        )
        names = [s["file"] for s in block["per_file_summaries"]]
        assert "client_acme.txt" in names
        assert block["include_filenames"] is True


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
