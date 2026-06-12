#!/usr/bin/env python3
"""acquire_openalex_core.py — academic scholarly articles (OpenAlex + CORE).

Two-stage acquirer for the ``scholarly_article`` population baseline. The
academic genre's crux is the discipline filter — argumentative disciplines
(law, philosophy, social science, policy) over STEM lab reports, which are
not argument-shaped. So:

  Stage 1 (OpenAlex, keyless): the precise filter — discipline (domain /
    topic), open-access, publication-year window, not-retracted, type
    article — yielding candidate works with DOIs.
  Stage 2 (CORE, keyed): deliver the extracted ``fullText`` by DOI. CORE
    fullText is plain text, so there is no PDF/OCR and no HTML parsing.

Works that OpenAlex surfaces but CORE has no full text for are skipped
(``no-fulltext``). The discipline filter is OpenAlex's — a wrong filter
ingests STEM, so the operator owns ``--openalex-filter`` and should verify
hit counts and CORE coverage with ``--dry-run`` before a bulk pull
(see references/acquire-corpus-pattern.md).

CORE requires an api.core.ac.uk key: ``--api-key`` else ``$CORE_API_KEY``.
The key is added only at the fetch boundary; the stored ``source_url`` is
the clean DOI URL, so the credential never lands in the corpus manifest.

Privacy: output goes under ``ai-prose-baselines-private/impostors/
<register>/<persona>/`` and the privacy guard refuses paths outside any
directory named ``ai-prose-baselines-private``.

Usage:

    python3 scripts/acquire_openalex_core.py \\
        --api-key "$CORE_API_KEY" \\
        --persona scholar \\
        --impostor-for argscope_scholarly_article \\
        --register scholarly_article \\
        --consent-status cc_licensed \\
        --era pre_chatgpt \\
        --since 2010-01-01 --until 2021-12-31 \\
        --min-words 3000 --max-items 500

See ``internal/SPEC_acquire_openalex_core.md`` for design context.
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
TOOL_NAME = "acquire_openalex_core"
SCRAPER_VERSION = "1.0"

OPENALEX_BASE = "https://api.openalex.org"
CORE_BASE = "https://api.core.ac.uk/v3"
DEFAULT_SINCE = "2000-01-01"
DEFAULT_UNTIL = "2021-12-31"
PAGE_SIZE = 100
MAX_PAGES = 10000

# Default OpenAlex filter: open-access research articles, not retracted, in
# the Social Sciences domain (id 2 — covers law, political science,
# sociology, economics; broaden via --openalex-filter for philosophy /
# humanities fields). The date window is appended from --since/--until.
# Brittle by nature (domain ids drift) — the operator verifies hit counts
# in --dry-run.
DEFAULT_OPENALEX_FILTER = (
    "primary_topic.domain.id:2,is_oa:true,type:article,is_retracted:false"
)


@dataclass
class ItemMeta:
    """One candidate work discovered via OpenAlex."""
    locator: str          # DOI URL (stored source; credential-free)
    title: str = ""
    date: _dt.date | None = None
    doi: str = ""         # bare DOI for the CORE join
    author: str = "Unknown"


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
    openalex_filter: str
    core_api_key: str
    output_dir: Path
    manifest_path: Path
    max_items: int
    min_words: int
    dry_run: bool
    allow_non_prose: bool
    strip_rules: str | None
    strip_aggressive: bool
    acquired_via: str


# ---- URL + parse helpers ------------------------------------------


def _add_query(url: str, **params: Any) -> str:
    """Return ``url`` with ``params`` set (existing kept, duplicates overwritten)."""
    parts = urllib.parse.urlparse(url)
    q = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    q.update({k: str(v) for k, v in params.items()})
    return urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(q)))


def _bare_doi(doi: str) -> str:
    """Strip the doi.org prefix → bare DOI for the CORE query."""
    d = (doi or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.lower().startswith(prefix):
            d = d[len(prefix):]
            break
    return d.strip()


def _first_author(work: dict[str, Any]) -> str:
    auths = work.get("authorships") or []
    if auths:
        name = ((auths[0].get("author") or {}).get("display_name") or "").strip()
        if name:
            return name
    return "Unknown"


def _work_date(work: dict[str, Any]) -> _dt.date | None:
    d = ac.parse_iso_date(work.get("publication_date"))
    if d is None and work.get("publication_year"):
        d = ac.parse_iso_date(str(work.get("publication_year")))
    return d


def _openalex_works_url(
    filter_str: str, *, per_page: int = PAGE_SIZE, cursor: str = "*",
) -> str:
    return _add_query(
        f"{OPENALEX_BASE}/works",
        filter=filter_str, per_page=per_page, cursor=cursor,
    )


def _core_doi_search_url(doi: str) -> str:
    """CORE search-by-DOI URL, KEY-FREE (the api_key is added at fetch time)."""
    return _add_query(f"{CORE_BASE}/search/works", q=f'doi:"{doi}"', limit=1)


# ---- Stage 1: OpenAlex discovery ----------------------------------


def _iter_openalex(
    filter_str: str, fetcher: ac.Fetcher,
) -> Iterator[dict[str, Any]]:
    """Yield OpenAlex works, following cursor pagination, bounded by MAX_PAGES."""
    cursor: str | None = "*"
    pages = 0
    while cursor and pages < MAX_PAGES:
        result = fetcher.fetch(_openalex_works_url(filter_str, cursor=cursor))
        if not result.ok or not result.text:
            return
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError:
            return
        results = data.get("results") or []
        for work in results:
            yield work
        # Stop on an empty page even if a cursor is echoed, to avoid loops.
        if not results:
            return
        cursor = (data.get("meta") or {}).get("next_cursor")
        pages += 1


def discover_items(
    options: ProcessOptions, fetcher: ac.Fetcher,
) -> Iterable[ItemMeta]:
    """Iterate OpenAlex works under the discipline/OA/year filter; yield the
    ones that carry a DOI (required for the CORE full-text join)."""
    since = options.since or _dt.date(2000, 1, 1)
    until = options.until or _dt.date(2021, 12, 31)
    filter_str = (
        f"{options.openalex_filter}"
        f",from_publication_date:{since.isoformat()}"
        f",to_publication_date:{until.isoformat()}"
    )
    for work in _iter_openalex(filter_str, fetcher):
        doi = _bare_doi(work.get("doi") or "")
        if not doi:
            continue  # no DOI → can't join to CORE
        title = (work.get("title") or work.get("display_name") or "").strip()
        yield ItemMeta(
            locator=f"https://doi.org/{doi}",
            title=title,
            date=_work_date(work),
            doi=doi,
            author=_first_author(work),
        )


# ---- Stage 2: CORE full text --------------------------------------


def _core_fulltext(
    doi: str, options: ProcessOptions, fetcher: ac.Fetcher,
) -> str | None:
    """Fetch the CORE record for ``doi`` and return its fullText, or None.

    The api_key is added here, at the fetch boundary, so it never enters a
    stored URL.
    """
    if not doi:
        return None
    url = _add_query(_core_doi_search_url(doi), api_key=options.core_api_key)
    result = fetcher.fetch(url)
    if not result.ok or not result.text:
        return None
    try:
        data = json.loads(result.text)
    except json.JSONDecodeError:
        return None
    results = data.get("results") or []
    if not results:
        return None
    full_text = results[0].get("fullText")
    return full_text if full_text and full_text.strip() else None


def extract_one(
    item: ItemMeta, options: ProcessOptions, fetcher: ac.Fetcher,
) -> tuple[str, str, str, _dt.date | None]:
    """Resolve one work's full text via CORE. Returns ``("", …)`` to skip
    (CORE has no record / no fullText for this DOI)."""
    full_text = _core_fulltext(item.doi, options, fetcher)
    if not full_text:
        return "", "", "", None
    return full_text, item.title, options.author or item.author, item.date


# ---- Per-article processing ---------------------------------------


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

    An empty body is recorded as ``no-fulltext`` (CORE had no usable text)
    rather than the generic empty-body reason."""
    if not body_text or len(body_text.strip()) < 200:
        summary.skipped_filtered += 1
        summary.log_skip(
            reason="no-fulltext", url=item.locator,
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
        author=author or "Unknown",
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
            "Acquire academic scholarly articles via OpenAlex (discipline/OA "
            "filter) + CORE (full text) into the impostor pool (the "
            "scholarly_article population baseline). See "
            "internal/SPEC_acquire_openalex_core.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--api-key", default=None,
                   help="CORE (api.core.ac.uk) key. Defaults to $CORE_API_KEY.")
    p.add_argument("--openalex-filter", default=DEFAULT_OPENALEX_FILTER,
                   help="OpenAlex filter string (the date window is appended "
                        "from --since/--until). Tune the discipline filter "
                        "here; verify hit counts with --dry-run.")

    # Persona / impostor metadata.
    p.add_argument("--persona", default="scholar",
                   help="Persona slug for emitted entries (default: scholar).")
    p.add_argument("--author", default="",
                   help="Author display name override. Default: the first "
                        "author from OpenAlex.")
    p.add_argument("--impostor-for", nargs="+", required=True,
                   help=("Persona slug(s) this impostor pool serves "
                         "(required; the schema rejects empty)."))
    p.add_argument("--register", required=True,
                   help="Manifest register; use scholarly_article.")
    p.add_argument("--register-match",
                   choices=["high", "medium", "low"], default="high")
    p.add_argument("--topic-match",
                   choices=["high", "medium", "low"], default="medium")
    p.add_argument("--consent-status", required=True,
                   choices=[
                       "public_record", "cc_licensed", "fair_use_research",
                       "author_consent", "undocumented",
                   ],
                   help="Consent / legal posture (cc_licensed for the OA "
                        "CC-BY tier).")
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
    p.add_argument("--max-items", type=int, default=500,
                   help="Maximum articles to acquire (default: 500).")
    p.add_argument("--min-words", type=int, default=3000,
                   help="Drop articles below this cleaned word count "
                        "(default: 3000; academic articles run long).")

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
                   help=("Comma-separated subset of preprocessing rules. "
                         "Default: all standard rules."))
    p.add_argument("--strip-aggressive", action="store_true",
                   help="Also apply aggressive (link/citation) strip rules.")

    return p


