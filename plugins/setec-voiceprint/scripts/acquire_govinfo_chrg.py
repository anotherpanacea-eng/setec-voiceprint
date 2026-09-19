#!/usr/bin/env python3
"""acquire_govinfo_chrg.py — pull prepared written congressional testimony.

Reads GovInfo's CHRG (congressional hearings) collection via the GovInfo
API and writes one ``.txt`` + ``.meta.json`` per candidate **prepared
written witness block** into a private impostor pool, plus a draft
reference-pool manifest with ``corpus_role: impostor`` entries.

The argument-dense material in a hearing is the prepared written witness
statement — not the oral Q&A colloquy, members' opening statements, or
procedural inserts. CHRG packages are a SINGLE whole-hearing granule (no
per-witness granules — verified against the live API), so the prepared
candidate blocks are split out of the hearing transcript itself: each is
anchored on a ``Prepared Statement of <Name>`` heading and must have a
detected closing boundary (see ``_split_prepared_statements``). Unbounded,
ambiguous, and overlong blocks are refused visibly. The word floor filters
short candidates; extraction alone does not establish a complete statement,
official-duty authorship, or rights. GovInfo publications may contain third-
party material. Draft manifest entries express reference-pool intent, not
production registration or corpus admission.

Source shape (verified 2026-06-11 against the live API):

  published  GET /published/{startDate}/{endDate}?collection=CHRG&…&api_key=KEY
             dates are ``YYYY-MM-DD`` — the ``…T00:00:00Z`` form returns 400
             -> {packages:[{packageId, dateIssued}], nextPage}
  granules   GET /packages/{packageId}/granules?…&api_key=KEY
             -> {granules:[{granuleId}], nextPage}   (one granule per CHRG pkg)
  granule    GET /packages/{packageId}/granules/{granuleId}/htm?api_key=KEY
             -> the whole-hearing HTML, split into per-witness statements

An api.data.gov key is required: --api-key, else $GOVINFO_API_KEY, else the
rate-limited public DEMO_KEY. The in-document boundary forms are
source-dependent and MUST be checked against the source before use. See
references/acquire-govinfo-chrg-boundaries.md.

Privacy: output goes under ``ai-prose-baselines-private/impostors/
<register>/<persona>/`` and the privacy guard refuses paths outside any
directory named ``ai-prose-baselines-private``. Robots is not consulted —
this is a documented public API, not a scrape — but the per-host rate limit
applies.

Usage:

    python3 scripts/acquire_govinfo_chrg.py \\
        --api-key "$GOVINFO_API_KEY" \\
        --persona chrg \\
        --impostor-for argscope_testimony_policy \\
        --register testimony_policy \\
        --consent-status public_record \\
        --era pre_chatgpt \\
        --since 2005-01-01 --until 2021-12-31 \\
        --min-words 1500 --max-items 400

See ``internal/SPEC_acquire_govinfo_chrg.md`` for design context.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

# Resolve repo-relative imports the same way the other scripts do.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402

TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "acquire_govinfo_chrg"
SCRAPER_VERSION = "1.1"

GOVINFO_API_BASE = "https://api.govinfo.gov"
DEFAULT_COLLECTION = "CHRG"
DEFAULT_SINCE = "2000-01-01"
DEFAULT_UNTIL = "2021-12-31"
DEMO_KEY = "DEMO_KEY"  # rate-limited api.data.gov shared key
PAGE_SIZE = 100
MAX_PAGES = 10000      # safety bound on pagination loops
WITNESS_FALLBACK = "Congressional Witness"

# CHRG hearing HTML is decoded to plain text before boundary scanning.
# These recognizers deliberately cover only evidenced transcript structures.
PREPARED_HEADING_RE = re.compile(
    r"(?im)^[ \t]*Prepared Statement of[ \t]+([^\r\n]+?)[ \t]*$"
)
_ORAL_HEADING_RE = re.compile(r"(?i)^STATEMENT OF[ \t]+\S")
_PROCEDURAL_END_RE = re.compile(
    r"(?i)^\[(?:Questions and answers follow\.|Whereupon\b[^\]]*\badjourned\.)\]$"
)
# Keep standalone content labels to a label token, not free-form procedural
# prose such as "[Exhibit A admitted.]".
_CONTENT_BRACKET_RE = re.compile(
    r"(?i)^\[(?:\d+|(?:Exhibit|Table)[ \t]+[A-Z0-9]+(?:[-.][A-Z0-9]+)*|"
    r"\d+[ \t]+U\.S\.C\.[ \t]+(?:§[ \t]*)?\d+[A-Z]?"
    r"(?:\([A-Z0-9]+\))*)\]$"
)
_SPEAKER_NAME = r"[A-Za-z][A-Za-z'’\-]*(?:[ \t]+[A-Za-z][A-Za-z'’\-]*){0,3}"
_NAMED_SPEAKER_RE = re.compile(
    r"(?i)^(?:Senator|Representative|General|Admiral|Colonel|Captain|"
    r"Secretary|Director|Rear Admiral|Vice Admiral|Brigadier General|"
    r"Major General|Lieutenant General|Lieutenant Colonel)[ \t]+"
    + _SPEAKER_NAME + r"\.(?:[ \t]+|$)"
)
_HONORIFIC_SPEAKER_RE = re.compile(
    r"(?i)^(?:Mr|Ms|Mrs|Dr)\.[ \t]+" + _SPEAKER_NAME
    + r"\.(?:[ \t]+|$)"
)
_STRUCTURAL_SPEAKER_RE = re.compile(
    r"(?i)^The[ \t]+(?:CHAIRMAN|CHAIRWOMAN|CHAIR|WITNESS|COUNSEL)\."
    r"(?:[ \t]+|$)"
)
_UNKNOWN_STRUCTURAL_RE = re.compile(
    r"^(?:The|THE)[ \t]+(?:[A-Z][A-Z \t-]*|"
    r"(?i:Staff Director))\.(?:[ \t]+|$)"
)
_GPO_TERMINAL_SEPARATOR_RE = re.compile(
    r"(?m)^[ \t]*_{3,}[ \t]*(?:\r?\n[ \t]*)*\Z"
)
MAX_STATEMENT_CHARS = 120_000

# Strip GPO page chrome on the granule HTML; html_to_text already drops
# nav/header/footer/script/style globally.
DEFAULT_STRIP_SELECTORS = (
    "nav", "header", "footer", ".site-header", ".site-footer",
)


@dataclass(frozen=True)
class StatementBlock:
    witness: str
    body: str
    heading_ordinal: int
    heading_start: int
    heading_end: int
    body_start: int
    body_end: int
    boundary_kind: str


@dataclass(frozen=True)
class BoundaryIssue:
    heading_ordinal: int
    heading_start: int
    reason: str


@dataclass
class SplitResult:
    statements: list[StatementBlock] = field(default_factory=list)
    issues: list[BoundaryIssue] = field(default_factory=list)


@dataclass
class ItemMeta:
    """One candidate written block parsed from a hearing transcript."""
    locator: str          # hearing granule HTM URL (key-free; key added at fetch)
    title: str = ""
    date: _dt.date | None = None
    package_id: str = ""
    granule_id: str = ""
    author: str = ""      # witness name parsed from the statement heading
    body_text: str = ""
    source_text_sha256: str = ""
    heading_ordinal: int = 0
    heading_start: int = 0
    heading_end: int = 0
    body_start: int = 0
    body_end: int = 0
    boundary_kind: str = ""


@dataclass
class ProcessOptions:
    """User-facing options + resolved defaults for the per-statement pipeline."""
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
    collection: str
    api_key: str
    output_dir: Path
    manifest_path: Path
    max_items: int
    min_words: int
    dry_run: bool
    allow_non_prose: bool
    strip_rules: str | None
    strip_aggressive: bool
    acquired_via: str


# ---- URL builders -------------------------------------------------


def _add_query(url: str, **params: Any) -> str:
    """Return ``url`` with ``params`` set in the query string.

    Existing params are preserved; duplicate keys are overwritten (so
    re-appending ``api_key`` to a ``nextPage`` URL that already carries one
    doesn't double it).
    """
    parts = urllib.parse.urlparse(url)
    q = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    q.update({k: str(v) for k, v in params.items()})
    return urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(q)))


def _govinfo_date(d: _dt.date) -> str:
    """GovInfo published-service date bound: ``YYYY-MM-DD``.

    The /published path rejects the dateTime form (``…T00:00:00Z`` → HTTP 400,
    "Use proper date format") and accepts the plain ``YYYY-MM-DD`` — verified
    against the live API 2026-06-11.
    """
    return d.isoformat()


def _published_url(
    start_dt: str, end_dt: str, key: str, *,
    collection: str = DEFAULT_COLLECTION, page_size: int = PAGE_SIZE,
    offset_mark: str = "*",
) -> str:
    base = f"{GOVINFO_API_BASE}/published/{start_dt}/{end_dt}"
    return _add_query(
        base, collection=collection, pageSize=page_size,
        offsetMark=offset_mark, api_key=key,
    )


def _granules_url(
    package_id: str, key: str, *, page_size: int = PAGE_SIZE,
    offset_mark: str = "*",
) -> str:
    base = f"{GOVINFO_API_BASE}/packages/{package_id}/granules"
    return _add_query(
        base, pageSize=page_size, offsetMark=offset_mark, api_key=key,
    )


def _granule_content_url(
    package_id: str, granule_id: str, *, content: str = "htm",
) -> str:
    """Key-free granule content URL.

    Deliberately omits the api_key so the value stored as an entry's
    ``source_url`` never embeds a credential; the key is added only at the
    fetch boundary in ``extract_one``.
    """
    return f"{GOVINFO_API_BASE}/packages/{package_id}/granules/{granule_id}/{content}"


# ---- Pagination ---------------------------------------------------


def _iter_pages(
    start_url: str, fetcher: ac.Fetcher, key: str, list_key: str,
) -> Iterator[dict[str, Any]]:
    """Yield each item from a GovInfo paged JSON list, following nextPage.

    Stops on the first unreachable / unparseable page (logged by the
    caller via an empty yield) and is bounded by ``MAX_PAGES``. The
    ``api_key`` is re-applied to each ``nextPage`` URL.
    """
    url: str | None = start_url
    pages = 0
    while url and pages < MAX_PAGES:
        result = fetcher.fetch(url)
        if not result.ok or not result.text:
            return
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError:
            return
        for item in data.get(list_key) or []:
            yield item
        next_url = data.get("nextPage")
        url = _add_query(next_url, api_key=key) if next_url else None
        pages += 1


# ---- Granule-title helpers ----------------------------------------


def _witness_name(heading_tail: str) -> str:
    """Witness name from a ``Prepared Statement of <Name>, <role>`` heading —
    the text up to the first comma. Informational, not load-bearing."""
    name = (heading_tail or "").split(",")[0].strip()
    return name or WITNESS_FALLBACK


def _speaker_kind(line: str) -> str | None:
    """Classify supported and visibly transcript-shaped physical lines."""
    if (
        _STRUCTURAL_SPEAKER_RE.match(line)
        or _NAMED_SPEAKER_RE.match(line)
        or _HONORIFIC_SPEAKER_RE.match(line)
    ):
        return "oral-speaker-turn"
    if _UNKNOWN_STRUCTURAL_RE.match(line):
        return "unsupported-speaker-turn"
    return None


def _split_prepared_statements(text: str) -> SplitResult:
    """Find candidate written blocks with a detected end or a named refusal.

    The source is decoded hearing text. Offsets refer to that exact string, and
    accepted body offsets exclude a terminal separator and surrounding space.
    A detected boundary is not a complete-work or authorship certification.
    """
    heads = list(PREPARED_HEADING_RE.finditer(text))
    result = SplitResult()
    for ordinal, head in enumerate(heads, start=1):
        witness = _witness_name(head.group(1))
        start = head.end()
        limit = heads[ordinal].start() if ordinal < len(heads) else len(text)
        boundary_end: int | None = None
        boundary_kind = ""
        refusal = ""
        offset = start
        open_quote = False

        for raw_line in text[start:limit].splitlines(keepends=True):
            line = raw_line.strip()
            line_start = offset
            offset += len(raw_line)
            if not line:
                continue
            # A standalone curly quote is a surviving textual cue. If a
            # transcript-shaped boundary appears before its close, refuse
            # this candidate rather than silently cutting a quotation.
            if line == "“":
                open_quote = True
                continue
            if line == "”" and open_quote:
                open_quote = False
                continue
            if open_quote and (
                _PROCEDURAL_END_RE.fullmatch(line)
                or _ORAL_HEADING_RE.match(line)
                or _speaker_kind(line)
            ):
                refusal = "ambiguous-quoted-boundary"
                break
            if _PROCEDURAL_END_RE.fullmatch(line):
                boundary_end = line_start
                boundary_kind = "procedural-bracket"
                break
            if line.startswith("[") and line.endswith("]"):
                if _CONTENT_BRACKET_RE.fullmatch(line):
                    continue
                refusal = (
                    "ambiguous-statement-transition"
                    if re.match(r"(?i)^\[The (?:prepared )?statement .*follows:\]$", line)
                    else "ambiguous-bracket-transition"
                )
                break
            if _ORAL_HEADING_RE.match(line):
                following = next(
                    (next_line.strip() for next_line in
                     text[offset:limit].splitlines() if next_line.strip()),
                    "",
                )
                paired = _speaker_kind(following) == "oral-speaker-turn"
                if paired:
                    boundary_end = line_start
                    boundary_kind = "oral-statement-heading"
                else:
                    refusal = "unpaired-oral-heading"
                break
            speaker_kind = _speaker_kind(line)
            if speaker_kind == "oral-speaker-turn":
                boundary_end = line_start
                boundary_kind = speaker_kind
                break
            if speaker_kind == "unsupported-speaker-turn":
                refusal = "ambiguous-speaker-turn"
                break

        if open_quote and not refusal and boundary_end is None:
            refusal = "ambiguous-open-quotation"
        if refusal:
            result.issues.append(BoundaryIssue(ordinal, head.start(), refusal))
            continue
        if boundary_end is None:
            if ordinal < len(heads):
                boundary_end = limit
                boundary_kind = "next-prepared-heading"
            else:
                reason = (
                    "overlong-statement" if limit - start > MAX_STATEMENT_CHARS
                    else "unbounded-eof"
                )
                result.issues.append(BoundaryIssue(ordinal, head.start(), reason))
                continue
        if boundary_end - start > MAX_STATEMENT_CHARS:
            result.issues.append(
                BoundaryIssue(ordinal, head.start(), "overlong-statement")
            )
            continue
        if boundary_kind == "next-prepared-heading":
            separator = _GPO_TERMINAL_SEPARATOR_RE.search(text[start:boundary_end])
            if separator:
                boundary_end = start + separator.start()

        raw_body = text[start:boundary_end]
        body_start = start + len(raw_body) - len(raw_body.lstrip())
        body_end = boundary_end - (len(raw_body) - len(raw_body.rstrip()))
        body = text[body_start:body_end]
        if not body:
            result.issues.append(
                BoundaryIssue(ordinal, head.start(), "empty-written-block")
            )
            continue
        result.statements.append(
            StatementBlock(
                witness=witness,
                body=body,
                heading_ordinal=ordinal,
                heading_start=head.start(),
                heading_end=head.end(),
                body_start=body_start,
                body_end=body_end,
                boundary_kind=boundary_kind,
            )
        )
    return result


# ---- Discovery ----------------------------------------------------


def discover_items(
    options: ProcessOptions, fetcher: ac.Fetcher, *,
    on_boundary_issue: Callable[[BoundaryIssue, str], None] | None = None,
) -> Iterable[ItemMeta]:
    """Page published -> packages -> hearing HTM; yield bounded candidates.

    CHRG packages are single whole-hearing granules, so per-witness statements
    are split out of the hearing text (``_split_prepared_statements``), not
    discovered as per-statement granules. The date window is applied on the
    package ``dateIssued``.
    """
    start_dt = _govinfo_date(options.since or _dt.date(2000, 1, 1))
    end_dt = _govinfo_date(options.until or _dt.date(2021, 12, 31))
    published = _published_url(
        start_dt, end_dt, options.api_key, collection=options.collection,
    )
    for pkg in _iter_pages(published, fetcher, options.api_key, "packages"):
        package_id = (pkg.get("packageId") or "").strip()
        if not package_id:
            continue
        date = ac.parse_iso_date(pkg.get("dateIssued"))
        if options.since and date and date < options.since:
            continue
        if options.until and date and date > options.until:
            continue
        # CHRG is single-granule; take the hearing granule's id.
        granule_id = ""
        for gran in _iter_pages(
            _granules_url(package_id, options.api_key), fetcher,
            options.api_key, "granules",
        ):
            granule_id = (gran.get("granuleId") or "").strip()
            if granule_id:
                break
        if not granule_id:
            continue
        # Fetch the hearing HTM (key at the fetch boundary only) and split it.
        locator = _granule_content_url(package_id, granule_id)
        result = fetcher.fetch(_add_query(locator, api_key=options.api_key))
        if not result.ok or not result.text:
            continue
        body_text, _title = ac.html_to_text(
            result.text, strip_selectors=DEFAULT_STRIP_SELECTORS,
        )
        split = _split_prepared_statements(body_text)
        for issue in split.issues:
            if on_boundary_issue is not None:
                on_boundary_issue(issue, locator)
        source_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
        for block in split.statements:
            yield ItemMeta(
                locator=locator,
                title=f"Prepared statement of {block.witness}",
                date=date,
                package_id=package_id,
                granule_id=granule_id,
                author=block.witness,
                body_text=block.body,
                source_text_sha256=source_hash,
                heading_ordinal=block.heading_ordinal,
                heading_start=block.heading_start,
                heading_end=block.heading_end,
                body_start=block.body_start,
                body_end=block.body_end,
                boundary_kind=block.boundary_kind,
            )


# ---- Extraction ---------------------------------------------------


def extract_one(
    item: ItemMeta, options: ProcessOptions, fetcher: ac.Fetcher,
) -> tuple[str, str, str, _dt.date | None]:
    """Return the statement text parsed during discovery.

    The body was extracted from the hearing HTM in ``discover_items`` (CHRG
    hearings are single granules holding many statements), so there is no
    per-item fetch here. ``("", "", "", None)`` skips an empty statement.
    """
    if not item.body_text:
        return "", "", "", None
    author = options.author or item.author or WITNESS_FALLBACK
    return item.body_text, item.title, author, item.date


# ---- Per-statement processing -------------------------------------


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
    """Preprocess -> length-gate -> hash -> dedupe -> piece. Mutates summary."""
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
        author=author or WITNESS_FALLBACK,
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
    piece: ac.AcquiredPiece, *, item: ItemMeta,
    options: ProcessOptions, summary: ac.RunSummary,
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
        extra_meta={
            "source_text_sha256": item.source_text_sha256,
            "heading_ordinal": item.heading_ordinal,
            "heading_start": item.heading_start,
            "heading_end": item.heading_end,
            "body_start": item.body_start,
            "body_end": item.body_end,
            "boundary_kind": item.boundary_kind,
            "role_review_status": "pending",
            "rights_review_status": "pending",
            "extraction_completeness_status": "pending_source_review",
        },
    )
    entry = ac.compose_manifest_entry(
        piece, text_path=text_path,
        manifest_relative_to=options.manifest_path.parent,
        language_status="unknown",
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
            "Stage bounded candidate prepared witness blocks from GovInfo CHRG "
            "with boundary evidence and pending source review. See "
            "references/acquire-govinfo-chrg-boundaries.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--api-key", default=None,
                   help="api.data.gov key. Defaults to $GOVINFO_API_KEY, "
                        "else the rate-limited public DEMO_KEY.")
    p.add_argument("--collection", default=DEFAULT_COLLECTION,
                   help="GovInfo collection (default: CHRG).")

    # Persona / impostor metadata.
    p.add_argument("--persona", default="chrg",
                   help="Persona slug for emitted entries (default: chrg).")
    p.add_argument("--author", default="",
                   help="Author display name override. Default: the witness "
                        "name parsed from each granule title.")
    p.add_argument("--impostor-for", nargs="+", required=True,
                   help=("Persona slug(s) this impostor pool serves "
                         "(required; the schema rejects empty)."))
    p.add_argument("--register", required=True,
                   help="Manifest register; use testimony_policy for CHRG.")
    p.add_argument("--register-match",
                   choices=["high", "medium", "low"], default="high")
    p.add_argument("--topic-match",
                   choices=["high", "medium", "low"], default="medium")
    p.add_argument("--consent-status", required=True,
                   choices=[
                       "public_record", "cc_licensed", "fair_use_research",
                       "author_consent", "undocumented",
                   ],
                   help="Source posture; public_record does not clear "
                        "third-party rights in a CHRG publication.")
    p.add_argument("--era",
                   choices=[
                       "pre_chatgpt", "pre_ai_widespread",
                       "post_ai_widespread", "undated",
                   ],
                   default="pre_chatgpt")

    # Date window + caps.
    p.add_argument("--since", default=DEFAULT_SINCE,
                   help=f"Inclusive lower-bound date (default: {DEFAULT_SINCE}).")
    p.add_argument("--until", default=DEFAULT_UNTIL,
                   help=f"Inclusive upper-bound date (default: {DEFAULT_UNTIL}).")
    p.add_argument("--max-items", type=int, default=400,
                   help="Maximum statements to acquire (default: 400).")
    p.add_argument("--min-words", type=int, default=1500,
                   help="Drop statements below this cleaned word count "
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
    ac.add_user_agent_arg(p)
    p.add_argument("--dry-run", action="store_true",
                   help="Inventory what would be acquired without writing.")
    ac.add_allow_empty_arg(p)
    p.add_argument("--allow-public-output", action="store_true",
                   help=("Allow writing outside ai-prose-baselines-private/. "
                         "Candidate source prose requires local review."))

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
    api_key = args.api_key or os.environ.get("GOVINFO_API_KEY") or DEMO_KEY

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

    acquired_via = f"acquire_govinfo_chrg_{_dt.date.today().isoformat()}"

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
        collection=args.collection,
        api_key=api_key,
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

    # Privacy guard up front.
    paths_to_check = [options.output_dir, options.manifest_path]
    if args.out:
        paths_to_check.append(Path(args.out).expanduser())
    ac.check_output_privacy(
        paths_to_check, allow_public=args.allow_public_output, tool=TOOL_NAME,
    )

    if options.api_key == DEMO_KEY:
        sys.stderr.write(
            "  note: using the rate-limited DEMO_KEY; pass --api-key (or set "
            "GOVINFO_API_KEY) for a bulk pull.\n"
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
        f"Acquiring CHRG prepared statements ({options.since}..{options.until}) "
        f"into {options.output_dir}\n"
        f"Persona: {options.persona} (impostor_for: {options.impostor_for})\n"
    )

    def record_boundary_issue(issue: BoundaryIssue, locator: str) -> None:
        summary.skipped_parse_error += 1
        summary.log_skip(
            reason=issue.reason, url=locator,
            detail=(f"heading_ordinal={issue.heading_ordinal} "
                    f"heading_start={issue.heading_start}"),
        )
        sys.stderr.write(
            f"  skipped candidate {issue.heading_ordinal} at "
            f"{locator}: {issue.reason}\n"
        )

    for item in discover_items(
        options, fetcher, on_boundary_issue=record_boundary_issue,
    ):
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
            emit_piece(piece, item=item, options=options, summary=summary)

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
            "No statements acquired. Verify the api key and the date window, "
            "(with --dry-run) that the hearings in range carry 'Prepared "
            "Statement of <Name>' headings (markups and short hearings have "
            "none), and pass --allow-empty to allow an empty run.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
