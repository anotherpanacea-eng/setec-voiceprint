#!/usr/bin/env python3
"""acquire_blog.py — pull a single author's blog or Substack archive.

Reads a single feed/archive URL and writes:
  1. One ``.txt`` per acquired post into a private pool dir.
  2. One ``.meta.json`` sidecar per post (URL, date, hash, scraper meta).
  3. One draft manifest JSONL ready to merge into ``corpus_manifest.jsonl``
     after review.

Corpus bucket (``--bucket``):
  ``impostor`` (default) — ``corpus_role: impostor``, ``use:
     [voice_impostor]``, ``split: baseline``: third-party reference prose
     for voice discrimination. Requires ``--impostor-for`` /
     ``--consent-status``.
  ``validation`` — no ``corpus_role``, ``use: [validation]``, ``split:
     test``: validation-spine material (e.g. your own AI-involved
     writing), excluded from the baseline and selected by the validation
     harness. Pair with ``--ai-status mixed`` + ``--notes-composite`` for
     writing whose AI involvement varies.

The script auto-detects which extraction path to use:

  Substack         (`*.substack.com` or Substack-shaped feed at
                    ``<url>/feed``) — RSS for recent posts (full text)
                    plus sitemap.xml for the full archive.
  WordPress/Ghost  (responds with WP/Ghost-shaped feed at ``/feed/`` or
                    ``/rss/``) — feed plus archive pages.
  Generic HTML     (no recognizable feed) — requires ``--archive-pattern``
                    pointing at the index page; follows post links.
  Wayback Machine  (passed via ``--wayback`` explicitly) — uses the CDX
                    API to enumerate snapshots of the URL pattern.

Privacy: acquired text is voice-cloning input from someone else's
prose. By default, output goes under
``ai-prose-baselines-private/impostors/<register>/<author_slug>/`` and
the privacy guard refuses paths outside any directory named
``ai-prose-baselines-private``. Pass ``--allow-public-output`` only
for non-personal corpora (rare).

Robots: this script honors robots.txt by default and ships no
override flag in v1. If a future version adds one, it must require
explicit user opt-in, emit a stderr warning, and record the override
in metadata.

Paid Substack content: paid-only posts come as excerpt-only and are
always skipped in v1 with a flag (``Skipped (paid-only)`` summary
line). Authenticated fetch is out of scope.

Usage:

    python3 scripts/acquire_blog.py https://jehsmith.substack.com \\
        --persona smith_jeh_substack \\
        --impostor-for blog \\
        --register blog_essay \\
        --consent-status fair_use_research \\
        --era pre_chatgpt \\
        --since 2018-01-01 --until 2022-11-01 \\
        --max-posts 25 \\
        --output-dir ../ai-prose-baselines-private/impostors/blog_essay/smith_jeh

    # Your own AI-involved Substack → the validation bucket:
    python3 scripts/acquire_blog.py https://anotherpanacea.substack.com \\
        --persona anotherpanacea --register blog_essay \\
        --bucket validation --ai-status mixed \\
        --notes-composite ai_assisted,ai_generated_from_outline

See ``internal/2026-05-08-impostor-corpus-spec.md`` for design context.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# Resolve repo-relative imports the same way other scripts do.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402

TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "acquire_blog"
SCRAPER_VERSION = "1.0"


# ---- Site-specific config registry --------------------------------

# Per-domain overrides for known targets. Acquisition scripts treat
# this as a convenience seed; users can always override via flags.
# Patterns target the Tier-1 candidates from the impostor research
# notes, with selectors verified against archive snapshots.
SITE_CONFIGS: dict[str, dict[str, Any]] = {
    "marginalrevolution.com": {
        "type": "generic_html",
        "archive_pattern": (
            "https://marginalrevolution.com/marginalrevolution/{year}/{month:02d}"
        ),
        "post_link_pattern": r"/marginalrevolution/\d{4}/\d{2}/[a-z0-9-]+\.html",
        "content_selector": ".pjgm-postcontent",
    },
    "slatestarcodex.com": {
        "type": "generic_html",
        "archive_pattern": "https://slatestarcodex.com/?cat=Essays",
        "content_selector": ".pjgm-postcontent",
    },
    "overcomingbias.com": {
        "type": "wordpress",
        "feed_url": "https://www.overcomingbias.com/feed",
    },
    "jehsmith.substack.com": {
        "type": "substack",
    },
    "thedarkmagazine.com": {
        "type": "wordpress",
        "feed_url": "https://thedarkmagazine.com/feed/",
    },
    "criticalanimal.com": {
        # Blogger / Blogspot blog on a custom domain. The default
        # `/feed/` and `/rss/` paths return 404; Blogger's Atom feed
        # lives at `/feeds/posts/default`. feedparser handles the
        # Atom payload via the same `entry.content[0].value` path
        # the wordpress source uses, so type="wordpress" is correct.
        "type": "wordpress",
        "feed_url": "https://www.criticalanimal.com/feeds/posts/default?max-results=500",
    },
}


def site_config_for(url: str) -> dict[str, Any] | None:
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return SITE_CONFIGS.get(host)


# ---- Source-type detection ---------------------------------------


SOURCE_SUBSTACK = "substack"
SOURCE_WORDPRESS = "wordpress"
SOURCE_GENERIC = "generic_html"
SOURCE_WAYBACK = "wayback"


def detect_source_type(
    url: str, fetcher: ac.Fetcher,
) -> tuple[str, dict[str, Any]]:
    """Probe ``url`` to decide which extraction path to use.

    Returns ``(source_type, hints)`` where ``hints`` carries
    site-specific URL fragments the per-source path expects (feed_url,
    sitemap_url, content_selector overrides).

    Probe order:
      1. Site config lookup (``SITE_CONFIGS``).
      2. Substack heuristic: hostname or ``<url>/feed`` body markers.
      3. WordPress / Ghost heuristic: ``/feed/`` body markers.
      4. Fall through to generic HTML.
    """
    config = site_config_for(url)
    if config:
        hints = {k: v for k, v in config.items() if k != "type"}
        return config["type"], hints

    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host.endswith(".substack.com"):
        return SOURCE_SUBSTACK, {}

    # Probe <url>/feed and look at the response body for telltale
    # markers. We only fetch one probe URL per site so the rate-limit
    # cost is small.
    feed_url = url.rstrip("/") + "/feed"
    feed_url_alt = url.rstrip("/") + "/feed/"

    for candidate in (feed_url, feed_url_alt):
        result = fetcher.fetch(candidate)
        if not result.ok or not result.text:
            continue
        body = result.text[:5000].lower()
        if "substack" in body:
            return SOURCE_SUBSTACK, {"feed_url": candidate}
        if "wp-content" in body or "wordpress" in body or "<rss" in body:
            return SOURCE_WORDPRESS, {"feed_url": candidate}
        if "generator" in body and "ghost" in body:
            return SOURCE_WORDPRESS, {"feed_url": candidate}
        # An RSS body with no clear platform marker is still a feed
        # we can parse; treat as generic WordPress shape.
        if "<rss" in body or "<feed" in body:
            return SOURCE_WORDPRESS, {"feed_url": candidate}

    return SOURCE_GENERIC, {}


# ---- Feed parsing -------------------------------------------------


@dataclass
class FeedItem:
    """Normalized feed entry across Substack/WordPress/Atom feeds.

    feedparser's per-source field names are inconsistent (e.g.,
    ``content`` vs ``content_encoded`` vs ``description``); this
    dataclass picks the right field per source so the per-post
    pipeline doesn't have to.
    """
    title: str
    link: str
    date: _dt.date | None
    body_html: str
    is_paid: bool = False
    raw_byte_length: int = 0


def parse_feed(feed_text: str, *, source_type: str) -> list[FeedItem]:
    """Parse a feed payload into normalized FeedItem records.

    Detects paid Substack posts via known class markers in the body
    HTML. Returns an empty list on parse error rather than raising,
    matching the spec's "report on stderr but don't abort" rule.
    """
    try:
        import feedparser  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "feedparser is not installed. Install acquisition deps "
            "with: pip install -r requirements-acquisition.txt"
        ) from e

    parsed = feedparser.parse(feed_text)
    items: list[FeedItem] = []
    for entry in parsed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        # Body resolution: try content[0]['value'], then summary, then
        # description. Substack puts full text in content; WordPress
        # may use either content_encoded or description depending on
        # feed version.
        body = ""
        contents = entry.get("content") or []
        if contents:
            body = (contents[0].get("value") or "").strip()
        if not body:
            body = (entry.get("summary") or "").strip()
        if not body:
            body = (entry.get("description") or "").strip()
        # Date resolution.
        date = _entry_date(entry)
        # Paid detection: Substack adds a "Subscribers Only" footer
        # or strips body content entirely; the marker is in the
        # entry summary or content.
        is_paid = _is_paid_excerpt(body, entry)
        raw_len = len(body.encode("utf-8")) if body else 0
        items.append(FeedItem(
            title=title, link=link, date=date,
            body_html=body, is_paid=is_paid, raw_byte_length=raw_len,
        ))
    return items


def _entry_date(entry: Any) -> _dt.date | None:
    """Extract a ``datetime.date`` from a feedparser entry.

    Tries ``published_parsed``, ``updated_parsed``, then falls back
    to parsing ``published`` / ``updated`` strings via
    `parse_iso_date`. Returns ``None`` if nothing usable is present.
    """
    for key in ("published_parsed", "updated_parsed"):
        ts = entry.get(key)
        if ts:
            try:
                return _dt.date(ts.tm_year, ts.tm_mon, ts.tm_mday)
            except (ValueError, TypeError):
                continue
    for key in ("published", "updated", "date"):
        s = entry.get(key)
        if s:
            d = ac.parse_iso_date(str(s))
            if d:
                return d
    return None


_PAID_MARKERS = (
    "subscribe to read",
    "this post is for paid subscribers",
    "subscriber-only",
    'class="paywall"',
    'class="subscriber-only"',
    '"audience":"only_paid"',
    "this post is for paying subscribers",
)


def _is_paid_excerpt(body: str, entry: Any) -> bool:
    body_lower = (body or "").lower()
    for marker in _PAID_MARKERS:
        if marker in body_lower:
            return True
    # Substack also sets <itunes:explicit> and other tags, plus
    # an `audience` field on paid posts.
    audience = (entry.get("audience") or "").lower()
    if audience == "only_paid":
        return True
    return False


# ---- Sitemap parsing (Substack) ----------------------------------


def parse_sitemap_urls(
    sitemap_text: str, *, since: _dt.date | None, until: _dt.date | None,
) -> list[tuple[str, _dt.date | None]]:
    """Pull `(url, lastmod_date)` pairs from a sitemap.xml payload.

    Handles both flat sitemaps (``<urlset>`` containing ``<url>``
    nodes) and sitemap indexes (``<sitemapindex>`` containing
    ``<sitemap>`` nodes). Substack's ``/sitemap.xml`` is most often
    a sitemap-index pointing at daughter sitemaps; the previous
    parser only looked at ``<url>`` nodes and silently returned an
    empty list on indexes, which made the daughter-fetch path in
    ``acquire_substack`` never run and archive-only posts were
    invisible. The fix accepts both element kinds — they share the
    same ``<loc>`` / ``<lastmod>`` shape — and the caller's
    daughter-detection (URLs ending in ``.xml`` with ``sitemap`` in
    the basename) handles the recursion.

    Stdlib XML parser to avoid a strict feedparser dependency on the
    sitemap path (sitemaps aren't RSS/Atom).
    """
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(sitemap_text)
    except ET.ParseError:
        return []
    # Strip namespace.
    ns_re = re.compile(r"^\{[^}]+\}")
    pairs: list[tuple[str, _dt.date | None]] = []
    for elem in root.iter():
        tag = ns_re.sub("", elem.tag)
        if tag not in ("url", "sitemap"):
            continue
        loc = None
        lastmod = None
        for child in elem:
            child_tag = ns_re.sub("", child.tag)
            if child_tag == "loc":
                loc = (child.text or "").strip()
            elif child_tag == "lastmod":
                lastmod = (child.text or "").strip()
        if not loc:
            continue
        date = ac.parse_iso_date(lastmod) if lastmod else None
        if since and date and date < since:
            continue
        if until and date and date > until:
            continue
        pairs.append((loc, date))
    return pairs


# ---- Post body extraction ----------------------------------------


# Default selectors used when the site config doesn't override.
DEFAULT_CONTENT_SELECTORS = (
    ".body.markup",          # Substack
    ".entry-content",        # WordPress / Ghost / Nightmare
    ".post-content",         # Ghost
    "article > .content",
    "article",
    "main",
)

# Selectors stripped on every blog page (footer/comments/related).
DEFAULT_STRIP_SELECTORS = (
    ".comments", ".comment-list", ".comment-respond", "#comments",
    ".related-posts", ".related", ".post-footer", ".entry-footer",
    ".sharedaddy", ".jp-relatedposts", ".author-bio",
    ".subscription-widget", ".subscriber-content",
    ".post-meta-share",
)


def extract_post_body(
    html: str, *, content_selector: str | None = None,
) -> tuple[str, str | None]:
    """Try the configured selector first; fall back to defaults.

    Returns ``(text, html_title_fallback)``. The HTML title is used
    when the feed didn't provide one (rare but happens for direct
    sitemap fetches that bypass the feed).
    """
    selectors = []
    if content_selector:
        selectors.append(content_selector)
    selectors.extend(DEFAULT_CONTENT_SELECTORS)
    text = ""
    title = None
    for sel in selectors:
        text, title_candidate = ac.html_to_text(
            html,
            content_selector=sel,
            strip_selectors=DEFAULT_STRIP_SELECTORS,
        )
        if title is None:
            title = title_candidate
        if text and len(text) > 200:
            return text, title
    return text, title


# ---- Per-post processing -----------------------------------------


@dataclass
class ProcessOptions:
    """User-facing options that control the per-post pipeline.

    Threads through the script so per-source paths don't have to
    juggle a long argument list. Built once from CLI args at the top
    of `main`.
    """
    persona: str
    impostor_for: list[str]
    register: str
    register_match: str
    topic_match: str
    consent_status: str
    era: str
    author: str
    since: _dt.date | None
    until: _dt.date | None
    output_dir: Path
    manifest_path: Path
    rate_limit: float
    max_posts: int
    dry_run: bool
    allow_non_prose: bool
    strip_rules: str | None
    strip_aggressive: bool
    acquired_via: str
    content_selector: str | None
    skip_robots: bool = False
    # Corpus-bucket controls. Defaults reproduce the historical impostor
    # shape exactly; --bucket validation flips them to the validation set.
    corpus_role: str | None = "impostor"
    use: list[str] = field(default_factory=lambda: ["voice_impostor"])
    split: str = "baseline"
    ai_status: str | None = None
    notes_obj: dict[str, Any] | None = None


def process_one_post(
    *,
    title: str,
    body_html: str,
    date: _dt.date | None,
    source_url: str,
    raw_byte_length: int,
    options: ProcessOptions,
    summary: ac.RunSummary,
    content_selector: str | None = None,
) -> Optional[ac.AcquiredPiece]:
    """Run a single post through extract → preprocess → hash → write.

    Returns the AcquiredPiece on success, ``None`` on skip (paid /
    duplicate / parse error / out-of-window). Mutates ``summary`` to
    record the outcome. The caller is responsible for emitting the
    manifest entry; we keep that step at the call site so dry-run
    can suppress writes uniformly.
    """
    # Date-window filter.
    if options.since and date and date < options.since:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="out-of-window-before",
            url=source_url,
            detail=date.isoformat() if date else "",
        )
        return None
    if options.until and date and date > options.until:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="out-of-window-after",
            url=source_url,
            detail=date.isoformat() if date else "",
        )
        return None

    # Extract.
    try:
        body_text, html_title = extract_post_body(
            body_html, content_selector=content_selector or options.content_selector,
        )
    except Exception as e:
        summary.skipped_parse_error += 1
        summary.log_skip(
            reason="parse-error",
            url=source_url,
            detail=str(e),
        )
        return None
    if not body_text or len(body_text) < 50:
        summary.skipped_parse_error += 1
        summary.log_skip(
            reason="empty-body",
            url=source_url,
            detail=f"len={len(body_text)}",
        )
        return None

    # Title fallback chain.
    final_title = title or html_title or f"untitled-{source_url}"

    # Preprocess.
    cleaned_text, prep_meta = ac.preprocess_text(
        body_text,
        rules=options.strip_rules,
        allow_non_prose=options.allow_non_prose,
        strip_aggressive=options.strip_aggressive,
    )

    if not cleaned_text or len(cleaned_text) < 50:
        summary.skipped_parse_error += 1
        summary.log_skip(
            reason="empty-after-preprocess",
            url=source_url,
            detail=f"raw_len={len(body_text)} clean_len={len(cleaned_text)}",
        )
        return None

    piece = ac.AcquiredPiece(
        title=final_title,
        author=options.author,
        persona=options.persona,
        register=options.register,
        date_written=date,
        source_url=source_url,
        cleaned_text=cleaned_text,
        raw_byte_length=raw_byte_length or len(body_html.encode("utf-8")),
        preprocessing_meta=prep_meta,
        acquired_via=options.acquired_via,
        consent_status=options.consent_status,
        era=options.era,
        register_match=options.register_match,
        topic_match=options.topic_match,
        impostor_for=list(options.impostor_for),
    )

    # Dedupe by content hash within output dir.
    existing = ac.content_hash_already_present(
        piece.content_hash, options.output_dir,
    )
    if existing is not None:
        summary.skipped_duplicate += 1
        summary.log_skip(
            reason="duplicate-hash",
            url=source_url,
            detail=str(existing),
        )
        sys.stderr.write(
            f"  duplicate hash; skipping {source_url} "
            f"(matches {existing.name})\n"
        )
        return None

    summary.record_strip_meta(prep_meta)
    summary.total_cleaned_words += piece.word_count
    return piece


def emit_piece(
    piece: ac.AcquiredPiece,
    *,
    options: ProcessOptions,
    summary: ac.RunSummary,
) -> None:
    """Write piece + sidecar + manifest entry. No-op for dry-run."""
    if options.dry_run:
        sys.stderr.write(
            f"  [dry-run] would write {piece.filename_stem()} "
            f"({piece.word_count} words)\n"
        )
        summary.acquired += 1
        return
    text_path, _meta_path = ac.write_piece(
        piece, output_dir=options.output_dir,
        scraper_version=SCRAPER_VERSION,
    )
    compose_kwargs: dict[str, Any] = dict(
        text_path=text_path,
        manifest_relative_to=options.manifest_path.parent,
        use=options.use,
        split=options.split,
        corpus_role=options.corpus_role,
    )
    # Only override the compose defaults when set, so the impostor path
    # stays byte-identical (ai_status -> "pre_ai_human", no extra notes).
    if options.ai_status is not None:
        compose_kwargs["ai_status"] = options.ai_status
    if options.notes_obj is not None:
        compose_kwargs["extra"] = {"notes": options.notes_obj}
    entry = ac.compose_manifest_entry(piece, **compose_kwargs)
    ac.append_manifest_entry(options.manifest_path, entry)
    summary.acquired += 1
    sys.stderr.write(
        f"  acquired {text_path.name} ({piece.word_count} words)\n"
    )


# ---- Per-source acquisition paths --------------------------------


def acquire_substack(
    url: str,
    fetcher: ac.Fetcher,
    options: ProcessOptions,
    summary: ac.RunSummary,
    *,
    hints: dict[str, Any] | None = None,
) -> None:
    """Substack acquisition: feed for recent + sitemap for the rest.

    Substack feeds carry full text for free posts; paid posts come
    excerpt-only and get skipped via `_is_paid_excerpt`. The sitemap
    is the source of truth for the full archive — we parse the index
    for individual post URLs whose dates fall in the window, then
    fetch each directly via the Substack-default content selector.
    """
    base = url.rstrip("/")
    feed_url = (hints or {}).get("feed_url") or f"{base}/feed"
    feed_result = fetcher.fetch(feed_url)
    feed_items: list[FeedItem] = []
    if feed_result.ok and feed_result.text:
        feed_items = parse_feed(feed_result.text, source_type=SOURCE_SUBSTACK)
    else:
        sys.stderr.write(f"  feed unreachable: {feed_url} (status={feed_result.status})\n")

    seen_urls: set[str] = set()

    # Pass 1: feed items (full text in body_html).
    for item in feed_items:
        if summary.acquired >= options.max_posts:
            return
        if not item.link:
            continue
        seen_urls.add(item.link)
        if item.is_paid:
            summary.skipped_paid += 1
            summary.log_skip(
                reason="paid-only",
                url=item.link,
                detail="excerpt only",
            )
            continue
        piece = process_one_post(
            title=item.title,
            body_html=item.body_html,
            date=item.date,
            source_url=item.link,
            raw_byte_length=item.raw_byte_length,
            options=options,
            summary=summary,
            content_selector=".body.markup",
        )
        if piece is not None:
            emit_piece(piece, options=options, summary=summary)

    # Pass 2: sitemap-only posts (need direct HTML fetch).
    if summary.acquired >= options.max_posts:
        return
    sitemap_url = f"{base}/sitemap.xml"
    sitemap_result = fetcher.fetch(sitemap_url)
    if not sitemap_result.ok or not sitemap_result.text:
        return
    archive_pairs = parse_sitemap_urls(
        sitemap_result.text,
        since=options.since, until=options.until,
    )
    # Substack's sitemap.xml is sometimes an index pointing at daughter
    # sitemaps. If we got <=2 entries and none look like post URLs,
    # check if any look like sitemap URLs and fetch them.
    daughters = [
        u for u, _ in archive_pairs
        if "sitemap" in u.split("/")[-1].lower() and u.endswith(".xml")
    ]
    if daughters and not any(
        "/p/" in u or "/posts/" in u for u, _ in archive_pairs
    ):
        for daughter_url in daughters:
            d_result = fetcher.fetch(daughter_url)
            if d_result.ok and d_result.text:
                archive_pairs.extend(parse_sitemap_urls(
                    d_result.text,
                    since=options.since, until=options.until,
                ))
    for post_url, post_date in archive_pairs:
        if summary.acquired >= options.max_posts:
            return
        if post_url in seen_urls:
            continue
        # Substack post URLs match `/p/<slug>`; sitemap also lists
        # author pages, archive pages, etc. Filter to post URLs.
        if "/p/" not in post_url:
            continue
        post_result = fetcher.fetch(post_url)
        if not post_result.ok:
            summary.skipped_network_error += 1
            summary.log_skip(
                reason="network-error",
                url=post_url,
                detail=f"status={post_result.status}",
            )
            continue
        # Paid-post check on the direct-HTML path. Feed-entry items
        # already get this via FeedItem.is_paid in pass 1, but
        # sitemap-only posts skipped that gate. A paid Substack page
        # served as raw HTML carries the same paywall markers
        # (`subscriber-only` / `paywall` classes, "Subscribe to
        # read", etc.); without this check we would write the
        # subscription wrapper as if it were the post body and emit
        # an impostor entry containing only paywall text.
        if _is_paid_excerpt(post_result.text, {}):
            summary.skipped_paid += 1
            summary.log_skip(
                reason="paid-only",
                url=post_url,
                detail="paywall markers in HTML",
            )
            continue
        # Title from the HTML's <title> tag — picked up by html_to_text.
        # Date from sitemap lastmod when available; otherwise None.
        piece = process_one_post(
            title="",  # Will fall back to HTML <title>.
            body_html=post_result.text,
            date=post_date,
            source_url=post_url,
            raw_byte_length=len(post_result.text.encode("utf-8")),
            options=options,
            summary=summary,
            content_selector=".body.markup",
        )
        if piece is not None:
            emit_piece(piece, options=options, summary=summary)


def acquire_wordpress(
    url: str,
    fetcher: ac.Fetcher,
    options: ProcessOptions,
    summary: ac.RunSummary,
    *,
    hints: dict[str, Any] | None = None,
) -> None:
    """WordPress / Ghost: feed + (later) archive page traversal.

    v1 reads the feed only. Sites that publish their full archive in
    the feed (Ghost, Substack-style) are fully covered. Older posts
    on long-running WordPress blogs that paginate the feed will need
    a follow-up pass via category/tag/year-month archive pages — the
    spec lists this as "per-site config" and v1 leaves that for a
    user-supplied `--archive-pattern`.
    """
    base = url.rstrip("/")
    feed_url = (hints or {}).get("feed_url") or f"{base}/feed/"
    feed_result = fetcher.fetch(feed_url)
    if not feed_result.ok or not feed_result.text:
        # Try /feed (no trailing slash) and /rss/ as fallbacks.
        for alt in (f"{base}/feed", f"{base}/rss/"):
            feed_result = fetcher.fetch(alt)
            if feed_result.ok and feed_result.text:
                feed_url = alt
                break
    if not feed_result.ok or not feed_result.text:
        sys.stderr.write(f"  no feed reachable for {url}\n")
        return
    items = parse_feed(feed_result.text, source_type=SOURCE_WORDPRESS)
    for item in items:
        if summary.acquired >= options.max_posts:
            return
        if not item.link:
            continue
        if item.is_paid:
            summary.skipped_paid += 1
            summary.log_skip(
                reason="paid-only", url=item.link, detail="excerpt only",
            )
            continue
        # If feed body looks too short, fetch the post page directly.
        body_html = item.body_html
        raw_len = item.raw_byte_length
        if len(body_html) < 1500:
            post_result = fetcher.fetch(item.link)
            if post_result.ok and post_result.text:
                body_html = post_result.text
                raw_len = len(post_result.text.encode("utf-8"))
        piece = process_one_post(
            title=item.title,
            body_html=body_html,
            date=item.date,
            source_url=item.link,
            raw_byte_length=raw_len,
            options=options,
            summary=summary,
        )
        if piece is not None:
            emit_piece(piece, options=options, summary=summary)


def discover_post_links(
    html: str, *, archive_url: str, link_pattern: str | None = None,
) -> list[str]:
    """Pull post links out of an archive page.

    Default heuristic: ``<a href>`` whose path matches a year/month
    or `/posts/` pattern. Site configs can override with a tighter
    pattern.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("beautifulsoup4 required") from e

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    pat = re.compile(link_pattern) if link_pattern else re.compile(
        r"(?:/\d{4}/\d{2}/|/posts/|/p/|/essays?/|/blog/)[a-zA-Z0-9_-]+"
    )
    links: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if not href:
            continue
        # Resolve relative URLs against the archive page.
        full = urllib.parse.urljoin(archive_url, href)
        if full in seen:
            continue
        if pat.search(full):
            links.append(full)
            seen.add(full)
    return links


