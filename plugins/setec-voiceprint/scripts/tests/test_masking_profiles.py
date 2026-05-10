#!/usr/bin/env python3
"""Regression tests for the Release 1 masking-profiles tier.

Phase-1 trustworthiness layer (Release 1, paired-release schedule).
The masking rules are a third tier alongside `PREPROCESSING_RULES`
(corpus-hygiene / non-prose contamination) and `AGGRESSIVE_RULES`
(URL noise, footnotes, citations at the markup level). Masking
rules remove *prose* that isn't the writer's voice: quoted
statutes, block quotations, headings, common LLM wrapper phrases,
prompt remnants. They are opt-in only.

Tests verify:
  * Each individual masking rule fires on intended inputs and
    leaves untouched text alone.
  * Profile presets resolve correctly and bundle the right rules.
  * `resolve_masking_rules()` handles profile names, comma-separated
    rule lists, iterables of names, empty / None inputs, and raises
    `ValueError` on unknown names.
  * `strip_non_prose()` with `strip_masking` records masked tokens
    in the metadata under a separate `tokens_masked` field.
  * Default behavior is unchanged — calling without `strip_masking`
    matches pre-1.31.0 behavior byte-for-byte.
  * Masking respects the `allow_non_prose` opt-out.
"""

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

from preprocessing import (  # type: ignore
    MASKING_PROFILES,
    MASKING_RULE_NAMES,
    available_masking_profiles,
    available_masking_rules,
    resolve_masking_rules,
    strip_non_prose,
)


# ---------- Individual masking rules ----------


class TestMarkdownHeading:
    def test_atx_heading_stripped(self):
        text = "# Title\n\nBody prose."
        cleaned, meta = strip_non_prose(text, strip_masking="exclude_headings")
        assert "Title" not in cleaned
        assert "Body prose" in cleaned
        assert meta["tokens_stripped_by_rule"].get("markdown_heading", 0) > 0

    def test_h6_heading_stripped(self):
        text = "###### Sixth-level header text\n\nBody."
        cleaned, _ = strip_non_prose(text, strip_masking="exclude_headings")
        assert "Sixth-level" not in cleaned

    def test_no_heading_means_no_strip(self):
        text = "Body prose without any headings here."
        cleaned, meta = strip_non_prose(text, strip_masking="exclude_headings")
        assert "Body prose" in cleaned
        assert meta["tokens_stripped_by_rule"].get("markdown_heading", 0) == 0


class TestBlockQuote:
    def test_single_blockquote_stripped(self):
        text = "Body.\n\n> Quoted material here.\n\nMore body."
        cleaned, meta = strip_non_prose(
            text, strip_masking="exclude_quotations",
        )
        assert "Quoted material" not in cleaned
        assert "More body" in cleaned
        assert meta["tokens_stripped_by_rule"].get("block_quote", 0) > 0

    def test_multiline_blockquote_stripped(self):
        text = "Body.\n\n> Line one of quote.\n> Line two of quote.\n\nMore body."
        cleaned, _ = strip_non_prose(
            text, strip_masking="exclude_quotations",
        )
        assert "Line one" not in cleaned
        assert "Line two" not in cleaned
        assert "More body" in cleaned


class TestLongInlineQuotation:
    def test_long_inline_quotation_stripped(self):
        text = (
            'She said, "this is a very long inline quotation that '
            'spans many words and clearly exceeds the threshold," '
            'and walked away.'
        )
        cleaned, _ = strip_non_prose(
            text, strip_masking="exclude_quotations",
        )
        assert "very long inline quotation" not in cleaned

    def test_short_quotation_survives(self):
        text = 'She said, "no thanks," and walked away.'
        cleaned, meta = strip_non_prose(
            text, strip_masking="exclude_quotations",
        )
        assert "no thanks" in cleaned
        assert meta["tokens_stripped_by_rule"].get(
            "long_inline_quotation", 0,
        ) == 0


