#!/usr/bin/env python3
"""Regression tests for kicker_density.py.

Three layers:

  * **Classifier unit tests** (`is_kicker_shape`): per-condition
    pass/fail behavior, edge cases (empty, single-token, sentence-
    initial proper nouns), regex vs spaCy paths.
  * **Density math tests** (`kicker_density`): aggregate density,
    spacing variance, baseline-comparison block.
  * **End-to-end fixture tests**: run the detector against the
    three shipped synthetic fixtures and verify the expected
    densities.

The spaCy code path is exercised only when `en_core_web_sm` is
available; otherwise gated with `@_skip_no_spacy`. The regex path
runs unconditionally.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kicker_density as k  # type: ignore


_FIXTURE_DIR = ROOT / "test_data" / "aic_8_9"


def _check_spacy_sm_available() -> bool:
    try:
        import spacy  # type: ignore
        try:
            spacy.load("en_core_web_sm")
            return True
        except OSError:
            return False
    except ImportError:
        return False


_HAS_SPACY = _check_spacy_sm_available()
_skip_no_spacy = pytest.mark.skipif(
    not _HAS_SPACY,
    reason="en_core_web_sm not installed",
)


# ----------------- Classifier: per-condition behavior --------------


def test_kicker_short_declarative_aphorism():
    """The canonical positive: short, period-final, no digits, no PROPN."""
    cls = k.is_kicker_shape("Every paragraph performs landing.")
    assert cls.is_kicker is True
    assert cls.confidence == 1.0


def test_kicker_long_sentence_fails():
    """Word count above the limit disqualifies."""
    long_sentence = " ".join(["word"] * 20) + "."
    cls = k.is_kicker_shape(long_sentence)
    assert cls.is_kicker is False
    assert any("word_count" in r for r in cls.reasons)


def test_kicker_question_fails():
    """Sentence-final question mark disqualifies."""
    cls = k.is_kicker_shape("Is every paragraph performing landing?")
    assert cls.is_kicker is False
    assert any("?" in r for r in cls.reasons)


def test_kicker_exclamation_fails():
    cls = k.is_kicker_shape("Every paragraph performs landing!")
    assert cls.is_kicker is False
    assert any("!" in r for r in cls.reasons)


def test_kicker_ellipsis_fails():
    cls = k.is_kicker_shape("Every paragraph performs landing…")
    assert cls.is_kicker is False


def test_kicker_digit_fails():
    """A digit anywhere in the sentence disqualifies."""
    cls = k.is_kicker_shape("Every paragraph in 2024 performs landing.")
    assert cls.is_kicker is False
    assert any("digit" in r for r in cls.reasons)


def test_kicker_year_in_compound_fails():
    cls = k.is_kicker_shape("The 1980s changed everything.")
    assert cls.is_kicker is False


def test_kicker_mid_sentence_proper_noun_fails_regex():
    """Regex path catches mid-sentence capitalized tokens not in allowlist."""
    cls = k.is_kicker_shape("Every paragraph by Borges performs landing.")
    assert cls.is_kicker is False
    assert any("capitalized" in r or "propn" in r for r in cls.reasons)


def test_kicker_first_person_pronoun_allowed():
    """`I`, `I'm`, `I've` are in the allowlist; not flagged as PROPN."""
    cls = k.is_kicker_shape("I think every paragraph performs landing.")
    assert cls.is_kicker is True


def test_kicker_word_limit_configurable():
    """Custom word_limit changes the verdict on borderline sentences."""
    sentence = "Every paragraph in the document performs landing now."  # 8 words
    cls_default = k.is_kicker_shape(sentence)  # limit 15
    cls_strict = k.is_kicker_shape(sentence, word_limit=5)  # limit 5
    assert cls_default.is_kicker is True
    assert cls_strict.is_kicker is False


def test_kicker_empty_input_fails():
    cls = k.is_kicker_shape("")
    assert cls.is_kicker is False
    assert cls.confidence == 0.0


def test_kicker_whitespace_only_fails():
    cls = k.is_kicker_shape("   \n\t  ")
    assert cls.is_kicker is False


def test_kicker_single_word_passes():
    """One-word period-final sentences satisfy all conditions."""
    cls = k.is_kicker_shape("Always.")
    assert cls.is_kicker is True


def test_kicker_classification_is_frozen():
    """KickerClassification records are immutable."""
    cls = k.is_kicker_shape("Every paragraph performs landing.")
    with pytest.raises((AttributeError, TypeError)):
        cls.is_kicker = False  # type: ignore


# ----------------- Classifier: spaCy code path ---------------------


@_skip_no_spacy
def test_kicker_spacy_propn_check_catches_common_names():
    """spaCy's POS tagger flags common proper nouns the regex misses."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    cls = k.is_kicker_shape(
        "Joshua Greene argues for utilitarianism.", nlp=nlp,
    )
    assert cls.is_kicker is False
    assert any("propn" in r for r in cls.reasons)