def parse_options(args: argparse.Namespace) -> ProcessOptions:
    core_api_key = args.api_key or os.environ.get("CORE_API_KEY") or ""

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

    acquired_via = f"acquire_openalex_core_{_dt.date.today().isoformat()}"

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
        openalex_filter=args.openalex_filter,
        core_api_key=core_api_key,
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

    if not options.core_api_key:
        sys.stderr.write(
            "  warning: no CORE api key (--api-key / $CORE_API_KEY); CORE "
            "full-text fetches will fail and nothing will be acquired.\n"
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
        f"Acquiring scholarly articles ({options.since}..{options.until}) "
        f"into {options.output_dir}\n"
        f"OpenAlex filter: {options.openalex_filter}\n"
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

    if summary.acquired == 0 and not args.allow_empty and not any(
        s.get("reason") == "duplicate-hash" for s in summary.skip_log
    ):
        # Zero acquired with no duplicate-hash skip seen: nothing matched the
        # source/filters (a likely misconfiguration), not a dedupe-only rerun.
        sys.stderr.write(
            "No articles acquired and nothing matched the source/filters. "
            "Verify the CORE api key, the OpenAlex filter (--dry-run shows hit "
            "counts), and CORE full-text coverage; pass --allow-empty to allow "
            "an empty run.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
