#!/usr/bin/env python3
"""Regression tests for acquire_blog.py + acquisition_core.py.

Strategy: mock the network with `acquisition_core.FixtureFetcher`,
which maps URLs to local fixture files. The fixture set under
``scripts/test_data/acquisition_blog_fixture/`` covers the four
acquisition paths the spec requires:

  * Substack feed with one full-text post and one paid/excerpt-only
    post that must be skipped, plus a sitemap pointing at one extra
    post that has to be fetched directly via HTML extraction.
  * WordPress / Ghost feed with one full-text post.
  * Generic HTML archive page with two post links and full post HTML.

Test invariants exercise the spec's stated assertions:

  * ``.txt`` files and ``.meta.json`` sidecars are produced 1:1.
  * Draft manifest entries carry the impostor schema fields
    (``corpus_role``, ``use``, ``register``, ``era``, ``consent_status``,
    ``content_hash``, ``acquired_via``).
  * Cleaned text contains no raw HTML residue.
  * Preprocessing metadata is present for each acquired text.
  * Duplicate content hashes are skipped.
  * Paid Substack posts are flagged and never written.
  * Source-type auto-detection picks the right path.
  * The privacy guard refuses non-private output paths unless
    ``--allow-public-output`` is set.
  * The persona-slug rule is deterministic and Unicode-safe.

Tests pin the validator's contract by running each emitted manifest
through ``manifest_validator.validate_manifest`` and asserting zero
errors and zero impostor-related warnings.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

# These imports require requirements-acquisition.txt to be installed
# (requests, feedparser, bs4, lxml, dateutil). When absent, the test
# module skips cleanly so ordinary `pytest` runs in environments
# without acquisition deps don't fail.
_acq_deps_available = True
_skip_reason = ""
try:
    import feedparser  # type: ignore  # noqa: F401
    import bs4  # type: ignore  # noqa: F401
except ImportError as _e:
    _acq_deps_available = False
    _skip_reason = (
        f"acquisition deps missing ({_e}); install with "
        "`pip install -r requirements-acquisition.txt`"
    )

if _acq_deps_available:
    import acquisition_core as ac  # type: ignore
    import acquire_blog as ab  # type: ignore
    import manifest_validator as mv  # type: ignore

if pytest is not None and not _acq_deps_available:
    pytestmark = pytest.mark.skip(reason=_skip_reason)


FIXTURE_DIR = ROOT / "test_data" / "acquisition_blog_fixture"

# Substack fixture URL mapping.
SUBSTACK_URL = "https://teststack.substack.com"
SUBSTACK_URLS = {
    f"{SUBSTACK_URL}/feed": "substack_feed.xml",
    f"{SUBSTACK_URL}/sitemap.xml": "substack_sitemap.xml",
    "https://teststack.substack.com/p/older-essay-from-the-archive":
        "substack_post_archive.html",
    "https://teststack.substack.com/robots.txt": None,  # 404 → fail-open allow
}

# WordPress fixture.
WP_URL = "https://testwp.example.com"
WP_URLS = {
    f"{WP_URL}/feed/": "wordpress_feed.xml",
    f"{WP_URL}/feed": "wordpress_feed.xml",
}

# Generic HTML archive fixture.
GENERIC_URL = "https://testgeneric.example.com"
GENERIC_ARCHIVE = f"{GENERIC_URL}/?cat=Essays"
GENERIC_URLS = {
    GENERIC_ARCHIVE: "generic_archive.html",
    f"{GENERIC_URL}/2020/01/the-quiet-room/":
        "generic_post_quiet_room.html",
    f"{GENERIC_URL}/2020/03/conditions-of-attention/":
        "generic_post_attention.html",
}


# ------------------- Helpers -------------------------------------


def make_args(**overrides) -> argparse.Namespace:
    """Default `argparse.Namespace` matching `acquire_blog.build_arg_parser`."""
    base = dict(
        url=SUBSTACK_URL,
        substack=False,
        wordpress=False,
        html_archive=False,
        wayback=False,
        archive_pattern=None,
        content_selector=None,
        persona="testauthor_substack",
        author="Test Author",
        impostor_for=["blog"],
        register="blog_essay",
        register_match="high",
        topic_match="medium",
        consent_status="fair_use_research",
        era="pre_chatgpt",
        since=None,
        until=None,
        max_posts=50,
        output_dir=None,
        emit_manifest=None,
        out=None,
        rate_limit=0.0,
        user_agent=None,
        dry_run=False,
        allow_public_output=True,  # tests write into tmp dirs
        allow_non_prose=False,
        strip_rules=None,
        strip_aggressive=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def make_fetcher(url_map: dict) -> ac.FixtureFetcher:
    return ac.FixtureFetcher(
        url_map={k: v for k, v in url_map.items() if v is not None},
        fixture_dir=FIXTURE_DIR,
        rate_limit_seconds=0.0,
        respect_robots=False,
    )


def read_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


# ------------------- acquisition_core unit tests -----------------


def test_slugify_basic():
    assert ac.slugify("Hello, World!") == "hello-world"
    assert ac.slugify("On the Particular and the General") == \
        "on-the-particular-and-the-general"
    assert ac.slugify("") == "untitled"
    assert ac.slugify("---!!---") == "untitled"


def test_slugify_unicode_folded():
    # Smart quotes and accented characters fold to ASCII.
    assert ac.slugify("Café — A Place") == "cafe-a-place"
    assert ac.slugify("Naïve “quotes”") == "naive-quotes"


def test_slugify_max_length():
    long = "the " * 30
    s = ac.slugify(long, max_length=20)
    assert len(s) <= 20
    # Should still be a valid slug.
    assert s.startswith("the")


def test_persona_slug_rule():
    assert ac.author_to_persona_slug("Justin E. H. Smith") == \
        "smith_justin_e_h_personal"
    assert ac.author_to_persona_slug("Plato") == "plato_personal"
    assert ac.author_to_persona_slug("José Rivera") == \
        "rivera_jose_personal"
    assert ac.author_to_persona_slug("") == "unknown_personal"


def test_compute_content_hash_deterministic():
    h1 = ac.compute_content_hash("hello world")
    h2 = ac.compute_content_hash("hello world")
    h3 = ac.compute_content_hash("hello world!")
    assert h1 == h2
    assert h1 != h3
    assert h1.startswith("sha256:")
    assert len(h1) == len("sha256:") + 64


def test_parse_iso_date_partial():
    import datetime as dt
    assert ac.parse_iso_date("2018-03-14") == dt.date(2018, 3, 14)
    assert ac.parse_iso_date("2018-03") == dt.date(2018, 3, 1)
    assert ac.parse_iso_date("2018") == dt.date(2018, 1, 1)
    assert ac.parse_iso_date("not-a-date") is None
    assert ac.parse_iso_date(None) is None
    # python-dateutil handles human-readable formats.
    assert ac.parse_iso_date("March 14, 2018") == dt.date(2018, 3, 14)


def test_is_private_safe_path():
    assert ac.is_private_safe_path(
        Path("/tmp/ai-prose-baselines-private/x.txt")
    )
    assert ac.is_private_safe_path(
        Path("/Users/x/foo/ai-prose-baselines-private/y/z.txt")
    )
    assert not ac.is_private_safe_path(Path("/tmp/x.txt"))
    assert not ac.is_private_safe_path(Path("/tmp/baselines-private/x.txt"))


def test_html_to_text_strips_script_and_style():
    html = (
        "<html><head><style>body { color: red; }</style>"
        "<title>T</title></head>"
        "<body><script>alert(1)</script>"
        "<p>Real prose here.</p>"
        "<nav>navigation noise</nav>"
        "</body></html>"
    )
    text, title = ac.html_to_text(html)
    assert title == "T"
    assert "Real prose here." in text
    assert "alert" not in text
    assert "color: red" not in text
    assert "navigation noise" not in text


def test_html_text_is_clean():
    assert ac.html_text_is_clean("Just normal prose. No tags here.")
    assert ac.html_text_is_clean("Comparison: 5 < 10 and 10 > 5 are fine.")
    assert not ac.html_text_is_clean("<p>Tag survived.</p>")
    assert not ac.html_text_is_clean("plain text <script>alert()</script>")


# ------------------- Substack feed parsing -----------------------


def test_substack_feed_parse_full_text():
    """Feed parser pulls full text, attaches dates, flags paid posts."""
    text = (FIXTURE_DIR / "substack_feed.xml").read_text(encoding="utf-8")
    items = ab.parse_feed(text, source_type=ab.SOURCE_SUBSTACK)
    assert len(items) == 3
    titles = [i.title for i in items]
    assert "On the Particular and the General" in titles
    assert "Field Notes from a Difficult Week" in titles
    assert "The Long Way Around" in titles

    # Paid post is flagged.
    paid = [i for i in items if i.is_paid]
    assert len(paid) == 1
    assert paid[0].title == "Field Notes from a Difficult Week"

    # Free posts have substantial body text.
    free = [i for i in items if not i.is_paid]
    assert all(len(i.body_html) > 300 for i in free), \
        "Full-text feed entries should have non-trivial body HTML"

    # Dates are parsed from RFC822 pubDate.
    import datetime as dt
    by_title = {i.title: i for i in items}
    assert by_title["On the Particular and the General"].date == \
        dt.date(2018, 3, 14)


def test_substack_paid_marker_detection():
    """Various paid markers all flag is_paid=True."""
    paid_bodies = [
        "<p>Subscribe to read</p>",
        '<div class="paywall">excerpt only</div>',
        "<p>This post is for paid subscribers</p>",
    ]
    for body in paid_bodies:
        assert ab._is_paid_excerpt(body, {}), f"Should flag paid: {body!r}"
    # Free-text body shouldn't flag.
    assert not ab._is_paid_excerpt("<p>Long-form prose here</p>", {})


# ------------------- Sitemap parsing -----------------------------


def test_sitemap_url_filtering_by_date():
    """Sitemap parser respects --since / --until window."""
    import datetime as dt
    text = (FIXTURE_DIR / "substack_sitemap.xml").read_text(encoding="utf-8")
    pairs = ab.parse_sitemap_urls(
        text, since=dt.date(2018, 1, 1), until=dt.date(2019, 12, 31),
    )
    urls = [u for u, _ in pairs]
    # 2017-11 entry filtered out by since.
    assert not any("older-essay" in u for u in urls)
    # 2024-01 archive URL filtered out by until.
    assert not any("/archive" in u for u in urls)
    # 2018 / 2019 posts remain.
    assert any("on-the-particular" in u for u in urls)
    assert any("the-long-way-around" in u for u in urls)


# ------------------- Source-type detection -----------------------


def test_source_detection_substack_hostname():
    fetcher = make_fetcher({})
    src, hints = ab.detect_source_type(SUBSTACK_URL, fetcher)
    assert src == ab.SOURCE_SUBSTACK


def test_source_detection_wordpress_via_feed_probe():
    fetcher = make_fetcher(WP_URLS)
    src, hints = ab.detect_source_type(WP_URL, fetcher)
    assert src == ab.SOURCE_WORDPRESS
    assert hints.get("feed_url")


def test_source_detection_generic_fallback():
    """No feed reachable → fall through to generic_html."""
    # Empty fetcher; every probe returns 404.
    fetcher = make_fetcher({})
    src, _ = ab.detect_source_type("https://nofeed.example.com", fetcher)
    assert src == ab.SOURCE_GENERIC


def test_derive_author_slug():
    assert ab.derive_author_slug("https://teststack.substack.com") == \
        "teststack_substack"
    assert ab.derive_author_slug(
        "https://teststack.substack.com/archive"
    ) == "teststack_substack"
    assert ab.derive_author_slug("https://example.com/blog") == \
        "example_blog"


# ------------------- End-to-end Substack acquisition -------------


def test_substack_end_to_end(tmp_path):
    """Acquire from Substack: feed + sitemap → 3 written files (the
    paid one is skipped). Manifest entries carry the impostor fields,
    cleaned text has no HTML residue, and the manifest validates clean.
    """
    output_dir = tmp_path / "ai-prose-baselines-private" / "impostors" / \
        "blog_essay" / "testauthor_substack"
    manifest_path = output_dir / "draft_manifest.jsonl"
    args = make_args(
        url=SUBSTACK_URL,
        substack=True,
        output_dir=str(output_dir),
        emit_manifest=str(manifest_path),
    )
    fetcher = make_fetcher(SUBSTACK_URLS)

    rc = ab.run(args, fetcher=fetcher)
    assert rc == 0, "Substack acquisition should succeed"

    # 3 free posts: 2 from feed (paid skipped) + 1 from sitemap-only.
    txt_files = sorted(output_dir.glob("*.txt"))
    meta_files = sorted(output_dir.glob("*.meta.json"))
    assert len(txt_files) == 3, \
        f"Expected 3 acquired posts, got {len(txt_files)}: {[f.name for f in txt_files]}"
    assert len(meta_files) == len(txt_files), \
        "Each .txt should have a paired .meta.json sidecar"

    # Cleaned text contains no raw HTML residue.
    for txt in txt_files:
        body = txt.read_text(encoding="utf-8")
        assert ac.html_text_is_clean(body), \
            f"HTML residue in {txt.name}: {body[:200]!r}"
        assert "Subscribe" not in body, \
            "Subscription widget should have been stripped"
        assert "comments" not in body.lower()[-200:], \
            "Trailing comments block should have been stripped"

    # Manifest entries carry the impostor fields.
    entries = read_manifest(manifest_path)
    assert len(entries) == 3
    for e in entries:
        assert e["corpus_role"] == "impostor"
        assert e["use"] == ["voice_impostor"]
        assert e["split"] == "baseline"
        assert e["privacy"] == "private"
        assert e["register"] == "blog_essay"
        assert e["era"] == "pre_chatgpt"
        assert e["consent_status"] == "fair_use_research"
        assert e["impostor_for"] == ["blog"]
        assert e["register_match"] == "high"
        assert e["topic_match"] == "medium"
        assert e["acquired_via"].startswith("acquire_blog_substack_rss_")
        assert e["content_hash"].startswith("sha256:")
        assert e["author"] == "Test Author"
        assert e["persona"] == "testauthor_substack"

    # Content hashes are unique (dedupe is wired and didn't false-fire).
    hashes = {e["content_hash"] for e in entries}
    assert len(hashes) == 3, "Each acquired post should have a unique hash"

    # Each meta sidecar has preprocessing metadata.
    for meta_file in meta_files:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        assert "preprocessing" in meta
        assert meta["preprocessing"]["applied"] is True
        assert "rules_active" in meta["preprocessing"]
        assert isinstance(meta["preprocessing"]["rules_active"], list)
        assert "content_hash" in meta
        assert "scraper" in meta and meta["scraper"].startswith("acquire_blog_")

    # Manifest validates clean (impostor schema enforcement).
    report = mv.validate_manifest(manifest_path)
    error_issues = [
        i for i in report["issues"] if i.get("severity") == "error"
    ]
    assert error_issues == [], \
        f"Manifest should validate clean: {error_issues}"
    # The persona-cross-check warning is expected because the test
    # manifest has no identity_baseline entry naming "blog" — that's
    # a real ratchet doing its job, but we verify there are no
    # IMPOSTOR-required-field errors.
    impostor_errors = [
        i for i in error_issues
        if "corpus_role" in i.get("message", "")
        or "impostor" in i.get("message", "").lower()
    ]
    assert impostor_errors == []


def test_substack_paid_post_is_skipped(tmp_path):
    """The paid/excerpt post from the feed never produces a .txt or
    a manifest entry, and the run summary records `skipped_paid`.
    """
    output_dir = tmp_path / "ai-prose-baselines-private" / "out"
    args = make_args(
        url=SUBSTACK_URL,
        substack=True,
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        max_posts=2,  # cap so sitemap path doesn't fire
    )
    fetcher = make_fetcher(SUBSTACK_URLS)
    ab.run(args, fetcher=fetcher)

    # The paid post's title-slug must not appear among written files.
    written = list(output_dir.glob("*.txt"))
    for f in written:
        assert "field-notes" not in f.name, \
            f"Paid post should not have been written: {f.name}"


# ------------------- WordPress / Ghost ---------------------------


def test_wordpress_end_to_end(tmp_path):
    """WordPress feed → 1 written post with the impostor schema."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "wp"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(
        url=WP_URL,
        wordpress=True,
        output_dir=str(output_dir),
        emit_manifest=str(manifest_path),
        persona="wpauthor_blog",
        author="WP Author",
    )
    fetcher = make_fetcher(WP_URLS)

    rc = ab.run(args, fetcher=fetcher)
    assert rc == 0

    txt_files = list(output_dir.glob("*.txt"))
    assert len(txt_files) == 1, \
        f"Expected 1 WP post, got {len(txt_files)}"

    body = txt_files[0].read_text(encoding="utf-8")
    assert "habit" in body.lower(), "Post body should contain key phrase"
    assert ac.html_text_is_clean(body)

    entries = read_manifest(manifest_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["corpus_role"] == "impostor"
    assert e["acquired_via"].startswith("acquire_blog_wordpress_feed_")
    assert e["persona"] == "wpauthor_blog"


# ------------------- Generic HTML archive ------------------------


def test_generic_html_end_to_end(tmp_path):
    """Generic-HTML archive: 2 post links → 2 written posts."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "generic"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(
        url=GENERIC_URL,
        html_archive=True,
        archive_pattern=GENERIC_ARCHIVE,
        output_dir=str(output_dir),
        emit_manifest=str(manifest_path),
        persona="generic_author_blog",
        author="Generic Author",
    )
    fetcher = make_fetcher(GENERIC_URLS)

    rc = ab.run(args, fetcher=fetcher)
    assert rc == 0

    txt_files = sorted(output_dir.glob("*.txt"))
    assert len(txt_files) == 2, \
        f"Expected 2 posts, got {len(txt_files)}: {[f.name for f in txt_files]}"

    for txt in txt_files:
        body = txt.read_text(encoding="utf-8")
        assert ac.html_text_is_clean(body), \
            f"HTML residue in {txt.name}"
        assert "</p>" not in body
        # Sidebar text and analytics noise must be gone.
        assert "Recent" not in body[:50], "Sidebar leaked"
        assert "console.log" not in body, "Script content leaked"

    entries = read_manifest(manifest_path)
    assert len(entries) == 2
    for e in entries:
        assert e["acquired_via"].startswith("acquire_blog_html_archive_")


def test_discover_post_links_default_pattern():
    """The default heuristic finds /YYYY/MM/ post URLs and skips
    bare /about/ pages."""
    html = (FIXTURE_DIR / "generic_archive.html").read_text(encoding="utf-8")
    links = ab.discover_post_links(html, archive_url=GENERIC_ARCHIVE)
    assert any("the-quiet-room" in url for url in links)
    assert any("conditions-of-attention" in url for url in links)
    assert not any(url.endswith("/about/") for url in links)


# ------------------- Dedupe --------------------------------------


def test_dedupe_within_output_dir(tmp_path):
    """A second run against the same output dir skips already-present
    content hashes. The .txt count stays at 3, and the manifest gets
    appended to (not duplicated)."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "dd"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(
        url=SUBSTACK_URL,
        substack=True,
        output_dir=str(output_dir),
        emit_manifest=str(manifest_path),
    )

    # Run 1.
    rc1 = ab.run(args, fetcher=make_fetcher(SUBSTACK_URLS))
    assert rc1 == 0
    first_count = len(list(output_dir.glob("*.txt")))
    first_manifest = read_manifest(manifest_path)
    assert first_count == 3
    assert len(first_manifest) == 3

    # Run 2 with same fixtures and output dir → all 3 hashes match,
    # no new files written. Exit code stays 0 because the script ran
    # successfully (skipped == intentional), but no new .txt files
    # appear and no new manifest entries are appended.
    ab.run(args, fetcher=make_fetcher(SUBSTACK_URLS))

    second_count = len(list(output_dir.glob("*.txt")))
    assert second_count == first_count, \
        "Dedupe should prevent rewrites"
    second_manifest = read_manifest(manifest_path)
    assert len(second_manifest) == len(first_manifest), \
        "No new manifest entries should be appended on a duplicate run"