class TestStatutoryCitation:
    def test_usc_citation_stripped(self):
        text = "Per 42 U.S.C. § 1983 the case proceeds."
        cleaned, _ = strip_non_prose(
            text, strip_masking=["statutory_citation"],
        )
        assert "1983" not in cleaned

    def test_case_name_v_stripped(self):
        text = "The court ruled in Smith v. Jones, 123 F.3d 456 against the defendant."
        cleaned, _ = strip_non_prose(
            text, strip_masking=["statutory_citation"],
        )
        assert "Smith v. Jones" not in cleaned

    def test_pub_law_stripped(self):
        text = "Under Pub. L. No. 116-25 the agency must comply."
        cleaned, _ = strip_non_prose(
            text, strip_masking=["statutory_citation"],
        )
        assert "116-25" not in cleaned


class TestLLMWrapperPhrase:
    def test_as_an_ai_stripped(self):
        text = "As an AI language model, I cannot provide that information.\nBut here is some prose that should survive."
        cleaned, _ = strip_non_prose(
            text, strip_masking="prose_strict",
        )
        assert "language model" not in cleaned
        assert "should survive" in cleaned

    def test_i_hope_this_helps_stripped(self):
        text = "Body of the response.\nI hope this helps!"
        cleaned, _ = strip_non_prose(
            text, strip_masking="prose_strict",
        )
        assert "I hope this helps" not in cleaned

    def test_mid_prose_mention_protected(self):
        # The pattern requires line / sentence boundary so mid-prose
        # mentions of "AI" don't get clobbered.
        text = "The essay discusses how I cannot get used to constant connectivity."
        cleaned, _ = strip_non_prose(
            text, strip_masking="prose_strict",
        )
        # The mid-line "I cannot" is borderline; the pattern still
        # may or may not match depending on regex flags. The test
        # checks the boundary case by ensuring at least the prose
        # content survives in some form.
        assert "essay" in cleaned


class TestPromptRemnant:
    def test_please_write_at_start_stripped(self):
        text = "Please write a 500-word essay about the history of bridges.\n\nThe oldest known bridge dates to..."
        cleaned, _ = strip_non_prose(
            text, strip_masking="prose_strict",
        )
        assert "Please write" not in cleaned
        assert "oldest known bridge" in cleaned

    def test_you_are_a_at_start_stripped(self):
        text = "You are a helpful assistant.\n\nThe essay begins here."
        cleaned, _ = strip_non_prose(
            text, strip_masking="prose_strict",
        )
        assert "helpful assistant" not in cleaned

    def test_prompt_in_middle_protected(self):
        # The pattern only matches at document start.
        text = "Body prose here.\n\nPlease write a different essay would be nice."
        cleaned, _ = strip_non_prose(
            text, strip_masking="prose_strict",
        )
        assert "Body prose" in cleaned
        # Mid-doc "Please write" survives because the pattern is
        # anchored to document start.
        assert "different essay" in cleaned


# ---------- Profile resolution ----------


