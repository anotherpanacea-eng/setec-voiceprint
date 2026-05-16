#!/usr/bin/env python3
"""pdf_inventory.py — classify PDFs in a library for impostor-corpus inclusion.

Walks a directory, opens every PDF found, and emits a JSONL inventory
the user reviews before extraction. Each entry classifies the PDF as
``text_extractable`` / ``image_only`` / ``mixed`` / ``corrupted`` based
on a first-five-pages text sample, plus metadata-quality and an
estimated total word count.

The inventory is the **review surface** between an opaque PDF library
and the impostor pool. The user keeps the rows that should join the
pool, optionally annotates them with persona / register / consent
metadata, and feeds the filtered file to ``pdf_extract.py``.

This script never writes cleaned text and never emits a manifest
entry. It only describes what's present. That separation matters —
the user needs to inspect what they're about to ingest before the
extraction step touches the impostor pool.

Privacy note: PDF metadata can leak personal information (author
names, file paths, software fingerprints). The inventory output is
treated as private by default. The marker-based privacy guard
(``ai-prose-baselines-private`` in any path component) governs the
``--output`` location; ``--allow-public-output`` is required to opt
out.

Usage:

    python3 scripts/pdf_inventory.py \\
        --root ~/Documents/papers \\
        --output ~/.../ai-prose-baselines-private/pdf_inventory.jsonl

    # Restrict to subset:
    python3 scripts/pdf_inventory.py \\
        --root ~/Documents/papers \\
        --include-glob '**/honig*.pdf' \\
        --include-glob '**/arendt*.pdf' \\
        --max-files 50 \\
        --output draft_inventory.jsonl

    # Verbose progress on a large library:
    python3 scripts/pdf_inventory.py \\
        --root ~/Documents/papers \\
        --workers 4 \\
        --verbose \\
        --output ../ai-prose-baselines-private/pdf_inventory.jsonl

See ``internal/2026-05-08-impostor-corpus-spec.md`` for design context.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402

TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "pdf_inventory"
SCRIPT_VERSION = "1.0"


# Classification thresholds. Spec: > 100 chars from first 5 pages →
# text_extractable; 0 chars → image_only; everything in between →
# mixed. The spec writes the boundary as "0–100" for image_only and
# "between" for mixed; we encode it as: ≤ 0 image_only, ≤ 100 mixed,
# > 100 text_extractable.
SAMPLE_PAGE_COUNT = 5
TEXT_EXTRACTABLE_THRESHOLD = 100
IMAGE_ONLY_MAX_CHARS = 0  # 0 chars in the sample = image_only

# Default file size cap to avoid hanging on multi-GB PDFs.
DEFAULT_MAX_FILE_BYTES = 200 * 1024 * 1024  # 200 MB


# --------------- Inventory entry dataclass ----------------------


@dataclass
class InventoryEntry:
    """One row of the inventory JSONL.

    Field order matches ``references/manifest-schema.md`` example for
    pdf_inventory output (path / file_hash / title / author /
    creation_date / page_count / extractable / needs_ocr /
    has_ocr_layer / estimated_words / classification /
    metadata_quality / notes). Optional impostor fields the user
    will fill in by hand are NOT emitted here — the inventory is a
    pre-impostor surface; the user adds them in the filtered file.
    """
    path: str
    file_hash: str
    title: str | None
    author: str | None
    creation_date: str | None
    page_count: int
    extractable: bool
    needs_ocr: bool
    has_ocr_layer: bool
    estimated_words: int
    classification: str
    metadata_quality: str
    notes: str | None = None
    # File-level provenance the user often wants to see at a glance:
    file_size_bytes: int = 0
    sample_chars_extracted: int = 0
    inventory_version: str = SCRIPT_VERSION

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None or k == "notes"}


@dataclass
class InventorySummary:
    """Aggregate counts shown on stderr at end of run."""
    inventoried: int = 0
    text_extractable: int = 0
    image_only: int = 0
    mixed: int = 0
    corrupted: int = 0
    skipped_too_large: int = 0
    skipped_filtered: int = 0
    duplicate_hashes: int = 0
    total_estimated_words: int = 0

    def render_stderr(self) -> str:
        lines = [
            f"PDFs inventoried: {self.inventoried}",
            f"  text_extractable: {self.text_extractable}",
            f"  image_only:       {self.image_only}",
            f"  mixed:            {self.mixed}",
            f"  corrupted:        {self.corrupted}",
        ]
        if self.skipped_too_large:
            lines.append(f"Skipped (too large): {self.skipped_too_large}")
        if self.skipped_filtered:
            lines.append(f"Skipped (filter):    {self.skipped_filtered}")
        if self.duplicate_hashes:
            lines.append(
                f"Duplicate file hashes: {self.duplicate_hashes} "
                "(rows still emitted; user filters)"
            )
        lines.append(f"Total estimated words: {self.total_estimated_words:,}")
        return "\n".join(lines) + "\n"


# --------------- Per-file hashing -------------------------------


def file_sha256(path: Path) -> str:
    """SHA-256 of the file bytes, ``sha256:`` prefixed.

    Buffered streaming so multi-MB PDFs don't blow out memory.
    Returns ``"sha256:<hex>"`` to match the manifest convention.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


