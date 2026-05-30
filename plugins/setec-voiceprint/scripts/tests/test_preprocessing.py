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


def test_brace_placeholders_in_prose_are_not_stripped() -> None:
    """css_rule_block must not fire on prose that merely contains a
    ``{token}`` template placeholder.

    Regression for the empirical MAGE failure mode: single-line documents
    (wikiHow-style steps with ``{substep}`` markers, or articles ending
    ``... on {date}``) were matched by the permissive opener regex and
    stripped in full (strip_ratio 1.0) despite containing no CSS.
    """
    cases = [
        "Just be careful not to overdo it. You may not be ready yet, so "
        "try going about things differently. {substepad1} {substepad2}",
        'Imperialism is defined as "A policy of extending a country\'s '
        'power." It was a major cause of World War I. More by Kaleb on {date}',
    ]
    for raw in cases:
        cleaned, meta = strip_non_prose(raw)
        assert cleaned.strip() == raw.strip(), (
            "prose with a brace placeholder should be preserved, got: "
            f"{cleaned!r}"
        )
        assert meta["tokens_stripped"] == 0
        assert meta["tokens_stripped_by_rule"].get("css_rule_block", 0) == 0


def test_single_line_real_css_is_still_stripped() -> None:
    """The declaration gate must not regress detection of genuine CSS,
    including a rule block that shares a single line with prose."""
    raw = (
        "Here is the widget styling we used. "
        ".reading-mode-toggle { font-size: 14px; color: rgb(0,0,0); } "
        "Back to the article."
    )
    cleaned, meta = strip_non_prose(raw)
    assert ".reading-mode-toggle" not in cleaned
    assert "font-size" not in cleaned
    assert meta["tokens_stripped"] > 0
    assert meta["tokens_stripped_by_rule"].get("css_rule_block", 0) > 0


def test_css_block_without_trailing_semicolon_is_stripped() -> None:
    """CSS allows the final declaration before ``}`` to omit the trailing
    semicolon. Such blocks are still valid CSS and must still be stripped.

    Regression for the review finding on the brace-placeholder fix: an
    earlier `CSS_DECL_RE` required a `;`, which would have skipped valid
    blocks like `.note { color: red }`.
    """
    cases = [
        ".note { color: red }",
        "Intro text. .note { color: red } and more prose.",
        ".box {\n  margin: 0;\n  color: blue\n}",
    ]
    for raw in cases:
        cleaned, meta = strip_non_prose(raw)
        assert "color" not in cleaned, f"CSS not stripped: {cleaned!r}"
        assert meta["tokens_stripped"] > 0
        assert meta["tokens_stripped_by_rule"].get("css_rule_block", 0) > 0


def test_colon_format_placeholders_in_prose_are_not_stripped() -> None:
    """Prose carrying a colon-bearing brace placeholder must be preserved.

    Regression for the review finding that accepting a declaration at
    end-of-inner (``(?:;|$)``) re-exposed ``{key: value}`` /
    ``{date:%Y-%m-%d}`` as CSS. The selector + declaration-list gate keeps
    these: a multi-word prose prefix is not a CSS selector, and a ``%Y``
    format directive is not a CSS value.
    """
    cases = [
        "Published on {date:%Y-%m-%d} by the editorial desk.",
        "Deploy to {server: prod} after the smoke tests pass.",
        "The build ran at {time:%H:%M:%S} and then exited cleanly.",
    ]
    for raw in cases:
        cleaned, meta = strip_non_prose(raw)
        assert cleaned.strip() == raw.strip(), (
            "prose with a colon/format placeholder should be preserved, "
            f"got: {cleaned!r}"
        )
        assert meta["tokens_stripped"] == 0
        assert meta["tokens_stripped_by_rule"].get("css_rule_block", 0) == 0


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
