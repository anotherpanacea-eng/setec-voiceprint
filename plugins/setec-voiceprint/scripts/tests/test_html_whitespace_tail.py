"""Every HTML extraction path shares one whitespace normalizer.

html_to_text, the trafilatura path and the CRS historical extractor used to
spell the same three substitutions separately; if one drifted, their cleaned
text and content hashes would diverge and exact-hash dedupe would miss
duplicates across sources without any error.
"""
from __future__ import annotations

import pytest

import acquisition_core as ac  # type: ignore

pytest.importorskip("bs4")
pytest.importorskip("lxml")


def test_normalizer_collapses_runs_and_keeps_paragraphs():
    raw = "  a \t b\n   c\n\n\n\nd  "
    assert ac.normalize_extracted_whitespace(raw) == "a b\nc\n\nd"


def _marked(monkeypatch):
    monkeypatch.setattr(ac, "normalize_extracted_whitespace", lambda t: "MARK")


def test_html_to_text_uses_the_shared_normalizer(monkeypatch):
    _marked(monkeypatch)
    assert ac.html_to_text("<p>x</p>")[0] == "MARK"


def test_crs_historical_extract_uses_the_shared_normalizer(monkeypatch):
    from setec.surfaces import acquire_everycrsreport as crs  # type: ignore

    _marked(monkeypatch)
    body = crs._historical_extract("<html><body><p>x</p></body></html>")[0]
    assert body == "MARK"


def test_trafilatura_path_uses_the_shared_normalizer(monkeypatch):
    pytest.importorskip("trafilatura")
    _marked(monkeypatch)
    html = "<html><body><article><p>" + "Words here. " * 40 + "</p></article></body></html>"
    result = ac._trafilatura_extract(html)
    assert result is not None and result[0] == "MARK"
