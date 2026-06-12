#!/usr/bin/env python3
"""acquire_mirrulations.py — regulatory comments via the Mirrulations S3 mirror.

Acquires substantive regulatory comments into the ``regulatory_comment``
population baseline. Mirrulations is the public AWS Open Data mirror of
regulations.gov; it has pre-extracted the text of comment PDF attachments to
``.txt`` in its S3 bucket. Substantive comments are almost always uploaded as
attachments on organizational letterhead, so filtering to the extracted-text
``.txt`` is itself the "PDF-attachments-only" filter — the one-line web-form
comments have no attachment and so no extracted text.

Acquisition is: list the extracted-text keys for operator-chosen dockets ->
read each ``.txt`` -> pipeline. No PDF parsing, no API key (anonymous public
bucket: ``aws s3 ls --no-sign-request s3://mirrulations/``).

Quality / impurity: exact-hash dedup (built into the pipeline) removes
identical form letters. Near-duplicate (>80%-similar) campaign variants are
NOT removed in v1 — an LSH near-dup pass is a flagged follow-up; the run
summary's duplicate count understates campaign text accordingly. Regulatory
comments are the highest AI-contamination genre post-2022, so pick pre-2020
dockets (the temporal cut rides on docket selection + ``--era``).

Bucket layout (verified 2026-06-11 against the live bucket): the top level is
``raw-data/`` and ``derived-data/``. ``raw-data/`` holds only the binary
attachments; the pre-extracted text lives under ``derived-data/`` at
``derived-data/<AGENCY>/<DOCKET>/mirrulations/extracted_txt/
comments_extracted_text/<engine>/<comment>_extracted.txt``. So ``--prefix``
must be rooted at ``derived-data/`` -- a bare-agency or ``raw-data/`` prefix
lists no extracted text and acquires nothing. The default
``--text-key-pattern`` matches these keys; still verify hit counts with
``--dry-run`` before a bulk pull (see references/acquire-corpus-pattern.md).

Privacy: output goes under ``ai-prose-baselines-private/impostors/<register>/
<persona>/`` and the privacy guard refuses paths outside any directory named
``ai-prose-baselines-private``.

Usage:

    python3 scripts/acquire_mirrulations.py \\
        --prefix derived-data/EPA/EPA-HQ-OAR-2013-0602 \\
        --persona mirrulations \\
        --impostor-for argscope_regulatory_comment \\
        --register regulatory_comment \\
        --consent-status public_record \\
        --era pre_chatgpt \\
        --min-words 1000 --max-items 500

See ``internal/SPEC_acquire_mirrulations.md`` for design context.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402

TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "acquire_mirrulations"
SCRAPER_VERSION = "1.0"

DEFAULT_BUCKET = "mirrulations"
DEFAULT_REGION = "us-east-1"
# Extracted comment-attachment text keys. Tolerant default; the operator
# verifies / tunes against the live bucket with --dry-run.
DEFAULT_TEXT_KEY_PATTERN = r"extracted.*\.txt$"
DEFAULT_AUTHOR = "Regulatory Commenter"


# ---- Object store (the S3 analogue of Fetcher/FixtureFetcher) -----


class ObjectStore:
    """Abstract key/value object store. Tests use ``FixtureObjectStore``;
    production uses the boto3-backed store from ``make_s3_store``."""

    def list_keys(self, prefix: str) -> Iterator[str]:  # pragma: no cover
        raise NotImplementedError

    def get_bytes(self, key: str) -> bytes | None:  # pragma: no cover
        raise NotImplementedError


class FixtureObjectStore(ObjectStore):
    """In-memory store backed by a ``{key: bytes}`` dict (no network)."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)
        self.listed_prefixes: list[str] = []
        self.got_keys: list[str] = []

    def list_keys(self, prefix: str) -> Iterator[str]:
        self.listed_prefixes.append(prefix)
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield key

    def get_bytes(self, key: str) -> bytes | None:
        self.got_keys.append(key)
        return self.objects.get(key)


