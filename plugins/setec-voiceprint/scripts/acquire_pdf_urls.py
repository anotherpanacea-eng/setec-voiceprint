#!/usr/bin/env python3
"""acquire_pdf_urls.py — acquire prose from a list of remote PDF URLs.

A generic remote-PDF acquirer: it reads an operator-curated list of PDF
URLs, downloads each, extracts its text layer, and writes it into the
impostor pool. It serves the PDF-native distant genres — grant proposals
(OpenGrants / NEH / NIH PDFs) and expert affidavits (Climate Case Chart /
PTAB PDFs) — and any other PDF source; the operator picks ``--register``
and supplies the matching URL list. Thin per-source list-builders (e.g. an
OpenGrants YAML -> Zenodo-PDF resolver, an NEH FOIA scraper) are follow-ups
that *feed* this acquirer's URL list.

Input (``urls_file``): one entry per line, each either a JSON object
``{"url": "...", "title": "...", "author": "...", "date": "YYYY-MM-DD"}``
(only ``url`` is required) or a bare PDF URL. Blank lines and ``#``
comments are skipped.

PDF text comes from ``acquisition_core.pdf_text_from_bytes`` (which reuses
``pdf_extract.extract_text_layer`` / pypdf). Image-only PDFs yield no text
and are skipped — run ``pdf_extract.py``'s OCR pass separately for those.
Zenodo/Figshare *record* URLs are not direct PDFs; the URL list must point
at direct PDF links. Verify with ``--dry-run`` before a bulk pull.

Privacy: output goes under ``ai-prose-baselines-private/impostors/
<register>/<persona>/`` and the privacy guard refuses paths outside any
directory named ``ai-prose-baselines-private``. Robots is honored on each
download.

Usage:

    python3 scripts/acquire_pdf_urls.py grant_pdf_urls.jsonl \\
        --persona opengrants \\
        --impostor-for argscope_grant_proposal \\
        --register grant_proposal \\
        --consent-status cc_licensed \\
        --era pre_chatgpt \\
        --min-words 1500 --max-items 300

See ``internal/SPEC_acquire_pdf_urls.md`` for design context.
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
TOOL_NAME = "acquire_pdf_urls"
SCRAPER_VERSION = "1.0"
DEFAULT_AUTHOR = "Unknown"


@dataclass
class ItemMeta:
    """One PDF to acquire, from the URL list."""
    locator: str          # PDF URL
    title: str = ""
    date: _dt.date | None = None
    author: str = ""


@dataclass
class ProcessOptions:
    persona: str
    author: str
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
    min_words: int
    dry_run: bool
    allow_non_prose: bool
    strip_rules: str | None
    strip_aggressive: bool
    acquired_via: str


# ---- URL-list parsing ---------------------------------------------


def _parse_line(line: str) -> dict[str, Any] | None:
    """Parse one URL-list line → an entry dict, or None to skip.

    Accepts a JSON object (with a ``url`` key) or a bare URL. Blank lines
    and ``#`` comments return None.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("{"):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return None
        if isinstance(obj, dict) and obj.get("url"):
            return obj
        return None
    return {"url": line}


def _title_from_url(url: str) -> str:
    """Best-effort title from a PDF URL's basename."""
    path = urllib.parse.urlparse(url).path
    stem = Path(path).stem
    return stem or "untitled"


# ---- Discovery ----------------------------------------------------


def discover_items(
    urls_path: Path, options: ProcessOptions,
) -> Iterable[ItemMeta]:
    """Read the URL list and yield one ItemMeta per in-window entry."""
    try:
        # utf-8-sig tolerates a leading BOM on lists authored by Windows
        # editors; without it the BOM (U+FEFF) rides on the first line and a
        # leading ``#`` comment is misparsed as a bare URL (str.strip() does
        # not drop U+FEFF).
        text = Path(urls_path).expanduser().read_text(encoding="utf-8-sig")
    except OSError as exc:
        sys.stderr.write(f"  cannot read urls_file {urls_path}: {exc}\n")
        return
    for line in text.splitlines():
        obj = _parse_line(line)
        if obj is None:
            continue
        url = str(obj.get("url") or "").strip()
        if not url:
            continue
        date = ac.parse_iso_date(obj.get("date")) if obj.get("date") else None
        if options.since and date and date < options.since:
            continue
        if options.until and date and date > options.until:
            continue
        yield ItemMeta(
            locator=url,
            title=str(obj.get("title") or "").strip(),
            date=date,
            author=str(obj.get("author") or "").strip(),
        )


