"""The HTML extraction paths share one whitespace tail.

html_to_text, the trafilatura path and the CRS historical extractor used to
spell the same three substitutions separately; if one drifted, their cleaned
text and content hashes would diverge and exact-hash dedupe would miss
duplicates across sources without any error. These pin each path's output.
"""
from __future__ import annotations

import pytest

import acquisition_core as ac  # type: ignore

# Tabs and space runs inside a line, indentation after a newline, and a run of
# blank lines between paragraphs: one case for each substitution plus strip.
HTML = "<html><body><p>a \t  b</p>\n\n\n\n<p>   c</p></body></html>"


def test_normalizer_collapses_runs_and_keeps_paragraphs():
    assert ac.normalize_extracted_whitespace("  a \t b\n   c\n\n\n\nd  ") == "a b\nc\n\nd"


def test_html_to_text_output():
    pytest.importorskip("bs4")
    assert ac.html_to_text(HTML)[0] == "a b\n\nc"


def test_crs_historical_extract_output():
    pytest.importorskip("bs4")
    from setec.surfaces import acquire_everycrsreport as crs  # type: ignore

    assert crs._historical_extract(HTML)[0] == "a b\n\nc"


def test_trafilatura_path_output():
    pytest.importorskip("trafilatura")
    html = ("<html><body><article>"
            + "".join(f"<p>Sentence {i} here \t  with   space.</p>\n\n\n" for i in range(30))
            + "</article></body></html>")
    result = ac._trafilatura_extract(html)
    assert result is not None
    assert result[0].startswith("Sentence 0 here with space.\nSentence 1 here with space.")
    assert "\t" not in result[0] and "  " not in result[0]