# ------------------- Privacy guard -------------------------------


def test_privacy_guard_refuses_non_private(tmp_path):
    """Without --allow-public-output, output paths outside any
    'ai-prose-baselines-private' component are refused."""
    public_dir = tmp_path / "public_oops"
    args = make_args(
        url=SUBSTACK_URL,
        substack=True,
        output_dir=str(public_dir),
        emit_manifest=str(public_dir / "draft.jsonl"),
        allow_public_output=False,
    )
    fetcher = make_fetcher(SUBSTACK_URLS)

    if pytest is not None:
        with pytest.raises(SystemExit) as exc_info:
            ab.run(args, fetcher=fetcher)
        assert exc_info.value.code == 2
    else:
        try:
            ab.run(args, fetcher=fetcher)
            assert False, "Should have called sys.exit(2)"
        except SystemExit as e:
            assert e.code == 2
    # Nothing should have been written.
    assert not public_dir.exists() or not list(public_dir.glob("*.txt"))


def test_privacy_guard_accepts_marker_in_path(tmp_path):
    """Any path component named ai-prose-baselines-private satisfies
    the marker check, including a sibling-style layout."""
    private = tmp_path / "ai-prose-baselines-private" / "impostors"
    args = make_args(
        url=SUBSTACK_URL,
        substack=True,
        output_dir=str(private / "blog_essay" / "x"),
        emit_manifest=str(private / "blog_essay" / "x" / "draft.jsonl"),
        allow_public_output=False,
    )
    rc = ab.run(args, fetcher=make_fetcher(SUBSTACK_URLS))
    assert rc == 0
    assert any((private / "blog_essay" / "x").glob("*.txt"))


