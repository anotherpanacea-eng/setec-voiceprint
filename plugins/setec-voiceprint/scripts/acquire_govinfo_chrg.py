#!/usr/bin/env python3
"""acquire_govinfo_chrg.py — pull prepared written congressional testimony.

Reads GovInfo's CHRG (congressional hearings) collection via the GovInfo
API and writes one ``.txt`` + ``.meta.json`` per admitted **prepared
written witness statement** into a private impostor pool, plus a draft
manifest with ``corpus_role: impostor`` entries.

The argument-dense material in a hearing is the prepared written witness
statement — not the oral Q&A colloquy, members' opening statements, or
procedural inserts. GovInfo breaks a hearing package into granules; this
acquirer keeps the granules whose title marks a prepared statement (the
written-statement quality gate), fetches each granule's HTML, extracts the
body, and admits statements at or above ``--min-words`` (default 1500).
CHRG is public domain (US-government work). The output builds the
``testimony_policy`` population baseline that ``argmove_profile.py``
profiles.

Source shape (best-effort; verify with --dry-run against the live API):

  published  GET /published/{startDT}/{endDT}?collection=CHRG&...&api_key=KEY
             -> {packages:[{packageId, dateIssued, title}], nextPage}
  granules   GET /packages/{packageId}/granules?...&api_key=KEY
             -> {granules:[{granuleId, title}], nextPage}
  granule    GET /packages/{packageId}/granules/{granuleId}/htm?api_key=KEY

An api.data.gov key is required: --api-key, else $GOVINFO_API_KEY, else the
rate-limited public DEMO_KEY. The exact endpoint shapes and the granule
title format are resolved tolerantly and MUST be spot-checked with
--dry-run before a bulk pull (see references/acquire-corpus-pattern.md).

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
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

# Resolve repo-relative imports the same way the other scripts do.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402

TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "acquire_govinfo_chrg"
SCRAPER_VERSION = "1.0"

GOVINFO_API_BASE = "https://api.govinfo.gov"
DEFAULT_COLLECTION = "CHRG"
DEFAULT_SINCE = "2000-01-01"
DEFAULT_UNTIL = "2021-12-31"
DEMO_KEY = "DEMO_KEY"  # rate-limited api.data.gov shared key
PAGE_SIZE = 100
MAX_PAGES = 10000      # safety bound on pagination loops
WITNESS_FALLBACK = "Congressional Witness"

# Granule-title prefixes that mark a prepared written statement. Lowercased
# prefix match; a few-shot prior, not exhaustive — the operator should
# spot-check real titles in --dry-run.
PREPARED_PREFIXES = (
    "prepared statement of",
    "prepared statement by",
)

# Strip GPO page chrome on the granule HTML; html_to_text already drops
# nav/header/footer/script/style globally.
DEFAULT_STRIP_SELECTORS = (
    "nav", "header", "footer", ".site-header", ".site-footer",
)


@dataclass
class ItemMeta:
    """One discovered prepared-statement granule."""
    locator: str          # granule HTM URL (api_key embedded)
    title: str = ""
    date: _dt.date | None = None
    package_id: str = ""
    granule_id: str = ""


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


def _govinfo_datetime(d: _dt.date) -> str:
    """GovInfo published-service datetime: ``YYYY-MM-DDT00:00:00Z``."""
    return d.isoformat() + "T00:00:00Z"


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


def _is_prepared_statement(title: str) -> bool:
    """True iff the granule title marks a prepared written statement."""
    t = (title or "").strip().lower()
    return any(t.startswith(p) for p in PREPARED_PREFIXES)


def _witness_from_title(title: str) -> str:
    """Best-effort witness name from ``Prepared statement of <NAME>, <role>``.

    Returns the text after ``of``/``by`` up to the first comma, or the
    fallback when the pattern doesn't match. Author is informational, not
    load-bearing for the population baseline.
    """
    m = re.match(r"\s*prepared statement (?:of|by)\s+(.+)", title or "", re.I)
    if not m:
        return WITNESS_FALLBACK
    name = m.group(1).split(",")[0].strip()
    return name or WITNESS_FALLBACK


# ---- Discovery ----------------------------------------------------


def discover_items(
    options: ProcessOptions, fetcher: ac.Fetcher,
) -> Iterable[ItemMeta]:
    """Page published -> packages -> granules; yield prepared-statement granules.

    The date window is applied on the package ``dateIssued`` (the published
    endpoint is already date-bounded, but re-filtering guards against an
    endpoint that returns adjacent material). Granules whose title is not a
    prepared statement (oral Q&A, member statements, procedural inserts) are
    dropped here — the written-statement quality gate.
    """
    start_dt = _govinfo_datetime(options.since) if options.since else _govinfo_datetime(_dt.date(2000, 1, 1))
    end_dt = _govinfo_datetime(options.until) if options.until else _govinfo_datetime(_dt.date(2021, 12, 31))
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
        granules_url = _granules_url(package_id, options.api_key)
        for gran in _iter_pages(granules_url, fetcher, options.api_key, "granules"):
            title = (gran.get("title") or "").strip()
            if not _is_prepared_statement(title):
                continue
            granule_id = (gran.get("granuleId") or "").strip()
            if not granule_id:
                continue
            yield ItemMeta(
                locator=_granule_content_url(package_id, granule_id),
                title=title, date=date,
                package_id=package_id, granule_id=granule_id,
            )


# ---- Extraction ---------------------------------------------------


def extract_one(
    item: ItemMeta, options: ProcessOptions, fetcher: ac.Fetcher,
) -> tuple[str, str, str, _dt.date | None]:
    """Fetch one granule's HTML and return (body_text, title, author, date).

    Returns ``("", "", "", None)`` to signal a silent skip (a 404 / empty
    granule is treated as a parse-error skip rather than aborting the run).
    """
    if not item.locator:
        return "", "", "", None
    # Add the api_key only here, at the fetch boundary — item.locator stays
    # credential-free so it can be stored as the entry's source_url.
    result = fetcher.fetch(_add_query(item.locator, api_key=options.api_key))
    if not result.ok or not result.text:
        return "", "", "", None
    body_text, _html_title = ac.html_to_text(
        result.text, strip_selectors=DEFAULT_STRIP_SELECTORS,
    )
    author = options.author or _witness_from_title(item.title)
    return body_text, item.title, author, item.date


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
            "Acquire prepared written congressional testimony from GovInfo "
            "CHRG into the impostor pool (the testimony_policy population "
            "baseline). See internal/SPEC_acquire_govinfo_chrg.md."
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
                   help="Consent / legal posture (use public_record for CHRG).")
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
            "No statements acquired. Verify the api key, the date window, "
            "and (with --dry-run) the granule-title filter + HTM endpoint.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
