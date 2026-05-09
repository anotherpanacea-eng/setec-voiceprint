#!/usr/bin/env python3
"""pdf_extract.py — extract plain text from a filtered PDF inventory.

Reads a filtered inventory JSONL produced by ``pdf_inventory.py`` (and
edited by the user to add impostor metadata + drop entries that
shouldn't join the pool), then for each entry:

  1. Looks up the source PDF by ``path``.
  2. If ``extractable: true``, extracts text with ``pypdf`` page-by-
     page; concatenates with double-newlines between pages.
  3. If ``extractable: false`` and ``--skip-ocr`` is not set, runs
     ``ocrmypdf`` to produce a searchable PDF, then extracts text
     from that.
  4. Pipes the extracted text through ``preprocessing.py`` for the
     same corpus-hygiene gate that identity baselines and live blog
     acquisition use.
  5. Writes ``<output-dir>/<author_slug>/<title_slug>.txt`` plus a
     ``.meta.json`` sidecar.
  6. Composes a draft manifest entry with ``corpus_role: impostor``,
     ``acquired_via: pdf_extract_<text_layer|ocrmypdf>_<date>``, and
     all five impostor-required fields read from the inventory row.

Privacy: extracted text from third-party PDFs is voice-cloning input.
Default output goes under ``ai-prose-baselines-private/impostors/
<register>/<author_slug>/``. The privacy guard refuses non-private
paths unless ``--allow-public-output`` is set.

OCR: ``ocrmypdf`` is a soft optional dependency. If it isn't
installed (or system binaries ``tesseract`` / ``ghostscript`` /
``qpdf`` aren't available), every ``image_only`` / ``mixed`` entry is
skipped with a clear stderr message — pass ``--skip-ocr`` to
acknowledge this and silence the warnings, or install the OCR layer
per ``requirements-acquisition.txt`` notes.

Usage:

    # Fast first pass: only text-extractable entries, no OCR.
    python3 scripts/pdf_extract.py \\
        --inventory filtered_inventory.jsonl \\
        --output-dir ../ai-prose-baselines-private/impostors/academic_philosophy/ \\
        --skip-ocr

    # Full pass with OCR (requires ocrmypdf + system binaries).
    python3 scripts/pdf_extract.py \\
        --inventory filtered_inventory.jsonl \\
        --output-dir ../ai-prose-baselines-private/impostors/academic_philosophy/ \\
        --workers 2

The filtered inventory must carry the impostor fields the manifest
validator requires (``persona``, ``register``, ``register_match``,
``topic_match``, ``consent_status``, ``era``, ``impostor_for``). The
inventory step doesn't add those — the user does, between
``pdf_inventory.py`` and this script.

See ``internal/2026-05-08-impostor-corpus-spec.md`` for context.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402

TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "pdf_extract"
SCRIPT_VERSION = "1.0"


REQUIRED_INVENTORY_FIELDS = (
    "path", "file_hash", "page_count", "classification",
)
REQUIRED_IMPOSTOR_FIELDS = (
    "persona", "register", "register_match", "topic_match",
    "consent_status", "era", "impostor_for",
)


# --------------- Inventory loading ------------------------------


def load_inventory(path: Path) -> list[dict[str, Any]]:
    """Read a filtered JSONL inventory.

    Skips blank lines and ``#``-prefixed comments so users can
    annotate manually-filtered files. Raises a ``ValueError`` if any
    row is missing the inventory-required fields; that's the
    earliest, cheapest place to catch a malformed inventory before
    we spend time on OCR.
    """
    if not path.exists():
        raise FileNotFoundError(f"inventory not found: {path}")
    rows: list[dict[str, Any]] = []
    for line_no, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1,
    ):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"inventory line {line_no}: {e}") from e
        missing_inv = [f for f in REQUIRED_INVENTORY_FIELDS if f not in row]
        if missing_inv:
            raise ValueError(
                f"inventory line {line_no} missing required fields: "
                f"{missing_inv}"
            )
        rows.append(row)
    return rows


def _row_has_impostor_fields(row: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check that the user-added impostor metadata is present.

    Returns ``(complete, missing)``. The validator would catch this
    later when the manifest is loaded, but checking here lets us
    skip the work and report the row clearly rather than emit an
    invalid manifest entry the user has to investigate downstream.
    """
    missing: list[str] = []
    for f in REQUIRED_IMPOSTOR_FIELDS:
        v = row.get(f)
        if v in (None, "", [], {}):
            missing.append(f)
    return (not missing, missing)


