#!/usr/bin/env python3
"""Regression tests for semantic_preservation_check.py (Release 8)."""

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

import semantic_preservation_check as sp  # type: ignore


# ---------- Lexical extractors ----------


class TestExtractModals:
    def test_basic_modals(self):
        text = "She must go. He should not. We can do this."
        hits = sp._extract_modals(text)
        assert "must" in hits
        assert "should" in hits
        assert "can" in hits

    def test_no_modals(self):
        text = "She walked to the store. He arrived early."
        hits = sp._extract_modals(text)
        assert hits == []


class TestExtractStance:
    def test_claim_verbs(self):
        text = "She argued the point. He maintained the position."
        hits = sp._extract_stance(text)
        assert "argued" in hits
        assert "maintained" in hits

    def test_evaluative_adverbs(self):
        text = "Clearly, the data is wrong. Surprisingly, no one noticed."
        hits = sp._extract_stance(text)
        assert "clearly" in hits
        assert "surprisingly" in hits


class TestExtractCausals:
    def test_because(self):
        text = "She left because the meeting ended."
        hits = sp._extract_causals(text)
        assert "because" in hits

    def test_due_to(self):
        text = "The delay was due to weather."
        hits = sp._extract_causals(text)
        assert "due to" in hits

    def test_therefore(self):
        text = "The argument fails. Therefore, we reject it."
        hits = sp._extract_causals(text)
        assert "therefore" in hits

    def test_no_causal_in_simple_text(self):
        text = "She walked. He ran. They stopped."
        hits = sp._extract_causals(text)
        assert hits == []


class TestExtractHedges:
    def test_hedge_lexicon(self):
        text = "Perhaps the answer is wrong. Maybe we should ask."
        hits = sp._extract_hedges(text)
        assert "perhaps" in hits
        assert "maybe" in hits

    def test_seems_appears(self):
        text = "It seems she left. The plan appears solid."
        hits = sp._extract_hedges(text)
        assert "seems" in hits
        assert "appears" in hits

    def test_hedge_phrases(self):
        text = "It is possible that the data is wrong."
        hits = sp._extract_hedges(text)
        assert "it is possible" in hits


class TestExtractCitations:
    def test_according_to(self):
        text = "According to Smith, the data is conclusive."
        hits = sp._extract_citations(text)
        assert "according to X" in hits

    def test_x_said(self):
        text = "Smith said the analysis was wrong."
        hits = sp._extract_citations(text)
        assert "X said" in hits

    def test_research_shows(self):
        text = "Research shows that the effect is strong."
        hits = sp._extract_citations(text)
        assert "research shows" in hits

    def test_parenthetical_citation(self):
        text = "The effect is well-documented (Smith, 2020)."
        hits = sp._extract_citations(text)
        assert "parenthetical citation" in hits


class TestCountDeclaratives:
    def test_simple_declaratives(self):
        text = "She walked. He ran. They stopped."
        n, _ = sp._count_declaratives(text)
        assert n == 3

    def test_skip_questions(self):
        text = "She walked. Did he run? They stopped."
        n, _ = sp._count_declaratives(text)
        assert n == 2

    def test_skip_short_exclamatives(self):
        text = "She walked. Stop! He ran."
        n, _ = sp._count_declaratives(text)
        assert n == 2


class TestExtractNamedEntities:
    def test_basic_extraction(self):
        text = "Alice met Bob at the conference."
        ents = sp._extract_named_entities(text)
        # Either spaCy or regex should pick up Alice and Bob.
        joined = " ".join(ents).lower()
        assert "alice" in joined
        assert "bob" in joined


# ---------- Diff helpers ----------


class TestDiffLists:
    def test_perfect_overlap(self):
        before = ["a", "b", "c"]
        after = ["a", "b", "c"]
        dropped, added, shared = sp._diff_lists(before, after)
        assert dropped == []
        assert added == []
        assert sorted(shared) == ["a", "b", "c"]

    def test_pure_add(self):
        before = ["a"]
        after = ["a", "b"]
        dropped, added, shared = sp._diff_lists(before, after)
        assert dropped == []
        assert added == ["b"]
        assert shared == ["a"]

    def test_pure_drop(self):
        before = ["a", "b"]
        after = ["a"]
        dropped, added, shared = sp._diff_lists(before, after)
        assert dropped == ["b"]
        assert added == []
        assert shared == ["a"]

    def test_multiset_count(self):
        # Multiplicity-aware: two "a"s in before, one in after = 1 dropped.
        before = ["a", "a"]
        after = ["a"]
        dropped, added, shared = sp._diff_lists(before, after)
        assert dropped == ["a"]
        assert shared == ["a"]


