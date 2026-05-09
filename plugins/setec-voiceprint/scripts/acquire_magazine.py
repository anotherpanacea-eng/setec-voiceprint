#!/usr/bin/env python3
"""acquire_magazine.py — pull literary-horror short fiction from online magazines.

Site-specific scraper modules behind a uniform CLI. v1 ships with two
working magazines (Nightmare and The Dark) — both run on WordPress
with the same ``.entry-content`` body selector and the same
issue-archive shape. Additional magazines (Strange Horizons, Apex,
Clarkesworld, Lightspeed) are deferred to v2 unless trivially
similar; the architecture supports adding them by appending one
entry to ``MAGAZINE_MODULES``.

The intended use case is impostor-pool acquisition for the General
Imposters validation harness: the user wants a register-matched
sample of contemporary literary-horror prose from named writers
(``--filter-author Brian Evenson Kelly Link``) that the harness can
compare against the user's own fiction baseline. ``--persona-from-
author`` mints one persona slug per author so the impostor pool can
be sliced by writer downstream.

Architecture mirrors ``acquire_blog.py``:
  * Shared ``acquisition_core.Fetcher`` abstraction so CI tests
    drive the scrapers off fixture HTML without network access.
  * Per-magazine config is a small dict (selectors + URL patterns).
  * The same per-piece pipeline: HTML extract → preprocessing
    corpus-hygiene gate → SHA-256 → within-output-dir dedupe →
    cleaned text + .meta.json + draft manifest entry.

Privacy: acquired stories live under
``ai-prose-baselines-private/impostors/<register>/<persona>/`` and
are never published or distributed. The marker-based privacy guard
refuses paths outside any ``ai-prose-baselines-private`` ancestor
unless ``--allow-public-output`` is set.

Robots: this script honors robots.txt by default. v1 ships no
override flag.

Usage:

    # All Nightmare stories by Brian Evenson and Kelly Link, 2014–2022:
    python3 scripts/acquire_magazine.py \\
        --magazine nightmare \\
        --persona-from-author \\
        --register literary_horror \\
        --consent-status fair_use_research \\
        --era pre_chatgpt \\
        --filter-author "Brian Evenson" "Kelly Link" \\
        --since 2014-01-01 --until 2022-11-01 \\
        --impostor-for fiction

    # Everything in The Dark since 2018, capped at 30 stories:
    python3 scripts/acquire_magazine.py \\
        --magazine the_dark \\
        --persona-from-author \\
        --register literary_horror \\
        --consent-status fair_use_research \\
        --since 2018-01-01 \\
        --max-stories 30 \\
        --impostor-for fiction

See ``internal/2026-05-08-impostor-corpus-spec.md`` for design.
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

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402

TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "acquire_magazine"
SCRAPER_VERSION = "1.0"


# ---- Magazine config registry ------------------------------------


@dataclass(frozen=True)
class MagazineConfig:
    """Static config for one magazine.

    Selectors are CSS strings passed to BeautifulSoup. Issue and
    story link patterns are regexes applied to ``href`` attributes
    after URL resolution; they're the second filter on top of the
    selector match (helps when a page has many matching anchors but
    only some of them point at issues / stories).
    """
    name: str
    archive_url: str
    issue_link_selector: str
    issue_href_pattern: str
    story_link_selector: str
    story_href_pattern: str
    story_content_selector: str
    author_selector: str
    title_selector: str
    date_selector: str
    # Anything matching this selector and everything after it inside
    # the content container is removed before extraction. Spec calls
    # out the Nightmare "Author Spotlight" interview block as the
    # canonical case.
    strip_after_selector: str = ""
    # Additional descendants of the content container that should
    # be dropped before extraction (editorial intros, share widgets,
    # etc.). Strings are CSS selectors.
    extra_strip_selectors: tuple[str, ...] = ()


# Selector patterns are based on the spec's documented Nightmare /
# The Dark archive shapes (both run on WordPress; the markup is
# stable across recent issues per spot-check). Adjust here when
# upstream markup changes.
NIGHTMARE = MagazineConfig(
    name="nightmare",
    archive_url="https://nightmare-magazine.com/issues/",
    issue_link_selector="a[href*='/issues/']",
    issue_href_pattern=r"/issues/[^/]+/?$",
    story_link_selector=".issue-toc a, .issue-content a, article a",
    story_href_pattern=r"/fiction/[^/]+/?$",
    story_content_selector=".entry-content, article .content",
    author_selector=".byline a, .byline, .author",
    title_selector=".entry-title, h1.title, h1",
    date_selector=".entry-date, time, .post-date",
    strip_after_selector="#author-spotlight, .author-spotlight",
    extra_strip_selectors=(
        ".sharedaddy", ".jp-relatedposts", ".comments-area",
        ".issue-nav", ".post-nav",
    ),
)

THE_DARK = MagazineConfig(
    name="the_dark",
    archive_url="https://thedarkmagazine.com/issues/",
    issue_link_selector="a[href*='/issues/']",
    issue_href_pattern=r"/issues/[^/]+/?$",
    story_link_selector=".issue-toc a, article a, .entry-content a",
    # Story slugs are top-level paths: ``/the-bone-orchard/``. The
    # negative lookbehind keeps us from matching nested paths like
    # ``/author/<name>/`` or ``/issues/<slug>/`` that otherwise
    # match a bare ``/[a-z0-9-]+/?$`` regex on the URL tail.
    story_href_pattern=(
        r"^https?://[^/]+/(?!author/|issues/|category/|tag/|wp-content/|"
        r"wp-includes/|feed/|page/)[a-z0-9-]+/?$"
    ),
    story_content_selector=".entry-content",
    author_selector=".byline a, .byline, .author",
    title_selector=".entry-title, h1",
    date_selector=".entry-date, time, .post-date",
    # The Dark doesn't run author-spotlight interviews after
    # stories, but it does run editorial intros and a "Get the
    # ebook" widget after the body.
    strip_after_selector=".ebook-widget, .post-bottom",
    extra_strip_selectors=(
        ".sharedaddy", ".jp-relatedposts", ".comments-area",
        ".author-bio", ".issue-nav",
    ),
)

MAGAZINE_MODULES: dict[str, MagazineConfig] = {
    NIGHTMARE.name: NIGHTMARE,
    THE_DARK.name: THE_DARK,
}


# ---- Story dataclass --------------------------------------------


@dataclass
class StoryMetadata:
    """One story discovered from an issue page."""
    title: str
    author: str
    url: str
    date: _dt.date | None
    # The issue URL that surfaced this story. Threaded through for
    # provenance + manifest ``source`` link.
    issue_url: str = ""


# ---- HTML extraction helpers ------------------------------------


def _select_text(soup: Any, selector: str) -> str:
    """Return the text content of the first selector match, or ``""``.

    For comma-separated selectors, tries each in order and returns
    the first non-empty match. ``select_one`` on a comma-list
    returns the first hit in document-tree order, so a parent
    container with a more specific child can mask the child;
    iterating gives us per-selector priority.
    """
    if not selector:
        return ""
    candidates = [s.strip() for s in selector.split(",") if s.strip()]
    for sel in candidates:
        try:
            elem = soup.select_one(sel)
        except Exception:
            continue
        if elem is None:
            continue
        text = elem.get_text(separator=" ", strip=True)
        if text:
            return text
    return ""


_BYLINE_PREFIX_RE = re.compile(r"^\s*by\s+", re.IGNORECASE)


def _clean_author(raw: str) -> str:
    """Drop ``"By "`` / ``"by "`` prefix and collapse whitespace.

    Magazine bylines are usually one of:
      ``<a class="author">Author Name</a>``
      ``By <a class="author">Author Name</a>``
      ``<span class="byline">by Author Name</span>``
    The author-selector heuristic prefers the inner anchor when
    present (because it's the most semantically scoped), but if we
    fall back to ``.byline`` text we get the prefix; strip it here.
    """
    if not raw:
        return ""
    cleaned = _BYLINE_PREFIX_RE.sub("", raw).strip()
    return re.sub(r"\s+", " ", cleaned)


def _select_first_attr(soup: Any, selector: str, attr: str) -> str:
    """Return the requested attribute of the first selector match."""
    if not selector:
        return ""
    try:
        elem = soup.select_one(selector)
    except Exception:
        return ""
    if elem is None:
        return ""
    return (elem.get(attr) or "").strip()


def _strip_after(container: Any, marker_selector: str) -> None:
    """Remove the marker element and everything after it inside
    ``container``. Mutates in place.

    The Nightmare "Author Spotlight" block sits at the end of the
    story page as a sibling of the body paragraphs; we walk forward
    from the marker and decompose every element we encounter.
    """
    if not marker_selector:
        return
    try:
        marker = container.select_one(marker_selector)
    except Exception:
        marker = None
    if marker is None:
        return
    # Walk forward through the marker's parent's children, dropping
    # everything from the marker onward. Most magazine layouts put
    # the body text and the author-spotlight block as siblings under
    # a common .entry-content parent.
    parent = marker.parent
    if parent is None:
        marker.decompose()
        return
    found = False
    for child in list(parent.children):
        if child is marker:
            found = True
        if found:
            try:
                child.decompose()
            except (AttributeError, NotImplementedError):
                # NavigableString instances don't have decompose;
                # extract them instead.
                try:
                    child.extract()
                except Exception:
                    pass


def parse_issue_page(
    html: str, *, config: MagazineConfig, base_url: str,
) -> list[StoryMetadata]:
    """Extract the list of stories on one issue page.

    Each magazine's issue page lists its TOC; we pull the per-story
    permalink from the configured selector, then read author / title
    / date from anchor text or surrounding markup. The returned list
    is filtered to entries whose href matches ``story_href_pattern``
    so navigation links and reprint pointers are excluded.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("beautifulsoup4 required") from e

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    href_pat = re.compile(config.story_href_pattern, re.IGNORECASE)
    stories: list[StoryMetadata] = []
    seen_urls: set[str] = set()
    for anchor in soup.select(config.story_link_selector):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue
        url = urllib.parse.urljoin(base_url, href)
        if not href_pat.search(url):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        # Title: anchor text. Author: try sibling .byline / .author
        # within the same item. Magazine TOCs usually wrap each
        # entry in <article> or <li> with a byline span next to the
        # title link.
        title = anchor.get_text(strip=True) or ""
        item = anchor.find_parent(["article", "li", "div"]) or anchor.parent
        author = ""
        if item is not None:
            for sel in ("a.author", ".author", ".byline a", ".byline", ".by"):
                candidate = item.select_one(sel)
                if candidate:
                    text = candidate.get_text(strip=True)
                    if text and text.lower() != title.lower():
                        author = _clean_author(text)
                        break
        stories.append(StoryMetadata(
            title=title,
            author=author,
            url=url,
            date=None,
            issue_url=base_url,
        ))
    return stories


def parse_story_page(
    html: str, *, config: MagazineConfig,
) -> tuple[str, str, str, _dt.date | None]:
    """Extract ``(body_text, title, author, date)`` from a story page.

    Body extraction:
      1. Find the configured ``story_content_selector`` container.
      2. Drop noise (``<script>``, ``<style>``, share widgets,
         editorial intros, author-spotlight blocks).
      3. Convert what remains to plain text via the existing
         ``acquisition_core.html_to_text`` pipeline.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("beautifulsoup4 required") from e

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    title = _select_text(soup, config.title_selector)
    author = _clean_author(_select_text(soup, config.author_selector))

    raw_date = _select_first_attr(soup, config.date_selector, "datetime") \
        or _select_text(soup, config.date_selector)
    date = ac.parse_iso_date(raw_date) if raw_date else None

    # Body container: prefer the configured selector; fall back to
    # html_to_text's defaults if the magazine ever drops the class.
    container = None
    try:
        container = soup.select_one(config.story_content_selector)
    except Exception:
        container = None
    if container is not None:
        # In-place strip of post-body cruft inside the container.
        _strip_after(container, config.strip_after_selector)
        for sel in config.extra_strip_selectors:
            for tag in container.select(sel):
                try:
                    tag.decompose()
                except Exception:
                    pass
        body_html = str(container)
    else:
        body_html = html

    # Reuse the shared HTML-to-text pipeline (drops <script> /
    # <style> / <nav> globally and handles whitespace collapse).
    body_text, html_title = ac.html_to_text(
        body_html,
        content_selector=config.story_content_selector,
        strip_selectors=(
            ".sharedaddy", ".jp-relatedposts", ".author-bio",
            ".comments-area", ".comment-list", ".comment-respond",
            ".post-meta-share", ".issue-nav",
        ),
    )
    if not title and html_title:
        title = html_title
    return body_text, title or "", author or "", date


# ---- Discovery driver -------------------------------------------


def discover_issue_urls(
    archive_html: str, *, config: MagazineConfig, base_url: str,
) -> list[str]:
    """Pull issue URLs out of the archive index page.

    Filters to URLs matching ``issue_href_pattern`` so the bare
    ``/issues/`` self-link and category pages don't leak through.
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("beautifulsoup4 required") from e

    try:
        soup = BeautifulSoup(archive_html, "lxml")
    except Exception:
        soup = BeautifulSoup(archive_html, "html.parser")

    pat = re.compile(config.issue_href_pattern, re.IGNORECASE)
    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.select(config.issue_link_selector):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url = urllib.parse.urljoin(base_url, href)
        if not pat.search(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


# ---- Per-row pipeline -------------------------------------------


@dataclass
class ProcessOptions:
    """User-facing options threaded through the per-story pipeline.

    Built once from CLI args at the top of ``run`` so the per-source
    helpers don't juggle a long argument list.
    """
    persona: str | None  # explicit override; None → derive from author
    persona_from_author: bool
    impostor_for: list[str]
    register: str
    register_match: str
    topic_match: str
    consent_status: str
    era: str
    filter_author: list[str]  # empty = no filter
    since: _dt.date | None
    until: _dt.date | None
    output_dir: Path
    manifest_path: Path
    rate_limit: float
    max_stories: int
    dry_run: bool
    allow_non_prose: bool
    strip_rules: str | None
    strip_aggressive: bool
    acquired_via: str


def _author_matches_filter(author: str, filters: list[str]) -> bool:
    """Case-insensitive substring match against any filter entry.

    The user passes display names (``"Brian Evenson"``); magazine
    pages may render as ``"Brian Evenson"`` or ``"By Brian
    Evenson"`` depending on the byline format. Substring matching
    handles both without per-magazine quirks.
    """
    if not filters:
        return True
    lo = author.lower()
    for f in filters:
        if f.lower() in lo:
            return True
    return False


def _resolve_persona(author: str, options: ProcessOptions) -> str:
    """Decide which persona slug to attach to a story.

    Priority order:
      1. Explicit ``--persona`` (lump everything together; rarely
         useful for impostor work but supported per spec).
      2. ``--persona-from-author``: derive via
         ``acquisition_core.author_to_persona_slug`` so collisions
         between two same-surname writers can be tracked downstream.
      3. Fallback: same as #2 (deriving is the safer default for
         per-story output organization).
    """
    if options.persona:
        return options.persona
    return ac.author_to_persona_slug(author or "Unknown")


def process_one_story(
    story: StoryMetadata,
    body_html: str,
    *,
    options: ProcessOptions,
    summary: ac.RunSummary,
    config: MagazineConfig,
) -> Optional[ac.AcquiredPiece]:
    """Run one story page through extract → preprocess → hash → write.

    Returns the AcquiredPiece on success, ``None`` on skip
    (filtered, out-of-window, parse error, duplicate). Mutates
    ``summary`` to record the outcome.
    """
    body_text, page_title, page_author, page_date = parse_story_page(
        body_html, config=config,
    )
    title = story.title or page_title or "untitled"
    author = page_author or story.author or "Unknown"
    date = page_date or story.date

    # Author filter happens after we've parsed the page so we can
    # match on the canonical byline (the issue-TOC version may be
    # truncated or formatted differently).
    if not _author_matches_filter(author, options.filter_author):
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="filter-author", url=story.url, detail=author,
        )
        return None

    # Date-window filter.
    if options.since and date and date < options.since:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="out-of-window-before",
            url=story.url,
            detail=date.isoformat() if date else "",
        )
        return None
    if options.until and date and date > options.until:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="out-of-window-after",
            url=story.url,
            detail=date.isoformat() if date else "",
        )
        return None

    if not body_text or len(body_text) < 200:
        summary.skipped_parse_error += 1
        summary.log_skip(
            reason="empty-body", url=story.url,
            detail=f"len={len(body_text)}",
        )
        return None

    cleaned, prep_meta = ac.preprocess_text(
        body_text,
        rules=options.strip_rules,
        allow_non_prose=options.allow_non_prose,
        strip_aggressive=options.strip_aggressive,
    )
    if not cleaned or len(cleaned) < 200:
        summary.skipped_parse_error += 1
        summary.log_skip(
            reason="empty-after-preprocess",
            url=story.url,
            detail=f"raw={len(body_text)} clean={len(cleaned)}",
        )
        return None

    persona_slug = _resolve_persona(author, options)
    piece = ac.AcquiredPiece(
        title=title,
        author=author,
        persona=persona_slug,
        register=options.register,
        date_written=date,
        source_url=story.url,
        cleaned_text=cleaned,
        raw_byte_length=len(body_html.encode("utf-8")),
        preprocessing_meta=prep_meta,
        acquired_via=options.acquired_via,
        consent_status=options.consent_status,
        era=options.era,
        register_match=options.register_match,
        topic_match=options.topic_match,
        impostor_for=list(options.impostor_for),
        notes=f"issue: {story.issue_url}" if story.issue_url else "",
    )

    # Per-author dedupe by content hash (within the persona subdir).
    author_subdir = options.output_dir / ac.slugify(persona_slug)
    existing = ac.content_hash_already_present(piece.content_hash, author_subdir)
    if existing is not None:
        summary.skipped_duplicate += 1
        summary.log_skip(
            reason="duplicate-hash",
            url=story.url,
            detail=str(existing),
        )
        sys.stderr.write(
            f"  duplicate hash; skipping {story.url} (matches {existing.name})\n"
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
    """Write piece + sidecar + draft manifest entry. No-op for dry-run."""
    if options.dry_run:
        sys.stderr.write(
            f"  [dry-run] would write {piece.persona}/{piece.filename_stem()} "
            f"({piece.word_count} words)\n"
        )
        summary.acquired += 1
        return
    author_subdir = options.output_dir / ac.slugify(piece.persona)
    text_path, _meta_path = ac.write_piece(
        piece, output_dir=author_subdir, scraper_version=SCRAPER_VERSION,
    )
    entry = ac.compose_manifest_entry(
        piece,
        text_path=text_path,
        manifest_relative_to=options.manifest_path.parent,
    )
    ac.append_manifest_entry(options.manifest_path, entry)
    summary.acquired += 1
    sys.stderr.write(
        f"  acquired {piece.persona}/{text_path.name} ({piece.word_count} words)\n"
    )


# ---- Top-level magazine driver ----------------------------------


def acquire_magazine(
    config: MagazineConfig,
    fetcher: ac.Fetcher,
    options: ProcessOptions,
    summary: ac.RunSummary,
) -> None:
    """Walk the magazine archive → issues → stories pipeline.

    Stops as soon as ``summary.acquired`` reaches ``options.max_stories``.
    Network or parse errors on a single issue / story are recorded
    in the skip log but don't abort the run.
    """
    archive_result = fetcher.fetch(config.archive_url)
    if not archive_result.ok or not archive_result.text:
        sys.stderr.write(
            f"  archive page unreachable: {config.archive_url} "
            f"(status={archive_result.status})\n"
        )
        return

    issue_urls = discover_issue_urls(
        archive_result.text, config=config, base_url=config.archive_url,
    )
    sys.stderr.write(
        f"  found {len(issue_urls)} issue(s) in {config.name} archive\n"
    )

    for issue_url in issue_urls:
        if summary.acquired >= options.max_stories:
            return
        issue_result = fetcher.fetch(issue_url)
        if not issue_result.ok or not issue_result.text:
            summary.skipped_network_error += 1
            summary.log_skip(
                reason="network-error", url=issue_url,
                detail=f"status={issue_result.status}",
            )
            continue
        stories = parse_issue_page(
            issue_result.text, config=config, base_url=issue_url,
        )
        for story in stories:
            if summary.acquired >= options.max_stories:
                return
            # Pre-fetch filter to save a round-trip when the issue
            # TOC carries an author byline that already excludes
            # the story.
            if (
                options.filter_author
                and story.author
                and not _author_matches_filter(story.author, options.filter_author)
            ):
                summary.skipped_filtered += 1
                summary.log_skip(
                    reason="filter-author-prefilter",
                    url=story.url, detail=story.author,
                )
                continue
            story_result = fetcher.fetch(story.url)
            if not story_result.ok or not story_result.text:
                summary.skipped_network_error += 1
                summary.log_skip(
                    reason="network-error", url=story.url,
                    detail=f"status={story_result.status}",
                )
                continue
            piece = process_one_story(
                story, story_result.text,
                options=options, summary=summary, config=config,
            )
            if piece is not None:
                emit_piece(piece, options=options, summary=summary)


# ---- CLI --------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Acquire literary-horror short fiction from online "
            "magazine archives for the impostor pool. Site-specific "
            "scrapers behind a uniform CLI. v1: nightmare, the_dark."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--magazine", required=True,
                   choices=sorted(MAGAZINE_MODULES.keys()),
                   help=(
                       "Magazine archive to scrape. v1 ships nightmare "
                       "and the_dark; additional magazines defer to v2."
                   ))
    persona_group = p.add_mutually_exclusive_group()
    persona_group.add_argument(
        "--persona-from-author", action="store_true",
        help=(
            "Mint one persona slug per author "
            "(e.g. 'evenson_brian_personal'). Default behavior; "
            "mutually exclusive with --persona."
        ),
    )
    persona_group.add_argument(
        "--persona",
        help=(
            "Lump every acquired story under one persona slug. "
            "Rarely useful for impostor work; included per spec."
        ),
    )
    p.add_argument("--register", required=True,
                   help="Manifest register; default fiction value: literary_horror.")
    p.add_argument("--register-match",
                   choices=["high", "medium", "low"], default="high")
    p.add_argument("--topic-match",
                   choices=["high", "medium", "low"], default="medium")
    p.add_argument("--consent-status", required=True,
                   choices=[
                       "public_record", "cc_licensed", "fair_use_research",
                       "author_consent", "undocumented",
                   ])
    p.add_argument("--era",
                   choices=[
                       "pre_chatgpt", "pre_ai_widespread",
                       "post_ai_widespread", "undated",
                   ],
                   default="pre_chatgpt")
    p.add_argument("--impostor-for", nargs="+", required=True,
                   help=(
                       "Persona slug(s) this impostor pool serves "
                       "(required; the schema rejects empty)."
                   ))
    p.add_argument("--filter-author", nargs="+", default=[],
                   help=(
                       "Restrict to stories by these authors "
                       "(case-insensitive substring match). Empty = "
                       "every author in the archive."
                   ))
    p.add_argument("--since", help="Inclusive lower-bound date (YYYY-MM-DD).")
    p.add_argument("--until", help="Inclusive upper-bound date (YYYY-MM-DD).")
    p.add_argument("--max-stories", type=int, default=30,
                   help="Cap on acquired stories per run (default: 30).")
    p.add_argument("--output-dir",
                   help=(
                       "Where per-author subdirs live. Defaults to "
                       "<baselines>/impostors/<register>/<magazine>/."
                   ))
    p.add_argument("--emit-manifest",
                   help=(
                       "Where to write the draft manifest JSONL. "
                       "Defaults to <output-dir>/draft_manifest.jsonl."
                   ))
    p.add_argument("--out", help="Write summary report (JSON) here.")
    p.add_argument("--rate-limit", type=float, default=2.0,
                   help="Seconds between same-host requests (default 2.0).")
    p.add_argument("--user-agent",
                   help="Override the User-Agent header.")
    p.add_argument("--dry-run", action="store_true",
                   help="Inventory what would be acquired without writing.")
    p.add_argument("--allow-public-output", action="store_true",
                   help=(
                       "Allow writing outside ai-prose-baselines-private/. "
                       "Acquired prose is voice-cloning input; only set "
                       "for non-personal corpora."
                   ))
    p.add_argument("--allow-non-prose", action="store_true",
                   help="Skip preprocessing's corpus-hygiene gate.")
    p.add_argument("--strip-rules",
                   help="Comma-separated subset of preprocessing rules.")
    p.add_argument("--strip-aggressive", action="store_true",
                   help="Apply aggressive (link/citation) strip rules.")
    return p


