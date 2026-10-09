from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from setec.surfaces.repetition_audit import load_anchors, tokenize


def test_anchors_with_accents_and_curly_apostrophes_match_tokens(tmp_path: Path) -> None:
    anchors = tmp_path / "anchors.txt"
    anchors.write_text("José, O'Brien\ncafe\u0301\n", encoding="utf-8")
    tokens = tokenize("José met O’Brien at the café.")
    assert {"josé", "o'brien", "café"} <= set(tokens)
    assert load_anchors(str(anchors)) <= set(tokens)


def test_tokenize_folds_forms_and_keeps_ascii_behavior() -> None:
    assert tokenize("don’t don't") == ["don't", "don't"]
    assert tokenize("cafe\u0301") == ["café"]
    assert tokenize("well-known 42 x_y") == ["well", "known", "x", "y"]


def test_curly_quotes_and_possessives_do_not_defeat_anchors() -> None:
    from setec.surfaces.repetition_audit import score_against_baseline_counts

    assert tokenize("\u2018Go to the garden\u2019, he said. \u2019\u2019") == [
        "go", "to", "the", "garden", "he", "said",
    ]
    text = " ".join(["Mark\u2019s garden grew."] * 6)
    words = {c["word"] for c in score_against_baseline_counts(
        text, Counter(), 1000, function_words=set(),
        anchor_words={"mark"}, min_count=1, min_ratio=0,
    )[0]}
    assert "mark's" not in words and "mark" not in words


@pytest.mark.parametrize("separator", ["\u00b2", "\u00bd", "\u2167", "\u0662", "2"])
def test_numbers_separate_words_without_defeating_anchors(separator: str) -> None:
    from setec.surfaces.repetition_audit import score_against_baseline_counts

    text = " ".join([f"garden{separator}garden"] * 3)
    assert tokenize(text) == ["garden"] * 6
    candidates, total = score_against_baseline_counts(
        text, Counter(), 1000, function_words=set(), anchor_words=set(),
        min_count=1, min_ratio=0,
    )
    assert total == 6
    assert [(row["word"], row["count"]) for row in candidates] == [("garden", 6)]
    anchored, anchored_total = score_against_baseline_counts(
        text, Counter(), 1000, function_words=set(), anchor_words={"garden"},
        min_count=1, min_ratio=0,
    )
    assert anchored_total == 6
    assert anchored == []

