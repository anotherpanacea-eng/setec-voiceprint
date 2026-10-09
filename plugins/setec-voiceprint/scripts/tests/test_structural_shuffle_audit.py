"""structural_shuffle_audit.split_sentences is host-independent.

It used to prefer a spaCy sentencizer whenever spaCy was importable, which
split differently from the regex that CI runs. This pins the regex behavior
even when a spaCy module is importable.
"""
from __future__ import annotations

import sys
import types

import structural_shuffle_audit as ssa  # type: ignore


def test_split_ignores_an_importable_spacy(monkeypatch):
    fake = types.ModuleType("spacy")

    def blank(_lang):  # pragma: no cover - must never be called
        raise AssertionError("split_sentences must not use spaCy")

    fake.blank = blank
    monkeypatch.setitem(sys.modules, "spacy", fake)
    text = "Dr. Smith arrived. \"Hello!\" she said. Then nothing."
    assert ssa.split_sentences(text) == [
        p.strip() for p in ssa._SENT_SPLIT_RE.split(text.strip()) if p.strip()
    ]