def run(
    args: argparse.Namespace, fetcher: ac.Fetcher | None = None,
) -> int:
    """Top-level driver. Returns shell-style exit code."""
    if fetcher is None:
        fetcher = ac.make_requests_fetcher(
            version=SCRAPER_VERSION,
            rate_limit_seconds=args.rate_limit,
            user_agent=getattr(args, "user_agent", None) or None,
        )

    config = MAGAZINE_MODULES[args.magazine]

    since = ac.parse_iso_date(args.since) if args.since else None
    until = ac.parse_iso_date(args.until) if args.until else None
    if args.since and not since:
        sys.stderr.write(f"  warning: could not parse --since={args.since}\n")
    if args.until and not until:
        sys.stderr.write(f"  warning: could not parse --until={args.until}\n")

    # Output paths.
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
    else:
        output_dir = ac.default_output_dir(
            register=args.register, author_slug=config.name,
        )
    if args.emit_manifest:
        manifest_path = Path(args.emit_manifest).expanduser()
    else:
        manifest_path = output_dir / "draft_manifest.jsonl"

    paths_to_check: list[Path] = [output_dir, manifest_path]
    if args.out:
        paths_to_check.append(Path(args.out).expanduser())
    ac.check_output_privacy(
        paths_to_check, allow_public=args.allow_public_output, tool=TOOL_NAME,
    )

    today = _dt.date.today().isoformat()
    acquired_via = f"acquire_magazine_{config.name}_{today}"

    persona_from_author = (
        args.persona_from_author or args.persona is None
    )

    options = ProcessOptions(
        persona=args.persona,
        persona_from_author=persona_from_author,
        impostor_for=list(args.impostor_for or []),
        register=args.register,
        register_match=args.register_match,
        topic_match=args.topic_match,
        consent_status=args.consent_status,
        era=args.era,
        filter_author=list(args.filter_author or []),
        since=since,
        until=until,
        output_dir=output_dir,
        manifest_path=manifest_path,
        rate_limit=args.rate_limit,
        max_stories=args.max_stories,
        dry_run=args.dry_run,
        allow_non_prose=args.allow_non_prose,
        strip_rules=args.strip_rules,
        strip_aggressive=args.strip_aggressive,
        acquired_via=acquired_via,
    )
    summary = ac.RunSummary(
        draft_manifest_path=str(manifest_path) if not args.dry_run else None,
        output_dir=str(output_dir),
    )

    sys.stderr.write(
        f"Acquiring from {config.name} into {output_dir}\n"
        f"Filter authors: {options.filter_author or '(all)'}\n"
        f"Impostor for: {options.impostor_for}\n"
    )

    acquire_magazine(config, fetcher, options, summary)

    sys.stderr.write("\n" + summary.render_stderr())
    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if summary.acquired == 0 and not summary.skip_log:
        sys.stderr.write(
            "No stories acquired. Verify the magazine choice, "
            "filter-author values, and date window.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
