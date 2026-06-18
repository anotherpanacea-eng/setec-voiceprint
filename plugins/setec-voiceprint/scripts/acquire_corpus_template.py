#!/usr/bin/env python3
"""acquire_corpus_template.py — scaffold for a new acquisition script.

This is a STARTING POINT, not a working script. Copy it to
``acquire_<source>.py``, fill in the four ``TODO(LLM)`` markers
below, and you have a working acquisition pipeline that:

  * Honors the manifest schema for impostor-pool entries
  * Respects the privacy guard
  * Uses the same preprocessing gate as identity baselines
  * Dedupes by content hash within the output directory
  * Emits a draft manifest the validator accepts
  * Surfaces a run summary with the standard skip-reason buckets

Read ``references/acquire-corpus-pattern.md`` for the full pattern
and worked examples. Read ``scripts/acquire_blog.py`` and
``scripts/acquire_blogger_takeout.py`` for two reference instances.

How to use:

  1. Copy this file: ``cp acquire_corpus_template.py acquire_<source>.py``
  2. Replace ``SOURCE_NAME`` and ``TOOL_NAME`` constants below.
  3. Fill the four ``TODO(LLM)`` markers — typically with help from
     an LLM that has read this file plus the pattern doc.
  4. Wire any source-specific CLI flags into ``build_arg_parser``.
  5. Add fixtures under ``scripts/test_data/acquire_<source>_fixture/``
     and tests under ``scripts/tests/test_acquire_<source>.py``
     mirroring the existing acquisition tests.
  6. Run ``--dry-run`` against your source first; verify the items
     get discovered correctly before spending the full budget.
  7. Run the manifest validator on the emitted draft to catch any
     impostor-required-field gaps.

If your source is reusable across SETEC users, promote the finished
script to a permanent place in the framework (file an issue or PR).
If it's a one-off, that's fine too — the pattern works either way.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402


# ============== EDIT BEFORE FIRST USE ============================
#
# Replace these with values appropriate for your source. ``SOURCE_NAME``
# becomes part of ``acquired_via`` (e.g. ``acquire_slack_2026-05-09``);
# ``TOOL_NAME`` is the argparse program name.

SOURCE_NAME = "TODO_SOURCE"  # e.g. "slack" / "obsidian" / "mbox"
TOOL_NAME = "acquire_corpus_template"
SCRAPER_VERSION = "0.1"
TASK_SURFACE = "voice_coherence_acquisition"

# =================================================================


# --------------- Source-specific dataclasses ---------------------


@dataclass
class ItemMeta:
    """One discovered item from the source.

    Discovery returns iterables of these. Add source-specific fields
    as needed (e.g. message ID, page slug, channel name) — the
    extraction stage uses whatever discovery surfaces.
    """
    locator: str  # URL or file path; whatever extract_one needs
    title: str = ""
    author: str = ""
    date: _dt.date | None = None
    # Source-specific metadata the per-item extractor uses. Free dict
    # so you don't have to fight the type system.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessOptions:
    """User-facing options + resolved defaults.

    Built once from CLI args at the top of ``run()``. Keeps the
    discovery / extraction helpers as pure functions for testability.
    """
    persona: str
    impostor_for: list[str]
    register: str
    register_match: str
    topic_match: str
    consent_status: str
    era: str
    since: _dt.date | None
    until: _dt.date | None
    output_dir: Path
    manifest_path: Path
    max_items: int
    dry_run: bool
    allow_non_prose: bool
    strip_rules: str | None
    strip_aggressive: bool
    acquired_via: str
    # Source-specific extras (archive path, API key, custom selectors).
    # Extend as needed.
    source_extras: dict[str, Any] = field(default_factory=dict)


# --------------- Source-specific helpers (TODO markers) ----------


def discover_items(
    source: str,
    options: ProcessOptions,
    fetcher: ac.Fetcher | None = None,
) -> Iterable[ItemMeta]:
    """List every candidate item in the source.

    TODO(LLM): Implement this for your source. Common shapes:

      * Network: fetch the source's index page (or RSS, or sitemap),
        parse links, yield one ItemMeta per item. Use the ``fetcher``
        argument so tests can inject FixtureFetcher.

      * Archive: read the archive's index file (e.g. JSON manifest
        for a Slack export, .csv for a Notion export). Yield items.

      * File system: walk the directory, glob the relevant
        extension, yield one ItemMeta per file. Use ``Path.rglob`` or
        ``os.walk``.

    Parameters:
      source — the positional argument the user passed (URL, dir, etc.)
      options — for date-window filtering when discovery has dates
      fetcher — only when the source is network-bound

    Yield ItemMeta instances. Apply early date-window filtering when
    cheap (sitemap lastmod, file mtime); leave hard-to-determine
    dates for the extract_one pass to compute.

    REPLACE THE STUB BELOW.
    """
    raise NotImplementedError(
        "Implement discover_items() for your source. "
        "See references/acquire-corpus-pattern.md and the existing "
        "acquire_blog.py / acquire_blogger_takeout.py for examples."
    )


def extract_one(
    item: ItemMeta,
    source: str,
    options: ProcessOptions,
    fetcher: ac.Fetcher | None = None,
) -> tuple[str, str, str, _dt.date | None]:
    """Given one item, return ``(body_text, title, author, date)``.

    TODO(LLM): Implement this for your source. The body MUST be plain
    text — convert HTML / Markdown / JSON / PDF here, not later.

    Common conversion paths:

      * HTML: ``ac.html_to_text(html, content_selector=...)``
      * PDF text layer: ``pypdf.PdfReader(path).pages[i].extract_text()``
      * PDF image: shell out to ``ocrmypdf`` (see pdf_extract.py)
      * Markdown: read file, optionally strip front-matter, run
        through ``ac.preprocess_text`` later in the caller's pipeline.
      * JSON message: pull the body field; handle quote / mention markers.

    Return ``("", "", "", None)`` if the item should be silently
    skipped (the caller treats short or empty body as a parse-error
    skip). Raise an exception if the failure should abort the run.

    REPLACE THE STUB BELOW.
    """
    raise NotImplementedError(
        "Implement extract_one() for your source. "
        "Return (body_text, title, author, date)."
    )


def build_acquired_via_tag() -> str:
    """Return the ``acquired_via`` tag for emitted manifest entries.

    Convention: ``acquire_<source>_<YYYY-MM-DD>``. The date stamp
    lets a future audit reconstruct when each pool entry was
    acquired. Override here if you want a different shape.
    """
    return f"acquire_{SOURCE_NAME}_{_dt.date.today().isoformat()}"


# --------------- Per-item processing (mostly shared) -------------


def process_one_item(
    item: ItemMeta,
    body_text: str,
    title: str,
    author: str,
    date: _dt.date | None,
    *,
    options: ProcessOptions,
    summary: ac.RunSummary,
) -> Optional[ac.AcquiredPiece]:
    """Run one item's extracted text through preprocess → hash →
    dedupe → AcquiredPiece. Returns the piece on success, ``None``
    on skip; mutates ``summary`` to record the outcome.

    This is shared logic across acquisition scripts; you usually
    don't need to edit it.
    """
    if options.since and date and date < options.since:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="out-of-window-before",
            url=item.locator,
            detail=date.isoformat() if date else "",
        )
        return None
    if options.until and date and date > options.until:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="out-of-window-after",
            url=item.locator,
            detail=date.isoformat() if date else "",
        )
        return None

    if not body_text or len(body_text.strip()) < 200:
        summary.skipped_parse_error += 1
        summary.log_skip(
            reason="empty-body",
            url=item.locator,
            detail=f"len={len(body_text)}",
        )
        return None

    cleaned, prep_meta = ac.preprocess_text(
        body_text,
        rules=options.strip_rules,
        allow_non_prose=options.allow_non_prose,
        strip_aggressive=options.strip_aggressive,
    )
    if not cleaned or len(cleaned.strip()) < 200:
        summary.skipped_parse_error += 1
        summary.log_skip(
            reason="empty-after-preprocess",
            url=item.locator,
            detail=f"raw={len(body_text)} clean={len(cleaned)}",
        )
        return None

    final_title = title or item.title or "untitled"
    final_author = author or item.author or "Unknown"

    piece = ac.AcquiredPiece(
        title=final_title,
        author=final_author,
        persona=options.persona,
        register=options.register,
        date_written=date or item.date,
        source_url=item.locator,
        cleaned_text=cleaned,
        raw_byte_length=len(body_text.encode("utf-8")),
        preprocessing_meta=prep_meta,
        acquired_via=options.acquired_via,
        consent_status=options.consent_status,
        era=options.era,
        register_match=options.register_match,
        topic_match=options.topic_match,
        impostor_for=list(options.impostor_for),
    )

    existing = ac.content_hash_already_present(
        piece.content_hash, options.output_dir,
    )
    if existing is not None:
        summary.skipped_duplicate += 1
        summary.log_skip(
            reason="duplicate-hash",
            url=item.locator,
            detail=str(existing),
        )
        sys.stderr.write(
            f"  duplicate hash; skipping {item.locator} "
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
    """Write piece + manifest entry. No-op for dry-run."""
    if options.dry_run:
        sys.stderr.write(
            f"  [dry-run] would write {piece.filename_stem()} "
            f"({piece.word_count} words)\n"
        )
        summary.acquired += 1
        return
    text_path, _ = ac.write_piece(
        piece, output_dir=options.output_dir,
        scraper_version=SCRAPER_VERSION,
    )
    entry = ac.compose_manifest_entry(
        piece, text_path=text_path,
        manifest_relative_to=options.manifest_path.parent,
    )
    ac.append_manifest_entry(options.manifest_path, entry)
    summary.acquired += 1
    sys.stderr.write(
        f"  acquired {text_path.name} ({piece.word_count} words)\n"
    )


# --------------- CLI --------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the standard CLI surface.

    TODO(LLM): Add source-specific flags here as needed (e.g.
    ``--archive-path``, ``--api-token``, ``--channel``, custom
    selectors). Keep the standard flags below — they're what every
    acquisition script in the framework supports.
    """
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Acquire prose from a custom source into the impostor pool. "
            "See references/acquire-corpus-pattern.md for the pipeline "
            "and adaptation guide."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "source",
        help=(
            "Positional source identifier. URL / directory / archive "
            "path — whatever discover_items() expects."
        ),
    )

    # Persona / impostor metadata.
    p.add_argument("--persona", required=False,
                   help="Persona slug for emitted entries.")
    p.add_argument("--author",
                   help="Author display name (used in manifest entries).")
    p.add_argument("--impostor-for", nargs="+", required=True,
                   help=(
                       "Persona slug(s) this impostor pool serves "
                       "(required; the schema rejects empty)."
                   ))
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

    # Date window + max.
    p.add_argument("--since", help="Inclusive lower-bound date (YYYY-MM-DD).")
    p.add_argument("--until", help="Inclusive upper-bound date (YYYY-MM-DD).")
    p.add_argument("--max-items", type=int, default=100,
                   help="Cap on acquired items per run (default 100).")

    # Output paths.
    p.add_argument("--output-dir",
                   help=(
                       "Where to write .txt and .meta.json files. "
                       "Defaults to <baselines>/impostors/<register>/<persona>/."
                   ))
    p.add_argument("--emit-manifest",
                   help=(
                       "Where to write draft manifest JSONL. Defaults "
                       "to <output-dir>/draft_manifest.jsonl."
                   ))
    p.add_argument("--out", help="Write summary report (JSON) here.")

    # Network-bound scripts only — comment out for local-source scripts.
    p.add_argument("--rate-limit", type=float, default=2.0,
                   help="Seconds between same-host requests (default 2.0).")
    ac.add_user_agent_arg(p)

    # Behavior.
    p.add_argument("--dry-run", action="store_true",
                   help="Inventory what would be acquired without writing.")
    p.add_argument("--allow-public-output", action="store_true",
                   help=(
                       "Allow writing outside ai-prose-baselines-private/. "
                       "Acquired prose is voice-cloning input; only set "
                       "for non-personal corpora."
                   ))

    # Preprocessing pass-throughs.
    p.add_argument("--allow-non-prose", action="store_true",
                   help="Skip preprocessing's corpus-hygiene gate.")
    p.add_argument("--strip-rules",
                   help=(
                       "Comma-separated subset of preprocessing rules. "
                       "Default: all standard rules."
                   ))
    p.add_argument("--strip-aggressive", action="store_true",
                   help="Apply aggressive (link/citation) strip rules.")

    # TODO(LLM): Add source-specific flags here. Examples:
    #   p.add_argument("--archive-path", help="Path to the export archive.")
    #   p.add_argument("--filter-channel", action="append", default=[])
    #   p.add_argument("--api-token-file", help="Path to API token file.")

    return p