def make_s3_store(
    bucket: str = DEFAULT_BUCKET, region: str = DEFAULT_REGION,
) -> ObjectStore:
    """Construct an anonymous (unsigned) boto3-backed S3 store.

    Imported lazily so scripts/tests that don't hit S3 run without boto3.
    Anonymous access matches the public Mirrulations Open Data bucket.
    """
    try:
        import boto3  # type: ignore
        from botocore import UNSIGNED  # type: ignore
        from botocore.config import Config  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "boto3 is not installed. Install acquisition dependencies with: "
            "pip install -r requirements-acquisition.txt"
        ) from e

    client = boto3.client(
        "s3", region_name=region, config=Config(signature_version=UNSIGNED),
    )

    class S3ObjectStore(ObjectStore):
        def list_keys(self, prefix: str) -> Iterator[str]:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []) or []:
                    yield obj["Key"]

        def get_bytes(self, key: str) -> bytes | None:
            try:
                resp = client.get_object(Bucket=bucket, Key=key)
                return resp["Body"].read()
            except Exception as exc:
                sys.stderr.write(f"  s3 get error {key}: {exc}\n")
                return None

    return S3ObjectStore()


@dataclass
class ItemMeta:
    """One extracted-comment-text object discovered in the bucket."""
    locator: str          # S3 key
    title: str = ""


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
    prefixes: list[str]
    text_key_re: re.Pattern[str]
    output_dir: Path
    manifest_path: Path
    max_items: int
    min_words: int
    dry_run: bool
    allow_non_prose: bool
    strip_rules: str | None
    strip_aggressive: bool
    acquired_via: str


# ---- Discovery + extraction ---------------------------------------


def _title_from_key(key: str) -> str:
    stem = Path(key).stem
    return stem or "untitled"


def discover_items(
    options: ProcessOptions, store: ObjectStore,
) -> Iterable[ItemMeta]:
    """List each prefix and yield keys matching the extracted-text pattern."""
    seen: set[str] = set()
    for prefix in options.prefixes:
        for key in store.list_keys(prefix):
            if key in seen:
                continue
            if not options.text_key_re.search(key):
                continue
            seen.add(key)
            yield ItemMeta(locator=key, title=_title_from_key(key))


def extract_one(
    item: ItemMeta, options: ProcessOptions, store: ObjectStore,
) -> tuple[str, str, str, _dt.date | None]:
    """Read the object and decode its text. ``("", …)`` skips on missing/empty."""
    data = store.get_bytes(item.locator)
    if not data:
        return "", "", "", None
    text = data.decode("utf-8", "replace")
    if not text.strip():
        return "", "", "", None
    return text, item.title or "untitled", options.author or DEFAULT_AUTHOR, None


