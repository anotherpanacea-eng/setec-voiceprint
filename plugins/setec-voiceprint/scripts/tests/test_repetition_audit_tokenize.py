from __future__ import annotations

from pathlib import Path

from setec.surfaces.repetition_audit import load_anchors, tokenize


def test_anchors_with_accents_and_curly_apostrophes_match_tokens(tmp_path: Path) -> None:
    anchors = tmp_path / "anchors.txt"
    anchors.write_text("José, O'Brien\ncafé\n", encoding="utf-8")
    tokens = tokenize("José met O’Brien at the café.")
    assert {"josé", "o'brien", "café"} <= set(tokens)
    assert load_anchors(str(anchors)) <= set(tokens)


def test_tokenize_folds_forms_and_keeps_ascii_behavior() -> None:
    assert tokenize("don’t don't") == ["don't", "don't"]
    assert tokenize("café") == ["café"]
    assert tokenize("well-known 42 x_y") == ["well", "known", "x", "y"]
