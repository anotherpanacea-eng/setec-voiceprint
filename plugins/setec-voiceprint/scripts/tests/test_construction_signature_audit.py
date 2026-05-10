#!/usr/bin/env python3
"""Regression tests for construction_signature_audit.py (Release 8)."""

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

import construction_signature_audit as csa  # type: ignore


# ---------- Construction registry ----------


class TestConstructionRegistry:
    def test_twelve_constructions_registered(self):
        # 9 regex-only + 3 spaCy-enhanced = 12 total.
        assert len(csa._CONSTRUCTION_REGISTRY) == 12

    def test_three_require_spacy(self):
        n_spacy = sum(
            1 for *_, requires_spacy in csa._CONSTRUCTION_REGISTRY
            if requires_spacy
        )
        assert n_spacy == 3

    def test_new_results_initializes_all(self):
        results = csa._new_results()
        assert len(results) == 12
        # Every construction starts with zero hits.
        for r in results.values():
            assert r.count == 0


# ---------- Regex detectors ----------


class TestCleftDetector:
    def test_basic_cleft(self):
        text = "It is the manuscript that matters most here."
        results, _ = csa.detect_constructions(text)
        assert results["cleft"].count >= 1

    def test_cleft_with_who(self):
        text = "It was Alice who first noticed the discrepancy."
        results, _ = csa.detect_constructions(text)
        assert results["cleft"].count >= 1

    def test_no_cleft_in_simple_sentence(self):
        text = "She walked to the library."
        results, _ = csa.detect_constructions(text)
        assert results["cleft"].count == 0


class TestPseudoCleftDetector:
    def test_what_matters_pseudo_cleft(self):
        text = "What matters is the writer's voice."
        results, _ = csa.detect_constructions(text)
        assert results["pseudo_cleft"].count >= 1

    def test_what_x_was_pseudo_cleft(self):
        text = "What the editor wanted was a complete rewrite."
        results, _ = csa.detect_constructions(text)
        assert results["pseudo_cleft"].count >= 1


class TestExistentialThereDetector:
    def test_there_is(self):
        text = "There is a problem with the draft."
        results, _ = csa.detect_constructions(text)
        assert results["existential_there"].count >= 1

    def test_there_are(self):
        text = "There are several issues to address."
        results, _ = csa.detect_constructions(text)
        assert results["existential_there"].count >= 1

    def test_there_has_been(self):
        text = "There has been considerable debate about the proposal."
        results, _ = csa.detect_constructions(text)
        assert results["existential_there"].count >= 1

    def test_locative_there_not_counted(self):
        # "He was there" is locative, not existential.
        text = "He was there yesterday."
        results, _ = csa.detect_constructions(text)
        assert results["existential_there"].count == 0


class TestExtrapositionDetector:
    def test_it_is_important_to(self):
        text = "It is important to consider the implications."
        results, _ = csa.detect_constructions(text)
        assert results["extraposition"].count >= 1

    def test_it_is_clear_that(self):
        text = "It is clear that the framework needs revision."
        results, _ = csa.detect_constructions(text)
        assert results["extraposition"].count >= 1


class TestCorrelativeDetector:
    def test_not_only_but_also(self):
        text = "The draft is not only verbose but also unfocused."
        results, _ = csa.detect_constructions(text)
        assert results["correlative"].count >= 1

    def test_either_or(self):
        text = "Either we revise the chapter or we cut it entirely."
        results, _ = csa.detect_constructions(text)
        assert results["correlative"].count >= 1

    def test_neither_nor(self):
        text = "Neither the prose nor the argument satisfies."
        results, _ = csa.detect_constructions(text)
        assert results["correlative"].count >= 1