def acquire_generic_html(
    url: str,
    fetcher: ac.Fetcher,
    options: ProcessOptions,
    summary: ac.RunSummary,
    *,
    archive_pattern: str | None,
    hints: dict[str, Any] | None = None,
) -> None:
    """Generic HTML archive: fetch index → enumerate post links → fetch each.

    The most fragile path. Requires ``--archive-pattern`` from the
    user (or a site config that supplies one). Default link heuristic
    is documented in `discover_post_links`; per-site overrides go via
    SITE_CONFIGS.
    """
    cfg = hints or {}
    if archive_pattern is None:
        archive_pattern = cfg.get("archive_pattern")
    if not archive_pattern:
        sys.stderr.write(
            "  generic-html mode needs --archive-pattern; pointing at "
            f"{url} as the index page\n"
        )
        archive_pattern = url
    link_pattern = cfg.get("post_link_pattern")
    content_selector = cfg.get("content_selector") or options.content_selector

    archive_result = fetcher.fetch(archive_pattern)
    if not archive_result.ok or not archive_result.text:
        sys.stderr.write(
            f"  archive page unreachable: {archive_pattern} "
            f"(status={archive_result.status})\n"
        )
        return

    post_links = discover_post_links(
        archive_result.text,
        archive_url=archive_pattern,
        link_pattern=link_pattern,
    )
    sys.stderr.write(
        f"  found {len(post_links)} post link(s) on {archive_pattern}\n"
    )
    for post_url in post_links:
        if summary.acquired >= options.max_posts:
            return
        post_result = fetcher.fetch(post_url)
        if not post_result.ok or not post_result.text:
            summary.skipped_network_error += 1
            summary.log_skip(
                reason="network-error",
                url=post_url,
                detail=f"status={post_result.status}",
            )
            continue
        piece = process_one_post(
            title="",
            body_html=post_result.text,
            date=None,
            source_url=post_url,
            raw_byte_length=len(post_result.text.encode("utf-8")),
            options=options,
            summary=summary,
            content_selector=content_selector,
        )
        if piece is not None:
            emit_piece(piece, options=options, summary=summary)