# ---- Extraction ---------------------------------------------------


def extract_one(
    item: ItemMeta, options: ProcessOptions, fetcher: ac.Fetcher,
) -> tuple[str, str, str, _dt.date | None]:
    """Download the PDF and extract its text. ``("", …)`` skips on a failed
    download / image-only / non-PDF."""
    data = fetcher.fetch_bytes(item.locator)
    if not data:
        return "", "", "", None
    text = ac.pdf_text_from_bytes(data)
    if not text or not text.strip():
        return "", "", "", None
    title = item.title or _title_from_url(item.locator)
    author = options.author or item.author or DEFAULT_AUTHOR
    return text, title, author, item.date


# ---- Per-document processing --------------------------------------


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
    """Preprocess -> length-gate -> hash -> dedupe -> piece. Mutates summary.

    An empty body means the download failed or the PDF had no extractable
    text (image-only) — recorded as ``no-pdf-text``."""
    if not body_text or len(body_text.strip()) < 200:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="no-pdf-text", url=item.locator,
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
            reason="empty-after-preprocess", url=item.locator,
            detail=f"raw={len(body_text)} clean={len(cleaned)}",
        )
        return None

    word_count = len(re.findall(r"\S+", cleaned))
    if word_count < options.min_words:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="below-min-words", url=item.locator,
            detail=f"words={word_count} < {options.min_words}",
        )
        return None

    piece = ac.AcquiredPiece(
        title=title or "untitled",
        author=author or DEFAULT_AUTHOR,
        persona=options.persona,
        register=options.register,
        date_written=date,
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
            reason="duplicate-hash", url=item.locator, detail=str(existing),
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
    piece: ac.AcquiredPiece, *, options: ProcessOptions, summary: ac.RunSummary,
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
        piece, output_dir=options.output_dir, scraper_version=SCRAPER_VERSION,
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


# ---- CLI ----------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Acquire prose from a curated list of remote PDF URLs into the "
            "impostor pool (grant_proposal / expert_affidavit baselines). See "
            "internal/SPEC_acquire_pdf_urls.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("urls_file",
                   help="Path to the URL list (JSONL objects with a 'url' key, "
                        "or one bare PDF URL per line; # comments allowed).")

    # Persona / impostor metadata.
    p.add_argument("--persona", default="pdf_corpus",
                   help="Persona slug for emitted entries (default: pdf_corpus).")
    p.add_argument("--author", default="",
                   help="Author display name override (default: per-entry "
                        "author, else 'Unknown').")
    p.add_argument("--impostor-for", nargs="+", required=True,
                   help=("Persona slug(s) this impostor pool serves "
                         "(required; the schema rejects empty)."))
    p.add_argument("--register", required=True,
                   help="Manifest register; use grant_proposal or "
                        "expert_affidavit.")
    p.add_argument("--register-match",
                   choices=["high", "medium", "low"], default="high")
    p.add_argument("--topic-match",
                   choices=["high", "medium", "low"], default="medium")
    p.add_argument("--consent-status", required=True,
                   choices=[
                       "public_record", "cc_licensed", "fair_use_research",
                       "author_consent", "undocumented",
                   ],
                   help="Consent / legal posture (cc_licensed for OpenGrants "
                        "CC-BY; public_record for court affidavits).")
    p.add_argument("--era",
                   choices=[
                       "pre_chatgpt", "pre_ai_widespread",
                       "post_ai_widespread", "undated",
                   ],
                   default="pre_chatgpt")

    # Date window + caps.
    p.add_argument("--since", help="Inclusive lower-bound date (YYYY-MM-DD).")
    p.add_argument("--until", help="Inclusive upper-bound date (YYYY-MM-DD).")
    p.add_argument("--max-items", type=int, default=300,
                   help="Maximum PDFs to acquire (default: 300).")
    p.add_argument("--min-words", type=int, default=1500,
                   help="Drop documents below this cleaned word count "
                        "(default: 1500).")

    # Output paths.
    p.add_argument("--output-dir",
                   help=("Where to write .txt and .meta.json files. Defaults "
                         "to <baselines>/impostors/<register>/<persona>/."))
    p.add_argument("--emit-manifest",
                   help=("Where to write draft manifest JSONL. Defaults to "
                         "<output-dir>/draft_manifest.jsonl."))
    p.add_argument("--out", help="Write summary report here (JSON).")

    # Behavior.
    p.add_argument("--rate-limit", type=float, default=2.0,
                   help="Seconds between same-host requests (default: 2.0).")
    p.add_argument("--user-agent", help="Override the User-Agent header.")
    p.add_argument("--dry-run", action="store_true",
                   help="Inventory what would be acquired without writing.")
    p.add_argument("--allow-public-output", action="store_true",
                   help=("Allow writing outside ai-prose-baselines-private/. "
                         "Acquired prose is corpus-baseline input; only use "
                         "for non-personal corpora."))

    # Preprocessing pass-throughs.
    p.add_argument("--allow-non-prose", action="store_true",
                   help="Skip preprocessing's corpus-hygiene gate.")
    p.add_argument("--strip-rules",
                   help=("Comma-separated subset of preprocessing rules. "
                         "Default: all standard rules."))
    p.add_argument("--strip-aggressive", action="store_true",
                   help="Also apply aggressive (link/citation) strip rules.")

    return p