class TestConcessiveOpenerDetector:
    def test_although_opener(self):
        text = "Although the prose is fluent, the argument falters."
        results, _ = csa.detect_constructions(text)
        assert results["concessive_opener"].count >= 1

    def test_while_opener(self):
        text = "While the framework is rigorous, it remains incomplete."
        results, _ = csa.detect_constructions(text)
        assert results["concessive_opener"].count >= 1

    def test_despite_opener(self):
        text = "Despite the editor's revisions, the voice is intact."
        results, _ = csa.detect_constructions(text)
        assert results["concessive_opener"].count >= 1

    def test_no_mid_sentence_although(self):
        # "She continued, although tired" — concessive but not opener.
        text = "She continued, although tired from the work."
        results, _ = csa.detect_constructions(text)
        assert results["concessive_opener"].count == 0


class TestParticipialOpenerDetector:
    def test_ing_opener(self):
        text = "Walking down the street, he noticed the change."
        results, _ = csa.detect_constructions(text)
        assert results["participial_opener"].count >= 1

    def test_ed_opener(self):
        text = "Frustrated by the delay, she rewrote the chapter."
        results, _ = csa.detect_constructions(text)
        assert results["participial_opener"].count >= 1


class TestFrontedAdverbialDetector:
    def test_pp_opener(self):
        text = "In the morning, the editor returned to her notes."
        results, _ = csa.detect_constructions(text)
        assert results["fronted_adverbial"].count >= 1

    def test_temporal_opener(self):
        text = "After the meeting, the team discussed the draft."
        results, _ = csa.detect_constructions(text)
        assert results["fronted_adverbial"].count >= 1

    def test_quoted_attribution_not_counted(self):
        # Dialogue-attribution sentences shouldn't false-trigger.
        text = '"That\'s impossible," she said.'
        results, _ = csa.detect_constructions(text)
        assert results["fronted_adverbial"].count == 0


class TestParentheticalInsertionDetector:
    def test_clause_medial_insertion(self):
        text = (
            "The editor, deeply concerned about the prose, "
            "rewrote the chapter."
        )
        results, _ = csa.detect_constructions(text)
        assert results["parenthetical_insertion"].count >= 1


# ---------- spaCy-enhanced detectors ----------


class TestSpacyDetectors:
    def test_passive_constructions_marked_unavailable_without_spacy(self):
        # When spaCy isn't loaded, the passive-voice and stacked-PP
        # constructions should report `available: false` rather
        # than producing degraded results.
        results = csa._new_results()
        if not csa.HAS_SPACY:
            assert results["agented_passive"].available is False
            assert results["agentless_passive"].available is False
            assert (
                results["stacked_prepositional_phrases"].available
                is False
            )
        else:
            assert results["agented_passive"].available is True

    def test_spacy_constructions_no_hits_without_spacy(self):
        text = (
            "The book was written by Alice. The chapter was edited."
        )
        results, _ = csa.detect_constructions(text)
        if not csa.HAS_SPACY:
            assert results["agented_passive"].count == 0
            assert results["agentless_passive"].count == 0


# ---------- detect_constructions integration ----------


class TestDetectConstructionsIntegration:
    def test_returns_word_count(self):
        text = "This is a short test sentence."
        results, n_words = csa.detect_constructions(text)
        assert n_words == 6

    def test_strips_blockquotes_by_default(self):
        text = (
            "Regular sentence here.\n"
            "> Quoted: It is the case that there is a problem.\n"
            "Another sentence."
        )
        results, _ = csa.detect_constructions(text)
        # The "It is the case" and "there is" inside the blockquote
        # shouldn't be counted toward the writer's density.
        assert results["existential_there"].count == 0

    def test_keep_quotes_includes_blockquote_constructions(self):
        text = (
            "> There is a problem in the quoted text."
        )
        results_strip, _ = csa.detect_constructions(text)
        results_keep, _ = csa.detect_constructions(
            text, keep_quotes=True,
        )
        assert results_strip["existential_there"].count == 0
        assert results_keep["existential_there"].count >= 1

    def test_multiple_constructions_in_one_text(self):
        text = (
            "What matters is the voice. There are many choices. "
            "It is important to revise carefully. Although hard, "
            "the work continues. Walking slowly, she paused."
        )
        results, _ = csa.detect_constructions(text)
        assert results["pseudo_cleft"].count >= 1
        assert results["existential_there"].count >= 1
        assert results["extraposition"].count >= 1
        assert results["concessive_opener"].count >= 1
        assert results["participial_opener"].count >= 1