# --------------- Text extraction --------------------------------


def extract_text_layer(path: Path) -> str:
    """Extract text from a PDF's text layer via pypdf, page-by-page.

    Pages are joined with double-newlines so paragraph structure
    survives the per-page extraction and downstream preprocessing
    can collapse runs of whitespace cleanly.
    """
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "pypdf is not installed. Install acquisition deps with: "
            "pip install -r requirements-acquisition.txt"
        ) from e

    reader = PdfReader(str(path), strict=False)
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            # Per-page failures shouldn't abort the whole file; the
            # inventory step already classified this PDF as
            # extractable, so a single bad page is recoverable.
            continue
    return "\n\n".join(parts)


def _ocr_dependencies_available() -> tuple[bool, str]:
    """Return ``(available, reason_if_unavailable)``.

    Checks for both the Python package and the system binaries
    ``ocrmypdf`` requires. The reason string is human-readable for
    a single stderr line.
    """
    try:
        import ocrmypdf  # type: ignore  # noqa: F401
    except ImportError:
        return False, "ocrmypdf Python package not installed"
    for binary in ("tesseract", "gs", "qpdf"):
        if shutil.which(binary) is None:
            return False, f"system binary not found: {binary}"
    return True, ""


def extract_text_via_ocr(
    path: Path, *, language: str, dpi: int,
) -> str:
    """OCR a PDF via ``ocrmypdf`` and return the extracted text.

    Two-step process: ocrmypdf produces a searchable PDF (image +
    text layer) and pypdf extracts the new text layer. The
    intermediate PDF goes to a tempfile and is cleaned up regardless
    of success.

    Raises ``RuntimeError`` with a clear message when OCR
    dependencies are missing — callers handle this by recording
    the skip rather than aborting the whole run.
    """
    available, reason = _ocr_dependencies_available()
    if not available:
        raise RuntimeError(
            f"OCR unavailable ({reason}). Pass --skip-ocr to "
            "process only text-extractable entries, or install "
            "ocrmypdf + tesseract + ghostscript + qpdf."
        )

    import ocrmypdf  # type: ignore

    with tempfile.NamedTemporaryFile(
        suffix=".pdf", delete=False,
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        # ocrmypdf is a thin wrapper around tesseract; a busy CLI
        # with a huge surface area, but for our usage we want:
        # force OCR (even if a layer exists, we re-OCR for
        # consistency), use the requested language, and rasterize
        # at the requested DPI for image-heavy scans.
        ocrmypdf.ocr(  # type: ignore[attr-defined]
            str(path),
            str(tmp_path),
            language=language,
            image_dpi=dpi,
            force_ocr=True,
            progress_bar=False,
            quiet=True,
        )
        return extract_text_layer(tmp_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


# --------------- Per-row processing -----------------------------


@dataclass
class ExtractOptions:
    """User-facing options threaded through the per-row pipeline."""
    output_dir: Path
    manifest_path: Path
    workers: int
    ocr_language: str
    ocr_dpi: int
    skip_ocr: bool
    dry_run: bool
    allow_non_prose: bool
    strip_rules: str | None
    strip_aggressive: bool
    today: str = field(default_factory=lambda: _dt.date.today().isoformat())


@dataclass
class ExtractSummary:
    """Aggregate statistics for the extraction run."""
    extracted: int = 0
    extracted_text_layer: int = 0
    extracted_via_ocr: int = 0
    skipped_missing_pdf: int = 0
    skipped_missing_metadata: int = 0
    skipped_corrupted: int = 0
    skipped_ocr_unavailable: int = 0
    skipped_short: int = 0
    skipped_duplicate: int = 0
    skipped_extract_error: int = 0
    total_words: int = 0
    per_rule_strips: dict[str, int] = field(default_factory=dict)
    skip_log: list[dict[str, str]] = field(default_factory=list)
    draft_manifest_path: str | None = None
    output_dir: str | None = None

    def record_strip_meta(self, meta: dict[str, Any]) -> None:
        for rule, count in (meta.get("tokens_stripped_by_rule") or {}).items():
            self.per_rule_strips[rule] = (
                self.per_rule_strips.get(rule, 0) + int(count)
            )

    def log_skip(self, *, reason: str, path: str, detail: str = "") -> None:
        self.skip_log.append({"reason": reason, "path": path, "detail": detail})

    def render_stderr(self) -> str:
        lines = [
            f"Extracted: {self.extracted} files",
            f"  via text layer: {self.extracted_text_layer}",
            f"  via OCR:        {self.extracted_via_ocr}",
            f"Skipped (missing PDF):       {self.skipped_missing_pdf}",
            f"Skipped (missing metadata):  {self.skipped_missing_metadata}",
            f"Skipped (corrupted):         {self.skipped_corrupted}",
            f"Skipped (OCR unavailable):   {self.skipped_ocr_unavailable}",
            f"Skipped (too-short text):    {self.skipped_short}",
            f"Skipped (duplicate hash):    {self.skipped_duplicate}",
            f"Skipped (extract error):     {self.skipped_extract_error}",
            f"Total cleaned words: {self.total_words:,}",
        ]
        if self.draft_manifest_path:
            lines.append(f"Draft manifest: {self.draft_manifest_path}")
        if self.per_rule_strips:
            strips = ", ".join(
                f"{k}={v}" for k, v in sorted(self.per_rule_strips.items())
            )
            lines.append(f"Per-rule preprocessing strips: {strips}")
        return "\n".join(lines) + "\n"


def _author_subdir(row: dict[str, Any]) -> Path:
    """Return ``<author-slug>/`` for the given row.

    Prefers the persona slug (the user-supplied identifier) over the
    title-mangled author field. Falls back to "unknown" when neither
    is set so files don't collide at the output-dir root.
    """
    persona = (row.get("persona") or "").strip()
    if persona:
        return Path(ac.slugify(persona) or "unknown")
    author = (row.get("author") or "").strip()
    if author:
        return Path(ac.author_to_persona_slug(author))
    return Path("unknown")


def _date_from_row(row: dict[str, Any]) -> _dt.date | None:
    """Pick a usable publication date.

    Order: explicit ``date_written`` (user-set), ``creation_date``
    (PDF metadata, less reliable but always present after
    inventory). Returns None if neither parses.
    """
    for key in ("date_written", "creation_date"):
        d = ac.parse_iso_date(row.get(key))
        if d is not None:
            return d
    return None


def _title_from_row(row: dict[str, Any]) -> str:
    """Title for the output filename. Falls back to the source PDF
    basename if the inventory doesn't carry a title (which is
    common for academic photocopies)."""
    title = (row.get("title") or "").strip()
    if title:
        return title
    src = row.get("path") or ""
    return Path(src).stem or "untitled"


def process_row(
    row: dict[str, Any],
    options: ExtractOptions,
    summary: ExtractSummary,
) -> None:
    """Extract one inventory row to disk + manifest."""
    src_path_str = row.get("path") or ""
    src_path = Path(src_path_str).expanduser()
    if not src_path.exists():
        summary.skipped_missing_pdf += 1
        summary.log_skip(
            reason="missing-pdf", path=src_path_str, detail="file not found",
        )
        sys.stderr.write(f"  missing PDF; skipping: {src_path_str}\n")
        return

    classification = row.get("classification")
    if classification == "corrupted":
        summary.skipped_corrupted += 1
        summary.log_skip(
            reason="corrupted-by-inventory", path=src_path_str,
            detail=str(row.get("notes") or ""),
        )
        sys.stderr.write(
            f"  inventory marked corrupted; skipping: {src_path_str}\n"
        )
        return

    complete, missing = _row_has_impostor_fields(row)
    if not complete:
        summary.skipped_missing_metadata += 1
        summary.log_skip(
            reason="missing-impostor-fields",
            path=src_path_str,
            detail=",".join(missing),
        )
        sys.stderr.write(
            f"  missing impostor fields {missing}; skipping: {src_path_str}\n"
        )
        return

    extractable = bool(row.get("extractable"))
    via = "text_layer" if extractable else "ocrmypdf"
    raw_text: str
    if extractable:
        try:
            raw_text = extract_text_layer(src_path)
        except Exception as exc:
            summary.skipped_extract_error += 1
            summary.log_skip(
                reason="text-layer-error",
                path=src_path_str,
                detail=f"{type(exc).__name__}: {exc}",
            )
            return
    else:
        if options.skip_ocr:
            summary.skipped_ocr_unavailable += 1
            summary.log_skip(
                reason="skip-ocr-flag", path=src_path_str,
                detail=classification,
            )
            return
        try:
            raw_text = extract_text_via_ocr(
                src_path, language=options.ocr_language, dpi=options.ocr_dpi,
            )
        except RuntimeError as exc:
            # OCR dependencies missing — record once per run and
            # let downstream rows raise the same condition (counted
            # separately so the user knows it's not just one file).
            summary.skipped_ocr_unavailable += 1
            summary.log_skip(
                reason="ocr-unavailable", path=src_path_str,
                detail=str(exc),
            )
            sys.stderr.write(f"  OCR skipped: {exc}\n")
            return
        except Exception as exc:
            summary.skipped_extract_error += 1
            summary.log_skip(
                reason="ocr-error",
                path=src_path_str,
                detail=f"{type(exc).__name__}: {exc}",
            )
            return

    if not raw_text or len(raw_text.strip()) < 100:
        summary.skipped_short += 1
        summary.log_skip(
            reason="too-short", path=src_path_str,
            detail=f"len={len(raw_text)}",
        )
        return

    cleaned, prep_meta = ac.preprocess_text(
        raw_text,
        rules=options.strip_rules,
        allow_non_prose=options.allow_non_prose,
        strip_aggressive=options.strip_aggressive,
    )

    if not cleaned or len(cleaned.strip()) < 100:
        summary.skipped_short += 1
        summary.log_skip(
            reason="too-short-after-preprocess",
            path=src_path_str,
            detail=f"raw={len(raw_text)} clean={len(cleaned)}",
        )
        return

    title = _title_from_row(row)
    author = (row.get("author") or "").strip() or "Unknown"
    persona = (row.get("persona") or "").strip() or ac.author_to_persona_slug(author)
    register = row.get("register") or ""
    impostor_for = list(row.get("impostor_for") or [])
    date_written = _date_from_row(row)

    piece = ac.AcquiredPiece(
        title=title,
        author=author,
        persona=persona,
        register=register,
        date_written=date_written,
        source_url=str(src_path),
        cleaned_text=cleaned,
        raw_byte_length=len(raw_text.encode("utf-8")),
        preprocessing_meta=prep_meta,
        acquired_via=f"pdf_extract_{via}_{options.today}",
        consent_status=row.get("consent_status") or "undocumented",
        era=row.get("era") or "undated",
        register_match=row.get("register_match") or "high",
        topic_match=row.get("topic_match") or "medium",
        impostor_for=impostor_for,
        notes=str(row.get("notes") or ""),
    )

    author_subdir = options.output_dir / _author_subdir(row)

    # Within-author dedupe by content hash. Two PDFs of the same
    # essay (a journal preprint and a republished collection
    # version, for example) hash the same after preprocessing and
    # one wins; the second logs a skip.
    existing = ac.content_hash_already_present(piece.content_hash, author_subdir)
    if existing is not None:
        summary.skipped_duplicate += 1
        summary.log_skip(
            reason="duplicate-hash",
            path=src_path_str,
            detail=str(existing),
        )
        sys.stderr.write(
            f"  duplicate hash; skipping {src_path_str} "
            f"(matches {existing.name})\n"
        )
        return

    if options.dry_run:
        sys.stderr.write(
            f"  [dry-run] would extract {piece.filename_stem()} "
            f"({piece.word_count} words, via {via})\n"
        )
        summary.extracted += 1
        if via == "text_layer":
            summary.extracted_text_layer += 1
        else:
            summary.extracted_via_ocr += 1
        return

    text_path, _meta_path = ac.write_piece(
        piece, output_dir=author_subdir, scraper_version=SCRIPT_VERSION,
    )
    entry = ac.compose_manifest_entry(
        piece,
        text_path=text_path,
        manifest_relative_to=options.manifest_path.parent,
    )
    # Carry the inventory's file_hash so downstream tools can trace
    # back to the original PDF byte stream.
    if row.get("file_hash"):
        entry["source_file_hash"] = row["file_hash"]
    ac.append_manifest_entry(options.manifest_path, entry)

    summary.extracted += 1
    if via == "text_layer":
        summary.extracted_text_layer += 1
    else:
        summary.extracted_via_ocr += 1
    summary.total_words += piece.word_count
    summary.record_strip_meta(prep_meta)
    sys.stderr.write(
        f"  extracted {text_path.name} ({piece.word_count} words, via {via})\n"
    )


# --------------- CLI --------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Extract plain text from PDFs flagged in a filtered inventory. "
            "Text-extractable files go through pypdf; image-only files "
            "go through ocrmypdf. Emits cleaned text + draft manifest "
            "entries with corpus_role: impostor."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--inventory", required=True,
                   help="Filtered inventory JSONL from pdf_inventory.py.")
    p.add_argument("--output-dir", required=True,
                   help=(
                       "Where to write per-author subdirs. Each entry "
                       "lands in <output-dir>/<persona-slug>/<title>.txt."
                   ))
    p.add_argument("--emit-manifest",
                   help=(
                       "Where to write the draft manifest JSONL. "
                       "Defaults to <output-dir>/draft_manifest.jsonl."
                   ))
    p.add_argument("--workers", type=int, default=1,
                   help=(
                       "Concurrent extraction workers. OCR is the "
                       "expensive step; raise carefully because each "
                       "worker spawns ocrmypdf + tesseract."
                   ))
    p.add_argument("--ocr-language", default="eng",
                   help="Tesseract language code for OCR (default: eng).")
    p.add_argument("--ocr-dpi", type=int, default=300,
                   help=(
                       "Render DPI for OCR (default 300). Lower = faster "
                       "but lossier; 200 is the practical floor for "
                       "academic stylometry."
                   ))
    p.add_argument("--skip-ocr", action="store_true",
                   help=(
                       "Process only text-extractable entries. Useful "
                       "for the fast first pass before committing to "
                       "the slower OCR step."
                   ))
    p.add_argument("--dry-run", action="store_true",
                   help="Inventory what would be extracted; don't write.")
    p.add_argument("--out", help="Write summary report (JSON) here.")
    p.add_argument("--allow-public-output", action="store_true",
                   help=(
                       "Allow writing outside ai-prose-baselines-private/. "
                       "PDF text is voice-cloning input; only set this "
                       "for non-personal corpora."
                   ))
    # Preprocessing pass-throughs.
    p.add_argument("--allow-non-prose", action="store_true",
                   help="Skip preprocessing's corpus-hygiene gate.")
    p.add_argument("--strip-rules",
                   help=(
                       "Comma-separated subset of preprocessing rules. "
                       "Default: all standard rules."
                   ))
    p.add_argument("--strip-aggressive", action="store_true",
                   help="Apply aggressive (link/citation) strip rules.")
    return p


def run(args: argparse.Namespace) -> int:
    """Top-level driver. Returns shell-style exit code."""
    inventory_path = Path(args.inventory).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    manifest_path = (
        Path(args.emit_manifest).expanduser() if args.emit_manifest
        else output_dir / "draft_manifest.jsonl"
    )

    paths_to_check: list[Path] = [output_dir, manifest_path]
    if args.out:
        paths_to_check.append(Path(args.out).expanduser())
    ac.check_output_privacy(
        paths_to_check, allow_public=args.allow_public_output, tool=TOOL_NAME,
    )

    try:
        rows = load_inventory(inventory_path)
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"inventory load failed: {exc}\n")
        return 2

    if not rows:
        sys.stderr.write(f"inventory is empty: {inventory_path}\n")
        return 1

    options = ExtractOptions(
        output_dir=output_dir,
        manifest_path=manifest_path,
        workers=max(1, args.workers),
        ocr_language=args.ocr_language,
        ocr_dpi=args.ocr_dpi,
        skip_ocr=args.skip_ocr,
        dry_run=args.dry_run,
        allow_non_prose=args.allow_non_prose,
        strip_rules=args.strip_rules,
        strip_aggressive=args.strip_aggressive,
    )
    summary = ExtractSummary(
        draft_manifest_path=str(manifest_path) if not args.dry_run else None,
        output_dir=str(output_dir),
    )

    sys.stderr.write(
        f"Reading inventory: {inventory_path} ({len(rows)} rows)\n"
        f"Output dir: {output_dir}\n"
    )

    # OCR availability is checked once up-front so the first
    # OCR-needing row's failure isn't a surprise.
    if not args.skip_ocr:
        available, reason = _ocr_dependencies_available()
        if not available:
            sys.stderr.write(
                f"OCR unavailable ({reason}); image_only / mixed "
                "entries will be skipped. Pass --skip-ocr to silence "
                "this notice, or install ocrmypdf + tesseract + "
                "ghostscript + qpdf.\n"
            )

    for row in rows:
        process_row(row, options, summary)

    sys.stderr.write("\n" + summary.render_stderr())

    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Strip dataclass-only mutables for JSON serialization.
        out_payload = {
            k: v for k, v in summary.__dict__.items()
            if not k.startswith("_")
        }
        out_path.write_text(
            json.dumps(out_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    return 0 if summary.extracted > 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
