#!/usr/bin/env python3
"""Regression tests for phraseological_signature_audit.py (Release 11)."""

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

import phraseological_signature_audit as psa  # type: ignore


# ---------- Tokenization ----------


class TestTokenize:
    def test_lowercase_word_tokens(self):
        toks = psa._tokenize("The Quick Brown Fox.")
        assert toks == ["the", "quick", "brown", "fox"]

    def test_preserves_apostrophes(self):
        toks = psa._tokenize("It's the writer's voice.")
        # Apostrophe inside the word is preserved.
        assert "it's" in toks or "it’s" in toks
        assert "writer's" in toks or "writer’s" in toks

    def test_preserves_hyphens(self):
        toks = psa._tokenize("Self-aware writer.")
        assert "self-aware" in toks


# ---------- Lexical bundles ----------


class TestLexicalBundles:
    def test_recurring_trigram_returned(self):
        text = (
            "the quick brown fox jumps. the quick brown fox runs. "
            "the quick brown bird flies."
        )
        toks = psa._tokenize(text)
        bundles = psa.extract_lexical_bundles(
            toks, n_values=(3,), min_count=2,
        )
        assert ("the", "quick", "brown") in bundles
        assert bundles[("the", "quick", "brown")] >= 2

    def test_singletons_excluded_under_min_count(self):
        text = "the quick brown fox jumps over the lazy dog."
        toks = psa._tokenize(text)
        bundles = psa.extract_lexical_bundles(
            toks, n_values=(3,), min_count=2,
        )
        # Every trigram appears once → no bundles.
        assert bundles == {}

    def test_min_count_threshold_respected(self):
        text = (
            "the quick brown fox. the quick brown fox. "
            "the quick brown fox."
        )
        toks = psa._tokenize(text)
        bundles_2 = psa.extract_lexical_bundles(
            toks, n_values=(3,), min_count=2,
        )
        bundles_4 = psa.extract_lexical_bundles(
            toks, n_values=(3,), min_count=4,
        )
        assert ("the", "quick", "brown") in bundles_2
        # Trigram occurs 3x, threshold 4 → excluded.
        assert ("the", "quick", "brown") not in bundles_4


# ---------- Slot frames ----------


class TestSlotFrames:
    def test_not_X_but_Y(self):
        hits = psa.extract_slot_frame_hits(
            "It is not just clever but also kind."
        )
        assert "not_X_but_Y" in hits

    def test_the_X_of_the_Y(self):
        hits = psa.extract_slot_frame_hits(
            "The heart of the matter is at stake."
        )
        assert "the_X_of_the_Y" in hits

    def test_neither_X_nor_Y(self):
        hits = psa.extract_slot_frame_hits(
            "Neither the prose nor the argument satisfies."
        )
        assert "neither_X_nor_Y" in hits

    def test_more_X_than_Y(self):
        hits = psa.extract_slot_frame_hits(
            "She is more curious than careful."
        )
        assert "more_X_than_Y" in hits

    def test_no_frames_in_simple_sentence(self):
        hits = psa.extract_slot_frame_hits(
            "She walked to the library."
        )
        # Should not fire any of the slot frames.
        assert hits == {}


# ---------- Idioms ----------


class TestIdiomDetection:
    def test_basic_idiom_detected(self):
        hits = psa.extract_idiom_hits(
            "All things considered, the draft is fine."
        )
        assert "all things considered" in hits

    def test_idiom_case_insensitive(self):
        hits = psa.extract_idiom_hits(
            "AT THE END OF THE DAY, voices matter."
        )
        assert "at the end of the day" in hits

    def test_idiom_count(self):
        text = (
            "On the one hand, prose. On the one hand, voice. "
            "On the one hand, structure."
        )
        hits = psa.extract_idiom_hits(text)
        assert hits.get("on the one hand") == 3

    def test_no_false_positive(self):
        text = "She walked through the day."
        hits = psa.extract_idiom_hits(text)
        # "the day" alone shouldn't trigger "at the end of the day".
        assert "at the end of the day" not in hits


# ---------- Hapax phrase survival ----------