# ---------- build_audit ----------


class TestBuildAudit:
    def test_audit_has_required_keys(self):
        text = "There is a draft. What matters is the voice."
        results, n_words = csa.detect_constructions(text)
        audit = csa.build_audit(
            target_path=Path("test.md"),
            target_text=text,
            target_results=results,
            target_words=n_words,
            baseline_density_per_1k=None,
            baseline_loaded=[],
            baseline_skipped=[],
            baseline_words=0,
            top=10,
            construction_filter=None,
            include_baseline_filenames=False,
        )
        for k in (
            "task_surface", "tool", "version", "target",
            "target_words", "spacy_available", "constructions",
            "claim_license",
        ):
            assert k in audit

    def test_construction_filter_restricts_output(self):
        text = "There is a draft. What matters is the voice."
        results, n_words = csa.detect_constructions(text)
        audit = csa.build_audit(
            target_path=Path("test.md"),
            target_text=text,
            target_results=results,
            target_words=n_words,
            baseline_density_per_1k=None,
            baseline_loaded=[],
            baseline_skipped=[],
            baseline_words=0,
            top=10,
            construction_filter=["existential_there"],
            include_baseline_filenames=False,
        )
        assert list(audit["constructions"].keys()) == ["existential_there"]

    def test_baseline_comparison_adds_delta_keys(self):
        text = "There is a problem here."
        results, n_words = csa.detect_constructions(text)
        audit = csa.build_audit(
            target_path=Path("test.md"),
            target_text=text,
            target_results=results,
            target_words=n_words,
            baseline_density_per_1k={"existential_there": 1.0},
            baseline_loaded=[Path("base.txt")],
            baseline_skipped=[],
            baseline_words=1000,
            top=10,
            construction_filter=None,
            include_baseline_filenames=False,
        )
        ex_block = audit["constructions"]["existential_there"]
        assert "baseline_density_per_1k" in ex_block
        assert "delta_per_1k" in ex_block

    def test_privacy_default_anonymizes_baseline_filenames(self):
        text = "Test."
        results, n_words = csa.detect_constructions(text)
        audit = csa.build_audit(
            target_path=Path("test.md"),
            target_text=text,
            target_results=results,
            target_words=n_words,
            baseline_density_per_1k={"cleft": 0.0},
            baseline_loaded=[Path("private/secret_doc.md")],
            baseline_skipped=[],
            baseline_words=500,
            top=10,
            construction_filter=None,
            include_baseline_filenames=False,
        )
        assert "baseline_files_loaded" not in audit
        assert audit.get("baseline_files_loaded_count") == 1

    def test_include_baseline_filenames_opt_in(self):
        text = "Test."
        results, n_words = csa.detect_constructions(text)
        audit = csa.build_audit(
            target_path=Path("test.md"),
            target_text=text,
            target_results=results,
            target_words=n_words,
            baseline_density_per_1k={"cleft": 0.0},
            baseline_loaded=[Path("base.md")],
            baseline_skipped=[],
            baseline_words=500,
            top=10,
            construction_filter=None,
            include_baseline_filenames=True,
        )
        assert "baseline_files_loaded" in audit


# ---------- Render ----------


