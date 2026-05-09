#!/usr/bin/env python3
"""Regression tests for adversarial validation fixtures."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from adversarial_fixtures import (
    SOFT_HYPHEN,
    ZERO_WIDTH_SPACE,
    apply_homoglyphs,
    insert_soft_hyphens,
    insert_zero_width_spaces,
)
from manifest_validator import validate_manifest
from validation_harness import run_harness


FIXTURE_DIR = ROOT / "test_data" / "adversarial"
MANIFEST = FIXTURE_DIR / "validation_adversarial_manifest.jsonl"


def test_unicode_transforms_insert_expected_characters() -> None:
    text = "alpha beta gamma delta epsilon zeta eta theta"

    assert ZERO_WIDTH_SPACE in insert_zero_width_spaces(text, every=2)
    assert SOFT_HYPHEN in insert_soft_hyphens(text, every=2)
    homoglyph = apply_homoglyphs(text, every=2)
    assert any(ord(ch) > 127 for ch in homoglyph)


def test_adversarial_manifest_validates_and_summarizes_class() -> None:
    result = validate_manifest(MANIFEST)

    assert result["n_errors"] == 0
    assert result["n_warnings"] == 0
    by_class = result["summary"]["by_adversarial_class"]
    assert by_class["none"] == 1
    assert by_class["unicode_zero_width"] == 1
    assert by_class["unicode_homoglyph"] == 1
    assert by_class["unicode_soft_hyphen"] == 1


def test_validation_harness_slices_by_adversarial_class() -> None:
    class Args:
        manifest = str(MANIFEST)
        surface = "smoothing_diagnosis"
        strict_manifest = False
        use = "validation"
        check_corpus = False
        corpus_warn_threshold = 0.01
        corpus_fail_threshold = 0.05
        positive_status = ["ai_generated"]
        negative_status = ["pre_ai_human"]
        mattr_window = 50
        no_tier2 = True
        no_tier3 = True
        allow_non_prose = False
        strip_rules = None
        strip_aggressive = False
        fpr_target = None
        confidence_level = 0.95
        ci_method = "wilson"
        metric_bootstrap_resamples = 0
        seed = None
        no_records_table = False
        records_limit = 100

    result = run_harness(Args())
    by_class = result["slices"]["by_adversarial_class"]

    assert "none" in by_class
    assert "unicode_zero_width" in by_class
    assert "unicode_homoglyph" in by_class
    assert "unicode_soft_hyphen" in by_class
