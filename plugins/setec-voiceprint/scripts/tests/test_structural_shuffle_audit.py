"""structural_shuffle_audit.split_sentences is host-independent.

It used to prefer a spaCy sentencizer whenever spaCy was importable, which
split differently from the regex that CI runs. A working fake spaCy that
returns the whole text as one sentence would have changed the old result.
"""
from __future__ import annotations

import sys
import types

import structural_shuffle_audit as ssa  # type: ignore


class _OneSentenceNLP:
    def add_pipe(self, _name):
        pass

    def __call__(self, text):
        return types.SimpleNamespace(sents=[types.SimpleNamespace(text=text)])


def test_split_ignores_an_importable_spacy(monkeypatch):
    fake = types.ModuleType("spacy")
    fake.blank = lambda _lang: _OneSentenceNLP()
    monkeypatch.setitem(sys.modules, "spacy", fake)
    assert ssa.split_sentences("One here. Two here! Three?") == [
        "One here.", "Two here!", "Three?",
    ]