# ------------------- Robots.txt ----------------------------------


def test_robots_disallow_blocks_fetch(tmp_path):
    """A Disallow: / robots.txt is honored; the fetcher returns 403
    for the feed URL and zero posts get acquired."""
    fixture_robots = make_fetcher({
        f"{SUBSTACK_URL}/feed": "substack_feed.xml",
        f"{SUBSTACK_URL}/sitemap.xml": "substack_sitemap.xml",
        f"{SUBSTACK_URL}/robots.txt": "robots_disallow.txt",
    })
    fixture_robots.respect_robots = True

    output_dir = tmp_path / "ai-prose-baselines-private" / "robotstest"
    args = make_args(
        url=SUBSTACK_URL,
        substack=True,
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
    )
    ab.run(args, fixture_robots)

    # The feed itself is blocked → no posts written.
    written = list(output_dir.glob("*.txt"))
    assert written == [], \
        f"robots.txt Disallow should block fetches; got {written}"


def test_robots_allow_permits_fetch(tmp_path):
    """An Allow: / robots.txt lets fetches through normally."""
    fixture_robots = make_fetcher({
        f"{SUBSTACK_URL}/feed": "substack_feed.xml",
        f"{SUBSTACK_URL}/sitemap.xml": "substack_sitemap.xml",
        "https://teststack.substack.com/p/older-essay-from-the-archive":
            "substack_post_archive.html",
        f"{SUBSTACK_URL}/robots.txt": "robots_allow.txt",
    })
    fixture_robots.respect_robots = True

    output_dir = tmp_path / "ai-prose-baselines-private" / "robotsallow"
    args = make_args(
        url=SUBSTACK_URL,
        substack=True,
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
    )
    rc = ab.run(args, fixture_robots)
    assert rc == 0
    assert len(list(output_dir.glob("*.txt"))) == 3