def parse_options(args: argparse.Namespace) -> ProcessOptions:
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
    else:
        output_dir = ac.default_output_dir(
            register=args.register, author_slug=args.persona,
        )
    if args.emit_manifest:
        manifest_path = Path(args.emit_manifest).expanduser()
    else:
        manifest_path = output_dir / "draft_manifest.jsonl"

    acquired_via = f"acquire_pdf_urls_{_dt.date.today().isoformat()}"

    return ProcessOptions(
        persona=args.persona,
        author=args.author,
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
        min_words=args.min_words,
        dry_run=args.dry_run,
        allow_non_prose=args.allow_non_prose,
        strip_rules=args.strip_rules,
        strip_aggressive=args.strip_aggressive,
        acquired_via=acquired_via,
    )


def run(args: argparse.Namespace, fetcher: ac.Fetcher | None = None) -> int:
    """Top-level acquisition driver. Returns the shell exit code."""
    options = parse_options(args)

    paths_to_check = [options.output_dir, options.manifest_path]
    if args.out:
        paths_to_check.append(Path(args.out).expanduser())
    ac.check_output_privacy(
        paths_to_check, allow_public=args.allow_public_output, tool=TOOL_NAME,
    )

    if fetcher is None:
        fetcher = ac.make_requests_fetcher(
            version=SCRAPER_VERSION,
            rate_limit_seconds=args.rate_limit,
            user_agent=getattr(args, "user_agent", None) or None,
        )

    summary = ac.RunSummary(
        draft_manifest_path=str(options.manifest_path) if not args.dry_run else None,
        output_dir=str(options.output_dir),
    )

    sys.stderr.write(
        f"Acquiring PDFs from {args.urls_file} into {options.output_dir}\n"
        f"Persona: {options.persona} (impostor_for: {options.impostor_for})\n"
    )

    for item in discover_items(args.urls_file, options):
        if summary.acquired >= options.max_items:
            break
        try:
            body_text, title, author, date = extract_one(item, options, fetcher)
        except Exception as exc:
            summary.skipped_parse_error += 1
            summary.log_skip(
                reason="extract-error", url=item.locator,
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
            "No PDFs acquired. Verify the urls_file, that the URLs are direct "
            "PDF links (not Zenodo record pages), and the date window.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