# --------------- PDF probe + classify ---------------------------


def _normalize_pdf_date(raw: str | None) -> str | None:
    """Parse PDF ``CreationDate`` strings into ISO ``YYYY-MM-DD``.

    PDF spec format is ``D:YYYYMMDDHHmmSS+OO'mm'``. Real-world inputs
    are messy: missing prefix, missing time, weird timezones. We do
    a minimal, defensive parse — return None on anything unparseable
    so the inventory shows ``creation_date: null`` rather than a
    fabricated date.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if s.startswith("D:"):
        s = s[2:]
    m = re.match(r"^(\d{4})(\d{2})?(\d{2})?", s)
    if not m:
        return None
    year = m.group(1)
    month = m.group(2) or "01"
    day = m.group(3) or "01"
    try:
        d = _dt.date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None
    return d.isoformat()


def _metadata_quality(title: str | None, author: str | None, date: str | None) -> str:
    """Bucket metadata completeness: good / partial / none."""
    populated = [bool(x) for x in (title, author, date)]
    if all(populated):
        return "good"
    if any(populated):
        return "partial"
    return "none"


def _classify_text_yield(sample_chars: int) -> tuple[str, bool, bool]:
    """Map a first-five-pages char count to classification.

    Returns ``(classification, extractable, needs_ocr)``.

    Spec encoding:
      - > 100 chars → text_extractable / extractable=True / needs_ocr=False
      - 0 chars → image_only / extractable=False / needs_ocr=True
      - 1..100 chars → mixed / extractable=False / needs_ocr=True
        (mixed isn't fully extractable; user runs OCR for completeness)
    """
    if sample_chars > TEXT_EXTRACTABLE_THRESHOLD:
        return "text_extractable", True, False
    if sample_chars <= IMAGE_ONLY_MAX_CHARS:
        return "image_only", False, True
    return "mixed", False, True


def _ocr_layer_heuristic(page_image_count: int, sample_chars: int) -> bool:
    """Heuristic: text + images on the same page → likely OCR layer.

    Pure-text PDFs (born-digital articles, papers typeset in LaTeX
    or Word) generally have NO inline images on each page. Scanned
    photocopies that have been OCR'd carry both: the original image
    plus a transparent text layer. The heuristic flags the latter
    so the user can prioritize them differently — a clean OCR of
    a 30-year-old reprint may still produce stylometrically usable
    text even if the visual layer suggests "image_only".
    """
    return page_image_count > 0 and sample_chars > 0


def _count_page_images(page: Any) -> int:
    """Return the number of /XObject Image resources referenced by
    a single page. Wrapped in best-effort exception handling because
    pypdf's resource walking varies across PDF dialects.
    """
    try:
        resources = page.get("/Resources")
        if resources is None:
            return 0
        if hasattr(resources, "get_object"):
            resources = resources.get_object()
        xobjects = resources.get("/XObject") if hasattr(resources, "get") else None
        if xobjects is None:
            return 0
        if hasattr(xobjects, "get_object"):
            xobjects = xobjects.get_object()
        count = 0
        for obj in xobjects.values():
            try:
                if hasattr(obj, "get_object"):
                    obj = obj.get_object()
                if obj.get("/Subtype") == "/Image":
                    count += 1
            except Exception:
                continue
        return count
    except Exception:
        return 0


def classify_pdf(path: Path) -> InventoryEntry:
    """Open one PDF and produce an inventory row.

    Errors surface via the ``corrupted`` classification rather than
    raising; the spec is explicit about not aborting the inventory
    run on a single bad file. ``notes`` carries the exception class
    name for the corrupted case so the user can spot category
    failures (e.g., every encrypted file failing the same way).
    """
    file_size = path.stat().st_size if path.exists() else 0
    file_hash = file_sha256(path) if path.exists() else "sha256:"

    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "pypdf is not installed. Install acquisition deps with: "
            "pip install -r requirements-acquisition.txt"
        ) from e

    try:
        reader = PdfReader(str(path), strict=False)
        # Touch trailer + first page lazily; if either blows up we
        # treat the whole file as corrupted.
        page_count = len(reader.pages)
        meta = reader.metadata or {}
        # pypdf accepts both /Title and 'Title' indexed access; prefer
        # the ``getattr`` shortcuts which fall back gracefully.
        title = getattr(meta, "title", None) or meta.get("/Title")
        author = getattr(meta, "author", None) or meta.get("/Author")
        raw_date = (
            getattr(meta, "creation_date_raw", None)
            or meta.get("/CreationDate")
        )
    except Exception as exc:
        return InventoryEntry(
            path=str(path),
            file_hash=file_hash,
            title=None,
            author=None,
            creation_date=None,
            page_count=0,
            extractable=False,
            needs_ocr=False,
            has_ocr_layer=False,
            estimated_words=0,
            classification="corrupted",
            metadata_quality="none",
            notes=f"{type(exc).__name__}: {exc}",
            file_size_bytes=file_size,
            sample_chars_extracted=0,
        )

    if page_count == 0:
        return InventoryEntry(
            path=str(path),
            file_hash=file_hash,
            title=str(title) if title else None,
            author=str(author) if author else None,
            creation_date=_normalize_pdf_date(raw_date),
            page_count=0,
            extractable=False,
            needs_ocr=False,
            has_ocr_layer=False,
            estimated_words=0,
            classification="corrupted",
            metadata_quality="none",
            notes="zero pages",
            file_size_bytes=file_size,
            sample_chars_extracted=0,
        )

    # Extract from the first SAMPLE_PAGE_COUNT pages.
    sample_text_parts: list[str] = []
    images_seen = 0
    sample_pages = min(SAMPLE_PAGE_COUNT, page_count)
    for idx in range(sample_pages):
        try:
            page = reader.pages[idx]
            page_text = page.extract_text() or ""
            sample_text_parts.append(page_text)
            images_seen += _count_page_images(page)
        except Exception:
            # Per-page extraction can blow up on corrupt streams.
            # Continue with the pages we have rather than abort the
            # whole file.
            continue
    sample_text = "\n".join(sample_text_parts)
    sample_chars = len(sample_text)
    sample_words = len(re.findall(r"\S+", sample_text))

    classification, extractable, needs_ocr = _classify_text_yield(sample_chars)
    has_ocr_layer = _ocr_layer_heuristic(images_seen, sample_chars)

    # Estimate full-document word count from the sample. Linear
    # extrapolation; not exact for documents with front-matter or
    # appendices, but sufficient for inventory triage.
    if sample_pages > 0 and sample_words > 0:
        estimated_words = int(sample_words * (page_count / sample_pages))
    else:
        estimated_words = 0

    iso_date = _normalize_pdf_date(raw_date)
    return InventoryEntry(
        path=str(path),
        file_hash=file_hash,
        title=str(title) if title else None,
        author=str(author) if author else None,
        creation_date=iso_date,
        page_count=page_count,
        extractable=extractable,
        needs_ocr=needs_ocr,
        has_ocr_layer=has_ocr_layer,
        estimated_words=estimated_words,
        classification=classification,
        metadata_quality=_metadata_quality(
            str(title) if title else None,
            str(author) if author else None,
            iso_date,
        ),
        notes=None,
        file_size_bytes=file_size,
        sample_chars_extracted=sample_chars,
    )


# --------------- File discovery ---------------------------------


def _matches_globs(path: Path, root: Path, patterns: list[str]) -> bool:
    """fnmatch-style glob match against the path-relative-to-root
    AND the bare filename. Either match wins. If ``patterns`` is
    empty the helper returns True (no filter).
    """
    if not patterns:
        return True
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    name = path.name
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat):
            return True
    return False


def discover_pdfs(
    root: Path,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    max_files: int | None = None,
) -> Iterable[Path]:
    """Yield PDF paths under ``root`` honoring include/exclude globs.

    Discovery is deterministic (sorted) so re-running an inventory
    against the same library produces row-identical output. Symlinks
    are followed but cycles are avoided via a visited set.
    """
    include = include or []
    exclude = exclude or []
    visited: set[Path] = set()
    yielded = 0

    def walk(dir_path: Path) -> Iterable[Path]:
        nonlocal yielded
        try:
            entries = sorted(dir_path.iterdir())
        except (OSError, PermissionError):
            return
        for entry in entries:
            try:
                resolved = entry.resolve()
            except OSError:
                continue
            if resolved in visited:
                continue
            visited.add(resolved)
            if entry.is_dir():
                yield from walk(entry)
                continue
            if entry.suffix.lower() != ".pdf":
                continue
            if include and not _matches_globs(entry, root, include):
                continue
            if exclude and _matches_globs(entry, root, exclude):
                continue
            if max_files is not None and yielded >= max_files:
                return
            yielded += 1
            yield entry

    if not root.exists() or not root.is_dir():
        return
    yield from walk(root)


# --------------- Inventory driver -------------------------------


def _partial_cache_path_for(output: Path) -> Path:
    """Canonical location of the partial-inventory cache that
    pairs with ``output``. We use a sidecar JSON file (path-keyed
    dict of classified entries) rather than appending to the final
    output JSONL because (a) deterministic output requires input-
    order, and a path-keyed dict lets us recompose order at the
    end, and (b) a JSON dict is naturally restartable — workers
    completing out of order don't risk interleaving with each
    other's lines.
    """
    return output.with_suffix(output.suffix + ".partial.json")


def _load_partial_inventory(
    partial_path: Path,
    *,
    max_file_bytes: int,
) -> tuple[dict[str, dict[str, Any]], int]:
    """Load a prior partial inventory keyed by path. Returns
    ``(path_to_entry_dict, n_loaded)``. The compatibility check is
    minimal — same ``max_file_bytes``, since that's the one knob
    that flips an entry from "classified" to "skipped_too_large"
    and we don't want to silently inherit a wrong skip decision."""
    if not partial_path.exists():
        return {}, 0
    try:
        cached = json.loads(partial_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        sys.stderr.write(
            f"  Partial inventory at {partial_path} is unreadable "
            f"({exc}); discarding and re-classifying.\n"
        )
        return {}, 0
    if not isinstance(cached, dict):
        return {}, 0
    cache_max_bytes = cached.get("_meta", {}).get("max_file_bytes")
    if (
        cache_max_bytes is not None
        and cache_max_bytes != max_file_bytes
    ):
        sys.stderr.write(
            f"  Partial inventory at {partial_path} is incompatible "
            f"(max_file_bytes differs: cached={cache_max_bytes}, "
            f"current={max_file_bytes}); discarding.\n"
        )
        return {}, 0
    entries = cached.get("entries") or {}
    if not isinstance(entries, dict):
        return {}, 0
    return entries, len(entries)


def _save_partial_inventory(
    partial_path: Path,
    entries_by_path: dict[str, dict[str, Any]],
    *,
    max_file_bytes: int,
) -> None:
    """Atomic write of the partial inventory cache."""
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = partial_path.with_suffix(partial_path.suffix + ".tmp")
    payload = {
        "_meta": {
            "tool": TOOL_NAME,
            "tool_version": "1.70.0",
            "max_file_bytes": max_file_bytes,
            "n_classified": len(entries_by_path),
        },
        "entries": entries_by_path,
    }
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
    tmp.replace(partial_path)


def write_inventory(
    paths: list[Path],
    *,
    output: Path,
    workers: int,
    max_file_bytes: int,
    verbose: bool,
    incremental_cache: bool = True,
    flush_every: int = 25,
    refresh_partial: bool = False,
) -> InventorySummary:
    """Classify each PDF and write the JSONL inventory.

    Rows are emitted in input order regardless of worker concurrency
    so the output stays deterministic. Hash collisions across rows
    are reported in the summary but each row is still emitted (the
    user filters duplicates manually).

    Incremental partial cache (1.70.0+): when ``incremental_cache``
    is True (default), a sidecar JSON file at ``output + ".partial
    .json"`` accumulates classified entries (path-keyed dict) every
    ``flush_every`` worker completions. A crash mid-classification
    loses at most ``flush_every`` rows. On the next run, the partial
    cache is loaded and any path already present is skipped — the
    expensive ``classify_pdf`` call doesn't fire again. After the
    final JSONL is written, the partial cache is deleted (it was
    only a checkpoint; the JSONL is the artifact).

    ``refresh_partial=True`` discards any existing partial and
    re-classifies. Use after a code change that should invalidate
    cached classifications.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = InventorySummary()
    if not paths:
        output.write_text("", encoding="utf-8")
        return summary

    partial_path = _partial_cache_path_for(output)
    entries_by_path: dict[str, dict[str, Any]] = {}
    if incremental_cache and not refresh_partial:
        entries_by_path, n_loaded = _load_partial_inventory(
            partial_path, max_file_bytes=max_file_bytes,
        )
        if n_loaded:
            sys.stderr.write(
                f"Resuming from partial inventory cache "
                f"({partial_path}): {n_loaded} of {len(paths)} "
                f"PDF(s) already classified.\n"
            )

    seen_hashes: set[str] = set()
    rows: list[InventoryEntry | None] = [None] * len(paths)

    # Pre-fill rows from the partial cache. The cache stores
    # ``asdict(entry)`` (preserving None fields), not the trimmed
    # ``entry.to_dict()`` — round-tripping through to_dict drops
    # None-valued required fields and breaks reconstruction.
    for idx, path in enumerate(paths):
        cached = entries_by_path.get(str(path))
        if cached is not None:
            try:
                rows[idx] = InventoryEntry(**cached)
            except (TypeError, KeyError):
                rows[idx] = None
                entries_by_path.pop(str(path), None)

    # Only classify paths NOT already in the partial cache.
    work = [
        (idx, p) for idx, p in enumerate(paths)
        if rows[idx] is None
    ]

    def _classify_one(idx_path: tuple[int, Path]) -> tuple[int, InventoryEntry | None]:
        idx, path = idx_path
        try:
            size = path.stat().st_size
        except OSError:
            return idx, None
        if size > max_file_bytes:
            if verbose:
                sys.stderr.write(
                    f"  skip {path.name}: too large ({size:,} bytes)\n"
                )
            return idx, None
        return idx, classify_pdf(path)

    def _maybe_flush_partial(n_completed: int, force: bool = False) -> None:
        if not incremental_cache:
            return
        if force or (n_completed > 0 and n_completed % flush_every == 0):
            try:
                _save_partial_inventory(
                    partial_path, entries_by_path,
                    max_file_bytes=max_file_bytes,
                )
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(
                    f"  WARNING: partial-inventory flush to "
                    f"{partial_path} failed: {type(exc).__name__}: "
                    f"{exc}. Continuing.\n"
                )

    n_completed_this_run = 0
    if workers <= 1:
        for ip in work:
            idx, entry = _classify_one(ip)
            if entry is None:
                summary.skipped_too_large += 1
            else:
                rows[idx] = entry
                entries_by_path[str(paths[idx])] = asdict(entry)
            n_completed_this_run += 1
            if verbose and entry is not None:
                sys.stderr.write(
                    f"  [{idx + 1}/{len(paths)}] "
                    f"{entry.classification}: {paths[idx].name}\n"
                )
            _maybe_flush_partial(n_completed_this_run)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_classify_one, ip): ip[0] for ip in work}
            for fut in as_completed(futures):
                idx, entry = fut.result()
                if entry is None:
                    summary.skipped_too_large += 1
                else:
                    rows[idx] = entry
                    entries_by_path[str(paths[idx])] = asdict(entry)
                n_completed_this_run += 1
                if verbose and entry is not None:
                    sys.stderr.write(
                        f"  [{futures[fut] + 1}/{len(paths)}] "
                        f"{entry.classification}: {paths[idx].name}\n"
                    )
                _maybe_flush_partial(n_completed_this_run)
    # Final partial flush (catches the tail < flush_every).
    _maybe_flush_partial(n_completed_this_run, force=True)

    with output.open("w", encoding="utf-8") as f:
        for entry in rows:
            if entry is None:
                continue
            if entry.file_hash in seen_hashes:
                summary.duplicate_hashes += 1
            seen_hashes.add(entry.file_hash)
            f.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
            summary.inventoried += 1
            summary.total_estimated_words += entry.estimated_words
            if entry.classification == "text_extractable":
                summary.text_extractable += 1
            elif entry.classification == "image_only":
                summary.image_only += 1
            elif entry.classification == "mixed":
                summary.mixed += 1
            elif entry.classification == "corrupted":
                summary.corrupted += 1

    # Clean up the partial cache once the final artifact is on
    # disk. The JSONL is the canonical output; the partial is just
    # a checkpoint. Leaving it behind would confuse a future
    # operator who deletes the JSONL to re-run from scratch.
    if incremental_cache and partial_path.exists():
        try:
            partial_path.unlink()
        except OSError:
            pass

    return summary


# --------------- CLI --------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Inventory PDFs in a library for impostor-corpus inclusion. "
            "Classifies each PDF as text_extractable / image_only / "
            "mixed / corrupted and emits a JSONL the user reviews "
            "before running pdf_extract.py. See "
            "internal/2026-05-08-impostor-corpus-spec.md for design."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--root", required=True,
                   help="Directory to walk for .pdf files.")
    p.add_argument("--output", required=True,
                   help="Where to write the inventory JSONL.")
    p.add_argument("--include-glob", action="append", default=[],
                   help=(
                       "fnmatch-style include pattern (relative path "
                       "or basename). Repeatable. Empty list = include "
                       "everything."
                   ))
    p.add_argument("--exclude-glob", action="append", default=[],
                   help="fnmatch-style exclude pattern. Repeatable.")
    p.add_argument("--max-files", type=int, default=None,
                   help="Cap on number of PDFs to inventory.")
    p.add_argument("--workers", type=int, default=1,
                   help=(
                       "Concurrent classification workers (default 1). "
                       "Each worker holds one PDF in memory; raise "
                       "carefully on large libraries."
                   ))
    p.add_argument("--max-file-bytes", type=int,
                   default=DEFAULT_MAX_FILE_BYTES,
                   help=(
                       "Skip PDFs larger than this many bytes "
                       f"(default {DEFAULT_MAX_FILE_BYTES // (1024 * 1024)} MB). "
                       "Multi-GB scans, image-heavy law-review PDFs, "
                       "and corrupted-but-large-on-disk files all "
                       "stop the inventory run; this cap protects "
                       "the runtime."
                   ))
    p.add_argument("--allow-public-output", action="store_true",
                   help=(
                       "Allow writing the inventory outside "
                       "ai-prose-baselines-private/. PDF metadata can "
                       "leak personal info; only set this when the "
                       "library is non-personal."
                   ))
    p.add_argument("--verbose", action="store_true",
                   help="One-line-per-PDF progress on stderr.")
    # Incremental partial-cache (1.70.0). Each PDF classification
    # is expensive (PyPDF parse + per-page image counting + maybe
    # an OCR-layer probe); on libraries of thousands of PDFs the
    # ThreadPoolExecutor parallelizes the work but the original
    # write_inventory accumulated results in memory and only wrote
    # the JSONL after every worker completed. A crash mid-run lost
    # everything. The partial-cache flag writes a path-keyed
    # sidecar JSON every N completions so a restart picks up where
    # it left off.
    p.add_argument(
        "--no-incremental-cache", action="store_true",
        help=(
            "Disable the path-keyed partial-inventory cache that "
            "accumulates as workers complete. Default behavior "
            "(cache on) writes <output>.partial.json every "
            "--flush-every classifications and deletes it after "
            "the final JSONL is written. Passing this flag "
            "reverts to the pre-1.70.0 'classify-all-then-write' "
            "behavior — a crash loses every classification done "
            "in the run."
        ),
    )
    p.add_argument(
        "--flush-every", type=int, default=25,
        help=(
            "Write the partial-inventory cache every N PDF "
            "classifications (default 25). Lower (5-10) for very "
            "slow per-PDF classifications with high crash exposure; "
            "higher (50-100) when classification is fast and flush "
            "I/O dominates. Ignored when --no-incremental-cache."
        ),
    )
    p.add_argument(
        "--refresh-partial", action="store_true",
        help=(
            "Discard any existing partial-inventory cache and re-"
            "classify every PDF from scratch. Use after code "
            "changes that should invalidate cached classifications."
        ),
    )
    return p


def run(args: argparse.Namespace) -> int:
    """Top-level driver. Returns shell-style exit code."""
    root = Path(args.root).expanduser().resolve()
    output = Path(args.output).expanduser()

    ac.check_output_privacy(
        [output], allow_public=args.allow_public_output, tool=TOOL_NAME,
    )

    if not root.exists():
        sys.stderr.write(f"--root does not exist: {root}\n")
        return 2
    if not root.is_dir():
        sys.stderr.write(f"--root is not a directory: {root}\n")
        return 2

    paths = list(discover_pdfs(
        root,
        include=args.include_glob,
        exclude=args.exclude_glob,
        max_files=args.max_files,
    ))
    sys.stderr.write(f"Found {len(paths)} PDF(s) under {root}\n")

    if not paths:
        output.write_text("", encoding="utf-8")
        sys.stderr.write("No PDFs to inventory; wrote empty file.\n")
        return 0

    summary = write_inventory(
        paths,
        output=output,
        workers=max(1, args.workers),
        max_file_bytes=args.max_file_bytes,
        verbose=args.verbose,
        incremental_cache=not args.no_incremental_cache,
        flush_every=max(1, int(args.flush_every)),
        refresh_partial=bool(args.refresh_partial),
    )
    sys.stderr.write(summary.render_stderr())
    sys.stderr.write(f"Inventory written to: {output}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
