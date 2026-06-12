#!/usr/bin/env python3
"""acquire_everycrsreport.py — pull Congressional Research Service reports.

Reads EveryCRSReport.com's bulk index (``reports.csv``) and writes one
``.txt`` + ``.meta.json`` per admitted report into a private impostor
pool, plus a draft manifest with ``corpus_role: impostor`` entries.

CRS reports are neutral, rigorously structured federal policy analysis
(problem -> analysis -> options/implications). They are **public domain**
(US-government work) with a deep pre-2022 archive, and EveryCRSReport
exposes them as a scripted bulk index + per-report HTML — so acquisition
is all text/HTML (no PDF/OCR, no API key). This makes CRS the cleanest
first distant-genre acquirer; it builds the ``policy_brief`` population
baseline that ``argmove_profile.py`` later profiles.

Source shape (https://www.everycrsreport.com/download.html):

  reports.csv     one row per report (latest version only): report
                  number, metadata-JSON path, SHA1, latest pub date,
                  title, latest PDF filename, latest HTML filename.
  per-report HTML at https://www.everycrsreport.com/<latestHTML>.

This script fetches the latest HTML per report (preferred over PDF —
clean text, no OCR), extracts the body, drops the CRS masthead / cover
metadata / "Author Information" / "Contacts" trailer, and admits reports
at or above ``--min-words`` (default 1500; CRS "In Focus" two-pagers fall
below and are dropped as snapshots rather than briefs).

Privacy: acquired text is corpus-baseline input. By default, output goes
under ``ai-prose-baselines-private/impostors/<register>/<persona>/`` and
the privacy guard refuses paths outside any directory named
``ai-prose-baselines-private``. Pass ``--allow-public-output`` only for
non-personal corpora.

Robots: honors robots.txt by default (the shared ``Fetcher``); ships no
override flag, matching ``acquire_blog.py``.

The exact ``reports.csv`` column names and HTML-URL shape are resolved
tolerantly (case-insensitive candidate matching), but the operator should
run ``--dry-run`` against live ``reports.csv`` once before a bulk pull to
confirm discovery and the body selector — see
``references/acquire-corpus-pattern.md``.

Usage:

    python3 scripts/acquire_everycrsreport.py \\
        --persona crs \\
        --impostor-for argscope_policy_brief \\
        --register policy_brief \\
        --consent-status public_record \\
        --era pre_chatgpt \\
        --since 2010-01-01 --until 2021-12-31 \\
        --min-words 1500 --max-items 400

See ``internal/SPEC_acquire_distant_genre.md`` for design context.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import json
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# Resolve repo-relative imports the same way the other scripts do.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402

TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "acquire_everycrsreport"
SCRAPER_VERSION = "1.0"

# The public bulk index. Positional ``reports_csv_url`` defaults to this.
DEFAULT_REPORTS_CSV_URL = "https://www.everycrsreport.com/reports.csv"

# CRS institutional author (reports are corporate-authored, not by-line).
CRS_AUTHOR = "Congressional Research Service"

# Column-name candidates in reports.csv, matched case-insensitively
# against the actual header so a casing/underscore change upstream
# doesn't silently break discovery. The first match wins.
CSV_TITLE_COLS = ("title",)
CSV_HTML_COLS = ("latesthtml", "latest_html", "html", "htmlfilename")
CSV_DATE_COLS = (
    "latestpubdate", "latest_pub_date", "date", "pubdate", "lastmodified",
)
CSV_NUMBER_COLS = ("number", "reportnumber", "report_number", "id")

# Body-container selectors tried in order before falling back to <body>.
# CRS HTML on EveryCRSReport wraps the report in a content container;
# html_to_text already drops <nav>/<header>-ish noise globally, so the
# <body> fallback is safe when none of these match.
DEFAULT_CONTENT_SELECTORS = (
    "#report", ".report", "#content", ".report-content",
    "article", "main",
)

# Stripped on every CRS page: site chrome and the metadata sidebar.
DEFAULT_STRIP_SELECTORS = (
    "nav", "header", "footer", ".site-header", ".site-footer",
    ".metadata", ".cover", ".sidebar", ".report-metadata",
    ".summary-box", ".breadcrumb",
)

# Trailer headings that mark the end of substantive argument in a CRS
# report (contact block / author block). Trimmed only when they appear
# in the last fifth of the text so a substantive appendix is preserved.
_TRAILER_HEADING_RE = re.compile(
    r"\n\s*(?:Author Information|Author Contact Information|Contacts?|"
    r"Acknowledgments?)\s*\n",
    re.IGNORECASE,
)


@dataclass
class ItemMeta:
    """One discovered report from reports.csv."""
    locator: str          # absolute URL to the report HTML
    title: str = ""
    date: _dt.date | None = None
    number: str = ""


@dataclass
class ProcessOptions:
    """User-facing options + resolved defaults for the per-report pipeline."""
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
    content_selector: str | None = None


# ---- Discovery ----------------------------------------------------


def _resolve_column(fieldnames: list[str], candidates: Iterable[str]) -> str | None:
    """Return the actual header matching any candidate (case-insensitive)."""
    lower_map = {name.lower().strip(): name for name in fieldnames if name}
    for cand in candidates:
        actual = lower_map.get(cand)
        if actual is not None:
            return actual
    return None


def _html_url(csv_url: str, html_value: str) -> str:
    """Build the absolute report-HTML URL from a reports.csv cell.

    Accepts a full URL (used as-is), a site-root-relative path, or a bare
    filename — all resolved against the reports.csv URL's base.
    """
    html_value = (html_value or "").strip()
    if not html_value:
        return ""
    if html_value.startswith(("http://", "https://")):
        return html_value
    return urllib.parse.urljoin(csv_url, html_value)


def discover_items(
    csv_url: str,
    options: ProcessOptions,
    fetcher: ac.Fetcher,
) -> Iterable[ItemMeta]:
    """Fetch reports.csv and yield one ItemMeta per in-window report.

    Column names are resolved tolerantly. Rows with no HTML filename
    (PDF-only legacy reports) are skipped — logged by the caller via the
    ``no-html`` reason. The date-window filter is applied here on the
    CSV's publication date (cheap; avoids fetching out-of-window HTML).
    Raises ValueError with the available headers if the title or HTML
    column can't be resolved, so a schema change fails loudly.
    """
    result = fetcher.fetch(csv_url)
    if not result.ok or not result.text:
        sys.stderr.write(
            f"  reports.csv unreachable: {csv_url} (status={result.status})\n"
        )
        return

    reader = csv.DictReader(io.StringIO(result.text))
    fieldnames = list(reader.fieldnames or [])
    title_col = _resolve_column(fieldnames, CSV_TITLE_COLS)
    html_col = _resolve_column(fieldnames, CSV_HTML_COLS)
    date_col = _resolve_column(fieldnames, CSV_DATE_COLS)
    number_col = _resolve_column(fieldnames, CSV_NUMBER_COLS)
    if not title_col or not html_col:
        raise ValueError(
            "reports.csv is missing an expected title/HTML column. "
            f"Found headers: {fieldnames}. Expected a title column in "
            f"{CSV_TITLE_COLS} and an HTML column in {CSV_HTML_COLS}."
        )

    for row in reader:
        html_value = (row.get(html_col) or "").strip()
        title = (row.get(title_col) or "").strip()
        date = ac.parse_iso_date(row.get(date_col)) if date_col else None
        number = (row.get(number_col) or "").strip() if number_col else ""

        # Cheap date-window filter before we ever fetch the HTML.
        if options.since and date and date < options.since:
            continue
        if options.until and date and date > options.until:
            continue

        url = _html_url(csv_url, html_value)
        yield ItemMeta(locator=url, title=title, date=date, number=number)


# ---- Extraction ---------------------------------------------------


def _trim_crs_trailer(text: str) -> str:
    """Drop the contact/author trailer when it sits in the last fifth.

    Conservative: only trims at a trailer heading that appears past the
    80% mark, so a mid-document "Contacts" subsection or a substantive
    appendix isn't truncated.
    """
    if not text:
        return text
    cutoff = int(len(text) * 0.8)
    last_match = None
    for m in _TRAILER_HEADING_RE.finditer(text):
        if m.start() >= cutoff:
            last_match = m
            break
    if last_match is not None:
        return text[: last_match.start()].rstrip()
    return text


def extract_one(
    item: ItemMeta,
    options: ProcessOptions,
    fetcher: ac.Fetcher,
) -> tuple[str, str, str, _dt.date | None]:
    """Fetch one report's HTML and return (body_text, title, author, date).

    Returns ``("", "", "", None)`` to signal a silent skip (the caller
    treats an empty body as a parse-error skip). The body selector falls
    through ``DEFAULT_CONTENT_SELECTORS`` to ``<body>`` so the extractor
    degrades gracefully when CRS HTML structure shifts.
    """
    if not item.locator:
        return "", "", "", None
    result = fetcher.fetch(item.locator)
    if not result.ok or not result.text:
        return "", "", "", None

    selectors: list[str | None] = []
    if options.content_selector:
        selectors.append(options.content_selector)
    selectors.extend(DEFAULT_CONTENT_SELECTORS)
    selectors.append(None)  # final fallback: whole <body>

    body_text = ""
    html_title = None
    for sel in selectors:
        text, title_candidate = ac.html_to_text(
            result.text,
            content_selector=sel,
            strip_selectors=DEFAULT_STRIP_SELECTORS,
        )
        if html_title is None:
            html_title = title_candidate
        if text and len(text) > 200:
            body_text = text
            break

    body_text = _trim_crs_trailer(body_text)
    title = item.title or html_title or "untitled"
    return body_text, title, options.author or CRS_AUTHOR, item.date


# ---- Per-report processing ----------------------------------------


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
    """Extract -> preprocess -> length-gate -> hash -> dedupe -> piece.

    Returns the piece on success, ``None`` on skip; mutates ``summary``.
    """
    if not body_text or len(body_text.strip()) < 200:
        summary.skipped_parse_error += 1
        summary.log_skip(
            reason="empty-body", url=item.locator,
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

    # CRS-specific length gate: drop "In Focus" two-pagers and snapshots
    # below the argument-depth floor.
    word_count = len(re.findall(r"\S+", cleaned))
    if word_count < options.min_words:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="below-min-words", url=item.locator,
            detail=f"words={word_count} < {options.min_words}",
        )
        return None

    piece = ac.AcquiredPiece(
        title=title,
        author=author or CRS_AUTHOR,
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
            "Acquire Congressional Research Service reports from "
            "EveryCRSReport.com into the impostor pool (the policy_brief "
            "population baseline). See internal/SPEC_acquire_distant_genre.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "reports_csv_url", nargs="?", default=DEFAULT_REPORTS_CSV_URL,
        help=(
            "URL of the EveryCRSReport bulk index CSV "
            f"(default: {DEFAULT_REPORTS_CSV_URL})."
        ),
    )

    # Persona / impostor metadata.
    p.add_argument("--persona", default="crs",
                   help="Persona slug for emitted entries (default: crs).")
    p.add_argument("--author", default=CRS_AUTHOR,
                   help="Author display name (default: Congressional "
                        "Research Service).")
    p.add_argument("--impostor-for", nargs="+", required=True,
                   help=("Persona slug(s) this impostor pool serves "
                         "(required; the schema rejects empty)."))
    p.add_argument("--register", required=True,
                   help="Manifest register; use policy_brief for CRS.")
    p.add_argument("--register-match",
                   choices=["high", "medium", "low"], default="high",
                   help="Register-match closeness for the impostor target.")
    p.add_argument("--topic-match",
                   choices=["high", "medium", "low"], default="medium",
                   help="Topical-match closeness for the impostor target.")
    p.add_argument("--consent-status", required=True,
                   choices=[
                       "public_record", "cc_licensed", "fair_use_research",
                       "author_consent", "undocumented",
                   ],
                   help="Consent / legal posture (use public_record for CRS).")
    p.add_argument("--era",
                   choices=[
                       "pre_chatgpt", "pre_ai_widespread",
                       "post_ai_widespread", "undated",
                   ],
                   default="pre_chatgpt",
                   help="Era classification of the acquired prose.")

    # Date window + caps.
    p.add_argument("--since", help="Inclusive lower-bound date (YYYY-MM-DD).")
    p.add_argument("--until", help="Inclusive upper-bound date (YYYY-MM-DD).")
    p.add_argument("--max-items", type=int, default=400,
                   help="Maximum number of reports to acquire (default: 400).")
    p.add_argument("--min-words", type=int, default=1500,
                   help="Drop reports whose cleaned text is below this "
                        "word count (default: 1500).")

    # Output paths.
    p.add_argument("--output-dir",
                   help=("Where to write .txt and .meta.json files. Defaults "
                         "to <baselines>/impostors/<register>/<persona>/."))
    p.add_argument("--emit-manifest",
                   help=("Where to write draft manifest JSONL. Defaults to "
                         "<output-dir>/draft_manifest.jsonl."))
    p.add_argument("--out", help="Write summary report here (JSON).")

    # Behavior.
    p.add_argument("--content-selector",
                   help="CSS selector for the report body (rare override).")
    p.add_argument("--rate-limit", type=float, default=2.0,
                   help="Seconds between same-host requests (default: 2.0).")
    p.add_argument("--user-agent", help="Override the User-Agent header.")
    p.add_argument("--dry-run", action="store_true",
                   help="Inventory what would be acquired without writing.")
    p.add_argument("--allow-empty", action="store_true",
                   help="Exit 0 even when nothing is acquired. By default a "
                        "zero-output run that isn't a dedupe-only rerun "
                        "(nothing matched the source/filters) fails.")
    p.add_argument("--allow-public-output", action="store_true",
                   help=("Allow writing outside ai-prose-baselines-private/. "
                         "Acquired prose is corpus-baseline input; only use "
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

    acquired_via = f"acquire_everycrsreport_{_dt.date.today().isoformat()}"

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
        content_selector=args.content_selector,
    )


def run(args: argparse.Namespace, fetcher: ac.Fetcher | None = None) -> int:
    """Top-level acquisition driver. Returns the shell exit code."""
    if args.since and not ac.parse_iso_date(args.since):
        sys.stderr.write(f"  warning: could not parse --since={args.since}\n")
    if args.until and not ac.parse_iso_date(args.until):
        sys.stderr.write(f"  warning: could not parse --until={args.until}\n")

    options = parse_options(args)

    # Privacy guard up front: output dir, manifest, and summary report
    # all have to live under a private root unless --allow-public-output.
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
        f"Acquiring CRS reports from {args.reports_csv_url} into "
        f"{options.output_dir}\n"
        f"Persona: {options.persona} (impostor_for: {options.impostor_for})\n"
    )

    for item in discover_items(args.reports_csv_url, options, fetcher):
        if summary.acquired >= options.max_items:
            break
        if not item.locator:
            summary.skipped_filtered += 1
            summary.log_skip(
                reason="no-html", url=item.title or item.number, detail="",
            )
            continue
        try:
            body_text, title, author, date = extract_one(
                item, options, fetcher,
            )
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

    if summary.acquired == 0 and not args.allow_empty and not any(
        s.get("reason") == "duplicate-hash" for s in summary.skip_log
    ):
        # Zero acquired with no duplicate-hash skip seen: nothing matched the
        # source/filters (a likely misconfiguration), not a dedupe-only rerun.
        sys.stderr.write(
            "No reports acquired and nothing matched the source/filters. "
            "Verify the reports.csv URL, the date window, and (with --dry-run) "
            "the body selector; pass --allow-empty to allow an empty run.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
