#!/usr/bin/env python3
"""acquire_everycrsreport.py — pull Congressional Research Service reports.

The legacy route reads EveryCRSReport.com's bulk index (``reports.csv``) and writes one
``.txt`` + ``.meta.json`` per admitted report into a private impostor
pool, plus a draft manifest with ``corpus_role: impostor`` entries.

CRS reports are structured federal policy analysis. Federal-government text
is generally public domain in the US, but embedded third-party matter
requires document-level review. EveryCRSReport exposes a deep pre-2022
archive through a scripted bulk index and per-report HTML (no API key).
The legacy path builds the ``policy_brief`` population
baseline that ``argmove_profile.py`` later profiles.

Source shape (https://www.everycrsreport.com/download.html):

  reports.csv     one row per report (latest version only): report
                  number, metadata-JSON path, SHA1, latest pub date,
                  title, latest PDF filename, latest HTML filename.
  per-report HTML at https://www.everycrsreport.com/<latestHTML>.

By default this script fetches the latest HTML per report (preferred over PDF —
clean text, no OCR), extracts the body, drops the CRS masthead / cover
metadata / "Author Information" / "Contacts" trailer, and admits reports
at or above ``--min-words`` (default 1500; CRS "In Focus" two-pagers fall
below and are dropped as snapshots rather than briefs).

Historical mode selects exact dated versions from per-report metadata in
finite batches. It emits candidate text, a source sidecar, and a private
receipt without a draft manifest. It retains recoverable visible text,
including summaries and author blocks, subject to nontext limitations.

Privacy: legacy text is corpus-baseline input; historical text is private
candidate material. By default, output goes
under ``ai-prose-baselines-private/impostors/<register>/<persona>/`` and
the privacy guard refuses paths outside any directory named
``ai-prose-baselines-private``. Pass ``--allow-public-output`` only for
non-personal corpora in legacy mode; historical mode rejects the override.

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
import hashlib
import os
import tempfile
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

# Legacy institutional display label; historical actual bylines need source review.
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



# ---- Opt-in historical candidate discovery -----------------------

HISTORICAL_RECEIPT_SCHEMA = "everycrsreport-historical-receipt/v1"
HISTORICAL_ORDER = "normalized-report-id-lexical/v1"
_REPORT_ID_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VERSION_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2})?$")
_HISTORICAL_STRIP_SELECTORS = (
    "nav", ".site-header", ".site-footer", ".breadcrumb",
)


def _historical_invalid(reason: str) -> None:
    sys.stderr.write(f"historical CRS: {reason}\n")
    raise SystemExit(2)


def _strict_day(value: str | None) -> _dt.date | None:
    if not isinstance(value, str) or not _DAY_RE.fullmatch(value):
        return None
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        return None


def _version_day(value: Any) -> _dt.date | None:
    if not isinstance(value, str) or not _VERSION_DATE_RE.fullmatch(value):
        return None
    try:
        if len(value) == 10:
            return _dt.date.fromisoformat(value)
        return _dt.datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _report_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if len(normalized) <= 64 and _REPORT_ID_RE.fullmatch(normalized) else None


def _decoded_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_provider_url(base: str, locator: Any, prefix: str, suffix: str) -> str | None:
    if not isinstance(locator, str) or not locator.strip():
        return None
    raw = locator.strip()
    if "\\" in raw or any(ord(c) < 32 for c in raw):
        return None
    parsed_raw = urllib.parse.urlsplit(raw)
    if parsed_raw.query or parsed_raw.fragment:
        return None
    decoded = urllib.parse.unquote(raw)
    if "%" in decoded or "\\" in decoded:
        return None
    url = urllib.parse.urljoin(base, raw)
    base_parts = urllib.parse.urlsplit(base)
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or (
        parts.scheme, parts.netloc.lower()
    ) != (base_parts.scheme, base_parts.netloc.lower()):
        return None
    path = urllib.parse.unquote(parts.path)
    if not path.startswith("/" + prefix + "/") or not path.endswith(suffix):
        return None
    if any(part in ("", ".", "..") for part in path.split("/")[2:]):
        return None
    return url


def _write_new_receipt(path: Path, receipt: dict[str, Any]) -> None:
    """Publish a complete private receipt without overwriting an old one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=".crs-receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link creation fails if path already exists and publishes
        # the fully-written bytes in one filesystem operation.
        os.link(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _historical_params(args: argparse.Namespace, index_sha: str, targets: list[str] | None) -> dict[str, Any]:
    return {
        "index_url": args.reports_csv_url,
        "index_sha256_decoded_text": index_sha,
        "target_ids": targets,
        "since": args.since,
        "until": args.until,
        "ordering": HISTORICAL_ORDER,
    }


def _historical_failure(
    out: Path, summary: ac.RunSummary, *,
    stage: str, reason: str, index_sha: str | None = None,
) -> int:
    receipt = {
        "schema": HISTORICAL_RECEIPT_SCHEMA,
        "kind": "failure",
        "failure_stage": stage,
        "failure_reason": reason,
        "index_sha256_decoded_text": index_sha,
        "selection": None,
        "selection_fingerprint": None,
        "total_in_scope": None,
        "batch_start_ordinal": None,
        "batch_end_ordinal": None,
        "batch_attempted": 0,
        "status_rows": [],
        "remaining_after_batch": None,
        "last_attempted_id": None,
        "next_cursor": None,
        "batch_complete": False,
        "scope_exhausted": False,
        "files_written": 0,
        "summary": summary.to_dict(),
    }
    _write_new_receipt(out, receipt)
    sys.stderr.write(f"historical CRS {stage}: {reason}\n")
    return 1


def _validated_prior(
    prior: dict[str, Any], worklist: list[str], selection: dict[str, Any],
    fingerprint: str, after: str,
) -> int:
    """Return next ordinal only for a typed, contiguous previous batch."""
    if type(prior) is not dict or prior.get("schema") != HISTORICAL_RECEIPT_SCHEMA or prior.get("kind") != "bounded_batch":
        raise ValueError("invalid-resume-receipt-kind")
    if prior.get("selection") != selection or prior.get("selection_fingerprint") != fingerprint:
        raise ValueError("changed-resume-selection")
    total = len(worklist)
    start, end, count = (prior.get(k) for k in (
        "batch_start_ordinal", "batch_end_ordinal", "batch_attempted",
    ))
    if any(type(x) is not int for x in (start, end, count)):
        raise ValueError("invalid-resume-count-types")
    rows = prior.get("status_rows")
    if type(rows) is not list or count != len(rows) or count < 1:
        raise ValueError("invalid-resume-status-count")
    if not 0 <= start <= end < total or count != end - start + 1:
        raise ValueError("invalid-resume-range")
    if type(prior.get("total_in_scope")) is not int or prior["total_in_scope"] != total:
        raise ValueError("invalid-resume-total")
    if type(prior.get("remaining_after_batch")) is not int or prior["remaining_after_batch"] != total - end - 1:
        raise ValueError("invalid-resume-remaining")
    if type(prior.get("batch_complete")) is not bool or prior["batch_complete"] is not True:
        raise ValueError("invalid-resume-batch-complete")
    if type(prior.get("scope_exhausted")) is not bool or prior["scope_exhausted"] != (end == total - 1):
        raise ValueError("invalid-resume-scope")
    if prior.get("last_attempted_id") != worklist[end] or prior.get("next_cursor") != worklist[end] or after != worklist[end]:
        raise ValueError("invalid-resume-cursor")
    if any(type(row) is not dict or type(row.get("id")) is not str or
           type(row.get("status")) is not str or not row["status"]
           for row in rows):
        raise ValueError("invalid-resume-status-row")
    if [row["id"] for row in rows] != worklist[start:end + 1]:
        raise ValueError("noncontiguous-resume-statuses")
    if type(prior.get("summary")) is not dict or prior["summary"].get("draft_manifest_path", object()) is not None:
        raise ValueError("invalid-resume-summary")
    if type(prior.get("index_sha256_decoded_text")) is not str or prior["index_sha256_decoded_text"] != selection["index_sha256_decoded_text"]:
        raise ValueError("invalid-resume-index")
    return end + 1


def _historical_index(csv_url: str, text: str) -> dict[str, str | None]:
    reader = csv.DictReader(io.StringIO(text))
    fields = list(reader.fieldnames or [])
    number_col = _resolve_column(fields, CSV_NUMBER_COLS)
    url_col = _resolve_column(fields, ("url", "metadataurl", "metadata_url"))
    if not number_col or not url_col:
        raise ValueError("missing-number-or-metadata-url-column")
    found: dict[str, str | None] = {}
    for row in reader:
        if not isinstance(row, dict):
            raise ValueError("malformed-index-row")
        number = _report_id(row.get(number_col))
        if number is None:
            raise ValueError("invalid-index-report-number")
        locator = row.get(url_col)
        if not isinstance(locator, str):
            locator = None
        if number in found and found[number] != locator:
            found[number] = None
        elif number not in found:
            found[number] = locator
    return found


def _historical_select(
    csv_url: str, number: str, locator: Any, until: _dt.date,
    since: _dt.date | None, fetcher: ac.Fetcher,
) -> tuple[dict[str, Any] | None, str]:
    metadata_url = _safe_provider_url(csv_url, locator, "reports", ".json")
    if not metadata_url:
        return None, "invalid-metadata-locator"
    fetched = fetcher.fetch(metadata_url)
    if fetched.final_url and fetched.final_url != metadata_url:
        return None, "metadata-redirected"
    if not fetched.ok or not fetched.text:
        return None, "metadata-fetch-failed"
    try:
        data = json.loads(fetched.text)
    except ValueError:
        return None, "malformed-metadata-json"
    if type(data) is not dict:
        return None, "nonobject-metadata-root"
    if _report_id(data.get("id")) != number:
        return None, "mismatched-report-id"
    versions = data.get("versions")
    if type(versions) is not list:
        return None, "missing-versions"
    if not versions:
        return None, "no-dated-versions"
    dated: list[tuple[_dt.date, dict[str, Any]]] = []
    for version in versions:
        if type(version) is not dict:
            return None, "malformed-version"
        day = _version_day(version.get("date"))
        if day is None:
            return None, "ambiguous-version-date"
        dated.append((day, version))
    eligible = [(day, version) for day, version in dated if day <= until]
    if not eligible:
        return None, "future-only"
    selected_day = max(day for day, _ in eligible)
    if since and selected_day < since:
        return None, "before-since"
    selected_versions = [v for day, v in eligible if day == selected_day]
    identities: dict[tuple[str, str], str] = {}
    for version in selected_versions:
        vid = version.get("id")
        if type(vid) is int:
            key = ("int", str(vid))
        elif type(vid) is str and vid.strip():
            key = ("str", vid.strip())
        else:
            return None, "invalid-selected-version-id"
        encoded = json.dumps(version, sort_keys=True)
        if key in identities and identities[key] != encoded:
            return None, "ambiguous-version-identity"
        identities[key] = encoded
    if len(identities) != 1:
        return None, "ambiguous-same-date-versions"
    version = selected_versions[0]
    vid = version["id"]
    title = version.get("title")
    if not isinstance(title, str) or not title.strip():
        return None, "missing-version-title"
    formats = version.get("formats")
    if type(formats) is not list:
        return None, "invalid-selected-formats"
    html_urls: dict[str, str] = {}
    for fmt in formats:
        if type(fmt) is not dict or type(fmt.get("format")) is not str:
            return None, "invalid-selected-formats"
        if fmt["format"].upper() != "HTML":
            continue
        filename = fmt.get("filename")
        url = _safe_provider_url(csv_url, filename, "files", ".html")
        if url is None:
            return None, "invalid-html-locator"
        html_urls[url] = filename
    if not html_urls:
        return None, "no-html"
    if len(html_urls) != 1:
        return None, "ambiguous-html"
    html_url, filename = next(iter(html_urls.items()))
    return {
        "report_number": number,
        "provider_version_id": vid,
        "provider_version_id_type": type(vid).__name__,
        "provider_version_date": version["date"],
        "date": selected_day,
        "title": title.strip(),
        "provider_metadata_url": metadata_url,
        "provider_html_filename": filename,
        "html_url": html_url,
        "metadata_decoded_text_sha256": _decoded_sha(fetched.text),
    }, "selected"


def _historical_extract(html: str, selector: str | None) -> tuple[str, bool, str]:
    from bs4 import BeautifulSoup  # type: ignore
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup.select("script, style, noscript, form, iframe"):
        tag.decompose()
    for sel in _HISTORICAL_STRIP_SELECTORS:
        for tag in soup.select(sel):
            tag.decompose()
    selectors = [selector] if selector else []
    selectors.extend(DEFAULT_CONTENT_SELECTORS)
    container = None
    for sel in selectors:
        if sel and (matched := soup.select_one(sel)) is not None:
            container = matched
            break
    container = container or soup.body or soup
    nontext = bool(container.select("img, svg, canvas, figure"))
    body = container.get_text("\n")
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n[ \t]+", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, nontext, title


def _historical_process(
    descriptor: dict[str, Any], args: argparse.Namespace,
    options: ProcessOptions, summary: ac.RunSummary, fetcher: ac.Fetcher,
) -> str:
    url = descriptor["html_url"]
    fetched = fetcher.fetch(url)
    if fetched.final_url and fetched.final_url != url:
        summary.skipped_network_error += 1
        summary.log_skip(reason="html-redirected", url=url)
        return "html-redirected"
    if not fetched.ok or not fetched.text:
        summary.skipped_network_error += 1
        summary.log_skip(reason="html-fetch-failed", url=url)
        return "html-fetch-failed"
    try:
        body, nontext, _html_title = _historical_extract(fetched.text, args.content_selector)
        if len(body) < 200:
            raise ValueError("empty-body")
        cleaned, prep_meta = ac.preprocess_text(
            body, rules="", allow_non_prose=options.allow_non_prose,
            strip_aggressive=False,
        )
        if len(cleaned) < 200:
            raise ValueError("empty-after-preprocess")
    except Exception as exc:
        summary.skipped_parse_error += 1
        reason = str(exc) if isinstance(exc, ValueError) and str(exc) in (
            "empty-body", "empty-after-preprocess",
        ) else "extract-error"
        summary.log_skip(reason=reason, url=url, detail=type(exc).__name__)
        return reason
    word_count = len(re.findall(r"\S+", cleaned))
    if word_count < options.min_words:
        summary.skipped_filtered += 1
        summary.log_skip(reason="below-min-words", url=url, detail=str(word_count))
        return "below-min-words"
    piece = ac.AcquiredPiece(
        title=descriptor["title"], author=options.author or CRS_AUTHOR,
        persona=options.persona, register=options.register,
        date_written=descriptor["date"], source_url=url,
        cleaned_text=cleaned, raw_byte_length=len(body.encode("utf-8")),
        preprocessing_meta=prep_meta, acquired_via=options.acquired_via,
        consent_status=options.consent_status, era=options.era,
        register_match=options.register_match, topic_match=options.topic_match,
        impostor_for=list(options.impostor_for),
    )
    if ac.content_hash_already_present(piece.content_hash, options.output_dir):
        summary.skipped_duplicate += 1
        summary.log_skip(reason="duplicate-hash", url=url)
        return "duplicate-hash"
    if options.dry_run:
        summary.acquired += 1
        return "would_write"
    author_match = re.search(
        r"(?im)^\s*(?:Author Information|Author Contact Information|Contacts?)\s*$",
        body,
    )
    author_block = body[author_match.start():author_match.start() + 1200] if author_match else ""
    if re.search(r"\b(?:author|analyst)\s+redacted\b", author_block or body, re.I):
        byline_status = "provider_redacted"
    elif author_match:
        byline_status = "source_block_unreviewed"
    else:
        byline_status = "unresolved"
    extra = {
        "author_block_body_char_offset": author_match.start() if author_match else None,
        "author_block_visible_text_sha256": _decoded_sha(author_block) if author_block else None,
        "author_block_hash_semantics": "up to 1200 characters from extracted visible body",
        "report_number": descriptor["report_number"],
        "provider_version_id": descriptor["provider_version_id"],
        "provider_version_id_type": descriptor["provider_version_id_type"],
        "provider_version_date": descriptor["provider_version_date"],
        "provider_metadata_url": descriptor["provider_metadata_url"],
        "provider_html_filename": descriptor["provider_html_filename"],
        "selection_mode": "latest_eligible_historical_version",
        "byline_status": byline_status,
        "extraction_completeness_status": (
            "visible_text_with_nontext_uncertainty" if nontext
            else "recoverable_visible_text_unreviewed"
        ),
        "rights_review_status": "pending_document_level_third_party_review",
        "paragraph_segmentation_status": "unreviewed",
        "metadata_decoded_text_sha256": descriptor["metadata_decoded_text_sha256"],
        "html_decoded_text_sha256": _decoded_sha(fetched.text),
        "source_snapshots_retained": False,
        "source_hash_semantics": "decoded FetchResult.text encoded as UTF-8; operator receipt only",
    }
    try:
        ac.write_piece(piece, output_dir=options.output_dir,
                       scraper_version=SCRAPER_VERSION, extra_meta=extra)
    except OSError as exc:
        summary.skipped_parse_error += 1
        summary.log_skip(reason="write-error", url=url, detail=type(exc).__name__)
        return "write-error"
    summary.acquired += 1
    summary.total_cleaned_words += piece.word_count
    summary.record_strip_meta(prep_meta)
    return "written"


def run_historical(args: argparse.Namespace, fetcher: ac.Fetcher | None = None) -> int:
    """Candidate-only finite historical-version batch; legacy run is separate."""
    until = _strict_day(getattr(args, "until", None))
    since = _strict_day(args.since) if args.since else None
    limit = getattr(args, "metadata_limit", None)
    out_value = getattr(args, "out", None)
    if until is None or (args.since and since is None) or (since and since > until):
        _historical_invalid("invalid-date-window")
    if type(limit) is not int or limit <= 0:
        _historical_invalid("invalid-metadata-limit")
    if type(args.max_items) is not int or args.max_items < limit:
        _historical_invalid("max-items-below-metadata-limit")
    if not out_value or args.emit_manifest or args.allow_public_output or args.strip_aggressive or args.strip_rules:
        _historical_invalid("invalid-historical-output-or-stripping")
    target_input = getattr(args, "report_number", None) or []
    targets: list[str] = []
    for raw in target_input:
        number = _report_id(raw)
        if number is None or number in targets:
            _historical_invalid("invalid-or-duplicate-report-number")
        targets.append(number)
    targets = sorted(targets)
    out = Path(out_value).expanduser().resolve()
    resume_value = getattr(args, "resume_receipt", None)
    after_raw = getattr(args, "after_report_number", None)
    expected_sha = getattr(args, "expected_index_sha256", None)
    if (after_raw is None) != (resume_value is None) or (after_raw is None) != (expected_sha is None):
        _historical_invalid("incomplete-resume-options")
    after = _report_id(after_raw) if after_raw is not None else None
    if after_raw is not None and (after is None or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha or "")):
        _historical_invalid("invalid-resume-cursor-or-hash")
    if out.exists():
        _historical_invalid("receipt-output-already-exists")
    prior_path = Path(resume_value).expanduser().resolve() if resume_value else None
    if prior_path == out:
        _historical_invalid("resume-receipt-output-equality")
    options = parse_options(args)
    private_paths = [out, options.output_dir]
    if prior_path:
        private_paths.append(prior_path)
    ac.check_output_privacy(private_paths, allow_public=False, tool=TOOL_NAME)
    prior = None
    prior_bytes = None
    if prior_path:
        try:
            prior_bytes = prior_path.read_bytes()
            prior = json.loads(prior_bytes)
        except (OSError, ValueError) as exc:
            _historical_invalid(f"unreadable-resume-receipt:{type(exc).__name__}")
        if type(prior) is not dict or prior.get("schema") != HISTORICAL_RECEIPT_SCHEMA or prior.get("kind") != "bounded_batch":
            _historical_invalid("invalid-resume-receipt-kind")
    if fetcher is None:
        fetcher = ac.make_requests_fetcher(
            version=SCRAPER_VERSION, rate_limit_seconds=args.rate_limit,
            user_agent=getattr(args, "user_agent", None) or None,
        )
    summary = ac.RunSummary(draft_manifest_path=None, output_dir=str(options.output_dir))
    index_result = fetcher.fetch(args.reports_csv_url)
    if index_result.final_url and index_result.final_url != args.reports_csv_url:
        return _historical_failure(out, summary, stage="index-fetch", reason="index-redirected")
    if not index_result.ok or not index_result.text:
        return _historical_failure(out, summary, stage="index-fetch", reason="index-fetch-failed")
    index_sha = _decoded_sha(index_result.text)
    try:
        index = _historical_index(args.reports_csv_url, index_result.text)
    except ValueError as exc:
        return _historical_failure(out, summary, stage="index-parse", reason=str(exc), index_sha=index_sha)
    worklist = targets if targets else sorted(index)
    selection = _historical_params(args, index_sha, targets or None)
    fingerprint = _decoded_sha(json.dumps(selection, sort_keys=True, separators=(",", ":")))
    start = 0
    if prior is not None:
        if expected_sha.lower() != index_sha:
            return _historical_failure(out, summary, stage="resume", reason="changed-index", index_sha=index_sha)
        try:
            start = _validated_prior(prior, worklist, selection, fingerprint, after)
        except ValueError as exc:
            return _historical_failure(out, summary, stage="resume", reason=str(exc), index_sha=index_sha)
    batch = worklist[start:start + limit]
    rows: list[dict[str, str]] = []
    for number in batch:
        if number not in index:
            status = "absent-from-index"
        elif index[number] is None:
            status = "ambiguous-index-locator"
        else:
            try:
                descriptor, status = _historical_select(
                    args.reports_csv_url, number, index[number], until, since, fetcher,
                )
                if descriptor is not None:
                    status = _historical_process(descriptor, args, options, summary, fetcher)
            except Exception as exc:
                status = "report-processing-error"
                summary.skipped_parse_error += 1
                summary.log_skip(reason=status, url=number, detail=type(exc).__name__)
        if status not in ("written", "would_write", "duplicate-hash"):
            summary.skipped_filtered += 1
            summary.log_skip(reason=status, url=number)
        rows.append({"id": number, "status": status})
    end = start + len(batch) - 1
    remaining = len(worklist) - start - len(batch)
    receipt = {
        "schema": HISTORICAL_RECEIPT_SCHEMA,
        "kind": "bounded_batch",
        "index_sha256_decoded_text": index_sha,
        "selection": selection,
        "selection_fingerprint": fingerprint,
        "predecessor_receipt_sha256": hashlib.sha256(prior_bytes).hexdigest() if prior_bytes else None,
        "total_in_scope": len(worklist),
        "batch_start_ordinal": start,
        "batch_end_ordinal": end,
        "batch_attempted": len(batch),
        "status_rows": rows,
        "remaining_after_batch": remaining,
        "last_attempted_id": batch[-1] if batch else None,
        "next_cursor": batch[-1] if batch else None,
        "batch_complete": True,
        "scope_exhausted": remaining == 0,
        "files_written": sum(row["status"] == "written" for row in rows),
        "summary": summary.to_dict(),
    }
    _write_new_receipt(out, receipt)
    sys.stderr.write(summary.render_stderr())
    if summary.acquired == 0 and not args.allow_empty and not any(
        row["status"] == "duplicate-hash" for row in rows
    ):
        return 1
    return 0

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
    p.add_argument("--historical-versions", action="store_true",
                   help="Select dated per-report versions in finite candidate-only batches.")
    p.add_argument("--report-number", action="append",
                   help="Optional report number filter in historical mode (repeatable).")
    p.add_argument("--metadata-limit", type=int,
                   help="Required positive metadata-decision cap for historical mode.")
    p.add_argument("--after-report-number",
                   help="Exclusive cursor from a prior historical receipt.")
    p.add_argument("--expected-index-sha256",
                   help="Expected decoded CSV text SHA-256 on resume.")
    p.add_argument("--resume-receipt",
                   help="Prior private bounded-batch receipt on resume.")
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
    ac.add_user_agent_arg(p)
    p.add_argument("--dry-run", action="store_true",
                   help="Inventory what would be acquired without writing.")
    ac.add_allow_empty_arg(p)
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
    if getattr(args, "historical_versions", False):
        return run_historical(args, fetcher)
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
