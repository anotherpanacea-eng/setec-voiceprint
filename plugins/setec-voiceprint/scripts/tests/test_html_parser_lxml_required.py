"""HTML acquisition uses lxml only.

The html.parser fallback was removed because it produced different text on
malformed markup, so a re-acquisition on a host without lxml changed content
hashes and dedupe missed it silently. These tests pin the lxml reading and
check that a missing lxml parser fails loudly instead of degrading.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import acquisition_core as ac  # type: ignore  # noqa: E402

bs4 = pytest.importorskip("bs4")
pytest.importorskip("lxml")


def _text(result):
    return result[0] if isinstance(result, tuple) else result


def test_malformed_markup_reads_the_lxml_way():
    # html.parser gave "a\nb" here; lxml drops the stray close tag.
    assert _text(ac.html_to_text("<p>a</div>b</p>")) == "ab"


def test_missing_lxml_fails_loudly(monkeypatch):
    real_lookup = bs4.builder.builder_registry.lookup

    def no_lxml(*features):
        if "lxml" in features:
            return None
        return real_lookup(*features)

    monkeypatch.setattr(bs4.builder.builder_registry, "lookup", no_lxml)
    with pytest.raises(bs4.FeatureNotFound):
        ac.html_to_text("<p>text</p>")
    with pytest.raises(bs4.FeatureNotFound):
        ac._prestrip_html("<p>text</p><nav>x</nav>", ["nav"])