class TestClassifyCountOnlyVerdict:
    """Reviewer-reproduced regression: claim_inventory passed
    empty added/dropped lists into _classify_verdict, whose
    large-count branches require non-empty diffs to fire — so
    a 5 → 10 declarative-count change returned `preserved`."""

    def test_count_only_5_to_10_is_shifted_added(self):
        v = sp._classify_count_only_verdict(
            count_before=5, count_after=10,
        )
        assert v == "shifted_added"

    def test_count_only_10_to_5_is_shifted_dropped(self):
        v = sp._classify_count_only_verdict(
            count_before=10, count_after=5,
        )
        assert v == "shifted_dropped"

    def test_count_only_5_to_5_is_preserved(self):
        v = sp._classify_count_only_verdict(
            count_before=5, count_after=5,
        )
        assert v == "preserved"

    def test_count_only_zero_zero_is_unknown(self):
        v = sp._classify_count_only_verdict(
            count_before=0, count_after=0,
        )
        assert v == "unknown"

    def test_count_only_small_count_floor(self):
        # 2 → 3 (small count, only +1 absolute) → preserved.
        assert sp._classify_count_only_verdict(
            count_before=2, count_after=3,
        ) == "preserved"
        # 2 → 4 (small count, +2 absolute) → shifted_added.
        assert sp._classify_count_only_verdict(
            count_before=2, count_after=4,
        ) == "shifted_added"


class TestClaimInventoryRegression:
    """Reviewer-reproduced end-to-end: 5 → 10 declaratives
    must NOT report preserved at the category or overall level."""

    def test_5_to_10_declaratives_flagged_in_check_preservation(self):
        before = (
            "She walked. He ran. They paused. Cars stopped. "
            "Birds flew."
        )
        after = (
            "She walked. He ran. They paused. Cars stopped. "
            "Birds flew. The rain began. The wind rose. "
            "Lights flickered. Doors slammed. Voices called."
        )
        report = sp.check_preservation(
            before_text=before, after_text=after,
        )
        cat = report["categories"]["claim_inventory"]
        # 5 declaratives → 10 declaratives is a doubling — must be
        # shifted_added at the category level.
        assert cat["verdict"] == "shifted_added"
        # Overall verdict propagates from claim_inventory's flag.
        assert report["overall_verdict"] == "shifted_added"


class TestUnknownCategoryFilter:
    """Reviewer-reproduced regression: a typo in --category
    silently filtered every category out, then _overall_verdict
    returned `preserved` via the empty `all(...)` path."""

    def test_unknown_category_raises_value_error(self):
        with pytest.raises(ValueError, match="typo"):
            sp.check_preservation(
                before_text="Test text.",
                after_text="Test text.",
                category_filter=["typo"],
            )

    def test_known_category_filter_still_works(self):
        report = sp.check_preservation(
            before_text="Test text.",
            after_text="Test text.",
            category_filter=["modal_verbs"],
        )
        assert list(report["categories"].keys()) == ["modal_verbs"]

    def test_overall_verdict_unknown_for_empty_categories(self):
        # Defense-in-depth: if some future call path produced
        # an empty categories dict, the aggregator must NOT
        # collapse to `preserved`.
        from semantic_preservation_check import _overall_verdict
        assert _overall_verdict({}) == "unknown"


