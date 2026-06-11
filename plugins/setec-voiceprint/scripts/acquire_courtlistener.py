#!/usr/bin/env python3
"""acquire_courtlistener.py — legal briefs from CourtListener / RECAP.

Acquires appellate / amicus legal briefs from the Free Law Project's
CourtListener v4 REST API into the ``legal_brief`` population baseline.
CourtListener returns an extracted ``plain_text`` field for RECAP
documents, so there is no PDF parsing. Briefs are the most formally
adversarial argument genre (question presented, summary of argument,
propositional headings, adverse-authority treatment).

Source shape (best-effort; verify with --dry-run against the live API):

  search   GET {CL}/search/?type=rd&q=<query>&cursor=...   (Token header)
           -> {results:[{id, description, dateFiled, ...}], next}
  detail   GET {CL}/recap-documents/{id}/                  (Token header)
           -> {plain_text, description, ...}
  {CL} = https://www.courtlistener.com/api/rest/v4

Auth: CourtListener uses an ``Authorization: Token <key>`` HEADER (no
query-param fallback). The token is supplied via --api-key /
$COURTLISTENER_API_KEY and carried in the fetcher's header — never in a
URL — so it cannot leak into a stored ``source_url``.

Only granules whose ``description`` marks a brief / memorandum / amicus
are kept (the argument-density filter; motions, notices, and orders are
dropped). The min-words gate backstops. The brief filter and endpoint
shapes are best-effort — spot-check with --dry-run before a bulk pull
(see references/acquire-corpus-pattern.md).

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
DEFAULT_QUERY = "brief"
DEFAULT_SINCE = "2000-01-01"
DEFAULT_UNTIL = "2021-12-31"
MAX_PAGES = 10000
DEFAULT_AUTHOR = "Legal Filing"

# Description substrings that mark an argument-dense filing. A few-shot
# prior, not exhaustive — the operator spot-checks descriptions in
# --dry-run and the min-words gate backstops.
BRIEF_TERMS = ("brief", "memorandum", "amicus")


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


def _search_url(query: str) -> str:
    """First-page RECAP-document search URL (no cursor; no token in URL)."""
    return _add_query(f"{CL_BASE}/search/", type="rd", q=query)


def _recap_doc_url(doc_id: str) -> str:
    """RECAP-document detail URL — the clean stored source (token is a header)."""
    return f"{CL_BASE}/recap-documents/{doc_id}/"


def _is_brief(description: str) -> bool:
    d = (description or "").lower()
    return any(term in d for term in BRIEF_TERMS)


# ---- Discovery ----------------------------------------------------


def _iter_search(
    query: str, fetcher: ac.Fetcher,
) -> Iterator[dict[str, Any]]:
    """Yield CourtListener search results, following the `next` cursor URL.

    The auth token rides in the fetcher header, so `next` (a full URL with
    the cursor) is followed as-is. Bounded by MAX_PAGES.
    """
    url: str | None = _search_url(query)
    pages = 0
    while url and pages < MAX_PAGES:
        result = fetcher.fetch(url)
        if not result.ok or not result.text:
            return
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError:
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
    """Search RECAP documents and yield brief-type filings in the date window."""
    for res in _iter_search(options.query, fetcher):
        description = (res.get("description") or "").strip()
        if not _is_brief(description):
            continue
        doc_id = res.get("id")
        if doc_id in (None, ""):
            continue
        date = ac.parse_iso_date(res.get("dateFiled") or res.get("date_filed"))
        if options.since and date and date < options.since:
            continue
        if options.until and date and date > options.until:
            continue
        yield ItemMeta(
            locator=_recap_doc_url(str(doc_id)),
            title=description or "untitled",
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
            "Acquire legal briefs from CourtListener / RECAP into the impostor "
            "pool (the legal_brief population baseline). See "
            "internal/SPEC_acquire_courtlistener.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--api-key", default=None,
                   help="CourtListener token. Defaults to "
                        "$COURTLISTENER_API_KEY.")
    p.add_argument("--query", default=DEFAULT_QUERY,
                   help=f"RECAP search query (default: {DEFAULT_QUERY!r}). Tune "
                        "to target the briefs you want; verify with --dry-run.")

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
                   help="Manifest register; use legal_brief.")
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
    p.add_argument("--min-words", type=int, default=3000,
                   help="Drop filings below this cleaned word count "
                        "(default: 3000; briefs run long).")

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
        query=args.query,
        api_token=api_token,
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
            "No briefs acquired. Verify the CourtListener token, the --query, "
            "and (with --dry-run) the brief-type filter.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
