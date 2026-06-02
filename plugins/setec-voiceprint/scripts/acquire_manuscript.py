#!/usr/bin/env python3
"""acquire_manuscript.py — ingest local prose manuscripts as IDENTITY-baseline
entries (the writer's own voice), or as impostor entries with --corpus-role.

A local-source acquisition script for `.docx` / `.md` / `.markdown` / `.txt`
files, built on the shared acquisition_core pipeline and the acquire-corpus
pattern. Unlike the impostor acquirers, this defaults to
``corpus_role=identity_baseline`` / ``use=[voice_profile]`` — the corpus a
draft is compared *against* (Burrows Delta, General Imposters, voice drift).

Implements the two source-specific steps:
  * discover_items: walk the file(s), segment each work into chapters
    (Markdown ATX headings / Word heading styles) or fixed word-windows.
  * extract_one: return the (already-segmented) plain text.

Everything else (preprocess -> content-hash dedupe -> .txt/.meta.json ->
draft manifest -> privacy guard) is shared.

DOCX is read with the stdlib (it is zipped XML); no python-docx dependency.

Example (build a personal identity baseline):

    python3 acquire_manuscript.py /path/to/my_novel.md \\
        --persona my_pen_name_fiction --register literary_horror \\
        --consent-status author_consent --ai-status pre_ai_human \\
        --segment chapter --min-words 300
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
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

SOURCE_NAME = "manuscript"
TOOL_NAME = "acquire_manuscript"
SCRAPER_VERSION = "0.1"
TASK_SURFACE = "voice_coherence_acquisition"

TEXT_EXTS = {".md", ".markdown", ".txt"}
DOCX_EXT = ".docx"
DOCX_NS_DOC = "word/document.xml"


@dataclass
class ItemMeta:
    locator: str
    title: str = ""
    author: str = ""
    date: _dt.date | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessOptions:
    persona: str
    author: str
    register: str
    corpus_role: str
    use: list[str]
    ai_status: str
    consent_status: str
    era: str | None
    impostor_for: list[str]
    register_match: str
    topic_match: str
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
    segment: str
    window_words: int
    min_words: int
    source_extras: dict[str, Any] = field(default_factory=dict)


# --------------- text/docx extraction ----------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _attr_val(el: ET.Element) -> str | None:
    for k, v in el.attrib.items():
        if k.rsplit("}", 1)[-1] == "val":
            return v
    return None


def _docx_paragraphs(path: Path) -> list[tuple[str, bool]]:
    """Return [(paragraph_text, is_heading)] from a .docx (stdlib only)."""
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read(DOCX_NS_DOC))
    paras: list[tuple[str, bool]] = []
    for p in root.iter():
        if _local(p.tag) != "p":
            continue
        texts: list[str] = []
        is_heading = False
        for el in p.iter():
            ln = _local(el.tag)
            if ln == "t" and el.text:
                texts.append(el.text)
            elif ln in ("tab",):
                texts.append("\t")
            elif ln == "pstyle":
                val = (_attr_val(el) or "").lower()
                if val.startswith("heading") or val == "title":
                    is_heading = True
        paras.append(("".join(texts).strip(), is_heading))
    return paras


_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")


def _strip_markdown(text: str) -> str:
    """Light Markdown -> prose: drop heading markers, emphasis, link syntax,
    images, code fences. Keeps the words, not the markup."""
    out: list[str] = []
    in_code = False
    for ln in text.splitlines():
        if ln.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        ln = _MD_HEADING.sub("", ln)                       # heading markers
        ln = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", ln)       # images
        ln = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", ln)   # links -> text
        ln = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", ln)  # emphasis
        ln = re.sub(r"^\s{0,3}>\s?", "", ln)               # blockquote
        out.append(ln)
    return "\n".join(out)


def _window_split(text: str, n_words: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks = [" ".join(words[i:i + n_words]) for i in range(0, len(words), n_words)]
    return chunks


def _segment_markdown(text: str, segment: str, window_words: int) -> list[str]:
    if segment == "work":
        return [_strip_markdown(text)]
    if segment == "chapter":
        lines = text.splitlines()
        segs: list[list[str]] = []
        cur: list[str] = []
        for ln in lines:
            if _MD_HEADING.match(ln):
                if any(s.strip() for s in cur):
                    segs.append(cur)
                cur = [ln]
            else:
                cur.append(ln)
        if any(s.strip() for s in cur):
            segs.append(cur)
        joined = [_strip_markdown("\n".join(s)) for s in segs]
        if len(joined) >= 2:
            return joined
        # fall back to windows when there are no real chapter headings
    return _window_split(_strip_markdown(text), window_words)


def _segment_docx(path: Path, segment: str, window_words: int) -> list[str]:
    paras = _docx_paragraphs(path)
    if segment == "work":
        return ["\n".join(t for t, _ in paras if t)]
    if segment == "chapter":
        segs: list[list[str]] = []
        cur: list[str] = []
        for text, is_heading in paras:
            if is_heading:
                if any(s.strip() for s in cur):
                    segs.append(cur)
                cur = [text] if text else []
            else:
                if text:
                    cur.append(text)
        if any(s.strip() for s in cur):
            segs.append(cur)
        joined = ["\n".join(s) for s in segs]
        if len(joined) >= 2:
            return joined
    whole = "\n".join(t for t, _ in paras if t)
    return _window_split(whole, window_words)


def _segment_plaintext(text: str, segment: str, window_words: int) -> list[str]:
    if segment == "work":
        return [text]
    return _window_split(text, window_words)


def _work_title(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\b(FINAL|DRAFT|v\d+|copy)\b", "", stem, flags=re.I)
    return re.sub(r"\s+", " ", stem).strip() or path.stem


# --------------- discover / extract ------------------------------


def discover_items(
    source: str,
    options: ProcessOptions,
    fetcher: ac.Fetcher | None = None,
) -> Iterable[ItemMeta]:
    src = Path(source).expanduser()
    if src.is_dir():
        files = sorted(
            p for p in src.rglob("*")
            if p.is_file() and p.suffix.lower() in (TEXT_EXTS | {DOCX_EXT})
        )
    elif src.suffix.lower() in (TEXT_EXTS | {DOCX_EXT}):
        files = [src]
    else:
        raise ValueError(f"source must be a .docx/.md/.txt file or a directory: {source}")

    for f in files:
        title = _work_title(f)
        try:
            mtime = _dt.date.fromtimestamp(f.stat().st_mtime)
        except Exception:
            mtime = None
        ext = f.suffix.lower()
        try:
            if ext == DOCX_EXT:
                segs = _segment_docx(f, options.segment, options.window_words)
            elif ext in (".md", ".markdown"):
                segs = _segment_markdown(
                    f.read_text(encoding="utf-8", errors="replace"),
                    options.segment, options.window_words,
                )
            else:
                segs = _segment_plaintext(
                    f.read_text(encoding="utf-8", errors="replace"),
                    options.segment, options.window_words,
                )
        except Exception as exc:
            sys.stderr.write(f"  skip (parse): {f.name}: {exc}\n")
            continue

        for idx, seg_text in enumerate(segs, start=1):
            label = f"{options.segment[:2]}{idx:02d}"
            yield ItemMeta(
                locator=f"{f}#{label}",
                title=f"{title} {label}" if len(segs) > 1 else title,
                author=options.author,
                date=mtime,
                extra={
                    "text": seg_text,
                    "work_title": title,
                    "seg_index": idx,
                    "ai_status": options.ai_status,
                },
            )


def extract_one(
    item: ItemMeta,
    source: str,
    options: ProcessOptions,
    fetcher: ac.Fetcher | None = None,
) -> tuple[str, str, str, _dt.date | None]:
    return item.extra.get("text", ""), item.title, item.author, item.date


def build_acquired_via_tag() -> str:
    return f"acquire_{SOURCE_NAME}_{_dt.date.today().isoformat()}"


# --------------- per-item processing -----------------------------


def process_one_item(
    item: ItemMeta, body_text: str, title: str, author: str, date: _dt.date | None,
    *, options: ProcessOptions, summary: ac.RunSummary,
) -> Optional[ac.AcquiredPiece]:
    if not body_text or len(body_text.strip()) < 200:
        summary.skipped_parse_error += 1
        summary.log_skip(reason="empty-body", url=item.locator, detail=f"len={len(body_text)}")
        return None

    cleaned, prep_meta = ac.preprocess_text(
        body_text, rules=options.strip_rules,
        allow_non_prose=options.allow_non_prose, strip_aggressive=options.strip_aggressive,
    )
    if not cleaned or len(cleaned.strip()) < 200:
        summary.skipped_parse_error += 1
        summary.log_skip(reason="empty-after-preprocess", url=item.locator,
                         detail=f"raw={len(body_text)} clean={len(cleaned)}")
        return None

    if len(re.findall(r"\S+", cleaned)) < options.min_words:
        summary.skipped_filtered += 1
        summary.log_skip(reason="below-min-words", url=item.locator,
                         detail=f"words<{options.min_words}")
        return None

    piece = ac.AcquiredPiece(
        title=title or item.title or "untitled",
        author=author or options.author or "Unknown",
        persona=options.persona,
        register=options.register,
        date_written=date or item.date,
        source_url=item.locator,
        cleaned_text=cleaned,
        raw_byte_length=len(body_text.encode("utf-8")),
        preprocessing_meta=prep_meta,
        acquired_via=options.acquired_via,
        consent_status=options.consent_status,
        era=options.era or "undated",
        register_match=options.register_match,
        topic_match=options.topic_match,
        impostor_for=list(options.impostor_for),
        notes=f"work={item.extra.get('work_title','')}",
    )
    existing = ac.content_hash_already_present(piece.content_hash, options.output_dir)
    if existing is not None:
        summary.skipped_duplicate += 1
        summary.log_skip(reason="duplicate-hash", url=item.locator, detail=str(existing))
        return None
    summary.record_strip_meta(prep_meta)
    summary.total_cleaned_words += piece.word_count
    return piece


def emit_piece(piece: ac.AcquiredPiece, item: ItemMeta, *,
               options: ProcessOptions, summary: ac.RunSummary) -> None:
    if options.dry_run:
        sys.stderr.write(f"  [dry-run] would write {piece.filename_stem()} "
                         f"({piece.word_count} words, {item.extra.get('ai_status')})\n")
        summary.acquired += 1
        return
    text_path, _ = ac.write_piece(piece, output_dir=options.output_dir,
                                  scraper_version=SCRAPER_VERSION)
    entry = ac.compose_manifest_entry(
        piece, text_path=text_path, manifest_relative_to=options.manifest_path.parent,
        corpus_role=options.corpus_role, use=options.use,
        ai_status=item.extra.get("ai_status", options.ai_status),
    )
    ac.append_manifest_entry(options.manifest_path, entry)
    summary.acquired += 1
    sys.stderr.write(f"  acquired {text_path.name} ({piece.word_count} words)\n")


# --------------- CLI --------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Ingest local prose manuscripts (.docx/.md/.txt) as identity-"
                    "baseline (or impostor) corpus entries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("source", help="A .docx/.md/.txt file, or a directory of them.")
    p.add_argument("--persona", required=True, help="Persona slug for the entries.")
    p.add_argument("--author", help="Author display name (default: persona).")
    p.add_argument("--register", required=True, help="Manifest register; e.g. literary_horror.")
    p.add_argument("--corpus-role", choices=["identity_baseline", "impostor"],
                   default="identity_baseline")
    p.add_argument("--use", nargs="+", default=None,
                   help="Manifest use tags (default: voice_profile for identity, "
                        "voice_impostor for impostor).")
    p.add_argument("--ai-status",
                   choices=["pre_ai_human", "ai_assisted", "ai_edited",
                            "ai_generated", "ai_generated_from_outline", "mixed", "unknown"],
                   default="pre_ai_human",
                   help="AI-involvement label applied to this run's entries. "
                        "Run once per work for mixed-per-work labeling.")
    p.add_argument("--consent-status",
                   choices=["public_record", "cc_licensed", "fair_use_research",
                            "author_consent", "undocumented"],
                   default="author_consent")
    p.add_argument("--era", choices=["pre_chatgpt", "pre_ai_widespread",
                                     "post_ai_widespread", "undated"], default=None)
    # impostor-only (ignored for identity_baseline)
    p.add_argument("--impostor-for", nargs="+", default=[])
    p.add_argument("--register-match", choices=["high", "medium", "low"], default="high")
    p.add_argument("--topic-match", choices=["high", "medium", "low"], default="medium")

    p.add_argument("--segment", choices=["chapter", "window", "work"], default="chapter")
    p.add_argument("--window-words", type=int, default=2500)
    p.add_argument("--min-words", type=int, default=300)

    p.add_argument("--since"); p.add_argument("--until")
    p.add_argument("--max-items", type=int, default=100000)
    p.add_argument("--output-dir")
    p.add_argument("--emit-manifest")
    p.add_argument("--out")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-public-output", action="store_true")
    p.add_argument("--allow-non-prose", action="store_true")
    p.add_argument("--strip-rules"); p.add_argument("--strip-aggressive", action="store_true")
    return p


def parse_options(args: argparse.Namespace) -> ProcessOptions:
    persona = args.persona
    author = args.author or persona
    use = args.use or (["voice_impostor"] if args.corpus_role == "impostor" else ["voice_profile"])
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
    else:
        sub = "impostors" if args.corpus_role == "impostor" else "identity"
        output_dir = ac.resolve_baselines_dir() / sub / args.register / persona
    manifest_path = (Path(args.emit_manifest).expanduser() if args.emit_manifest
                     else output_dir / "draft_manifest.jsonl")
    return ProcessOptions(
        persona=persona, author=author, register=args.register,
        corpus_role=args.corpus_role, use=list(use), ai_status=args.ai_status,
        consent_status=args.consent_status, era=args.era,
        impostor_for=list(args.impostor_for or []),
        register_match=args.register_match, topic_match=args.topic_match,
        since=ac.parse_iso_date(args.since) if args.since else None,
        until=ac.parse_iso_date(args.until) if args.until else None,
        output_dir=output_dir, manifest_path=manifest_path,
        max_items=args.max_items, dry_run=args.dry_run,
        allow_non_prose=args.allow_non_prose, strip_rules=args.strip_rules,
        strip_aggressive=args.strip_aggressive, acquired_via=build_acquired_via_tag(),
        segment=args.segment, window_words=args.window_words, min_words=args.min_words,
    )


def run(args: argparse.Namespace, fetcher: ac.Fetcher | None = None) -> int:
    options = parse_options(args)
    paths = [options.output_dir, options.manifest_path]
    if args.out:
        paths.append(Path(args.out).expanduser())
    ac.check_output_privacy(paths, allow_public=args.allow_public_output, tool=TOOL_NAME)

    summary = ac.RunSummary(
        draft_manifest_path=str(options.manifest_path) if not options.dry_run else None,
        output_dir=str(options.output_dir),
    )
    sys.stderr.write(
        f"Acquiring manuscripts from {args.source} into {options.output_dir}\n"
        f"role={options.corpus_role} persona={options.persona} register={options.register} "
        f"ai_status={options.ai_status} segment={options.segment} min_words={options.min_words}\n"
    )
    for item in discover_items(args.source, options):
        if summary.acquired >= options.max_items:
            break
        try:
            body, title, author, date = extract_one(item, args.source, options)
        except Exception as exc:
            summary.skipped_parse_error += 1
            summary.log_skip(reason="extract-error", url=item.locator,
                             detail=f"{type(exc).__name__}: {exc}")
            continue
        piece = process_one_item(item, body, title, author, date,
                                 options=options, summary=summary)
        if piece is not None:
            emit_piece(piece, item, options=options, summary=summary)

    sys.stderr.write("\n" + summary.render_stderr())
    if args.out:
        op = Path(args.out).expanduser()
        op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text(json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    if summary.acquired == 0 and not summary.skip_log:
        sys.stderr.write("No items acquired. Check the source path and filters.\n")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