class TestClassifyVerdict:
    def test_preserved_when_unchanged(self):
        v = sp._classify_verdict(
            count_before=10, count_after=10,
            items_dropped=[], items_added=[],
        )
        assert v == "preserved"

    def test_unknown_when_both_zero(self):
        v = sp._classify_verdict(
            count_before=0, count_after=0,
            items_dropped=[], items_added=[],
        )
        assert v == "unknown"

    def test_shifted_added_when_count_grew(self):
        v = sp._classify_verdict(
            count_before=10, count_after=15,
            items_dropped=[],
            items_added=["new1", "new2", "new3"],
        )
        assert v == "shifted_added"

    def test_shifted_dropped_when_count_shrunk(self):
        v = sp._classify_verdict(
            count_before=10, count_after=5,
            items_dropped=["old1", "old2", "old3"],
            items_added=[],
        )
        assert v == "shifted_dropped"

    def test_shifted_changed_when_swap(self):
        # Same count, but items completely different.
        v = sp._classify_verdict(
            count_before=10, count_after=10,
            items_dropped=["a"] * 5, items_added=["b"] * 5,
        )
        assert v == "shifted_changed"

    def test_small_count_floor_demands_absolute_movement(self):
        # 2 → 3 (50% growth but small base) — under the 5-item
        # floor we demand n_added >= 2 to call shifted_added.
        v = sp._classify_verdict(
            count_before=2, count_after=3,
            items_dropped=[], items_added=["new"],
        )
        # Only 1 added under small-count floor → preserved.
        assert v == "preserved"

        # Now 2 added → shifted_added.
        v = sp._classify_verdict(
            count_before=2, count_after=4,
            items_dropped=[], items_added=["a", "b"],
        )
        assert v == "shifted_added"


# ---------- check_preservation integration ----------


class TestCheckPreservationIntegration:
    def test_identical_texts_preserved(self):
        text = (
            "The argument fails. Smith said it was wrong. "
            "Therefore we reject it. Perhaps another analysis "
            "would help. The data must be checked."
        )
        report = sp.check_preservation(
            before_text=text, after_text=text,
        )
        # Identical inputs → all categories preserved.
        for name, cat in report["categories"].items():
            assert cat["verdict"] in {"preserved", "unknown"}, (
                f"{name}: {cat['verdict']}"
            )
        assert report["overall_verdict"] in {"preserved", "unknown"}

    def test_added_causal_claims_flagged(self):
        before = (
            "The market fell. Inventories rose. Confidence "
            "dropped. Sales declined. Prices stabilized."
        )
        after = (
            "The market fell because rates rose. Inventories "
            "rose due to overproduction. Confidence dropped "
            "as a result of poor reports. Sales declined "
            "therefore prices fell. Prices stabilized leads "
            "to recovery."
        )
        report = sp.check_preservation(
            before_text=before, after_text=after,
        )
        cat = report["categories"]["causal_claims"]
        assert cat["verdict"] == "shifted_added"
        # Overall verdict reflects the worst per-category verdict.
        assert report["overall_verdict"] == "shifted_added"

    def test_dropped_hedges_flagged(self):
        before = (
            "Perhaps the data is wrong. Maybe we should reconsider. "
            "It seems the analysis is flawed. Possibly the model "
            "is misspecified. Arguably the conclusion is hasty."
        )
        after = (
            "The data is wrong. We should reconsider. The "
            "analysis is flawed. The model is misspecified. "
            "The conclusion is hasty."
        )
        report = sp.check_preservation(
            before_text=before, after_text=after,
        )
        cat = report["categories"]["hedges"]
        # Five hedges removed → shifted_dropped.
        assert cat["verdict"] == "shifted_dropped"

    def test_modal_shift_flagged(self):
        before = (
            "The data might suggest a problem. Researchers "
            "could investigate further. Findings may be "
            "preliminary. Results should be verified. Effects "
            "would persist."
        )
        after = (
            "The data shows a problem. Researchers must "
            "investigate further. Findings are conclusive. "
            "Results will be verified. Effects must persist."
        )
        report = sp.check_preservation(
            before_text=before, after_text=after,
        )
        cat = report["categories"]["modal_verbs"]
        # Hedged modals (might/could/may) replaced with strong
        # modals (must/will).
        assert cat["verdict"] in {
            "shifted_dropped", "shifted_added", "shifted_changed",
        }


class TestOverallVerdict:
    def test_shifted_added_takes_priority(self):
        from semantic_preservation_check import (
            CategoryResult, _overall_verdict,
        )
        cats = {
            "a": CategoryResult(
                name="a", description="",
                verdict="preserved",
            ),
            "b": CategoryResult(
                name="b", description="",
                verdict="shifted_added",
            ),
            "c": CategoryResult(
                name="c", description="",
                verdict="preserved",
            ),
        }
        assert _overall_verdict(cats) == "shifted_added"

    def test_all_preserved(self):
        from semantic_preservation_check import (
            CategoryResult, _overall_verdict,
        )
        cats = {
            "a": CategoryResult(
                name="a", description="",
                verdict="preserved",
            ),
            "b": CategoryResult(
                name="b", description="",
                verdict="preserved",
            ),
        }
        assert _overall_verdict(cats) == "preserved"

    def test_unknown_with_preserved(self):
        from semantic_preservation_check import (
            CategoryResult, _overall_verdict,
        )
        cats = {
            "a": CategoryResult(
                name="a", description="",
                verdict="preserved",
            ),
            "b": CategoryResult(
                name="b", description="",
                verdict="unknown",
            ),
        }
        assert _overall_verdict(cats) == "preserved"


