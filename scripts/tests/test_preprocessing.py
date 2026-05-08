#!/usr/bin/env python3
"""Regression tests for corpus-hygiene preprocessing.

The CSS fixture is a synthetic miniature of an empirical failure mode:
essayistic prose with loose reading-mode widget CSS embedded between prose
paragraphs. It is not private text. When spaCy is available, the KL test
checks that stripping CSS collapses the POS-bigram divergence against the
clean reference baseline by at least 3x.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover - direct unittest-style invocation
    pytest = None

from preprocessing import count_tokens, strip_non_prose
from variance_audit import (
    HAS_SPACY,
    audit_baseline,
    audit_text,
    compare_distributions,
)


FIXTURE_DIR = ROOT / "test_data" / "preprocessing"
CONTAMINATED = FIXTURE_DIR / "css_contaminated_fixture.md"
CLEAN = FIXTURE_DIR / "css_contaminated_fixture_clean.md"
CLEAN_BASELINE = ROOT / "test_data" / "clean_baseline"


def test_css_rule_blocks_are_stripped_and_attributed() -> None:
    raw = CONTAMINATED.read_text(encoding="utf-8")
    clean_reference = CLEAN.read_text(encoding="utf-8")
    cleaned, meta = strip_non_prose(raw)

    assert ".reading-mode-toggle" not in cleaned
    assert "font-size" not in cleaned
    assert "The council room was ordinary" in cleaned
    assert count_tokens(cleaned) <= count_tokens(clean_reference) + 12
    assert meta["tokens_stripped"] > 0
    assert meta["dominant_rule"] == "css_rule_block"
    assert meta["tokens_stripped_by_rule"]["css_rule_block"] == max(
        meta["tokens_stripped_by_rule"].values()
    )


def test_allow_non_prose_is_a_noop_with_metadata() -> None:
    raw = CONTAMINATED.read_text(encoding="utf-8")
    cleaned, meta = strip_non_prose(raw, allow_non_prose=True)

    assert cleaned == raw
    assert meta["applied"] is False
    assert meta["opt_out"] is True
    assert meta["tokens_stripped"] == 0
    assert "--allow-non-prose" in meta["warning"]


def test_pos_bigram_kl_drops_after_css_stripping_when_spacy_is_available() -> None:
    """Structural-presence check, not a magnitude-match check.

    This synthetic fixture is significantly smaller than the empirical
    case the rule was motivated by, and the clean reference baseline
    shares prose with the cleaned target, so the KL floor approaches
    zero on the cleaned side. The 3x assertion here demonstrates that
    CSS stripping produces a meaningful KL collapse on this fixture;
    it does not preserve the empirical magnitude ratio against an
    independent register-matched corpus. A larger fixture and a
    register-matched-but-different-content baseline are tracked as
    future work.
    """
    if not HAS_SPACY:
        if pytest is not None:
            pytest.skip("spaCy model unavailable; POS-bigram KL test skipped")
        return

    raw = CONTAMINATED.read_text(encoding="utf-8")
    baseline = audit_baseline(
        str(CLEAN_BASELINE),
        do_tier3=False,
        allow_non_prose=True,
    )
    unstripped = audit_text(
        raw,
        do_tier3=False,
        allow_non_prose=True,
    )
    stripped = audit_text(raw, do_tier3=False)

    raw_kl = compare_distributions(unstripped, baseline)["pos_bigrams"]["kl_to_baseline"]
    stripped_kl = compare_distributions(stripped, baseline)["pos_bigrams"]["kl_to_baseline"]

    assert stripped_kl < raw_kl / 3
    assert stripped["preprocessing"]["dominant_rule"] == "css_rule_block"