# ---- Per-comment processing ---------------------------------------


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
            "Acquire regulatory comments from the Mirrulations S3 mirror into "
            "the impostor pool (the regulatory_comment population baseline). "
            "See internal/SPEC_acquire_mirrulations.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--prefix", action="append", required=True, dest="prefixes",
                   help="S3 key prefix to list, rooted at derived-data/, e.g. "
                        "derived-data/EPA/EPA-HQ-OAR-2013-0602 (repeatable; "
                        "required). raw-data/ holds only binaries; the "
                        "extracted text is under derived-data/. Pick "
                        "substantive, pre-2020 dockets.")
    p.add_argument("--bucket", default=DEFAULT_BUCKET,
                   help=f"S3 bucket (default: {DEFAULT_BUCKET}).")
    p.add_argument("--region", default=DEFAULT_REGION,
                   help=f"AWS region (default: {DEFAULT_REGION}).")
    p.add_argument("--text-key-pattern", default=DEFAULT_TEXT_KEY_PATTERN,
                   help="Regex selecting extracted-text keys "
                        f"(default: {DEFAULT_TEXT_KEY_PATTERN!r}).")

    # Persona / impostor metadata.
    p.add_argument("--persona", default="mirrulations",
                   help="Persona slug for emitted entries "
                        "(default: mirrulations).")
    p.add_argument("--author", default="",
                   help="Author display name override (default: "
                        "'Regulatory Commenter').")
    p.add_argument("--impostor-for", nargs="+", required=True,
                   help=("Persona slug(s) this impostor pool serves "
                         "(required; the schema rejects empty)."))
    p.add_argument("--register", required=True,
                   help="Manifest register; use regulatory_comment.")
    p.add_argument("--register-match",
                   choices=["high", "medium", "low"], default="high")
    p.add_argument("--topic-match",
                   choices=["high", "medium", "low"], default="medium")
    p.add_argument("--consent-status", required=True,
                   choices=[
                       "public_record", "cc_licensed", "fair_use_research",
                       "author_consent", "undocumented",
                   ],
                   help="Consent / legal posture (public_record for federal "
                        "docket comments).")
    p.add_argument("--era",
                   choices=[
                       "pre_chatgpt", "pre_ai_widespread",
                       "post_ai_widespread", "undated",
                   ],
                   default="pre_chatgpt")

    # Caps.
    p.add_argument("--max-items", type=int, default=500,
                   help="Maximum comments to acquire (default: 500).")
    p.add_argument("--min-words", type=int, default=1000,
                   help="Drop comments below this cleaned word count "
                        "(default: 1000).")

    # Output paths.
    p.add_argument("--output-dir",
                   help=("Where to write .txt and .meta.json files. Defaults "
                         "to <baselines>/impostors/<register>/<persona>/."))
    p.add_argument("--emit-manifest",
                   help=("Where to write draft manifest JSONL. Defaults to "
                         "<output-dir>/draft_manifest.jsonl."))
    p.add_argument("--out", help="Write summary report here (JSON).")

    # Behavior.
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

    acquired_via = f"acquire_mirrulations_{_dt.date.today().isoformat()}"

    return ProcessOptions(
        persona=args.persona,
        author=args.author,
        impostor_for=list(args.impostor_for or []),
        register=args.register,
        register_match=args.register_match,
        topic_match=args.topic_match,
        consent_status=args.consent_status,
        era=args.era,
        prefixes=list(args.prefixes or []),
        text_key_re=re.compile(args.text_key_pattern),
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


def run(args: argparse.Namespace, store: ObjectStore | None = None) -> int:
    """Top-level acquisition driver. Returns the shell exit code."""
    options = parse_options(args)

    paths_to_check = [options.output_dir, options.manifest_path]
    if args.out:
        paths_to_check.append(Path(args.out).expanduser())
    ac.check_output_privacy(
        paths_to_check, allow_public=args.allow_public_output, tool=TOOL_NAME,
    )

    if store is None:
        store = make_s3_store(args.bucket, args.region)

    summary = ac.RunSummary(
        draft_manifest_path=str(options.manifest_path) if not args.dry_run else None,
        output_dir=str(options.output_dir),
    )

    sys.stderr.write(
        f"Acquiring Mirrulations comments from {len(options.prefixes)} "
        f"prefix(es) into {options.output_dir}\n"
        f"Persona: {options.persona} (impostor_for: {options.impostor_for})\n"
        "  note: exact-hash dedup only; near-duplicate campaign text is not "
        "removed (LSH follow-up).\n"
    )

    for item in discover_items(options, store):
        if summary.acquired >= options.max_items:
            break
        try:
            body_text, title, author, date = extract_one(item, options, store)
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
            "No comments acquired. Verify the --prefix is rooted at "
            "derived-data/ (raw-data/ holds only binaries), the "
            "--text-key-pattern (with --dry-run), and S3 connectivity; pass "
            "--allow-empty to allow an empty run.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