class TestRender:
    def test_markdown_includes_claim_license(self):
        text = "There is a problem."
        results, n_words = csa.detect_constructions(text)
        audit = csa.build_audit(
            target_path=Path("test.md"),
            target_text=text,
            target_results=results,
            target_words=n_words,
            baseline_density_per_1k=None,
            baseline_loaded=[],
            baseline_skipped=[],
            baseline_words=0,
            top=10,
            construction_filter=None,
            include_baseline_filenames=False,
        )
        md = csa.render_report(audit)
        assert "## What this result licenses" in md

    def test_markdown_renders_per_construction_table(self):
        text = "There is a problem. What matters is voice."
        results, n_words = csa.detect_constructions(text)
        audit = csa.build_audit(
            target_path=Path("test.md"),
            target_text=text,
            target_results=results,
            target_words=n_words,
            baseline_density_per_1k=None,
            baseline_loaded=[],
            baseline_skipped=[],
            baseline_words=0,
            top=10,
            construction_filter=None,
            include_baseline_filenames=False,
        )
        md = csa.render_report(audit)
        assert "## Per-construction density" in md
        assert "Existential there" in md

    def test_markdown_top_hits_section(self):
        text = (
            "There is a problem. There is another. There is a third."
        )
        results, n_words = csa.detect_constructions(text)
        audit = csa.build_audit(
            target_path=Path("test.md"),
            target_text=text,
            target_results=results,
            target_words=n_words,
            baseline_density_per_1k=None,
            baseline_loaded=[],
            baseline_skipped=[],
            baseline_words=0,
            top=10,
            construction_filter=None,
            include_baseline_filenames=False,
        )
        md = csa.render_report(audit)
        assert "## Top hits" in md


# ---------- CLI ----------


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        target = tmp_path / "draft.md"
        target.write_text(
            "There is a problem. What matters is the writing.\n"
            "It is important to revise. Although tired, she "
            "continued.",
            encoding="utf-8",
        )
        out = tmp_path / "audit.json"
        rc = csa.main([
            str(target), "--json", "--out", str(out),
        ])
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["task_surface"] == "voice_coherence"
        assert "constructions" in payload

    def test_cli_missing_target_returns_2(self, tmp_path):
        rc = csa.main([str(tmp_path / "missing.md")])
        assert rc == 2

    def test_cli_empty_target_returns_2(self, tmp_path):
        empty = tmp_path / "empty.md"
        empty.write_text("", encoding="utf-8")
        rc = csa.main([str(empty)])
        assert rc == 2

    def test_cli_missing_baseline_dir_returns_2(self, tmp_path):
        target = tmp_path / "draft.md"
        target.write_text("Some text.", encoding="utf-8")
        rc = csa.main([
            str(target),
            "--baseline-dir", str(tmp_path / "missing-dir"),
        ])
        assert rc == 2

    def test_cli_baseline_dir_with_no_readable_files(self, tmp_path):
        target = tmp_path / "draft.md"
        target.write_text("Some text.", encoding="utf-8")
        bdir = tmp_path / "baseline"
        bdir.mkdir()
        # Drop only a non-text file in the baseline dir.
        (bdir / "image.png").write_bytes(b"\x89PNG\r\n")
        rc = csa.main([
            str(target),
            "--baseline-dir", str(bdir),
        ])
        assert rc == 2

    def test_cli_baseline_dir_round_trip(self, tmp_path):
        target = tmp_path / "draft.md"
        target.write_text(
            "There is a problem. What matters is voice.",
            encoding="utf-8",
        )
        bdir = tmp_path / "baseline"
        bdir.mkdir()
        (bdir / "doc1.txt").write_text(
            "It was clear that the prose held together. "
            "Although flawed, the draft cohered.",
            encoding="utf-8",
        )
        out = tmp_path / "audit.json"
        rc = csa.main([
            str(target),
            "--baseline-dir", str(bdir),
            "--json", "--out", str(out),
        ])
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        # Baseline data should be present.
        assert "baseline_words" in payload
        assert "baseline_files_loaded_count" in payload
        # Per-construction blocks should carry the delta.
        ex_block = payload["constructions"]["existential_there"]
        assert "baseline_density_per_1k" in ex_block

    def test_cli_construction_filter(self, tmp_path):
        target = tmp_path / "draft.md"
        target.write_text(
            "There is a problem. What matters is voice.",
            encoding="utf-8",
        )
        out = tmp_path / "audit.json"
        rc = csa.main([
            str(target),
            "--construction", "existential_there",
            "--json", "--out", str(out),
        ])
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert list(payload["constructions"].keys()) == [
            "existential_there",
        ]


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