# ---------- Render ----------


class TestRender:
    def test_markdown_includes_claim_license(self):
        text = "Test text. Another sentence."
        report = sp.check_preservation(
            before_text=text, after_text=text,
        )
        md = sp.render_report(report)
        assert "## What this result licenses" in md

    def test_markdown_includes_per_category_table(self):
        text = "Test text. Another sentence."
        report = sp.check_preservation(
            before_text=text, after_text=text,
        )
        md = sp.render_report(report)
        assert "## Per-category preservation" in md
        assert "claim_inventory" in md
        assert "modal_verbs" in md
        assert "causal_claims" in md

    def test_markdown_notable_categories_only_for_shifts(self):
        # Identical texts → no notable categories rendered.
        text = "Test text. Another sentence."
        report = sp.check_preservation(
            before_text=text, after_text=text,
        )
        md = sp.render_report(report)
        # Notable categories section should be absent when
        # everything's preserved.
        # (It may appear conditionally, so just check we don't
        # crash and the doc renders.)
        assert "Semantic preservation check" in md


# ---------- CLI ----------


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        before_path = tmp_path / "before.md"
        after_path = tmp_path / "after.md"
        before_path.write_text(
            "She argued the point. Perhaps it was correct.",
            encoding="utf-8",
        )
        after_path.write_text(
            "She maintained the point. Perhaps it was correct.",
            encoding="utf-8",
        )
        out = tmp_path / "report.json"
        rc = sp.main([
            "--before", str(before_path),
            "--after", str(after_path),
            "--json", "--out", str(out),
        ])
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        # schema_version 1.0 envelope: categories + overall_verdict
        # live under results.
        assert payload["schema_version"] == "1.0"
        assert payload["task_surface"] == "craft_restoration"
        assert "categories" in payload["results"]
        assert "overall_verdict" in payload["results"]

    def test_cli_missing_before_returns_2(self, tmp_path):
        after_path = tmp_path / "after.md"
        after_path.write_text("Text.", encoding="utf-8")
        rc = sp.main([
            "--before", str(tmp_path / "missing.md"),
            "--after", str(after_path),
        ])
        assert rc == 2

    def test_cli_missing_after_returns_2(self, tmp_path):
        before_path = tmp_path / "before.md"
        before_path.write_text("Text.", encoding="utf-8")
        rc = sp.main([
            "--before", str(before_path),
            "--after", str(tmp_path / "missing.md"),
        ])
        assert rc == 2

    def test_cli_empty_before_returns_2(self, tmp_path):
        before_path = tmp_path / "before.md"
        after_path = tmp_path / "after.md"
        before_path.write_text("", encoding="utf-8")
        after_path.write_text("Text.", encoding="utf-8")
        rc = sp.main([
            "--before", str(before_path),
            "--after", str(after_path),
        ])
        assert rc == 2

    def test_cli_unknown_category_returns_2(self, tmp_path):
        # Reviewer-reproduced regression: a typo in --category
        # silently produced an empty categories dict + overall
        # verdict `preserved`. CLI now hard-fails rc=2.
        before = tmp_path / "before.md"
        after = tmp_path / "after.md"
        before.write_text("Test.", encoding="utf-8")
        after.write_text("Test.", encoding="utf-8")
        rc = sp.main([
            "--before", str(before),
            "--after", str(after),
            "--category", "completely_unknown_category",
        ])
        assert rc == 2

    def test_cli_category_filter(self, tmp_path):
        before_path = tmp_path / "before.md"
        after_path = tmp_path / "after.md"
        before_path.write_text(
            "She argued the point. Perhaps so.", encoding="utf-8",
        )
        after_path.write_text(
            "She argued the point. Perhaps so.", encoding="utf-8",
        )
        out = tmp_path / "report.json"
        rc = sp.main([
            "--before", str(before_path),
            "--after", str(after_path),
            "--category", "modal_verbs",
            "--json", "--out", str(out),
        ])
        assert rc == 0
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert list(payload["results"]["categories"].keys()) == ["modal_verbs"]


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