def acquire_wayback(
    url: str,
    fetcher: ac.Fetcher,
    options: ProcessOptions,
    summary: ac.RunSummary,
) -> None:
    """Wayback Machine path: enumerate snapshots via CDX, fetch each.

    Soft-depends on the optional `wayback` package. If unavailable,
    falls back to the public CDX HTTP endpoint and parses the JSON
    response directly. v1 fetches the most recent snapshot per URL
    pattern within the date window.
    """
    # CDX API endpoint. Returns JSON list-of-lists with header row.
    since_str = options.since.strftime("%Y%m%d") if options.since else ""
    until_str = options.until.strftime("%Y%m%d") if options.until else ""
    cdx_url = (
        "https://web.archive.org/cdx/search/cdx?"
        f"url={urllib.parse.quote(url)}/&"
        "matchType=prefix&"
        "output=json&"
        "filter=statuscode:200&"
        "filter=mimetype:text/html&"
        "collapse=urlkey&"
        f"from={since_str}&to={until_str}"
    )
    cdx_result = fetcher.fetch(cdx_url)
    if not cdx_result.ok or not cdx_result.text:
        sys.stderr.write(f"  Wayback CDX unreachable: {cdx_url}\n")
        return
    try:
        rows = json.loads(cdx_result.text)
    except json.JSONDecodeError:
        sys.stderr.write(f"  CDX response not JSON: {cdx_url}\n")
        return
    if not rows or len(rows) < 2:
        return
    header = rows[0]
    try:
        ts_idx = header.index("timestamp")
        url_idx = header.index("original")
    except ValueError:
        return
    seen_urls: set[str] = set()
    for row in rows[1:]:
        if summary.acquired >= options.max_posts:
            return
        ts = row[ts_idx]
        original = row[url_idx]
        if original in seen_urls:
            continue
        seen_urls.add(original)
        wayback_url = f"https://web.archive.org/web/{ts}/{original}"
        snapshot = fetcher.fetch(wayback_url)
        if not snapshot.ok or not snapshot.text:
            summary.skipped_network_error += 1
            continue
        # Date inferred from wayback timestamp (YYYYMMDDhhmmss).
        date = None
        if len(ts) >= 8:
            date = ac.parse_iso_date(f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}")
        piece = process_one_post(
            title="",
            body_html=snapshot.text,
            date=date,
            source_url=original,
            raw_byte_length=len(snapshot.text.encode("utf-8")),
            options=options,
            summary=summary,
        )
        if piece is not None:
            emit_piece(piece, options=options, summary=summary)


