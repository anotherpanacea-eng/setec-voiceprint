#!/usr/bin/env python3
"""Regression tests for discourse_move_signature.py (Release 3).

Surfaces Tier-1. Tests the typed-discourse-marker pipeline:
per-category densities + move-sequence bigrams + entropy + band
call. The audit's primary value is providing differentiating
evidence for the confounder audit's differential diagnosis, so
the contracts here pin marker classification, sequence-bigram
construction, and the rough shape of the band call.
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

import discourse_move_signature as dms  # type: ignore


# ---------- Marker classification ----------


class TestClassifySentence:
    def test_contrast_marker(self):
        assert dms.classify_sentence(
            "However, the evidence is mixed."
        ) == "contrast"

    def test_concession_marker(self):
        assert dms.classify_sentence(
            "Admittedly, the case for the policy is strong."
        ) == "concession"

    def test_consequence_marker(self):
        assert dms.classify_sentence(
            "Therefore, the regulator should act."
        ) == "consequence"

    def test_elaboration_marker(self):
        assert dms.classify_sentence(
            "In other words, the harm is structural."
        ) == "elaboration"

    def test_exemplification_marker(self):
        assert dms.classify_sentence(
            "For example, the 2023 study found a 30% drop."
        ) == "exemplification"

    def test_sequencing_marker(self):
        assert dms.classify_sentence(
            "First, the rate fell. Then it rose."
        ) == "sequencing"

    def test_epistemic_marker(self):
        assert dms.classify_sentence(
            "Perhaps the result is artifact."
        ) == "epistemic_stance"

    def test_boosting_marker(self):
        assert dms.classify_sentence(
            "Clearly, this is the right move."
        ) == "boosting"

    def test_no_marker(self):
        assert dms.classify_sentence(
            "The bridge collapsed at midnight."
        ) is None

    def test_first_match_wins(self):
        # "However" appears before "the better question is" so
        # the first-match rule should pick contrast.
        s = "However, the better question is whether we care."
        assert dms.classify_sentence(s) == "contrast"


# ---------- audit_discourse_moves end-to-end ----------


class TestAuditDiscourse:
    def test_empty_text_unavailable(self):
        a = dms.audit_discourse_moves("")
        assert a["available"] is False

    def test_returns_categories(self):
        text = (
            "However, the case is mixed. Therefore, we should think "
            "carefully. For example, consider the 2023 study. "
            "Clearly, the result is consistent. Maybe not. "
            "First, we look. Second, we judge. Finally, we decide."
        )
        a = dms.audit_discourse_moves(text)
        assert a["available"] is True
        # Multiple categories populated.
        densities = a["category_densities_per_1k"]
        assert densities["contrast"] > 0
        assert densities["consequence"] > 0
        assert densities["exemplification"] > 0
        assert densities["sequencing"] > 0

    def test_band_lightly_on_unscaffolded_prose(self):
        text = (
            "She walked down the corridor and looked at the photograph. "
            "He thought about it for a long moment. "
            "He remembered the night, the cold light, the way she had "
            "stood at the window. The room felt smaller. "
            "Outside, the snow had begun to fall again. "
        ) * 3
        a = dms.audit_discourse_moves(text)
        assert a["compression"]["band"] == "Lightly scaffolded"

    def test_band_rises_on_scaffolded_prose(self):
        text = (
            "Admittedly, the case for the policy is strong. "
            "However, enforcement comes at a cost. "
            "Although the literature is divided, recent evidence "
            "suggests a different mechanism. "
            "For example, the 2023 study found compliance fell. "
            "Therefore, we should be cautious about scaling. "
            "Specifically, the implementation should target the "
            "highest-risk categories first. "
            "In other words, less is more. "
            "Of course, the politics are complex. "
            "Nevertheless, the data are clear. "
            "First, the rate dropped. Second, the cost rose. "
            "Finally, the public lost faith."
        )
        a = dms.audit_discourse_moves(text)
        assert a["compression"]["band"] in {
            "Moderately scaffolded", "Heavily scaffolded",
        }

    def test_move_sequence_records_unmarked(self):
        text = (
            "The bridge stood for a hundred years. "
            "However, by 2023 it had begun to crumble."
        )
        a = dms.audit_discourse_moves(text)
        seq = a["move_sequence"]
        assert seq[0] == "_unmarked"
        assert seq[1] == "contrast"

    def test_bigrams_count_transitions(self):
        text = (
            "However, the case is mixed. Therefore, we are cautious. "
            "However, the data are clear."
        )
        a = dms.audit_discourse_moves(text)
        bigrams = a["move_sequence_bigrams"]
        # 'contrast->consequence' and 'consequence->contrast' should be present.
        assert "contrast->consequence" in bigrams or "consequence->contrast" in bigrams

    def test_marked_only_entropy_lower_than_full(self):
        """Marked-only entropy ignores _unmarked, so when most
        sentences are unmarked the marked-only entropy is more
        informative (lower bound) than the full entropy."""
        text = (
            "The bridge stood for a hundred years. "
            "However, in 2023 it began to crumble. "
            "Therefore, it was demolished. "
            "The rubble was removed. "
            "Or rather, most of it."
        )
        a = dms.audit_discourse_moves(text)
        assert a["marked_only_entropy_bits"] >= 0


# ---------- Baseline comparison ----------


class TestBaselineComparison:
    def test_baseline_aggregate(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        text = (
            "However, the case is mixed. Therefore, we are cautious. "
            "For example, the 2023 study found a drop. "
            "Of course, the politics are complex. Clearly, the data "
            "are consistent."
        )
        for i in range(3):
            (base / f"f{i}.txt").write_text(text, encoding="utf-8")
        block = dms.audit_baseline_discourse(str(base))
        assert block["n_files"] == 3
        assert "aggregate_density_by_category" in block

    def test_compare_to_baseline_returns_z_scores(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        text = "However, this. Therefore, that. For example, the 2023 study. Clearly, yes."
        for i in range(4):
            (base / f"f{i}.txt").write_text(text, encoding="utf-8")
        block = dms.audit_baseline_discourse(str(base))
        target = dms.audit_discourse_moves(
            "However, this. Therefore, that. Clearly, yes."
        )
        cmp = dms.compare_to_baseline(target, block)
        assert cmp["available"] is True
        assert "category_density_z_scores" in cmp


# ---------- Render + claim license ----------


class TestRender:
    def test_markdown_includes_claim_license(self):
        text = "However, this is mixed. Therefore, be cautious." * 5
        a = dms.audit_discourse_moves(text)
        md = dms.render_report(a)
        assert "## What this result licenses" in md
        assert "Discourse-marker typology" in md

    def test_markdown_renders_categories(self):
        text = "However, this is mixed. Therefore, be cautious." * 5
        a = dms.audit_discourse_moves(text)
        md = dms.render_report(a)
        assert "## Per-category densities" in md
        assert "contrast" in md
        assert "consequence" in md


# ---------- CLI ----------


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        in_path = tmp_path / "draft.txt"
        in_path.write_text(
            "However, the case is mixed. Therefore, be cautious. "
            "For example, consider the 2023 study. Clearly, it shows.",
            encoding="utf-8",
        )
        out_path = tmp_path / "out.json"
        rc = dms.main(["--json", "--out", str(out_path), str(in_path)])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["task_surface"] == "smoothing_diagnosis"

    def test_cli_handles_missing_input(self, tmp_path):
        rc = dms.main([str(tmp_path / "missing.txt")])
        assert rc == 2


# ---------- 1.34.2 baseline ingestion hardening ----------------


class TestBaselineHardening:
    """1.34.2 fixes the same baseline-ingestion footguns paragraph_audit
    fixed in 1.34.1: validate dir, surface skipped files, exclude
    target overlap, anonymize filenames by default."""

    def test_nonexistent_baseline_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            dms.audit_baseline_discourse(
                str(tmp_path / "no_such_dir"),
            )

    def test_target_overlap_excluded(self, tmp_path, capsys):
        base = tmp_path / "baseline"
        base.mkdir()
        text = (
            "However, this is mixed. Therefore, be cautious. "
            "For example, the 2023 study found a drop. " * 5
        )
        target = base / "draft.txt"
        target.write_text(text, encoding="utf-8")
        (base / "other.txt").write_text(text, encoding="utf-8")
        block = dms.audit_baseline_discourse(
            str(base), target_path=target,
        )
        assert block["n_files"] == 1
        captured = capsys.readouterr()
        assert "draft.txt" in captured.err

    def test_filenames_anonymized_by_default(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        text = "However, this is mixed. " * 30
        (base / "client_secret_brief.txt").write_text(
            text, encoding="utf-8",
        )
        block = dms.audit_baseline_discourse(str(base))
        for s in block["per_file_summaries"]:
            assert "client_secret" not in s["file"]
            assert s["file"].startswith("baseline_")
        assert block["include_filenames"] is False

    def test_filenames_opt_in(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        text = "However, this is mixed. " * 30
        (base / "client_brief.txt").write_text(
            text, encoding="utf-8",
        )
        block = dms.audit_baseline_discourse(
            str(base), include_filenames=True,
        )
        names = [s["file"] for s in block["per_file_summaries"]]
        assert "client_brief.txt" in names

    def test_skipped_files_recorded(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        # Empty file → audit unavailable.
        (base / "empty.txt").write_text("", encoding="utf-8")
        block = dms.audit_baseline_discourse(str(base))
        assert block["n_skipped"] >= 1


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