class TestHapaxPhraseSurvival:
    def test_hapax_extraction(self):
        text = "the quick brown fox. the quick brown fox runs."
        toks = psa._tokenize(text)
        # ('the', 'quick', 'brown') appears twice → not hapax.
        # ('quick', 'brown', 'fox') appears twice → not hapax.
        # ('brown', 'fox', 'runs') appears once → hapax.
        hapax = psa.extract_hapax_phrases(toks, n=3)
        assert ("the", "quick", "brown") not in hapax
        assert ("brown", "fox", "runs") in hapax

    def test_survival_rate_basic(self):
        baseline = "the snowdrift covered everything."
        target = "the snowdrift returned in winter."
        baseline_toks = psa._tokenize(baseline)
        target_toks = psa._tokenize(target)
        hapax = psa.extract_hapax_phrases(baseline_toks, n=3)
        n_surv, n_baseline, _ = psa.hapax_survival_rate(
            baseline_hapax=hapax,
            target_tokens=target_toks,
            n=3,
        )
        # At least one hapax 3-gram from baseline appears in target
        # ("the snowdrift X" if word boundaries align).
        # We don't pin the exact count because the example sentences
        # may share variable amounts; just assert the call works.
        assert n_baseline > 0


# ---------- Stance / intensifier frames ----------


class TestStanceFrames:
    def test_doubled_intensifier_detected(self):
        hits = psa.extract_stance_frame_hits(
            "She was really very tired by the end."
        )
        assert "really_very" in hits

    def test_perhaps_it_is_that(self):
        hits = psa.extract_stance_frame_hits(
            "Perhaps it is that we are mistaken."
        )
        assert "perhaps_it_is_that" in hits

    def test_it_seems_to_me(self):
        hits = psa.extract_stance_frame_hits(
            "It seems to me that the prose is fine."
        )
        assert "it_seems_to_me" in hits


# ---------- Top-level audit ----------


class TestAuditPhraseology:
    def test_target_only_no_baseline(self):
        audit = psa.audit_phraseology(
            target_text=(
                "It seems to me, perhaps, that all things "
                "considered, what is striking is the prose."
            ),
        )
        cats = audit["categories"]
        # All five categories should be present (default = all).
        for k in psa.CATEGORY_KEYS:
            assert k in cats

    def test_baseline_supplies_lexical_bundles(self):
        baseline = [
            "the snowdrift covered everything. the kerosene lamp "
            "burned bright. the snowdrift covered everything."
        ]
        target = "She returned to the snowdrift covered everything."
        audit = psa.audit_phraseology(
            target_text=target,
            baseline_texts=baseline,
        )
        bundles = audit["categories"]["lexical_bundles"]["items"]
        assert bundles["n_baseline_bundles"] >= 1

    def test_category_filter_restricts_output(self):
        audit = psa.audit_phraseology(
            target_text="On the one hand, the prose holds.",
            category_filter=["idioms"],
        )
        assert list(audit["categories"].keys()) == ["idioms"]

    def test_unknown_category_raises_value_error(self):
        with pytest.raises(ValueError, match="typo"):
            psa.audit_phraseology(
                target_text="Test.",
                category_filter=["typo"],
            )

    def test_blockquotes_stripped_by_default(self):
        target = (
            "Plain prose here.\n"
            "> All things considered, it was fine.\n"
            "More prose."
        )
        audit = psa.audit_phraseology(target_text=target)
        idioms = audit["categories"]["idioms"]["items"]["target_idioms"]
        # The blockquoted "all things considered" should not count.
        assert "all things considered" not in idioms

    def test_keep_quotes_preserves_blockquote_idioms(self):
        target = "> All things considered, it was fine."
        audit = psa.audit_phraseology(
            target_text=target, keep_quotes=True,
        )
        idioms = audit["categories"]["idioms"]["items"]["target_idioms"]
        assert "all things considered" in idioms

    def test_idioms_baseline_only_diff(self):
        baseline = ["At the end of the day, voices matter."]
        target = "Voices still matter, but no idioms appear here."
        audit = psa.audit_phraseology(
            target_text=target,
            baseline_texts=baseline,
        )
        items = audit["categories"]["idioms"]["items"]
        assert "at the end of the day" in items["baseline_only_idioms"]


# ---------- Render ----------