# ------------------- Date window --------------------------------


def test_since_until_filters(tmp_path):
    """--since/--until filters posts by date_written."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "window"
    args = make_args(
        url=SUBSTACK_URL,
        substack=True,
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        since="2019-01-01",
        until="2019-12-31",
    )
    rc = ab.run(args, fetcher=make_fetcher(SUBSTACK_URLS))
    assert rc == 0
    written = list(output_dir.glob("*.txt"))
    # Only "The Long Way Around" (2019-09) is in window.
    # The 2018 post and the 2017 archive post are filtered out.
    # The paid post is also a 2019 entry but skipped as paid.
    assert len(written) == 1, \
        f"Expected 1 post in 2019 window, got {[f.name for f in written]}"
    assert "long-way" in written[0].name


# ------------------- Dry-run -------------------------------------


def test_dry_run_writes_nothing(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dry"
    args = make_args(
        url=SUBSTACK_URL,
        substack=True,
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        dry_run=True,
    )
    rc = ab.run(args, fetcher=make_fetcher(SUBSTACK_URLS))
    assert rc == 0
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))
    assert not (output_dir / "draft.jsonl").exists()


# ------------------- Manifest entry composition -----------------


def test_compose_manifest_entry_required_fields():
    """Direct test of compose_manifest_entry: every impostor-required
    field is emitted, with no None values (they would trip validator
    warnings)."""
    import datetime as dt
    piece = ac.AcquiredPiece(
        title="Sample Post",
        author="Sample Author",
        persona="sample_blog",
        register="blog_essay",
        date_written=dt.date(2019, 5, 22),
        source_url="https://example.com/p/sample",
        cleaned_text="Some prose. " * 100,
        raw_byte_length=2400,
        preprocessing_meta={"applied": True, "rules_active": []},
        acquired_via="acquire_blog_substack_rss_2026-05-08",
        consent_status="fair_use_research",
        era="pre_chatgpt",
        register_match="high",
        topic_match="medium",
        impostor_for=["blog"],
    )
    text_path = Path("/tmp/x/2019-05-22_sample-post.txt")
    manifest_dir = Path("/tmp/x")
    entry = ac.compose_manifest_entry(
        piece, text_path=text_path, manifest_relative_to=manifest_dir,
    )
    required = {
        "id", "path", "ai_status", "use", "corpus_role",
        "impostor_for", "register_match", "topic_match",
        "consent_status", "era", "acquired_via", "content_hash",
    }
    missing = required - set(entry.keys())
    assert not missing, f"Missing required fields: {missing}"
    assert entry["use"] == ["voice_impostor"]
    assert entry["corpus_role"] == "impostor"
    assert entry["split"] == "baseline"
    assert entry["privacy"] == "private"
    # No None values that would trip validator warnings.
    assert all(v is not None for v in entry.values()), \
        f"Found None in manifest entry: {entry}"


# ------------------- Run summary --------------------------------


def test_run_summary_render():
    s = ac.RunSummary(
        acquired=4, skipped_paid=1, skipped_duplicate=2,
        total_cleaned_words=12345,
        per_rule_strips={"html_tag": 200, "indented_code": 5},
        draft_manifest_path="/tmp/draft.jsonl",
    )
    rendered = s.render_stderr()
    assert "Acquired: 4 files" in rendered
    assert "Skipped (paid-only): 1" in rendered
    assert "Skipped (duplicate hash): 2" in rendered
    assert "Total cleaned words: 12,345" in rendered
    assert "html_tag=200" in rendered
    assert "/tmp/draft.jsonl" in rendered


# ------------------- Allow CLI as smoke -------------------------


def test_cli_help_lists_required_flags():
    """The --help output includes every spec-mandated flag."""
    parser = ab.build_arg_parser()
    help_text = parser.format_help()
    for flag in (
        "--persona", "--impostor-for", "--register", "--register-match",
        "--topic-match", "--consent-status", "--era",
        "--since", "--until", "--max-posts",
        "--dry-run", "--emit-manifest", "--rate-limit", "--user-agent",
        "--allow-non-prose", "--strip-rules", "--strip-aggressive",
        "--out", "--allow-public-output",
    ):
        assert flag in help_text, f"--help is missing {flag}"


# ------------------- Integration with manifest_validator --------


def test_emitted_manifest_passes_validator(tmp_path):
    """End-to-end: the emitted draft manifest should validate clean
    when augmented with one identity_baseline entry naming the
    persona this impostor targets."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "vt"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(
        url=SUBSTACK_URL,
        substack=True,
        output_dir=str(output_dir),
        emit_manifest=str(manifest_path),
    )
    rc = ab.run(args, fetcher=make_fetcher(SUBSTACK_URLS))
    assert rc == 0

    # Add an identity_baseline entry naming the "blog" persona so the
    # impostor's persona-reference cross-check has a target.
    baseline_text = output_dir / "fake_baseline.txt"
    baseline_text.write_text(
        "Identity baseline prose. " * 100, encoding="utf-8",
    )
    baseline_entry = {
        "id": "fake_baseline",
        "path": "fake_baseline.txt",
        "author": "Test User",
        "persona": "blog",
        "register": "blog_essay",
        "ai_status": "pre_ai_human",
        "language_status": "native",
        "use": ["baseline", "voice_profile"],
        "split": "baseline",
        "privacy": "private",
        "corpus_role": "identity_baseline",
        "era": "pre_chatgpt",
    }
    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(baseline_entry, sort_keys=True) + "\n")

    report = mv.validate_manifest(manifest_path)
    error_issues = [
        i for i in report["issues"] if i.get("severity") == "error"
    ]
    assert error_issues == [], \
        f"Augmented manifest should validate without errors: " \
        f"{error_issues}"

    # No persona-reference warnings now that "blog" exists.
    persona_warns = [
        i for i in report["issues"]
        if i.get("severity") == "warning"
        and "persona" in i.get("message", "").lower()
        and ("blog" in i.get("message", "")
             or "blog" in str(i.get("entry", "")))
    ]
    assert persona_warns == [], \
        f"persona-reference warnings unexpected: {persona_warns}"


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