@_skip_no_spacy
def test_kicker_spacy_path_reports_in_reasons():
    """When spaCy is used, the 'reasons' list names the spacy path."""
    import spacy
    nlp = spacy.load("en_core_web_sm")
    cls = k.is_kicker_shape("Every paragraph performs landing.", nlp=nlp)
    assert cls.is_kicker is True
    assert any("spacy" in r for r in cls.reasons)


# ----------------- Density: aggregate behavior ---------------------


def test_density_zero_when_no_paragraph_finals_pass():
    """Document with only long-form paragraphs returns density 0."""
    text = "When the writer is in the middle of a paragraph and the words tumble across multiple clauses with embedded asides and parenthetical insertions and qualifying phrases.\n\nWhen the writer continues for another long-form paragraph and the words tumble across multiple clauses with embedded asides and parenthetical insertions and qualifying phrases that the reader follows but does not summarize."
    block = k.kicker_density(text)
    assert block["value"] == 0.0
    assert block["diagnostics"]["kicker_count"] == 0


def test_density_one_when_every_paragraph_ends_with_kicker():
    """All-kickers fixture returns density 1.0."""
    text = "First paragraph. Every paragraph performs landing.\n\nSecond paragraph. The pattern is clear.\n\nThird paragraph. Closure compresses."
    block = k.kicker_density(text)
    assert block["value"] == 1.0
    assert block["diagnostics"]["kicker_count"] == 3
    assert block["diagnostics"]["total_paragraphs"] == 3


def test_density_partial_returns_correct_ratio():
    """3 kickers / 5 paragraphs = 0.6. The two non-kicker paragraph
    endings exceed the 15-word default limit, so they fail
    classification on word count alone."""
    long_non_kicker = (
        "The writer is in the middle of a long-form paragraph and the "
        "words tumble across multiple clauses with embedded asides and "
        "parenthetical insertions and qualifying phrases that the reader "
        "follows but does not summarize at the end of the paragraph."
    )
    text = (
        "Paragraph one. Every paragraph performs landing.\n\n"  # kicker
        f"Paragraph two. {long_non_kicker}\n\n"  # not (long)
        "Paragraph three. The pattern is clear.\n\n"  # kicker
        f"Paragraph four. {long_non_kicker}\n\n"  # not (long)
        "Paragraph five. Closure compresses."  # kicker
    )
    block = k.kicker_density(text)
    assert block["value"] == pytest.approx(0.6, abs=0.01)
    assert block["diagnostics"]["kicker_count"] == 3


def test_density_empty_document():
    """Empty input returns density 0 with no errors."""
    block = k.kicker_density("")
    assert block["value"] == 0.0
    assert block["diagnostics"]["total_paragraphs"] == 0


def test_density_json_schema_complete():
    """The output dict carries every key the spec specifies."""
    text = "A paragraph. Every paragraph performs landing."
    block = k.kicker_density(text)
    assert block["signal_path"] == "aic_8_9.kicker_density"
    assert block["family"] == "aic-9-closure-inflation"
    assert block["polarity"] == "↑"
    assert block["status"] == "provisional"
    assert block["task_surface"] == "smoothing_diagnosis"
    assert block["claim_license"] == "voice_diagnostic"
    assert "value" in block
    assert "spacing_variance" in block
    assert "paragraphs" in block
    assert "diagnostics" in block


def test_density_per_paragraph_diagnostics():
    """Per-paragraph results carry index, final sentence, and reasons."""
    text = "Paragraph one. Every paragraph performs landing.\n\nParagraph two. The pattern continues for far too long with subordinated clauses and parenthetical insertions and qualifying phrases."
    block = k.kicker_density(text)
    assert len(block["paragraphs"]) == 2
    assert block["paragraphs"][0]["is_kicker"] is True
    assert block["paragraphs"][1]["is_kicker"] is False
    assert "reasons" in block["paragraphs"][0]
    assert "final_sentence" in block["paragraphs"][0]


# ----------------- Spacing variance --------------------------------


def test_spacing_variance_zero_for_uniform_kickers():
    """Every paragraph a kicker → distances all 1 → SD 0."""
    text = "\n\n".join(
        ["Sentence one. Every paragraph performs landing."] * 5
    )
    block = k.kicker_density(text)
    assert block["spacing_variance"] == 0.0


def test_spacing_variance_zero_for_single_kicker():
    """One kicker → no inter-kicker distance → SD defined as 0."""
    long_non_kicker = (
        "A long opening paragraph with subordinated clauses and "
        "qualifying phrases and embedded asides and parenthetical "
        "insertions that does not perform landing aphoristically at "
        "the end of the paragraph the way a kicker would."
    )
    text = (
        f"{long_non_kicker}\n\n"
        f"{long_non_kicker}\n\n"
        "Short paragraph. The kicker lands.\n\n"
        f"{long_non_kicker}"
    )
    block = k.kicker_density(text)
    assert block["diagnostics"]["kicker_count"] == 1
    assert block["spacing_variance"] == 0.0