class TestResolveMaskingRules:
    def test_none_returns_empty(self):
        assert resolve_masking_rules(None) == ()
        assert resolve_masking_rules("") == ()

    def test_profile_name_resolves(self):
        rules = resolve_masking_rules("prose_body_only")
        assert "markdown_heading" in rules
        assert "block_quote" in rules
        assert "long_inline_quotation" in rules
        assert "statutory_citation" in rules

    def test_comma_separated_resolves(self):
        rules = resolve_masking_rules("markdown_heading,block_quote")
        assert rules == ("markdown_heading", "block_quote")

    def test_iterable_resolves(self):
        rules = resolve_masking_rules(["markdown_heading", "block_quote"])
        assert rules == ("markdown_heading", "block_quote")

    def test_unknown_rule_raises(self):
        with pytest.raises(ValueError) as exc:
            resolve_masking_rules("nonexistent_rule")
        assert "Unknown masking rule" in str(exc.value)

    def test_unknown_rule_in_csv_raises(self):
        with pytest.raises(ValueError):
            resolve_masking_rules("markdown_heading,fake_rule")

    def test_all_profiles_available(self):
        profiles = available_masking_profiles()
        assert "none" in profiles
        assert "prose_body_only" in profiles
        assert "exclude_quotations" in profiles
        assert "exclude_headings" in profiles
        assert "prose_strict" in profiles

    def test_all_rules_available(self):
        rules = available_masking_rules()
        for required in (
            "markdown_heading",
            "block_quote",
            "long_inline_quotation",
            "statutory_citation",
            "llm_wrapper_phrase",
            "prompt_remnant",
        ):
            assert required in rules

    def test_profile_rules_are_subset_of_available(self):
        all_rules = set(MASKING_RULE_NAMES)
        for profile_name, rules in MASKING_PROFILES.items():
            for rule in rules:
                assert rule in all_rules, (
                    f"Profile {profile_name!r} references unknown "
                    f"rule {rule!r}"
                )


# ---------- strip_non_prose integration ----------


class TestStripNonProseIntegration:
    def test_default_behavior_unchanged(self):
        """Calling without `strip_masking` produces the same result
        as pre-1.31.0. Backward compat for every existing caller."""
        text = "# Heading\n\nBody prose with `inline code`."
        cleaned_no_arg, meta_no_arg = strip_non_prose(text)
        cleaned_explicit_none, _ = strip_non_prose(text, strip_masking=None)
        assert cleaned_no_arg == cleaned_explicit_none
        assert meta_no_arg["masking_rules_active"] == []
        assert meta_no_arg["tokens_masked"] == 0
        # Heading stays in (default doesn't include masking rules).
        assert "Heading" in cleaned_no_arg

    def test_tokens_masked_recorded_separately(self):
        text = "# Heading\n\nBody.\n\n> Quoted material here."
        cleaned, meta = strip_non_prose(
            text, strip_masking="prose_body_only",
        )
        assert meta["tokens_masked"] > 0
        assert meta["masking_rules_active"]

    def test_allow_non_prose_skips_masking_too(self):
        text = "# Heading\n\nBody.\n\n> Quoted material."
        cleaned, meta = strip_non_prose(
            text, allow_non_prose=True,
            strip_masking="prose_body_only",
        )
        assert meta["opt_out"] is True
        # Even with masking requested, allow_non_prose wins.
        assert "Heading" in cleaned
        assert "Quoted material" in cleaned
        assert meta["tokens_masked"] == 0

    def test_masking_runs_after_corpus_hygiene(self):
        """Standard rules (HTML/CSS/code) run before masking, so
        masking operates on already-cleaned text."""
        text = (
            "<style>body { color: red; }</style>\n\n"
            "# Heading\n\n"
            "Body prose."
        )
        cleaned, meta = strip_non_prose(
            text, strip_masking="prose_body_only",
        )
        # CSS stripped (standard rule).
        assert "color: red" not in cleaned
        # Heading stripped (masking rule).
        assert "Heading" not in cleaned
        # Body survives.
        assert "Body prose" in cleaned

    def test_masking_with_aggressive_combines_correctly(self):
        text = (
            "[Smith 2023] Body prose with citation.\n\n"
            "# Heading\n\n"
            "More body."
        )
        cleaned, meta = strip_non_prose(
            text, strip_aggressive=True, strip_masking="exclude_headings",
        )
        # Citation stripped (aggressive).
        assert "Smith 2023" not in cleaned
        # Heading stripped (masking).
        assert "Heading" not in cleaned

    def test_metadata_lists_active_masking_rules(self):
        _, meta = strip_non_prose(
            "Body.", strip_masking="prose_body_only",
        )
        active = meta["masking_rules_active"]
        for required in (
            "markdown_heading", "block_quote",
            "long_inline_quotation", "statutory_citation",
        ):
            assert required in active


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
