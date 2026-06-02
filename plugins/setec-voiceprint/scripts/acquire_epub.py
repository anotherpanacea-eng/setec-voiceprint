#!/usr/bin/env python3
"""acquire_epub.py — acquire prose from EPUB ebooks into the impostor pool.

A local-source acquisition script (no network). Walks a directory of
``.epub`` files (or a single ``.epub``), reads each book's OPF package
metadata (title / author / date / language), and extracts the reading-
order spine into the impostor pool — one manifest entry per chapter by
default (``--segment chapter``), or one per whole book (``--segment book``).

Built on the shared pipeline in ``acquisition_core.py`` (preprocess →
content-hash dedupe → write .txt + .meta.json → emit draft manifest) and
the pattern in ``references/acquire-corpus-pattern.md``. Only the two
source-specific steps — ``discover_items`` and ``extract_one`` — are
EPUB-specific; everything downstream is shared.

Multi-author aware: a directory of books by different authors becomes one
pool, with each entry's ``persona`` derived from that book's author
(``author_to_persona_slug``). Pass ``--persona`` to force a single persona
(e.g. when ingesting one author's books).

EPUB only. ``.mobi`` / ``.azw3`` are reported and skipped (no clean stdlib
reader); convert them with Calibre ``ebook-convert book.mobi book.epub``
first if you need them.

Example (register-matched literary-horror impostor pool):

    python3 acquire_epub.py /path/to/ebooks \\
        --impostor-for my_fiction_persona \\
        --register literary_horror \\
        --consent-status fair_use_research \\
        --segment chapter --min-words 500 \\
        --max-items 100000 --dry-run

Then drop ``--dry-run`` and validate the draft with manifest_validator.py.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import posixpath
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402

# EPUB content documents are XHTML; html_to_text parses them with the HTML
# parser on purpose. Silence BeautifulSoup's "parsed XML as HTML" notice.
try:  # pragma: no cover - depends on bs4 being installed
    import warnings as _warnings
    from bs4 import XMLParsedAsHTMLWarning  # type: ignore

    _warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except Exception:
    pass

SOURCE_NAME = "epub"
TOOL_NAME = "acquire_epub"
SCRAPER_VERSION = "0.1"
TASK_SURFACE = "voice_coherence_acquisition"

EBOOK_SKIP_EXTS = {".mobi", ".azw", ".azw3", ".kfx", ".fb2", ".lit", ".pdb"}
XHTML_MEDIA = "application/xhtml+xml"
CONTAINER_PATH = "META-INF/container.xml"


# --------------- Source-specific dataclasses ---------------------


@dataclass
class ItemMeta:
    """One discovered item — a single chapter (or a whole book in
    ``--segment book`` mode). ``extra`` carries everything ``extract_one``
    needs without re-parsing the OPF."""
    locator: str  # "<epub-path>!<href>" for a chapter; "<epub-path>" for a book
    title: str = ""
    author: str = ""
    date: _dt.date | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessOptions:
    persona: str | None  # None => derive per-book from author
    impostor_for: list[str]
    register: str
    corpus_role: str
    use: list[str]
    ai_status: str
    register_match: str
    topic_match: str
    consent_status: str
    era: str | None  # None => derive per-book from date
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
    segment: str  # "chapter" | "book"
    min_words: int
    languages: list[str]  # accepted dc:language prefixes; empty => accept all
    source_extras: dict[str, Any] = field(default_factory=dict)


# --------------- EPUB parsing helpers ----------------------------


def _local(tag: str) -> str:
    """Local name of a possibly-namespaced ElementTree tag."""
    return tag.rsplit("}", 1)[-1].lower()


def _find_opf_path(zf: zipfile.ZipFile) -> str:
    """Resolve the OPF package-document path via META-INF/container.xml,
    falling back to the first ``*.opf`` in the archive."""
    try:
        root = ET.fromstring(zf.read(CONTAINER_PATH))
        for el in root.iter():
            if _local(el.tag) == "rootfile":
                fp = el.attrib.get("full-path")
                if fp:
                    return fp
    except (KeyError, ET.ParseError):
        pass
    opfs = [n for n in zf.namelist() if n.lower().endswith(".opf")]
    if not opfs:
        raise ValueError("no OPF package document found")
    return opfs[0]


def _parse_epub_date(text: str | None) -> _dt.date | None:
    """Tolerant date parse for OPF dc:date (often a full timestamp).
    Pulls the leading YYYY[-MM[-DD]]."""
    if not text:
        return None
    m = re.search(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", text)
    if not m:
        return None
    year = int(m.group(1))
    if not (1000 <= year <= 2100):
        return None
    month = int(m.group(2)) if m.group(2) else 1
    day = int(m.group(3)) if m.group(3) else 1
    try:
        return _dt.date(year, max(1, min(12, month)), max(1, min(28, day)))
    except ValueError:
        return _dt.date(year, 1, 1)


def _normalize_author(raw: str) -> str:
    """Normalize an OPF dc:creator string to a consistent ``First Last``
    display form so per-author personas don't fracture across publishers.

    Handles the common variations:
      * ``"Evenson, Brian"`` -> ``"Brian Evenson"`` (sortable "Last, First")
      * ``"Evenson, Brian Keith"`` -> ``"Brian Keith Evenson"``
      * multi-author (``";"`` / ``" & "`` / ``" and "`` / a 2+2 comma) ->
        keep the primary (first) author only

    Pseudonyms (e.g. a media tie-in pen name) are left distinct on purpose.
    """
    s = (raw or "").strip()
    for sep in (";", " & ", " and "):
        if sep in s:
            s = s.split(sep)[0].strip()
    if s.count(",") == 1:
        left, right = (p.strip() for p in s.split(","))
        if left and right and len(left.split()) == 1 and 1 <= len(right.split()) <= 2:
            s = f"{right} {left}"          # "Last, First[ Middle]"
        else:
            s = left                        # "Author One, Author Two" -> first
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class _EpubInfo:
    title: str
    author: str
    date: _dt.date | None
    language: str
    # ordered list of content-document hrefs (zip-internal, normalized)
    chapter_hrefs: list[str]


def _read_epub_info(path: Path) -> _EpubInfo:
    """Parse one EPUB's OPF: Dublin Core metadata + reading-order spine
    restricted to XHTML content documents."""
    with zipfile.ZipFile(path) as zf:
        opf_path = _find_opf_path(zf)
        opf_root = ET.fromstring(zf.read(opf_path))

        title = author = language = ""
        date_raw = ""
        manifest: dict[str, tuple[str, str]] = {}  # id -> (href, media_type)
        spine_ids: list[str] = []

        for el in opf_root.iter():
            tag = _local(el.tag)
            if tag == "title" and not title:
                title = (el.text or "").strip()
            elif tag == "creator" and not author:
                author = (el.text or "").strip()
            elif tag == "date" and not date_raw:
                date_raw = (el.text or "").strip()
            elif tag == "language" and not language:
                language = (el.text or "").strip()
            elif tag == "item":
                iid = el.attrib.get("id", "")
                href = el.attrib.get("href", "")
                media = el.attrib.get("media-type", "")
                if iid and href:
                    manifest[iid] = (href, media)
            elif tag == "itemref":
                idref = el.attrib.get("idref", "")
                if idref:
                    spine_ids.append(idref)

        opf_dir = posixpath.dirname(opf_path)
        chapter_hrefs: list[str] = []
        for sid in spine_ids:
            href_media = manifest.get(sid)
            if not href_media:
                continue
            href, media = href_media
            is_xhtml = media == XHTML_MEDIA or href.lower().split("#")[0].endswith(
                (".xhtml", ".html", ".htm")
            )
            if not is_xhtml:
                continue
            full = posixpath.normpath(posixpath.join(opf_dir, href.split("#")[0]))
            chapter_hrefs.append(full)

        return _EpubInfo(
            title=title or path.stem,
            author=_normalize_author(author) or "Unknown",
            date=_parse_epub_date(date_raw),
            language=language,
            chapter_hrefs=chapter_hrefs,
        )


def _era_from_date(date: _dt.date | None) -> str:
    """Map a publication date to the manifest ``era`` enum.

    pre_chatgpt (< Nov 2022) / pre_ai_widespread (Nov 2022 .. mid-2024) /
    post_ai_widespread (>= mid-2024) / undated.
    """
    if date is None:
        return "undated"
    if date < _dt.date(2022, 11, 1):
        return "pre_chatgpt"
    if date < _dt.date(2024, 7, 1):
        return "pre_ai_widespread"
    return "post_ai_widespread"


def _language_ok(language: str, accepted: list[str]) -> bool:
    if not accepted:
        return True
    if not language:
        return True  # unknown language: don't exclude
    lang = language.lower()
    return any(lang.startswith(p.lower()) for p in accepted)


# --------------- Source-specific helpers -------------------------


def discover_items(
    source: str,
    options: ProcessOptions,
    fetcher: ac.Fetcher | None = None,
) -> Iterable[ItemMeta]:
    """Yield one ItemMeta per chapter (``--segment chapter``) or per book
    (``--segment book``) across the EPUB(s) at ``source``."""
    src = Path(source).expanduser()
    if src.is_dir():
        epubs = sorted(p for p in src.glob("*.epub"))
        skipped = sorted(
            p for p in src.iterdir()
            if p.is_file() and p.suffix.lower() in EBOOK_SKIP_EXTS
        )
        if skipped:
            sys.stderr.write(
                f"Note: skipping {len(skipped)} non-EPUB ebook file(s) "
                f"(no stdlib reader): {', '.join(p.name for p in skipped[:5])}"
                f"{' ...' if len(skipped) > 5 else ''}\n"
            )
    elif src.suffix.lower() == ".epub":
        epubs = [src]
    else:
        raise ValueError(f"source must be a directory or a .epub file: {source}")

    for epub in epubs:
        try:
            info = _read_epub_info(epub)
        except Exception as exc:  # malformed EPUB
            sys.stderr.write(f"  skip (parse): {epub.name}: {exc}\n")
            continue

        if not _language_ok(info.language, options.languages):
            sys.stderr.write(
                f"  skip (language={info.language!r}): {epub.name}\n"
            )
            continue

        persona = options.persona or ac.author_to_persona_slug(info.author)
        era = options.era or _era_from_date(info.date)
        common = {
            "epub_path": str(epub),
            "persona": persona,
            "era": era,
            "book_title": info.title,
            "language": info.language,
        }

        if options.segment == "book":
            yield ItemMeta(
                locator=str(epub),
                title=info.title,
                author=info.author,
                date=info.date,
                extra={**common, "hrefs": info.chapter_hrefs},
            )
        else:  # chapter
            for idx, href in enumerate(info.chapter_hrefs, start=1):
                yield ItemMeta(
                    locator=f"{epub}!{href}",
                    title=f"{info.title} ch{idx:02d}",
                    author=info.author,
                    date=info.date,
                    extra={**common, "href": href, "chapter_index": idx},
                )


def extract_one(
    item: ItemMeta,
    source: str,
    options: ProcessOptions,
    fetcher: ac.Fetcher | None = None,
) -> tuple[str, str, str, _dt.date | None]:
    """Return ``(body_text, title, author, date)`` for one chapter/book.
    XHTML is converted to plain text via the shared ``html_to_text``."""
    epub_path = item.extra["epub_path"]
    with zipfile.ZipFile(epub_path) as zf:
        if options.segment == "book":
            parts: list[str] = []
            for href in item.extra.get("hrefs", []):
                html = _read_member(zf, href)
                if html is None:
                    continue
                text, _ = ac.html_to_text(html)
                if text and text.strip():
                    parts.append(text.strip())
            body = "\n\n".join(parts)
        else:
            html = _read_member(zf, item.extra["href"])
            body = ""
            if html is not None:
                text, _ = ac.html_to_text(html)
                body = text or ""

    return body, item.title, item.author, item.date


def _read_member(zf: zipfile.ZipFile, href: str) -> str | None:
    """Read a zip member as UTF-8 text, tolerating path-normalization
    differences. Returns None if absent."""
    try:
        return zf.read(href).decode("utf-8", "replace")
    except KeyError:
        # Some EPUBs store members with a leading path component the OPF
        # dir join didn't capture; try a basename match as a fallback.
        base = posixpath.basename(href)
        for name in zf.namelist():
            if posixpath.basename(name) == base:
                return zf.read(name).decode("utf-8", "replace")
        return None


def build_acquired_via_tag() -> str:
    return f"acquire_{SOURCE_NAME}_{_dt.date.today().isoformat()}"


# --------------- Per-item processing -----------------------------


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
    """Preprocess → word-floor → hash → dedupe → AcquiredPiece. Returns
    the piece on success, ``None`` on skip; mutates ``summary``.

    EPUB-specific deltas from the template's shared version:
      * per-item ``persona`` and ``era`` (a book corpus is multi-author
        and spans publication eras), pulled from ``item.extra``
      * a configurable ``--min-words`` floor on the cleaned text, which
        drops front/back matter (title pages, TOC, copyright, colophon).
    """
    if options.since and date and date < options.since:
        summary.skipped_filtered += 1
        summary.log_skip(reason="out-of-window-before", url=item.locator,
                         detail=date.isoformat() if date else "")
        return None
    if options.until and date and date > options.until:
        summary.skipped_filtered += 1
        summary.log_skip(reason="out-of-window-after", url=item.locator,
                         detail=date.isoformat() if date else "")
        return None

    if not body_text or len(body_text.strip()) < 200:
        summary.skipped_parse_error += 1
        summary.log_skip(reason="empty-body", url=item.locator,
                         detail=f"len={len(body_text)}")
        return None

    cleaned, prep_meta = ac.preprocess_text(
        body_text,
        rules=options.strip_rules,
        allow_non_prose=options.allow_non_prose,
        strip_aggressive=options.strip_aggressive,
    )
    if not cleaned or len(cleaned.strip()) < 200:
        summary.skipped_parse_error += 1
        summary.log_skip(reason="empty-after-preprocess", url=item.locator,
                         detail=f"raw={len(body_text)} clean={len(cleaned)}")
        return None

    word_count = len(re.findall(r"\S+", cleaned))
    if word_count < options.min_words:
        summary.skipped_filtered += 1
        summary.log_skip(reason="below-min-words", url=item.locator,
                         detail=f"words={word_count} < {options.min_words}")
        return None

    eff_persona = options.persona or item.extra.get("persona") or "unknown_personal"
    eff_era = options.era or item.extra.get("era") or "undated"

    piece = ac.AcquiredPiece(
        title=title or item.title or "untitled",
        author=author or item.author or "Unknown",
        persona=eff_persona,
        register=options.register,
        date_written=date or item.date,
        source_url=item.locator,
        cleaned_text=cleaned,
        raw_byte_length=len(body_text.encode("utf-8")),
        preprocessing_meta=prep_meta,
        acquired_via=options.acquired_via,
        consent_status=options.consent_status,
        era=eff_era,
        register_match=options.register_match,
        topic_match=options.topic_match,
        impostor_for=list(options.impostor_for),
    )

    existing = ac.content_hash_already_present(piece.content_hash, options.output_dir)
    if existing is not None:
        summary.skipped_duplicate += 1
        summary.log_skip(reason="duplicate-hash", url=item.locator,
                         detail=str(existing))
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
    if options.dry_run:
        sys.stderr.write(
            f"  [dry-run] would write {piece.filename_stem()} "
            f"({piece.word_count} words, persona={piece.persona})\n"
        )
        summary.acquired += 1
        return
    text_path, _ = ac.write_piece(
        piece, output_dir=options.output_dir, scraper_version=SCRAPER_VERSION,
    )
    entry = ac.compose_manifest_entry(
        piece, text_path=text_path,
        manifest_relative_to=options.manifest_path.parent,
        corpus_role=options.corpus_role, use=options.use, ai_status=options.ai_status,
    )
    ac.append_manifest_entry(options.manifest_path, entry)
    summary.acquired += 1
    sys.stderr.write(f"  acquired {text_path.name} ({piece.word_count} words)\n")


# --------------- CLI --------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Acquire prose from EPUB ebooks into the impostor pool. "
            "See references/acquire-corpus-pattern.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("source", help="Directory of .epub files, or a single .epub.")

    p.add_argument("--persona",
                   help="Force a single persona slug for every entry. "
                        "Omit to derive per-book from the author.")
    p.add_argument("--impostor-for", nargs="*", default=[],
                   help="Persona slug(s) this impostor pool serves "
                        "(required when --corpus-role impostor).")
    p.add_argument("--register", required=True,
                   help="Manifest register; e.g. literary_horror.")
    p.add_argument("--corpus-role", choices=["impostor", "identity_baseline"],
                   default="impostor",
                   help="impostor pool (default) or the writer's own identity baseline.")
    p.add_argument("--ai-status",
                   choices=["pre_ai_human", "ai_assisted", "ai_edited", "ai_generated",
                            "ai_generated_from_outline", "mixed", "unknown"],
                   default="pre_ai_human",
                   help="AI-involvement label for the emitted entries.")
    p.add_argument("--use", nargs="+", default=None,
                   help="Manifest use tags (default: voice_impostor / voice_profile).")
    p.add_argument("--register-match", choices=["high", "medium", "low"],
                   default="high")
    p.add_argument("--topic-match", choices=["high", "medium", "low"],
                   default="medium")
    p.add_argument("--consent-status", required=True,
                   choices=["public_record", "cc_licensed", "fair_use_research",
                            "author_consent", "undocumented"])
    p.add_argument("--era",
                   choices=["pre_chatgpt", "pre_ai_widespread",
                            "post_ai_widespread", "undated"],
                   default=None,
                   help="Force a single era. Omit to derive per-book from "
                        "the OPF dc:date.")

    p.add_argument("--segment", choices=["chapter", "book"], default="chapter",
                   help="One entry per spine chapter (default) or per book.")
    p.add_argument("--min-words", type=int, default=500,
                   help="Drop segments with fewer than N cleaned words "
                        "(default 500; filters front/back matter).")
    p.add_argument("--languages", nargs="*", default=["en"],
                   help="Accepted dc:language prefixes (default: en). "
                        "Pass nothing after the flag to accept all.")

    p.add_argument("--since", help="Inclusive lower-bound date (YYYY-MM-DD).")
    p.add_argument("--until", help="Inclusive upper-bound date (YYYY-MM-DD).")
    p.add_argument("--max-items", type=int, default=100000,
                   help="Cap on acquired entries per run (default 100000).")

    p.add_argument("--output-dir",
                   help="Where .txt + .meta.json go. Defaults to "
                        "<baselines>/impostors/<register>/<persona-or-'pool'>/.")
    p.add_argument("--emit-manifest",
                   help="Draft manifest JSONL path. Defaults to "
                        "<output-dir>/draft_manifest.jsonl.")
    p.add_argument("--out", help="Write summary report (JSON) here.")

    p.add_argument("--dry-run", action="store_true",
                   help="Inventory what would be acquired without writing.")
    p.add_argument("--allow-public-output", action="store_true",
                   help="Allow output outside ai-prose-baselines-private/. "
                        "Acquired prose is voice-cloning input.")

    p.add_argument("--allow-non-prose", action="store_true")
    p.add_argument("--strip-rules")
    p.add_argument("--strip-aggressive", action="store_true")
    return p


def parse_options(args: argparse.Namespace) -> ProcessOptions:
    persona = args.persona  # may be None => derive per-book (impostor mode)
    corpus_role = args.corpus_role
    if corpus_role == "impostor" and not args.impostor_for:
        raise SystemExit("acquire_epub: --impostor-for is required with --corpus-role impostor")
    if corpus_role == "identity_baseline" and not persona:
        raise SystemExit("acquire_epub: --persona is required with --corpus-role identity_baseline")
    use = args.use or (["voice_impostor"] if corpus_role == "impostor" else ["voice_profile"])
    dir_slug = persona or "pool"
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
    elif corpus_role == "impostor":
        output_dir = ac.default_output_dir(register=args.register,
                                           author_slug=dir_slug)
    else:
        output_dir = ac.resolve_baselines_dir() / "identity" / args.register / dir_slug
    if args.emit_manifest:
        manifest_path = Path(args.emit_manifest).expanduser()
    else:
        manifest_path = output_dir / "draft_manifest.jsonl"

    return ProcessOptions(
        persona=persona,
        impostor_for=list(args.impostor_for or []),
        register=args.register,
        corpus_role=corpus_role,
        use=list(use),
        ai_status=args.ai_status,
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
        segment=args.segment,
        min_words=args.min_words,
        languages=list(args.languages or []),
    )


def run(args: argparse.Namespace, fetcher: ac.Fetcher | None = None) -> int:
    options = parse_options(args)

    paths_to_check: list[Path] = [options.output_dir, options.manifest_path]
    if args.out:
        paths_to_check.append(Path(args.out).expanduser())
    ac.check_output_privacy(
        paths_to_check, allow_public=args.allow_public_output, tool=TOOL_NAME,
    )

    summary = ac.RunSummary(
        draft_manifest_path=str(options.manifest_path) if not options.dry_run else None,
        output_dir=str(options.output_dir),
    )

    sys.stderr.write(
        f"Acquiring EPUBs from {args.source} into {options.output_dir}\n"
        f"segment={options.segment} min_words={options.min_words} "
        f"register={options.register} impostor_for={options.impostor_for}\n"
        f"persona={'per-book' if options.persona is None else options.persona} "
        f"era={'per-book' if options.era is None else options.era}\n"
    )

    for item in discover_items(args.source, options):
        if summary.acquired >= options.max_items:
            break
        try:
            body_text, title, author, date = extract_one(item, args.source, options)
        except Exception as exc:
            summary.skipped_parse_error += 1
            summary.log_skip(reason="extract-error", url=item.locator,
                             detail=f"{type(exc).__name__}: {exc}")
            continue

        piece = process_one_item(item, body_text, title, author, date,
                                 options=options, summary=summary)
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
            "No items acquired. Verify the source path contains .epub files "
            "and the filters aren't excluding everything.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