def test_spacing_variance_positive_when_distances_vary():
    """Kickers at paragraphs 0, 3, 7 → distances [3, 4] → SD > 0."""
    paras = []
    paras.append("Paragraph zero. Every paragraph performs landing.")  # kicker @ 0
    for i in range(1, 3):
        paras.append(
            f"Paragraph {i}. The writer continues for a far longer sentence with subordinated clauses and qualifying phrases that does not perform landing aphoristically."
        )
    paras.append("Paragraph three. Closure compresses.")  # kicker @ 3
    for i in range(4, 7):
        paras.append(
            f"Paragraph {i}. The writer continues for a far longer sentence with subordinated clauses and qualifying phrases that does not perform landing aphoristically."
        )
    paras.append("Paragraph seven. The pattern emerges.")  # kicker @ 7
    text = "\n\n".join(paras)
    block = k.kicker_density(text)
    # Kickers should be at paragraph indices 0, 3, 7 (distances 3 and 4).
    # However the regex flags 'Paragraph' as mid-sentence capital, so
    # the digit-numbered sentences ('Paragraph 1', etc.) won't qualify.
    # The kicker sentences (no digit, no PROPN) at 0, 3, 7 should.
    assert block["diagnostics"]["kicker_count"] == 3
    assert block["spacing_variance"] > 0.0


# ----------------- Baseline comparison -----------------------------


def test_baseline_comparison_emitted_when_provided():
    """Passing baseline_value adds a baseline_comparison block."""
    text = "A paragraph. Every paragraph performs landing."
    block = k.kicker_density(text, baseline_value=0.10, baseline_source="test_baseline")
    assert "baseline_comparison" in block
    assert block["baseline_comparison"]["baseline_value"] == 0.10
    assert block["baseline_comparison"]["baseline_source"] == "test_baseline"
    assert block["baseline_comparison"]["elevation_factor"] == pytest.approx(10.0)


def test_baseline_comparison_absent_when_not_provided():
    """No baseline_value → no baseline_comparison key."""
    text = "A paragraph. Every paragraph performs landing."
    block = k.kicker_density(text)
    assert "baseline_comparison" not in block


def test_baseline_comparison_handles_zero_baseline():
    """Zero baseline produces None elevation factor (don't divide by zero)."""
    text = "A paragraph. Every paragraph performs landing."
    block = k.kicker_density(text, baseline_value=0.0)
    assert block["baseline_comparison"]["elevation_factor"] is None


# ----------------- Fixture integration -----------------------------


def test_aphoristic_fixture_high_density():
    """Aphoristic positive fixture: 5/6 paragraphs are kickers (one
    closes with an AIC-9 reference that legitimately fails the
    proper-noun + digit checks)."""
    fixture = _FIXTURE_DIR / "kicker_aphoristic_positive.md"
    text = fixture.read_text(encoding="utf-8")
    block = k.kicker_density(text)
    assert block["value"] >= 0.75  # high-density expectation
    assert block["diagnostics"]["kicker_count"] >= 5


def test_normal_fixture_zero_density():
    """Normal negative fixture: long-form paragraphs, no kickers."""
    fixture = _FIXTURE_DIR / "kicker_normal_negative.md"
    text = fixture.read_text(encoding="utf-8")
    block = k.kicker_density(text)
    assert block["value"] == 0.0


def test_mixed_clustered_fixture_moderate_density():
    """Mixed/clustered fixture: 2 kickers across 7 paragraphs."""
    fixture = _FIXTURE_DIR / "kicker_mixed_clustered.md"
    text = fixture.read_text(encoding="utf-8")
    block = k.kicker_density(text)
    assert 0.1 < block["value"] < 0.5  # moderate density window
    assert block["diagnostics"]["kicker_count"] >= 2


# ----------------- CLI smoke ---------------------------------------


def test_cli_runs_on_fixture_and_emits_json():
    """End-to-end: CLI invocation should produce parsable JSON with
    the expected schema."""
    fixture = _FIXTURE_DIR / "kicker_aphoristic_positive.md"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "kicker_density.py"),
            str(fixture),
            "--force-regex",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["signal_path"] == "aic_8_9.kicker_density"
    assert data["family"] == "aic-9-closure-inflation"
    assert "value" in data


def test_cli_baseline_flag_emits_comparison(tmp_path: Path):
    """`--baseline 0.10` should add the baseline_comparison block."""
    fixture = _FIXTURE_DIR / "kicker_aphoristic_positive.md"
    out = tmp_path / "out.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "kicker_density.py"),
            str(fixture),
            "--baseline", "0.10",
            "--baseline-source", "test_cli_baseline",
            "--force-regex",
            "--out", str(out),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(out.read_text())
    assert "baseline_comparison" in data
    assert data["baseline_comparison"]["baseline_source"] == "test_cli_baseline"
    assert data["baseline_comparison"]["elevation_factor"] > 1.0


def test_cli_missing_file_returns_error():
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "kicker_density.py"),
            "/tmp/does_not_exist.md",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower()


def test_cli_help_runs_cleanly():
    """`--help` exits 0 and prints usage."""
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "kicker_density.py"),
            "--help",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "AIC-9" in result.stdout or "kicker" in result.stdout.lower()