def parse_options(args: argparse.Namespace) -> ProcessOptions:
    """Build a ProcessOptions from parsed CLI args.

    Edit this when you add source-specific flags. The standard
    fields below should remain.
    """
    persona = args.persona
    if not persona:
        # TODO(LLM): If your source has a natural persona derivation
        # (e.g. archive author name, file system root name), do it
        # here. Default fallback: prompt the user to provide --persona.
        if args.author:
            persona = ac.author_to_persona_slug(args.author)
        else:
            persona = "unknown_personal"

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

    return ProcessOptions(
        persona=persona,
        impostor_for=list(args.impostor_for or []),
        register=args.register,
        register_match=args.register_match,
        topic_match=args.topic_match,
        consent_status=args.consent_status,
        era=args.era,
        since=ac.parse_iso_date(args.since) if args.since else None,
        until=ac.parse_iso_date(args.until) if args.until else None,
        output_dir=output_dir,
        manifest_path=manifest_path,
        max_items=args.max_items,
        dry_run=args.dry_run,
        allow_non_prose=args.allow_non_prose,
        strip_rules=args.strip_rules,
        strip_aggressive=args.strip_aggressive,
        acquired_via=build_acquired_via_tag(),
    )


def run(
    args: argparse.Namespace,
    fetcher: ac.Fetcher | None = None,
) -> int:
    """Top-level driver. Returns shell-style exit code.

    If your source is network-bound, construct a default
    ``RequestsFetcher`` here when ``fetcher`` is None. If it's
    local-only (file system, archive), leave ``fetcher`` as None
    throughout.
    """
    options = parse_options(args)

    # Privacy guard up front.
    paths_to_check: list[Path] = [options.output_dir, options.manifest_path]
    if args.out:
        paths_to_check.append(Path(args.out).expanduser())
    ac.check_output_privacy(
        paths_to_check, allow_public=args.allow_public_output, tool=TOOL_NAME,
    )

    # Network sources: construct the production fetcher here.
    # Comment out for local-only sources.
    if fetcher is None and getattr(args, "rate_limit", None) is not None:
        try:
            fetcher = ac.make_requests_fetcher(
                version=SCRAPER_VERSION,
                rate_limit_seconds=args.rate_limit,
                user_agent=getattr(args, "user_agent", None) or None,
            )
        except RuntimeError:
            # ``requests`` isn't installed; OK if your source is local-only.
            fetcher = None

    summary = ac.RunSummary(
        draft_manifest_path=str(options.manifest_path) if not options.dry_run else None,
        output_dir=str(options.output_dir),
    )

    sys.stderr.write(
        f"Acquiring from {args.source} into {options.output_dir}\n"
        f"Persona: {options.persona} (impostor_for: {options.impostor_for})\n"
    )

    for item in discover_items(args.source, options, fetcher=fetcher):
        if summary.acquired >= options.max_items:
            break
        try:
            body_text, title, author, date = extract_one(
                item, args.source, options, fetcher=fetcher,
            )
        except Exception as exc:
            summary.skipped_parse_error += 1
            summary.log_skip(
                reason="extract-error",
                url=item.locator,
                detail=f"{type(exc).__name__}: {exc}",
            )
            continue

        piece = process_one_item(
            item, body_text, title, author, date,
            options=options, summary=summary,
        )
        if piece is not None:
            emit_piece(piece, options=options, summary=summary)

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
            "No items acquired. Verify discover_items() finds the source "
            "and the date / max-item filters aren't excluding everything.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
