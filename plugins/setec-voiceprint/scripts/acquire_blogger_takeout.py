#!/usr/bin/env python3
"""acquire_blogger_takeout.py - import Google Takeout Blogger exports.

Offline sibling to ``acquire_blog.py``. It reads a Google Takeout
Blogger export directory, or a single Blogger ``feed.atom`` file, and
writes:

  1. One cleaned ``.txt`` file per acquired post.
  2. One ``.meta.json`` sidecar per post.
  3. One draft manifest JSONL with ``corpus_role: impostor`` entries.

This path is intentionally separate from the live blog scraper:
Blogger Takeout already contains the full Atom payload, so fetching the
public site would be slower, less complete, and less respectful of the
archive the author actually shared.

By default the importer only reads ``Blogger/Blogs/*/feed.atom`` and
ignores ``Blogger/Comments/*/feed.atom``. Comment feeds are a different
register and can contain conversation context or other people's prose;
pass ``--include-comments`` only when that is intentional.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402


TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "acquire_blogger_takeout"
SCRAPER_VERSION = "1.0"


_BLOGGER_POST_RE = re.compile(r"\.post-([A-Za-z0-9_-]+)$")
_LOCATOR_ONLY_RE = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)


@dataclass
class BloggerEntry:
    """One entry from a Blogger Takeout Atom feed."""

    entry_id: str
    short_id: str
    title: str
    content_html: str
    published: _dt.date | None
    updated: str
    source_url: str
    labels: list[str]
    feed_path: Path


@dataclass
class ProcessOptions:
    """User-facing options threaded through per-entry processing."""

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
    min_words: int
    max_posts: int
    output_dir: Path
    manifest_path: Path
    dry_run: bool
    allow_non_prose: bool
    strip_rules: str | None
    strip_aggressive: bool
    acquired_via: str


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[1]
    return tag


def _child_text(elem: ET.Element, name: str) -> str:
    for child in elem:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _parse_blogger_date(text: str) -> _dt.date | None:
    """Parse Blogger timestamps without requiring python-dateutil."""

    if not text:
        return None
    # Blogger emits full RFC3339 timestamps. The date portion is enough
    # for corpus windows and avoids a hard dependency on dateutil.
    if len(text) >= 10:
        parsed = ac.parse_iso_date(text[:10])
        if parsed is not None:
            return parsed
    return ac.parse_iso_date(text)


def _entry_short_id(entry_id: str) -> str:
    m = _BLOGGER_POST_RE.search(entry_id)
    if m:
        return m.group(1)[-16:]
    slug = ac.slugify(entry_id, max_length=32)
    return slug[-16:] if len(slug) > 16 else slug


def _alternate_link(entry: ET.Element) -> str:
    for child in entry:
        if _local_name(child.tag) != "link":
            continue
        rel = child.attrib.get("rel", "alternate")
        href = child.attrib.get("href", "")
        if href and rel == "alternate":
            return href
    return ""


def parse_blogger_feed(feed_path: Path) -> tuple[str, list[BloggerEntry]]:
    """Parse a Blogger Takeout Atom feed.

    Returns ``(feed_title, entries)``. Empty-title posts are preserved:
    Blogger exports sometimes contain real posts whose ``<title>`` is
    blank, so the caller supplies a stable post-ID fallback rather than
    dropping them.
    """

    try:
        root = ET.parse(feed_path).getroot()
    except (ET.ParseError, OSError) as e:
        raise RuntimeError(f"Could not parse Blogger feed {feed_path}: {e}") from e

    feed_title = _child_text(root, "title") or feed_path.parent.name.strip()
    entries: list[BloggerEntry] = []
    for elem in root:
        if _local_name(elem.tag) != "entry":
            continue
        entry_id = _child_text(elem, "id")
        if not entry_id:
            continue
        content_html = _child_text(elem, "content")
        if not content_html:
            # Some Atom exports use summary for short entries. Keep the
            # fallback conservative; empty entries still skip later.
            content_html = _child_text(elem, "summary")
        labels = [
            child.attrib.get("term", "").strip()
            for child in elem
            if _local_name(child.tag) == "category"
            and child.attrib.get("term", "").strip()
        ]
        source_url = _alternate_link(elem) or f"blogger-takeout:{entry_id}"
        entries.append(BloggerEntry(
            entry_id=entry_id,
            short_id=_entry_short_id(entry_id),
            title=_child_text(elem, "title"),
            content_html=content_html,
            published=_parse_blogger_date(_child_text(elem, "published")),
            updated=_child_text(elem, "updated"),
            source_url=source_url,
            labels=labels,
            feed_path=feed_path,
        ))
    return feed_title, entries


def discover_blog_feeds(path: Path, *, include_comments: bool = False) -> list[Path]:
    """Resolve a Takeout root or feed file into Blogger feed paths."""

    path = path.expanduser()
    if path.is_file():
        if path.name != "feed.atom":
            raise ValueError(f"Expected a Blogger feed.atom file, got {path}")
        parts = {p.lower() for p in path.parts}
        if "comments" in parts and not include_comments:
            raise ValueError(
                "Refusing a Blogger Comments feed without --include-comments"
            )
        return [path]
    if not path.exists():
        raise ValueError(f"Path does not exist: {path}")
    if not path.is_dir():
        raise ValueError(f"Path is neither directory nor file: {path}")

    blog_root = path / "Blogger" / "Blogs"
    if blog_root.exists():
        feeds = sorted(blog_root.glob("*/feed.atom"))
    else:
        feeds = sorted(path.glob("**/feed.atom"))
        if not include_comments:
            feeds = [
                p for p in feeds
                if "comments" not in {part.lower() for part in p.parts}
            ]
    if not feeds:
        raise ValueError(f"No Blogger blog feed.atom files found under {path}")
    return feeds


def _default_title(entry: BloggerEntry) -> str:
    return entry.title or f"untitled-{entry.short_id}"


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _looks_like_locator_only(text: str) -> bool:
    """True when an entry body is only a URL or locator-like string."""

    stripped = text.strip()
    return bool(stripped and _LOCATOR_ONLY_RE.match(stripped))


def _make_piece(
    entry: BloggerEntry,
    *,
    title: str,
    cleaned_text: str,
    prep_meta: dict,
    options: ProcessOptions,
) -> ac.AcquiredPiece:
    return ac.AcquiredPiece(
        title=title,
        author=options.author,
        persona=options.persona,
        register=options.register,
        date_written=entry.published,
        source_url=entry.source_url,
        cleaned_text=cleaned_text,
        raw_byte_length=len(entry.content_html.encode("utf-8")),
        preprocessing_meta=prep_meta,
        acquired_via=options.acquired_via,
        consent_status=options.consent_status,
        era=options.era,
        register_match=options.register_match,
        topic_match=options.topic_match,
        impostor_for=list(options.impostor_for),
    )


def _avoid_filename_collision(
    piece: ac.AcquiredPiece,
    *,
    entry: BloggerEntry,
    output_dir: Path,
) -> ac.AcquiredPiece:
    """Avoid overwriting same-date/same-title Blogger posts."""

    stem = piece.filename_stem()
    if not (output_dir / f"{stem}.txt").exists():
        return piece
    base_title = piece.title or _default_title(entry)
    title = f"{base_title}-{entry.short_id}"
    candidate = replace(piece, title=title)
    suffix = 2
    while (output_dir / f"{candidate.filename_stem()}.txt").exists():
        candidate = replace(piece, title=f"{title}-{suffix}")
        suffix += 1
    return candidate


def process_entry(
    entry: BloggerEntry,
    *,
    options: ProcessOptions,
    summary: ac.RunSummary,
) -> ac.AcquiredPiece | None:
    """Extract, clean, filter, hash, and dedupe one Blogger entry."""

    if options.since and entry.published and entry.published < options.since:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="out-of-window-before",
            url=entry.source_url,
            detail=entry.published.isoformat(),
        )
        return None
    if options.until and entry.published and entry.published > options.until:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="out-of-window-after",
            url=entry.source_url,
            detail=entry.published.isoformat(),
        )
        return None

    if _looks_like_locator_only(entry.content_html):
        summary.skipped_parse_error += 1
        summary.log_skip(
            reason="locator-only-body",
            url=entry.source_url,
            detail=entry.content_html.strip()[:120],
        )
        return None

    try:
        body_text, html_title = ac.html_to_text(entry.content_html)
    except Exception as e:
        summary.skipped_parse_error += 1
        summary.log_skip(reason="parse-error", url=entry.source_url, detail=str(e))
        return None
    if not body_text or len(body_text) < 50:
        summary.skipped_parse_error += 1
        summary.log_skip(
            reason="empty-body",
            url=entry.source_url,
            detail=f"len={len(body_text)}",
        )
        return None

    cleaned_text, prep_meta = ac.preprocess_text(
        body_text,
        rules=options.strip_rules,
        allow_non_prose=options.allow_non_prose,
        strip_aggressive=options.strip_aggressive,
    )
    wc = _word_count(cleaned_text)
    if not cleaned_text or len(cleaned_text) < 50:
        summary.skipped_parse_error += 1
        summary.log_skip(
            reason="empty-after-preprocess",
            url=entry.source_url,
            detail=f"raw_len={len(body_text)} clean_len={len(cleaned_text)}",
        )
        return None
    if wc < options.min_words:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="below-min-words",
            url=entry.source_url,
            detail=f"{wc} < {options.min_words}",
        )
        return None

    title = entry.title or html_title or _default_title(entry)
    piece = _make_piece(
        entry,
        title=title,
        cleaned_text=cleaned_text,
        prep_meta=prep_meta,
        options=options,
    )
    existing = ac.content_hash_already_present(piece.content_hash, options.output_dir)
    if existing is not None:
        summary.skipped_duplicate += 1
        summary.log_skip(
            reason="duplicate-hash",
            url=entry.source_url,
            detail=str(existing),
        )
        return None

    summary.record_strip_meta(prep_meta)
    summary.total_cleaned_words += piece.word_count
    return _avoid_filename_collision(piece, entry=entry, output_dir=options.output_dir)


def emit_piece(
    piece: ac.AcquiredPiece,
    *,
    entry: BloggerEntry,
    options: ProcessOptions,
    summary: ac.RunSummary,
) -> None:
    """Write piece, sidecar metadata, and one draft manifest row."""

    if options.dry_run:
        sys.stderr.write(
            f"  [dry-run] would write {piece.filename_stem()} "
            f"({piece.word_count} words)\n"
        )
        summary.acquired += 1
        return

    text_path, meta_path = ac.write_piece(
        piece, output_dir=options.output_dir, scraper_version=SCRAPER_VERSION,
    )
    # Keep Blogger-specific provenance in the sidecar, not the manifest:
    # the manifest schema remains stable and validator-clean.
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        meta = {}
    meta["blogger_takeout"] = {
        "entry_id": entry.entry_id,
        "short_id": entry.short_id,
        "updated": entry.updated or None,
        "labels": entry.labels,
        "feed_path": str(entry.feed_path),
    }
    meta_path.write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest_entry = ac.compose_manifest_entry(
        piece,
        text_path=text_path,
        manifest_relative_to=options.manifest_path.parent,
    )
    ac.append_manifest_entry(options.manifest_path, manifest_entry)
    summary.acquired += 1
    sys.stderr.write(f"  acquired {text_path.name} ({piece.word_count} words)\n")


def acquire_feeds(
    feeds: Iterable[Path],
    *,
    options: ProcessOptions,
    summary: ac.RunSummary,
) -> None:
    for feed_path in feeds:
        feed_title, entries = parse_blogger_feed(feed_path)
        sys.stderr.write(
            f"Reading {feed_path} ({feed_title or 'untitled feed'}, "
            f"{len(entries)} entries)\n"
        )
        for entry in entries:
            if options.max_posts and summary.acquired >= options.max_posts:
                return
            piece = process_entry(entry, options=options, summary=summary)
            if piece is not None:
                emit_piece(piece, entry=entry, options=options, summary=summary)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Import posts from a Google Takeout Blogger export into a "
            "private SETEC impostor corpus."
        )
    )
    p.add_argument(
        "takeout_path",
        help="Google Takeout root, Blogger directory, or a Blogger feed.atom file.",
    )

    p.add_argument("--persona", required=True,
                   help="Persona slug for emitted entries.")
    p.add_argument("--author", required=True,
                   help="Author display name for emitted entries.")
    p.add_argument("--impostor-for", nargs="+", required=True,
                   help="Persona slug(s) this impostor serves.")
    p.add_argument("--register", required=True,
                   help="Manifest register; e.g. blog_essay.")
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

    p.add_argument("--since", help="Inclusive lower-bound date (YYYY-MM-DD).")
    p.add_argument("--until", help="Inclusive upper-bound date (YYYY-MM-DD).")
    p.add_argument("--min-words", type=int, default=250,
                   help="Skip entries below this cleaned word count (default: 250).")
    p.add_argument("--max-posts", type=int, default=0,
                   help="Maximum posts to acquire; 0 means no cap.")

    p.add_argument("--output-dir",
                   help=("Where to write .txt and .meta.json files. Defaults "
                         "to <baselines>/impostors/<register>/<persona>."))
    p.add_argument("--emit-manifest",
                   help=("Where to write draft manifest JSONL. Defaults to "
                         "<output-dir>/draft_manifest.jsonl."))
    p.add_argument("--out",
                   help="Optional JSON summary report path.")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would be acquired without writing files.")
    p.add_argument("--include-comments", action="store_true",
                   help=("Allow Blogger/Comments feeds. Default excludes them "
                         "because comments are a different register."))
    p.add_argument("--allow-public-output", action="store_true",
                   help=("Allow output outside ai-prose-baselines-private. "
                         "Use only for non-personal corpora."))

    p.add_argument("--allow-non-prose", action="store_true",
                   help="Pass through preprocessing non-prose guard.")
    p.add_argument("--strip-rules",
                   help="Comma-separated preprocessing rule names to apply.")
    p.add_argument("--strip-aggressive", action="store_true",
                   help="Enable aggressive URL/citation/alt-text stripping.")
    return p


def run(args: argparse.Namespace) -> int:
    try:
        feeds = discover_blog_feeds(
            Path(args.takeout_path), include_comments=args.include_comments,
        )
    except ValueError as e:
        sys.stderr.write(f"{e}\n")
        return 2

    since = ac.parse_iso_date(args.since)
    until = ac.parse_iso_date(args.until)
    if args.since and not since:
        sys.stderr.write(f"  warning: could not parse --since={args.since}\n")
    if args.until and not until:
        sys.stderr.write(f"  warning: could not parse --until={args.until}\n")

    output_dir = (
        Path(args.output_dir).expanduser()
        if args.output_dir else
        ac.default_output_dir(args.register, args.persona)
    )
    manifest_path = (
        Path(args.emit_manifest).expanduser()
        if args.emit_manifest else
        output_dir / "draft_manifest.jsonl"
    )
    paths_to_check = [output_dir, manifest_path]
    if args.out:
        paths_to_check.append(Path(args.out).expanduser())
    ac.check_output_privacy(
        paths_to_check, allow_public=args.allow_public_output, tool=TOOL_NAME,
    )

    today = _dt.date.today().isoformat()
    options = ProcessOptions(
        persona=args.persona,
        author=args.author,
        impostor_for=list(args.impostor_for or []),
        register=args.register,
        register_match=args.register_match,
        topic_match=args.topic_match,
        consent_status=args.consent_status,
        era=args.era,
        since=since,
        until=until,
        min_words=max(0, int(args.min_words)),
        max_posts=max(0, int(args.max_posts)),
        output_dir=output_dir,
        manifest_path=manifest_path,
        dry_run=args.dry_run,
        allow_non_prose=args.allow_non_prose,
        strip_rules=args.strip_rules,
        strip_aggressive=args.strip_aggressive,
        acquired_via=f"acquire_blogger_takeout_{today}",
    )
    summary = ac.RunSummary(
        draft_manifest_path=str(manifest_path) if not args.dry_run else None,
        output_dir=str(output_dir),
    )

    sys.stderr.write(
        f"Importing Blogger Takeout into {output_dir}\n"
        f"Persona: {options.persona} (impostor_for: {options.impostor_for})\n"
        f"Feeds: {len(feeds)}\n"
    )
    acquire_feeds(feeds, options=options, summary=summary)

    sys.stderr.write("\n" + summary.render_stderr())
    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if summary.acquired == 0:
        sys.stderr.write(
            "No posts acquired. Check the feed path, date window, "
            "and --min-words threshold.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
