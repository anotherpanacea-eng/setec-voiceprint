#!/usr/bin/env python3
"""Tests for acquisition_core.extract_main_content — the trafilatura-primary
HTML extraction path with a BeautifulSoup fallback.

Two axes are exercised:

  * **trafilatura present** — the primary path isolates the article body and
    drops boilerplate (nav / footer / comments) from a fixed HTML fixture.
  * **fallback** — when trafilatura is absent OR declines (returns nothing),
    extraction transparently degrades to the existing ``html_to_text``
    BeautifulSoup path, so no piece is dropped.

The BeautifulSoup fallback needs bs4/lxml (acquisition tier); when those are
absent the whole module skips, mirroring test_acquire_blog.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import acquisition_core as ac  # type: ignore  # noqa: E402

# The fallback path needs BeautifulSoup; skip the module cleanly when the
# acquisition tier isn't installed (core CI).
_bs4_available = True
try:
    import bs4  # type: ignore  # noqa: F401
except ImportError as _e:  # pragma: no cover
    _bs4_available = False

# trafilatura is optional within the acquisition tier — its presence gates
# only the primary-path assertions, not the fallback ones.
_trafilatura_available = True
try:
    import trafilatura  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover
    _trafilatura_available = False

if pytest is not None and not _bs4_available:  # pragma: no cover
    pytestmark = pytest.mark.skip(
        reason="acquisition deps missing (bs4); install "
        "requirements-acquisition.txt"
    )


# A realistic article page: nav + sidebar + footer + comments boilerplate
# wrapped around a clearly delimited <article> body. trafilatura should
# isolate the article prose and drop the chrome; the BeautifulSoup fallback
# (driven by content_selector/strip_selectors) reaches the same body.
ARTICLE_HTML = """<!DOCTYPE html>
<html>
<head><title>The Long-Form Essay Title</title></head>
<body>
<nav class="site-nav"><a href="/">Home</a><a href="/about">About</a></nav>
<header class="masthead">Subscribe to our newsletter today!</header>
<aside class="sidebar">Related posts: widget one, widget two, widget three.</aside>
<article class="post-body">
<h1>The Long-Form Essay Title</h1>
<p>The first paragraph carries the argument the reader came for, and it runs
long enough that any main-content heuristic should recognize it as the core of
the page rather than navigational chrome.</p>
<p>The second paragraph continues that same argument across several clauses,
adding the connective tissue that distinguishes real prose from a list of
links, so extraction has an unambiguous body to lock onto.</p>
<p>A third paragraph seals it: the density of running text here dwarfs the
surrounding boilerplate, which is exactly the signal a readability extractor
uses to separate article from apparatus.</p>
</article>
<div class="comments"><p>First commenter says: nice post!</p></div>
<footer class="site-footer">Copyright 2024. All rights reserved. Privacy policy.</footer>
</body>
</html>"""


def test_extract_main_content_returns_text_and_title():
    text, title = ac.extract_main_content(
        ARTICLE_HTML,
        content_selector=".post-body",
        strip_selectors=(".comments", ".sidebar", ".site-footer"),
    )
    assert text
    assert "argument the reader came for" in text
    # Title is recovered from either path.
    assert title is not None and "Long-Form Essay Title" in title
    # Cleaned text carries no raw HTML residue.
    assert ac.html_text_is_clean(text)


def test_extract_main_content_drops_boilerplate():
    text, _ = ac.extract_main_content(
        ARTICLE_HTML,
        content_selector=".post-body",
        strip_selectors=(".comments", ".sidebar", ".site-footer",
                         ".site-nav", ".masthead"),
    )
    # The article body survives; the surrounding chrome does not.
    assert "seals it" in text
    assert "Subscribe to our newsletter" not in text
    assert "Related posts" not in text
    assert "First commenter" not in text
    assert "All rights reserved" not in text


@pytest.mark.skipif(
    not _trafilatura_available,
    reason="trafilatura not installed; primary-path assertion is N/A",
)
def test_primary_path_used_when_trafilatura_present():
    # With trafilatura present and prefer_trafilatura=True, the primary path
    # returns a non-empty body directly (no reliance on content_selector).
    text, _ = ac.extract_main_content(
        ARTICLE_HTML, prefer_trafilatura=True,
    )
    assert text and "argument the reader came for" in text


def test_fallback_when_trafilatura_disabled():
    # prefer_trafilatura=False forces the BeautifulSoup path; it must still
    # extract the body via content_selector. This is the exact degradation
    # that happens when trafilatura is absent, exercised deterministically.
    text, title = ac.extract_main_content(
        ARTICLE_HTML,
        content_selector=".post-body",
        strip_selectors=(".comments", ".sidebar", ".site-footer"),
        prefer_trafilatura=False,
    )
    assert text and "seals it" in text
    assert title is not None and "Long-Form Essay Title" in title


def test_fallback_on_trafilatura_miss():
    # A fragment with no extractable main content: trafilatura returns nothing,
    # so extraction falls through to the BeautifulSoup path rather than
    # dropping the piece. The <div> body still yields its text.
    tiny = "<html><body><div id='c'>Just a short line of real prose here.</div></body></html>"
    text, _ = ac.extract_main_content(tiny, content_selector="#c")
    assert "short line of real prose" in text


def test_trafilatura_extract_returns_none_on_empty():
    # The private sentinel contract: empty/blank input yields None so the
    # caller falls back rather than treating "" as a successful extraction.
    assert ac._trafilatura_extract("") is None
    assert ac._trafilatura_extract("   ") is None