# ---- CLI ---------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="acquire_blog",
        description=(
            "Acquire a single author's blog or Substack archive into "
            "the impostor pool. See "
            "internal/2026-05-08-impostor-corpus-spec.md for context."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("url", help="Blog/Substack root URL to acquire from.")

    # Source-type override flags (mutually exclusive).
    src_group = p.add_mutually_exclusive_group()
    src_group.add_argument("--substack", action="store_true",
                           help="Force Substack extraction path.")
    src_group.add_argument("--wordpress", action="store_true",
                           help="Force WordPress / Ghost path.")
    src_group.add_argument("--html-archive", action="store_true",
                           help="Force generic HTML archive path.")
    src_group.add_argument("--wayback", action="store_true",
                           help="Use the Wayback Machine CDX API.")

    p.add_argument("--archive-pattern",
                   help="Archive index URL for generic-HTML mode.")
    p.add_argument("--content-selector",
                   help="CSS selector for the post body (rare override).")

    # Persona / impostor metadata.
    p.add_argument("--persona", required=False,
                   help=("Persona slug for emitted entries. Defaults to "
                         "the author slug derived from the URL."))
    p.add_argument("--author",
                   help=("Author display name. Defaults to a humanized "
                         "version of the persona slug."))
    # impostor_for / consent_status are required ONLY for the (default)
    # impostor bucket; run() enforces that post-parse so they can stay
    # optional here for --bucket validation, which emits neither. For the
    # impostor bucket the validator errors on empty impostor_for, so run()
    # rejects it early — before any network budget is spent.
    p.add_argument("--impostor-for", nargs="+",
                   help=("Persona slug(s) this impostor serves "
                         "(required for --bucket impostor; the schema "
                         "rejects empty)."))
    p.add_argument("--register", required=True,
                   help="Manifest register; e.g. blog_essay.")
    p.add_argument("--register-match",
                   choices=["high", "medium", "low"], default="high",
                   help="Register-match closeness for the impostor target.")
    p.add_argument("--topic-match",
                   choices=["high", "medium", "low"], default="medium",
                   help="Topical-match closeness for the impostor target.")
    p.add_argument("--consent-status",
                   choices=[
                       "public_record", "cc_licensed", "fair_use_research",
                       "author_consent", "undocumented",
                   ],
                   help=("Consent / legal posture for the impostor entry "
                         "(required for --bucket impostor)."))
    p.add_argument("--era",
                   choices=[
                       "pre_chatgpt", "pre_ai_widespread",
                       "post_ai_widespread", "undated",
                   ],
                   default="pre_chatgpt",
                   help="Era classification of the acquired prose.")

    # ---- Corpus bucket (impostor reference pool vs. validation set) ----
    p.add_argument("--bucket", choices=["impostor", "validation"],
                   default="impostor",
                   help=("Which corpus bucket the entries land in. "
                         "'impostor' (default): corpus_role=impostor, "
                         "use=[voice_impostor], split=baseline — the "
                         "reference pool for voice discrimination. "
                         "'validation': no corpus_role, use=[validation], "
                         "split=test — validation-spine material (e.g. your "
                         "own AI-involved writing), excluded from the "
                         "baseline and selected by the validation harness."))
    p.add_argument("--ai-status",
                   choices=[
                       "pre_ai_human", "ai_generated", "ai_assisted",
                       "ai_edited", "mixed", "unknown",
                       "ai_generated_from_outline",
                   ],
                   help=("AI-authorship status of the acquired prose. "
                         "Unset → compose's 'pre_ai_human'. Use 'mixed' for "
                         "writing whose AI involvement varies by piece "
                         "(requires --notes-composite)."))
    p.add_argument("--notes-composite",
                   help=("Comma-separated authorship states for an "
                         "--ai-status mixed entry (e.g. "
                         "'ai_assisted,ai_generated_from_outline'); written "
                         "to notes.composite_states, which the schema "
                         "requires for mixed entries."))
    p.add_argument("--notes-description",
                   help="Free-text note stored at notes.description.")

    # Date window + max.
    p.add_argument("--since", help="Inclusive lower-bound date (YYYY-MM-DD).")
    p.add_argument("--until", help="Inclusive upper-bound date (YYYY-MM-DD).")
    p.add_argument("--max-posts", type=int, default=50,
                   help="Maximum number of posts to acquire (default: 50).")

    # Output paths.
    p.add_argument("--output-dir",
                   help=("Where to write .txt and .meta.json files. "
                         "Defaults to <baselines>/impostors/<register>/<author>."))
    p.add_argument("--emit-manifest",
                   help=("Where to write draft manifest JSONL. Defaults "
                         "to <output-dir>/draft_manifest.jsonl."))
    p.add_argument("--out", help="Write summary report here (JSON).")

    # Behavior.
    p.add_argument("--rate-limit", type=float, default=2.0,
                   help="Seconds between same-host requests (default: 2.0).")
    ac.add_user_agent_arg(p)
    p.add_argument("--dry-run", action="store_true",
                   help="Inventory what would be acquired without writing.")
    p.add_argument("--allow-public-output", action="store_true",
                   help=("Allow writing outside ai-prose-baselines-private/."
                         " Acquired prose is voice-cloning input; only use "
                         "for non-personal corpora."))

    # Preprocessing pass-throughs.
    p.add_argument("--allow-non-prose", action="store_true",
                   help="Skip preprocessing's corpus-hygiene gate.")
    p.add_argument("--strip-rules",
                   help=("Comma-separated subset of preprocessing rules to "
                         "apply. Default: all standard rules."))
    p.add_argument("--strip-aggressive", action="store_true",
                   help="Also apply aggressive (link/citation) strip rules.")

    return p


def humanize_persona(slug: str) -> str:
    """Best-effort author display name from a persona slug."""
    if not slug:
        return "Unknown"
    parts = slug.split("_")
    if parts and parts[-1] in {"personal", "substack", "blog"}:
        parts = parts[:-1]
    if not parts:
        return "Unknown"
    return " ".join(p.capitalize() for p in parts)


def determine_source_type(
    args: argparse.Namespace, fetcher: ac.Fetcher,
) -> tuple[str, dict[str, Any]]:
    if args.substack:
        return SOURCE_SUBSTACK, {}
    if args.wordpress:
        return SOURCE_WORDPRESS, {}
    if args.html_archive:
        return SOURCE_GENERIC, {}
    if args.wayback:
        return SOURCE_WAYBACK, {}
    return detect_source_type(args.url, fetcher)


def derive_author_slug(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    # Substack: <slug>.substack.com → <slug>_substack
    if host.endswith(".substack.com"):
        return host.split(".")[0] + "_substack"
    # Other domains: derive from the second-level domain.
    parts = host.split(".")
    if len(parts) >= 2:
        return parts[-2] + "_blog"
    return ac.slugify(host) or "unknown_blog"


def run(
    args: argparse.Namespace, fetcher: ac.Fetcher | None = None,
) -> int:
    """Top-level acquisition driver. Returns the exit code."""
    if fetcher is None:
        fetcher = ac.make_requests_fetcher(
            version=SCRAPER_VERSION,
            rate_limit_seconds=args.rate_limit,
            user_agent=getattr(args, "user_agent", None) or None,
        )

    # Resolve persona / author.
    persona = args.persona or derive_author_slug(args.url)
    author = args.author or humanize_persona(persona)

    # ---- Corpus bucket: presets + post-parse requirement checks -------
    bucket = getattr(args, "bucket", "impostor")
    bucket_presets = {
        "impostor": (["voice_impostor"], "baseline", "impostor"),
        # use=validation (the ALLOWED_USE tag the validation harness
        # selects on; test_set is not a recognized use); split=test is the
        # canonical partition for validation entries (manifest-schema.md).
        "validation": (["validation"], "test", None),
    }
    use_tags, split_tag, corpus_role = bucket_presets[bucket]
    ai_status = getattr(args, "ai_status", None)
    if bucket == "impostor":
        missing = [name for name, val in (
            ("--impostor-for", args.impostor_for),
            ("--consent-status", args.consent_status),
        ) if not val]
        if missing:
            raise SystemExit(
                f"{TOOL_NAME}: {', '.join(missing)} required for "
                "--bucket impostor"
            )
    notes_composite = getattr(args, "notes_composite", None)
    notes_description = getattr(args, "notes_description", None)
    if ai_status == "mixed" and not notes_composite:
        raise SystemExit(
            f"{TOOL_NAME}: --ai-status mixed requires --notes-composite "
            "(the schema needs notes.composite_states)"
        )
    notes_obj: dict[str, Any] | None = None
    if notes_composite or notes_description:
        notes_obj = {}
        if notes_composite:
            notes_obj["composite_states"] = [
                s.strip() for s in notes_composite.split(",") if s.strip()
            ]
        if notes_description:
            notes_obj["description"] = notes_description

    # Source-type detection.
    source_type, hints = determine_source_type(args, fetcher)
    sys.stderr.write(f"Detected source type: {source_type} (url={args.url})\n")

    # Date filters.
    since = ac.parse_iso_date(args.since)
    until = ac.parse_iso_date(args.until)
    if args.since and not since:
        sys.stderr.write(f"  warning: could not parse --since={args.since}\n")
    if args.until and not until:
        sys.stderr.write(f"  warning: could not parse --until={args.until}\n")

    # Output paths.
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
    else:
        output_dir = ac.default_output_dir(
            register=args.register, author_slug=persona,
        )
    if args.emit_manifest:
        manifest_path = Path(args.emit_manifest).expanduser()
    else:
        manifest_path = output_dir / "draft_manifest.jsonl"

    # Privacy guard. The summary report --out also has to live under
    # a private root unless --allow-public-output.
    paths_to_check = [output_dir, manifest_path]
    if args.out:
        paths_to_check.append(Path(args.out).expanduser())
    ac.check_output_privacy(
        paths_to_check, allow_public=args.allow_public_output, tool=TOOL_NAME,
    )

    # Acquired-via tag for manifest entries.
    today = _dt.date.today().isoformat()
    acquired_via_map = {
        SOURCE_SUBSTACK: f"acquire_blog_substack_rss_{today}",
        SOURCE_WORDPRESS: f"acquire_blog_wordpress_feed_{today}",
        SOURCE_GENERIC: f"acquire_blog_html_archive_{today}",
        SOURCE_WAYBACK: f"acquire_blog_wayback_cdx_{today}",
    }
    acquired_via = acquired_via_map[source_type]

    options = ProcessOptions(
        persona=persona,
        author=author,
        impostor_for=list(args.impostor_for or []),
        register=args.register,
        register_match=args.register_match,
        topic_match=args.topic_match,
        consent_status=args.consent_status or "",
        era=args.era,
        since=since,
        until=until,
        output_dir=output_dir,
        manifest_path=manifest_path,
        rate_limit=args.rate_limit,
        max_posts=args.max_posts,
        dry_run=args.dry_run,
        allow_non_prose=args.allow_non_prose,
        strip_rules=args.strip_rules,
        strip_aggressive=args.strip_aggressive,
        acquired_via=acquired_via,
        content_selector=args.content_selector,
        corpus_role=corpus_role,
        use=list(use_tags),
        split=split_tag,
        ai_status=ai_status,
        notes_obj=notes_obj,
    )
    summary = ac.RunSummary(
        draft_manifest_path=str(manifest_path) if not args.dry_run else None,
        output_dir=str(output_dir),
    )

    sys.stderr.write(
        f"Acquiring into {output_dir}\n"
        f"Persona: {persona} (impostor_for: {options.impostor_for})\n"
    )

    if source_type == SOURCE_SUBSTACK:
        acquire_substack(args.url, fetcher, options, summary, hints=hints)
    elif source_type == SOURCE_WORDPRESS:
        acquire_wordpress(args.url, fetcher, options, summary, hints=hints)
    elif source_type == SOURCE_GENERIC:
        acquire_generic_html(
            args.url, fetcher, options, summary,
            archive_pattern=args.archive_pattern,
            hints=hints,
        )
    elif source_type == SOURCE_WAYBACK:
        acquire_wayback(args.url, fetcher, options, summary)
    else:
        sys.stderr.write(f"Unknown source type: {source_type}\n")
        return 2

    sys.stderr.write("\n" + summary.render_stderr())
    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if summary.acquired == 0 and summary.skipped_paid == 0 and \
            not summary.skip_log:
        sys.stderr.write(
            "No posts acquired. Verify the URL, source-type detection, "
            "and date window.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