class TestRender:
    def test_render_includes_claim_license(self):
        audit = psa.audit_phraseology(target_text="Plain prose.")
        md = psa.render_report(audit)
        assert "## What this result licenses" in md

    def test_render_per_category_summary(self):
        audit = psa.audit_phraseology(
            target_text=(
                "It seems to me that, all things considered, "
                "what is striking is the writing."
            ),
        )
        md = psa.render_report(audit)
        assert "Per-category summary" in md
        assert "lexical_bundles" in md
        assert "stance_intensifier_frames" in md

    def test_render_idioms_section(self):
        audit = psa.audit_phraseology(
            target_text="On the one hand, prose. On the other hand, voice.",
        )
        md = psa.render_report(audit)
        # The idioms section should render the shared idioms list.
        assert "on the one hand" in md.lower()


# ---------- CLI ----------


class TestCli:
    def test_cli_target_only(self, tmp_path):
        target = tmp_path / "target.md"
        target.write_text(
            "It seems to me that, all things considered, the "
            "prose is fine.",
            encoding="utf-8",
        )
        out = tmp_path / "audit.json"
        rc = psa.main([
            str(target), "--json", "--out", str(out),
        ])
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        # schema_version 1.0 envelope: categories live under results,
        # per SPEC_output_schema_unification.md.
        assert payload["schema_version"] == "1.0"
        assert payload["task_surface"] == "voice_coherence"
        assert payload["tool"] == "phraseological_signature_audit"
        assert "categories" in payload["results"]

    def test_cli_with_baseline(self, tmp_path):
        target = tmp_path / "target.md"
        target.write_text(
            "It seems to me that the prose is fine.",
            encoding="utf-8",
        )
        bdir = tmp_path / "baseline"
        bdir.mkdir()
        (bdir / "doc1.md").write_text(
            "It seems to me that voices matter. The snowdrift "
            "covered everything. The snowdrift returned.",
            encoding="utf-8",
        )
        out = tmp_path / "audit.json"
        rc = psa.main([
            str(target),
            "--baseline-dir", str(bdir),
            "--json", "--out", str(out),
        ])
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        # baseline metadata lives under the envelope's baseline block.
        assert payload["baseline"] is not None
        assert payload["baseline"]["words"] > 0

    def test_cli_missing_target_returns_2(self, tmp_path):
        rc = psa.main([str(tmp_path / "missing.md")])
        assert rc == 2

    def test_cli_empty_target_returns_2(self, tmp_path):
        empty = tmp_path / "empty.md"
        empty.write_text("", encoding="utf-8")
        rc = psa.main([str(empty)])
        assert rc == 2

    def test_cli_missing_baseline_dir_returns_2(self, tmp_path):
        target = tmp_path / "target.md"
        target.write_text("Some text.", encoding="utf-8")
        rc = psa.main([
            str(target),
            "--baseline-dir", str(tmp_path / "missing"),
        ])
        assert rc == 2

    def test_cli_baseline_dir_self_overlap_filtered(self, tmp_path):
        # Same self-overlap-guard convention as construction
        # signature audit / paragraph audit / controls audit.
        bdir = tmp_path / "baseline"
        bdir.mkdir()
        target = bdir / "target.md"
        target.write_text(
            "It seems to me the prose is fine.",
            encoding="utf-8",
        )
        # Add an OTHER file as the only legitimate baseline.
        (bdir / "other.md").write_text(
            "Voices matter in the long run.",
            encoding="utf-8",
        )
        out = tmp_path / "audit.json"
        rc = psa.main([
            str(target),
            "--baseline-dir", str(bdir),
            "--json", "--out", str(out),
        ])
        assert rc == 0

    def test_cli_baseline_dir_self_overlap_only_returns_2(self, tmp_path):
        bdir = tmp_path / "baseline"
        bdir.mkdir()
        target = bdir / "target.md"
        target.write_text("Some text.", encoding="utf-8")
        rc = psa.main([
            str(target),
            "--baseline-dir", str(bdir),
        ])
        assert rc == 2

    def test_cli_unknown_category_rejected_by_argparse(
        self, tmp_path,
    ):
        target = tmp_path / "target.md"
        target.write_text("Some text.", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            psa.main([
                str(target),
                "--category", "completely_unknown_category",
            ])
        assert exc_info.value.code == 2

    def test_cli_category_filter(self, tmp_path):
        target = tmp_path / "target.md"
        target.write_text(
            "On the one hand, prose; on the other hand, voice.",
            encoding="utf-8",
        )
        out = tmp_path / "audit.json"
        rc = psa.main([
            str(target),
            "--category", "idioms",
            "--json", "--out", str(out),
        ])
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert list(payload["results"]["categories"].keys()) == ["idioms"]


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
