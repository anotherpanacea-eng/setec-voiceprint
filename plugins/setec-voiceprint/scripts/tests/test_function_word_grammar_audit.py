#!/usr/bin/env python3
"""Regression tests for function_word_grammar_audit.py (Release 5)."""

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

import function_word_grammar_audit as fg  # type: ignore


_VARIED = (
    "Although the discipline of attention is older than its "
    "applications, it remains the precondition for craft. While the "
    "carpenter and the mathematician share habits, they do not share "
    "vocabularies. We have come to expect, after all, that what we "
    "say is what we mean — but writers know better. They suspect "
    "that meaning is what we discover. When we look longer, we see "
    "what we missed. If we never looked, we would have nothing to "
    "say. Because she insists on the longer look, the photographer "
    "produces images that her colleagues cannot."
) * 3


class TestAuditBasics:
    def test_empty_text_unavailable(self):
        a = fg.audit_function_word_grammar("")
        assert a["available"] is False

    def test_returns_n_words_function_ratio(self):
        a = fg.audit_function_word_grammar(_VARIED)
        assert a["n_words"] > 0
        assert 0.0 < a["function_word_ratio"] < 1.0

    def test_function_bigrams_dict(self):
        a = fg.audit_function_word_grammar(_VARIED)
        assert isinstance(a["function_bigrams"], dict)

    def test_preposition_counts_recorded(self):
        a = fg.audit_function_word_grammar(_VARIED)
        # Common prepositions should be in the counts.
        prepos = a["preposition_counts"]
        assert "of" in prepos or "in" in prepos


class TestSubordinatorProfile:
    def test_subordinator_entropy_meaningful_on_varied_prose(self):
        a = fg.audit_function_word_grammar(_VARIED)
        # Varied prose uses multiple subordinators (although, while,
        # because, when, if).
        assert sum(a["subordinator_counts"].values()) >= 5
        assert a["subordinator_entropy_bits"] > 0.5


class TestPronounTransition:
    def test_records_transitions(self):
        text = (
            "He walked. He waited. She arrived. She spoke. They left."
        )
        a = fg.audit_function_word_grammar(text)
        pt = a["pronoun_transition"]
        assert pt["total"] >= 1
        assert "same_share" in pt


class TestBandCall:
    def test_varied_prose_lightly_shifted(self):
        a = fg.audit_function_word_grammar(_VARIED)
        # Genuinely-varied prose has high entropy; band should be
        # Lightly grammar-shifted.
        assert a["compression"]["band"] in {
            "Lightly grammar-shifted", "Moderately grammar-shifted",
        }


class TestBaselineHardening:
    def test_nonexistent_baseline_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            fg.audit_baseline_function_grammar(
                str(tmp_path / "no_dir"),
            )

    def test_target_overlap_excluded(self, tmp_path, capsys):
        base = tmp_path / "baseline"
        base.mkdir()
        target = base / "draft.txt"
        target.write_text(_VARIED, encoding="utf-8")
        (base / "other.txt").write_text(_VARIED, encoding="utf-8")
        block = fg.audit_baseline_function_grammar(
            str(base), target_path=target,
        )
        assert block["n_files"] == 1

    def test_filenames_anonymized(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        (base / "private_brief.txt").write_text(
            _VARIED, encoding="utf-8",
        )
        block = fg.audit_baseline_function_grammar(str(base))
        for s in block["per_file_summaries"]:
            assert "private" not in s["file"]


class TestRender:
    def test_markdown_includes_claim_license(self):
        a = fg.audit_function_word_grammar(_VARIED)
        md = fg.render_report(a)
        assert "## What this result licenses" in md


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        in_path = tmp_path / "draft.txt"
        in_path.write_text(_VARIED, encoding="utf-8")
        out_path = tmp_path / "out.json"
        rc = fg.main(["--json", "--out", str(out_path), str(in_path)])
        assert rc == 0


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
