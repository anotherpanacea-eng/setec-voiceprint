#!/usr/bin/env python3
"""acquire_courtlistener.py — legal briefs from CourtListener / RECAP.

Acquires appellate / amicus legal briefs from the Free Law Project's
CourtListener v4 REST API into the ``legal_brief`` population baseline.
CourtListener returns an extracted ``plain_text`` field for RECAP
documents, so there is no PDF parsing. Briefs are the most formally
adversarial argument genre (question presented, summary of argument,
propositional headings, adverse-authority treatment).

Source shape (verified 2026-06-11 against the live v4 API):

  search   GET {CL}/search/?type=rd&q=<query>
           &filed_after=&filed_before=&order_by=score desc   (Token header)
           -> {results:[{id, short_description, snippet,
                         entry_date_filed, ...}], next}
  detail   GET {CL}/recap-documents/{id}/                     (Token header)
           -> {plain_text, description, ...}
  {CL} = https://www.courtlistener.com/api/rest/v4

Auth: CourtListener uses an ``Authorization: Token <key>`` HEADER (no
query-param fallback). The token is supplied via --api-key /
$COURTLISTENER_API_KEY and carried in the fetcher's header — never in a
URL — so it cannot leak into a stored ``source_url``.

Text availability is the binding constraint, and two real fields gate
discovery (the v4 search result's ``description`` is empty, and most RECAP
entries are docket stubs — summonses, notices, objections — with no
extracted text):

  * ``snippet`` is populated only when the document body is indexed, which
    is exactly when the detail endpoint returns a non-empty ``plain_text``.
    So a non-empty snippet is the text-availability gate — without it we
    would fetch a detail only to drop it for empty text.
  * ``short_description`` carries the brief / memorandum / amicus label;
    entries whose label is a non-brief docket action are dropped (the
    argument-density filter). The default --query is a full-text brief
    phrase so the search returns text-bearing briefs, not metadata hits.

The date is ``entry_date_filed``; the window is also applied server-side
via ``filed_after``/``filed_before``. The min-words gate backstops.
Spot-check with --dry-run before a bulk pull (see
references/acquire-corpus-pattern.md).

Privacy: output goes under ``ai-prose-baselines-private/impostors/
<register>/<persona>/`` and the privacy guard refuses paths outside any
directory named ``ai-prose-baselines-private``.

Usage:

    python3 scripts/acquire_courtlistener.py \\
        --api-key "$COURTLISTENER_API_KEY" \\
        --persona courtlistener \\
        --impostor-for argscope_legal_brief \\
        --register legal_brief \\
        --consent-status public_record \\
        --era pre_chatgpt \\
        --query "brief" --since 2005-01-01 --until 2021-12-31 \\
        --min-words 3000 --max-items 400

See ``internal/SPEC_acquire_courtlistener.md`` for design context.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402

TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "acquire_courtlistener"
SCRAPER_VERSION = "1.0"

CL_BASE = "https://www.courtlistener.com/api/rest/v4"
# A full-text phrase query: it forces matches in the document body (so the
# search returns text-bearing briefs with a populated snippet) rather than
# bare metadata hits. Operators tune --query for other brief shapes.
DEFAULT_QUERY = '"brief in support of"'
DEFAULT_SINCE = "2000-01-01"
DEFAULT_UNTIL = "2021-12-31"
MAX_PAGES = 10000
DEFAULT_AUTHOR = "Legal Filing"
# Search pagination is brittle on a single failed page (we need that page's
# `next` cursor to continue), so retry a failed page with linear backoff
# before giving up. This covers transient read-timeouts and short rate-limit
# throttles (HTTP 429). Module-level so tests can zero the sleep.
_SEARCH_RETRIES = 4
_RETRY_SLEEP_SECONDS = 2.0

# Description substrings that mark an argument-dense filing. A few-shot
# prior, not exhaustive — the operator spot-checks descriptions in
# --dry-run and the min-words gate backstops.
BRIEF_TERMS = ("brief", "memorandum", "amicus")

# Expert-affidavit / declaration document-type terms (the expert_affidavit
# register). Like BRIEF_TERMS, a short_description few-shot prior; the
# structural screen (_is_substantive_affidavit) is the real quality gate that
# rejects form affidavits, notary boilerplate, and one-paragraph stubs.
AFFIDAVIT_TERMS = (
    "affidavit", "declaration", "expert report", "expert witness",
    "expert disclosure", "verified statement",
)

# Per-doc-type defaults: the short_description filter terms, a full-text query
# that surfaces the type (so results are text-bearing, snippet-gated), and the
# word floor. --query / --min-words override; --register is always explicit.
DOC_TYPE_PROFILES = {
    "brief": {"terms": BRIEF_TERMS, "query": '"brief in support of"', "min_words": 3000},
    "affidavit": {"terms": AFFIDAVIT_TERMS, "query": '"declaration of"', "min_words": 1000},
}


@dataclass
class ItemMeta:
    """One candidate RECAP document discovered via search."""
    locator: str          # recap-document detail URL (token is header-only)
    title: str = ""
    date: _dt.date | None = None
    doc_id: str = ""


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
    query: str
    doc_type: str
    api_token: str
    output_dir: Path
    manifest_path: Path
    max_items: int
    min_words: int
    dry_run: bool
    allow_non_prose: bool
    strip_rules: str | None
    strip_aggressive: bool
    acquired_via: str


# ---- URL + header helpers -----------------------------------------


def _add_query(url: str, **params: Any) -> str:
    parts = urllib.parse.urlparse(url)
    q = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    q.update({k: str(v) for k, v in params.items()})
    return urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(q)))


def _auth_headers(token: str) -> dict[str, str]:
    """Header-auth dict for CourtListener (empty when no token)."""
    return {"Authorization": f"Token {token}"} if token else {}


def _search_url(
    query: str,
    since: _dt.date | None = None,
    until: _dt.date | None = None,
) -> str:
    """First-page RECAP-document search URL (no cursor; no token in URL).

    ``filed_after``/``filed_before`` apply the date window server-side so
    pagination stays inside it; ``order_by`` is left at relevance so the
    brief query surfaces text-bearing documents first."""
    params: dict[str, Any] = {"type": "rd", "q": query, "order_by": "score desc"}
    if since is not None:
        params["filed_after"] = since.isoformat()
    if until is not None:
        params["filed_before"] = until.isoformat()
    return _add_query(f"{CL_BASE}/search/", **params)


def _recap_doc_url(doc_id: str) -> str:
    """RECAP-document detail URL — the clean stored source (token is a header)."""
    return f"{CL_BASE}/recap-documents/{doc_id}/"


def _matches_doc_terms(description: str, terms: tuple[str, ...]) -> bool:
    """True iff the short_description contains any of the doc-type terms."""
    d = (description or "").lower()
    return any(term in d for term in terms)


def _is_brief(description: str) -> bool:
    """Back-compat wrapper: brief-type short_description filter."""
    return _matches_doc_terms(description, BRIEF_TERMS)


# Expert-affidavit structural screen. A real expert affidavit/declaration
# carries a qualifications/background section, stated opinions, and a
# basis/methodology; a form affidavit, notary block, or one-paragraph stub
# does not. Heuristic (tune in --dry-run); two of the three families present
# = substantive. Length is gated separately by --min-words.
_QUAL_RE = re.compile(
    r"\b(qualif|curriculum vitae|\bc\.?v\.?\b|education|experience|degrees?|"
    r"expert in|retained|engaged to|board[- ]certified)\b", re.I)
_OPINION_RE = re.compile(
    r"\b(opinions?|in my (?:professional |expert )?opinion|i conclude|"
    r"it is my opinion|reasonable degree of (?:scientific|professional|medical) "
    r"certainty)\b", re.I)
_BASIS_RE = re.compile(
    r"\b(based (?:up)?on|basis for|methodolog|reviewed|relied (?:up)?on|"
    r"in reaching|in forming|materials? (?:i )?considered)\b", re.I)


def _is_substantive_affidavit(text: str) -> bool:
    """True iff the text reads like a real expert affidavit/declaration:
    >=2 of the three families (qualifications, opinion, basis)."""
    families = sum(bool(rx.search(text)) for rx in (_QUAL_RE, _OPINION_RE, _BASIS_RE))
    return families >= 2


def _looks_like_ocr_garbage(text: str, *, sample: int = 4000) -> bool:
    """Heuristic OCR-garbage detector (FLP OCRs scanned filings; quality
    varies). Flags text with a very low alphabetic-token ratio or a high
    fraction of 1-2 character fragments — hallmarks of OCR noise."""
    toks = re.findall(r"\S+", text[:sample])
    if len(toks) < 50:
        return False  # too little to judge; --min-words handles it
    alpha = [t for t in toks if re.search(r"[A-Za-z]", t)]
    if not alpha:
        return True
    alpha_ratio = len(alpha) / len(toks)
    short_frac = sum(1 for t in alpha if len(t) <= 2) / len(alpha)
    return alpha_ratio < 0.55 or short_frac > 0.45


# ---- Discovery ----------------------------------------------------


def _fetch_search_page(fetcher: ac.Fetcher, url: str):
    """Fetch one search page, retrying a failed request with linear backoff.

    Returns the OK ``FetchResult``, or ``None`` after exhausting retries (a
    transient blip clears; a persistent 429/timeout is reported so the run
    doesn't end on a silent one-page hiccup)."""
    last_status = None
    for attempt in range(_SEARCH_RETRIES):
        result = fetcher.fetch(url)
        if result.ok:
            return result
        last_status = getattr(result, "status", None)
        if attempt < _SEARCH_RETRIES - 1:
            time.sleep(_RETRY_SLEEP_SECONDS * (attempt + 1))
    sys.stderr.write(
        f"  CourtListener search page failed after {_SEARCH_RETRIES} attempts "
        f"(last HTTP status {last_status}); stopping discovery. HTTP 429 means "
        "rate-limited — raise --rate-limit and re-run (dedupe makes it safe).\n"
    )
    return None


def _iter_search(
    query: str, fetcher: ac.Fetcher,
    since: _dt.date | None = None, until: _dt.date | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield CourtListener search results, following the `next` cursor URL.

    The auth token rides in the fetcher header, so `next` (a full URL with
    the cursor) is followed as-is. Bounded by MAX_PAGES. A failed page is
    retried with backoff (``_fetch_search_page``); only a persistent failure
    stops discovery, so one flaky/throttled page doesn't truncate the run.
    """
    url: str | None = _search_url(query, since, until)
    pages = 0
    while url and pages < MAX_PAGES:
        result = _fetch_search_page(fetcher, url)
        if result is None:
            return
        if not result.text:
            return
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError:
            sys.stderr.write(
                "  CourtListener search returned non-JSON; stopping discovery.\n"
            )
            return
        results = data.get("results") or []
        for item in results:
            yield item
        if not results:
            return
        url = data.get("next")
        pages += 1


def discover_items(
    options: ProcessOptions, fetcher: ac.Fetcher,
) -> Iterable[ItemMeta]:
    """Search RECAP documents and yield brief-type, text-bearing filings.

    Two real fields gate discovery (see the module docstring): a non-empty
    ``snippet`` means the body is indexed and the detail's ``plain_text``
    will be present; ``short_description`` carries the brief label (the v4
    search ``description`` is empty). The date is ``entry_date_filed``.
    """
    terms = DOC_TYPE_PROFILES[options.doc_type]["terms"]
    for res in _iter_search(options.query, fetcher, options.since, options.until):
        if not (res.get("snippet") or "").strip():
            continue  # body not indexed -> detail plain_text would be empty
        short_desc = (res.get("short_description") or "").strip()
        if not _matches_doc_terms(short_desc, terms):
            continue  # not the target document type
        doc_id = res.get("id")
        if doc_id in (None, ""):
            continue
        date = ac.parse_iso_date(
            res.get("entry_date_filed")
            or res.get("dateFiled") or res.get("date_filed")
        )
        if options.since and date and date < options.since:
            continue
        if options.until and date and date > options.until:
            continue
        yield ItemMeta(
            locator=_recap_doc_url(str(doc_id)),
            title=short_desc or "untitled",
            date=date,
            doc_id=str(doc_id),
        )


# ---- Extraction ---------------------------------------------------


def extract_one(
    item: ItemMeta, options: ProcessOptions, fetcher: ac.Fetcher,
) -> tuple[str, str, str, _dt.date | None]:
    """Fetch the RECAP-document detail and return (plain_text, title, author,
    date). Returns ``("", …)`` to skip on a missing/empty document."""
    result = fetcher.fetch(item.locator)
    if not result.ok or not result.text:
        return "", "", "", None
    try:
        data = json.loads(result.text)
    except json.JSONDecodeError:
        return "", "", "", None
    plain_text = data.get("plain_text")
    if not plain_text or not plain_text.strip():
        return "", "", "", None
    author = options.author or DEFAULT_AUTHOR
    return plain_text, item.title, author, item.date


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
    """Preprocess -> length-gate -> hash -> dedupe -> piece. Mutates summary."""
    if not body_text or len(body_text.strip()) < 200:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="no-text", url=item.locator, detail=f"len={len(body_text)}",
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

    # Affidavit-mode quality screens: reject OCR garbage and form/notary/stub
    # affidavits that pass the type filter but aren't substantive expert work.
    if options.doc_type == "affidavit":
        if _looks_like_ocr_garbage(cleaned):
            summary.skipped_parse_error += 1
            summary.log_skip(
                reason="ocr-garbage", url=item.locator, detail=f"words={word_count}",
            )
            return None
        if not _is_substantive_affidavit(cleaned):
            summary.skipped_filtered += 1
            summary.log_skip(
                reason="not-substantive-affidavit", url=item.locator,
                detail="missing >=2 of qualifications/opinion/basis",
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
            "Acquire legal briefs (--doc-type brief -> legal_brief) or expert "
            "affidavits/declarations (--doc-type affidavit -> expert_affidavit) "
            "from CourtListener / RECAP into the impostor pool. See "
            "internal/SPEC_acquire_courtlistener.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--api-key", default=None,
                   help="CourtListener token. Defaults to "
                        "$COURTLISTENER_API_KEY.")
    p.add_argument("--doc-type", choices=["brief", "affidavit"], default="brief",
                   help="Document type to target (default: brief). 'affidavit' "
                        "selects the declaration/affidavit short_description "
                        "filter, an affidavit-shaped default --query, and the "
                        "expert-affidavit structural + OCR screens.")
    p.add_argument("--query", default=None,
                   help="RECAP full-text search query. A phrase that appears in "
                        "the document body works best (text-bearing, snippet-"
                        "gated). Defaults per --doc-type "
                        f"(brief: {DOC_TYPE_PROFILES['brief']['query']!r}, "
                        f"affidavit: {DOC_TYPE_PROFILES['affidavit']['query']!r}). "
                        "Verify hit counts with --dry-run.")

    # Persona / impostor metadata.
    p.add_argument("--persona", default="courtlistener",
                   help="Persona slug for emitted entries "
                        "(default: courtlistener).")
    p.add_argument("--author", default="",
                   help="Author display name override (default: 'Legal Filing').")
    p.add_argument("--impostor-for", nargs="+", required=True,
                   help=("Persona slug(s) this impostor pool serves "
                         "(required; the schema rejects empty)."))
    p.add_argument("--register", required=True,
                   help="Manifest register; legal_brief for --doc-type brief, "
                        "expert_affidavit for --doc-type affidavit.")
    p.add_argument("--register-match",
                   choices=["high", "medium", "low"], default="high")
    p.add_argument("--topic-match",
                   choices=["high", "medium", "low"], default="medium")
    p.add_argument("--consent-status", required=True,
                   choices=[
                       "public_record", "cc_licensed", "fair_use_research",
                       "author_consent", "undocumented",
                   ],
                   help="Consent / legal posture (public_record for court "
                        "filings).")
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
                   help="Maximum briefs to acquire (default: 400).")
    p.add_argument("--min-words", type=int, default=None,
                   help="Drop filings below this cleaned word count. Defaults "
                        "per --doc-type (brief: 3000; affidavit: 1000).")

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
    api_token = args.api_key or os.environ.get("COURTLISTENER_API_KEY") or ""

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

    acquired_via = f"acquire_courtlistener_{_dt.date.today().isoformat()}"

    # Resolve doc-type-dependent defaults: an unset --query / --min-words
    # falls back to the profile for the chosen --doc-type.
    doc_type = getattr(args, "doc_type", "brief")
    profile = DOC_TYPE_PROFILES[doc_type]
    query = args.query or profile["query"]
    min_words = args.min_words if args.min_words is not None else profile["min_words"]

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
        query=query,
        doc_type=doc_type,
        api_token=api_token,
        output_dir=output_dir,
        manifest_path=manifest_path,
        max_items=args.max_items,
        min_words=min_words,
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

    if not options.api_token:
        sys.stderr.write(
            "  warning: no CourtListener token (--api-key / "
            "$COURTLISTENER_API_KEY); requests will be unauthenticated and "
            "rate-limited.\n"
        )

    if fetcher is None:
        fetcher = ac.make_requests_fetcher(
            version=SCRAPER_VERSION,
            rate_limit_seconds=args.rate_limit,
            user_agent=getattr(args, "user_agent", None) or None,
            extra_headers=_auth_headers(options.api_token),
        )

    summary = ac.RunSummary(
        draft_manifest_path=str(options.manifest_path) if not args.dry_run else None,
        output_dir=str(options.output_dir),
    )

    sys.stderr.write(
        f"Acquiring CourtListener briefs (q={options.query!r}, "
        f"{options.since}..{options.until}) into {options.output_dir}\n"
        f"Persona: {options.persona} (impostor_for: {options.impostor_for})\n"
    )

    for item in discover_items(options, fetcher):
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
            "No briefs acquired. Most RECAP entries have no extracted text "
            "(no snippet) or are non-brief docket actions. Verify the token, "
            "widen/tune --query to a phrase that appears in brief bodies, and "
            "spot-check the snippet/short_description fields with --dry-run.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
