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
``raw-data/`` and ``derived-data/``. ``raw-data/`` holds per-comment JSON
metadata and binary attachments; the pre-extracted text lives under ``derived-data/`` at
``derived-data/<AGENCY>/<DOCKET>/mirrulations/extracted_txt/
comments_extracted_text/<engine>/<comment>_extracted.txt``. So ``--prefix``
must be rooted at ``derived-data/`` -- a bare-agency or ``raw-data/`` prefix
lists no extracted text and acquires nothing. The default
``--text-key-pattern`` matches these keys; still verify hit counts with
``--dry-run`` before a bulk pull (see references/acquire-corpus-pattern.md).
The opt-in --metadata-mode standard verifies the comment JSON/attachment
join and records source dates in the private sidecar; off is the default.
Neither mode infers an authored date. See
references/mirrulations-metadata-provenance.md.

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
import hashlib
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
SCRAPER_VERSION = "1.2"

METADATA_MAX_BYTES = 2 * 1024 * 1024
_METADATA_REASONS = frozenset({
    "metadata-unsupported-key", "metadata-missing", "metadata-transport",
    "metadata-too-large", "metadata-invalid-json", "metadata-invalid-schema",
    "metadata-identity-mismatch", "metadata-attachment-mismatch",
    "metadata-attachment-ambiguous", "metadata-custody-mismatch",
})

DEFAULT_BUCKET = "mirrulations"
DEFAULT_REGION = "us-east-1"
# Extracted comment-attachment text keys. Tolerant default; the operator
# verifies / tunes against the live bucket with --dry-run.
DEFAULT_TEXT_KEY_PATTERN = r"extracted.*\.txt$"
DEFAULT_AUTHOR = "Regulatory Commenter"


# ---- Object store (the S3 analogue of Fetcher/FixtureFetcher) -----


class MetadataSkip(Exception):
    """A fixed, prose-free reason to skip one standard-mode item."""

    def __init__(self, reason: str) -> None:
        if reason not in _METADATA_REASONS:
            raise ValueError("invalid metadata skip reason")
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class MetadataRead:
    status: str  # ok, missing, transport, too_large
    data: bytes | None = None


@dataclass
class MetadataReceipt:
    stage: str  # fetched, processed
    locator: str
    bucket: str
    mode: str
    decoded_body_sha256: str
    raw_text_sha256: str
    raw_text_bytes: int
    source_metadata: dict[str, Any]
    source_metadata_sha256: str = ""
    piece_source_url: str | None = None
    piece_content_hash: str | None = None


def _read_metadata_body(body: Any) -> MetadataRead:
    """Read through short chunks and detect one byte beyond the 2 MiB limit."""
    chunks: list[bytes] = []
    total = 0
    try:
        while total <= METADATA_MAX_BYTES:
            chunk = body.read(METADATA_MAX_BYTES + 1 - total)
            if not isinstance(chunk, bytes):
                return MetadataRead("transport")
            if not chunk:
                break
            total += len(chunk)
            if total > METADATA_MAX_BYTES:
                return MetadataRead("too_large")
            chunks.append(chunk)
        return MetadataRead("ok", b"".join(chunks))
    except Exception:
        return MetadataRead("transport")
    finally:
        try:
            body.close()
        except Exception:
            pass


class ObjectStore:
    """Abstract key/value object store. Tests use ``FixtureObjectStore``;
    production uses the boto3-backed store from ``make_s3_store``."""

    def list_keys(self, prefix: str) -> Iterator[str]:  # pragma: no cover
        raise NotImplementedError

    def get_bytes(self, key: str) -> bytes | None:  # pragma: no cover
        raise NotImplementedError

    def get_metadata(self, key: str) -> MetadataRead:  # pragma: no cover
        raise NotImplementedError


class FixtureObjectStore(ObjectStore):
    """In-memory store backed by a ``{key: bytes}`` dict (no network)."""

    def __init__(
        self, objects: dict[str, bytes],
        metadata_outcomes: dict[str, MetadataRead] | None = None,
    ) -> None:
        self.objects = dict(objects)
        self.metadata_outcomes = dict(metadata_outcomes or {})
        self.listed_prefixes: list[str] = []
        self.got_keys: list[str] = []
        self.metadata_got_keys: list[str] = []

    def list_keys(self, prefix: str) -> Iterator[str]:
        self.listed_prefixes.append(prefix)
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield key

    def get_bytes(self, key: str) -> bytes | None:
        self.got_keys.append(key)
        return self.objects.get(key)

    def get_metadata(self, key: str) -> MetadataRead:
        self.metadata_got_keys.append(key)
        if key in self.metadata_outcomes:
            return self.metadata_outcomes[key]
        data = self.objects.get(key)
        if data is None:
            return MetadataRead("missing")
        if len(data) > METADATA_MAX_BYTES:
            return MetadataRead("too_large")
        return MetadataRead("ok", data)


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
    metadata_client = None

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

        def get_metadata(self, key: str) -> MetadataRead:
            nonlocal metadata_client
            try:
                if metadata_client is None:
                    metadata_client = boto3.client(
                        "s3", region_name=region,
                        config=Config(
                            signature_version=UNSIGNED,
                            connect_timeout=10,
                            read_timeout=25,
                            retries={"total_max_attempts": 2},
                        ),
                    )
                resp = metadata_client.get_object(Bucket=bucket, Key=key)
            except Exception as exc:
                response = getattr(exc, "response", None)
                error = response.get("Error") if isinstance(response, dict) else None
                if isinstance(error, dict) and error.get("Code") in {
                    "NoSuchKey", "404",
                }:
                    return MetadataRead("missing")
                return MetadataRead("transport")
            if not isinstance(resp, dict):
                return MetadataRead("transport")
            body = resp.get("Body")
            if body is None:
                return MetadataRead("transport")
            content_length = resp.get("ContentLength")
            if isinstance(content_length, int) and content_length > METADATA_MAX_BYTES:
                try:
                    return MetadataRead("too_large")
                finally:
                    try:
                        body.close()
                    except Exception:
                        pass
            return _read_metadata_body(body)

    return S3ObjectStore()


@dataclass
class ItemMeta:
    """One extracted-comment-text object discovered in the bucket."""
    locator: str          # S3 key
    title: str = ""
    metadata_receipt: MetadataReceipt | None = field(
        default=None, repr=False, compare=False,
    )


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
    bucket: str
    metadata_mode: str
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
    language_status: str = "unknown"


# ---- Bounded metadata join ----------------------------------------

_AGENCY_RE = re.compile(r"[A-Z][A-Z0-9]*")
_DOCKET_TAIL_RE = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)*")
_ENGINE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_TEXT_NAME_RE = re.compile(
    r"(?P<comment>[^/]+)_attachment_(?P<number>[1-9][0-9]*)_extracted\.txt"
)
_TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:\.[0-9]+)?)?"
    r"(?:Z|[+-][0-9]{2}(?::?[0-9]{2}(?::?[0-9]{2}(?:\.[0-9]+)?)?)?)"
)


def _standard_metadata_key(text_key: str) -> tuple[str, str, str, int, str]:
    if not isinstance(text_key, str):
        raise MetadataSkip("metadata-unsupported-key")
    parts = text_key.split("/")
    if len(parts) != 8 or parts[:1] != ["derived-data"] or parts[3:6] != [
        "mirrulations", "extracted_txt", "comments_extracted_text",
    ]:
        raise MetadataSkip("metadata-unsupported-key")
    agency, docket, engine, name = parts[1], parts[2], parts[6], parts[7]
    if not (
        _AGENCY_RE.fullmatch(agency)
        and docket.startswith(agency + "-")
        and _DOCKET_TAIL_RE.fullmatch(docket[len(agency) + 1:])
        and _ENGINE_RE.fullmatch(engine)
    ):
        raise MetadataSkip("metadata-unsupported-key")
    match = _TEXT_NAME_RE.fullmatch(name)
    if match is None:
        raise MetadataSkip("metadata-unsupported-key")
    comment = match.group("comment")
    suffix = comment[len(docket) + 1:] if comment.startswith(docket + "-") else ""
    if not re.fullmatch(r"[0-9]+", suffix):
        raise MetadataSkip("metadata-unsupported-key")
    if len(match.group("number")) > 18:
        raise MetadataSkip("metadata-unsupported-key")
    number = int(match.group("number"))
    metadata_key = (
        f"raw-data/{agency}/{docket}/text-{docket}/comments/{comment}.json"
    )
    return metadata_key, docket, comment, number, (
        f"https://downloads.regulations.gov/{comment}/attachment_{number}.pdf"
    )


def _reported_timestamp(value: Any) -> dict[str, str | None]:
    if value is None:
        return {"status": "missing", "value": None}
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        return {"status": "invalid", "value": None}
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return {"status": "invalid", "value": None}
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return {"status": "invalid", "value": None}
    return {"status": "valid", "value": value}


def _source_metadata(
    text_key: str, text_bytes: bytes, options: ProcessOptions,
    store: ObjectStore,
) -> dict[str, Any]:
    metadata_key, docket, comment, number, expected_url = (
        _standard_metadata_key(text_key)
    )
    try:
        result = store.get_metadata(metadata_key)
    except Exception:
        raise MetadataSkip("metadata-transport") from None
    if not isinstance(result, MetadataRead):
        raise MetadataSkip("metadata-transport")
    if result.status != "ok":
        reason = {
            "missing": "metadata-missing",
            "too_large": "metadata-too-large",
            "transport": "metadata-transport",
        }.get(result.status, "metadata-transport")
        raise MetadataSkip(reason)
    data = result.data
    if not isinstance(data, bytes):
        raise MetadataSkip("metadata-transport")
    if len(data) > METADATA_MAX_BYTES:
        raise MetadataSkip("metadata-too-large")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise MetadataSkip("metadata-invalid-json") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise MetadataSkip("metadata-invalid-schema")
    obj = payload["data"]
    attrs = obj.get("attributes")
    if not isinstance(attrs, dict) or not all(
        isinstance(obj.get(field), str) for field in ("id", "type")
    ):
        raise MetadataSkip("metadata-invalid-schema")
    if (
        obj["id"] != comment or obj["type"] != "comments"
        or attrs.get("docketId") != docket
    ):
        raise MetadataSkip("metadata-identity-mismatch")

    relationships = obj.get("relationships", {})
    if not isinstance(relationships, dict):
        raise MetadataSkip("metadata-invalid-schema")
    attachment_rel = relationships.get("attachments", {})
    if not isinstance(attachment_rel, dict):
        raise MetadataSkip("metadata-invalid-schema")
    related = attachment_rel.get("data", [])
    included = payload.get("included", [])
    if not isinstance(related, list) or not isinstance(included, list):
        raise MetadataSkip("metadata-invalid-schema")
    related_ids: set[str] = set()
    for relation in related:
        if not isinstance(relation, dict):
            raise MetadataSkip("metadata-invalid-schema")
        if relation.get("type") != "attachments":
            continue
        ident = relation.get("id")
        if not isinstance(ident, str) or not ident:
            raise MetadataSkip("metadata-invalid-schema")
        if ident in related_ids:
            raise MetadataSkip("metadata-attachment-ambiguous")
        related_ids.add(ident)
    seen_included: set[str] = set()
    matches: list[tuple[str, dict[str, Any]]] = []
    for attachment in included:
        if not isinstance(attachment, dict):
            raise MetadataSkip("metadata-invalid-schema")
        if attachment.get("type") != "attachments":
            continue
        ident = attachment.get("id")
        if not isinstance(ident, str):
            raise MetadataSkip("metadata-invalid-schema")
        if ident not in related_ids:
            continue
        if ident in seen_included:
            raise MetadataSkip("metadata-attachment-ambiguous")
        seen_included.add(ident)
        attachment_attrs = attachment.get("attributes")
        if not isinstance(attachment_attrs, dict):
            raise MetadataSkip("metadata-invalid-schema")
        formats = attachment_attrs.get("fileFormats", [])
        if not isinstance(formats, list):
            raise MetadataSkip("metadata-invalid-schema")
        for entry in formats:
            if not isinstance(entry, dict):
                raise MetadataSkip("metadata-invalid-schema")
            if entry.get("format") == "pdf" and entry.get("fileUrl") == expected_url:
                matches.append((ident, attachment_attrs))
    if not matches:
        raise MetadataSkip("metadata-attachment-mismatch")
    if len(matches) != 1:
        raise MetadataSkip("metadata-attachment-ambiguous")
    attachment_id, attachment_attrs = matches[0]
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", attachment_id) is None:
        raise MetadataSkip("metadata-invalid-schema")
    dates = {
        "received": _reported_timestamp(attrs.get("receiveDate")),
        "posted": _reported_timestamp(attrs.get("postedDate")),
        "postmark": _reported_timestamp(attrs.get("postmarkDate")),
        "comment_modified": _reported_timestamp(attrs.get("modifyDate")),
        "attachment_modified": _reported_timestamp(
            attachment_attrs.get("modifyDate")
        ),
    }
    return {
        "schema": "setec.mirrulations_source_metadata.v1",
        "status": "binding_verified",
        "bucket": options.bucket,
        "text_object_key": text_key,
        "text_object_sha256": hashlib.sha256(text_bytes).hexdigest(),
        "text_object_bytes": len(text_bytes),
        "metadata_object_key": metadata_key,
        "metadata_object_sha256": hashlib.sha256(data).hexdigest(),
        "metadata_object_bytes": len(data),
        "comment_id": comment,
        "docket_id": docket,
        "attachment_number": number,
        "attachment_relationship_id": attachment_id,
        "attachment_pdf_file_url": expected_url,
        "retrieved_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "reported_dates": dates,
        "authored_date_proven": False,
        "historical_text_bytes_proven": False,
    }


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
    item.metadata_receipt = None
    data = store.get_bytes(item.locator)
    if not data:
        return "", "", "", None
    text = data.decode("utf-8", "replace")
    if not text.strip():
        return "", "", "", None
    if options.metadata_mode == "standard":
        evidence = _source_metadata(item.locator, data, options, store)
        item.metadata_receipt = MetadataReceipt(
            stage="fetched",
            locator=item.locator,
            bucket=options.bucket,
            mode=options.metadata_mode,
            decoded_body_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            raw_text_sha256=hashlib.sha256(data).hexdigest(),
            raw_text_bytes=len(data),
            source_metadata=evidence,
            source_metadata_sha256=_source_metadata_digest(evidence),
        )
    return text, item.title or "untitled", options.author or DEFAULT_AUTHOR, None


# ---- Per-comment processing ---------------------------------------


def _source_metadata_digest(source_metadata: dict[str, Any]) -> str:
    canonical = json.dumps(
        source_metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_receipt(
    item: ItemMeta, options: ProcessOptions, *, stage: str,
) -> MetadataReceipt:
    receipt = item.metadata_receipt
    if (
        receipt is None or receipt.stage != stage
        or receipt.locator != item.locator
        or receipt.bucket != options.bucket
        or receipt.mode != options.metadata_mode
        or not isinstance(receipt.source_metadata, dict)
    ):
        raise MetadataSkip("metadata-custody-mismatch")
    try:
        metadata_key, docket, comment, number, expected_url = (
            _standard_metadata_key(item.locator)
        )
        metadata_digest = _source_metadata_digest(receipt.source_metadata)
    except (MetadataSkip, TypeError, ValueError, OverflowError, RecursionError):
        raise MetadataSkip("metadata-custody-mismatch") from None
    expected = {
        "schema": "setec.mirrulations_source_metadata.v1",
        "status": "binding_verified",
        "bucket": options.bucket,
        "text_object_key": item.locator,
        "text_object_sha256": receipt.raw_text_sha256,
        "text_object_bytes": receipt.raw_text_bytes,
        "metadata_object_key": metadata_key,
        "docket_id": docket,
        "comment_id": comment,
        "attachment_number": number,
        "attachment_pdf_file_url": expected_url,
    }
    if (
        receipt.source_metadata_sha256 != metadata_digest
        or any(receipt.source_metadata.get(key) != value
               for key, value in expected.items())
    ):
        raise MetadataSkip("metadata-custody-mismatch")
    return receipt


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
    completed = False
    try:
        if options.metadata_mode == "standard":
            receipt = _require_receipt(item, options, stage="fetched")
            if (
                date is not None
                or receipt.decoded_body_sha256
                != hashlib.sha256(body_text.encode("utf-8")).hexdigest()
            ):
                raise MetadataSkip("metadata-custody-mismatch")
        else:
            item.metadata_receipt = None

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
        if options.metadata_mode == "standard":
            receipt.stage = "processed"
            receipt.piece_source_url = piece.source_url
            receipt.piece_content_hash = piece.content_hash
        completed = True
        return piece
    finally:
        if not completed:
            item.metadata_receipt = None


def emit_piece(
    piece: ac.AcquiredPiece, *, options: ProcessOptions, summary: ac.RunSummary,
    item: ItemMeta | None = None,
) -> None:
    """Write piece + sidecar + manifest entry. No-op for dry-run."""
    try:
        extra_meta = None
        if options.metadata_mode == "standard":
            if item is None:
                raise MetadataSkip("metadata-custody-mismatch")
            receipt = _require_receipt(item, options, stage="processed")
            if (
                receipt.piece_source_url != piece.source_url
                or receipt.piece_content_hash != piece.content_hash
                or piece.date_written is not None
            ):
                raise MetadataSkip("metadata-custody-mismatch")
            extra_meta = {"source_metadata": receipt.source_metadata}
        if options.dry_run:
            sys.stderr.write(
                f"  [dry-run] would write {piece.filename_stem()} "
                f"({piece.word_count} words)\n"
            )
            summary.acquired += 1
            return
        text_path, _meta_path = ac.write_piece(
            piece, output_dir=options.output_dir, scraper_version=SCRAPER_VERSION,
            extra_meta=extra_meta,
        )
        entry = ac.compose_manifest_entry(
            piece, text_path=text_path,
            manifest_relative_to=options.manifest_path.parent,
            language_status=options.language_status,
        )
        ac.append_manifest_entry(options.manifest_path, entry)
        summary.acquired += 1
        sys.stderr.write(
            f"  acquired {text_path.name} ({piece.word_count} words)\n"
        )
    finally:
        if item is not None:
            item.metadata_receipt = None


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
                        "required). raw-data/ also holds comment JSON; "
                        "extracted text is under derived-data/. Pick "
                        "substantive, pre-2020 dockets.")
    p.add_argument("--bucket", default=DEFAULT_BUCKET,
                   help=f"S3 bucket (default: {DEFAULT_BUCKET}).")
    p.add_argument("--region", default=DEFAULT_REGION,
                   help=f"AWS region (default: {DEFAULT_REGION}).")
    p.add_argument("--text-key-pattern", default=DEFAULT_TEXT_KEY_PATTERN,
                   help="Regex selecting extracted-text keys "
                        f"(default: {DEFAULT_TEXT_KEY_PATTERN!r}).")
    p.add_argument(
        "--metadata-mode", choices=["off", "standard"], default="off",
        help=("off (default): current text-only route; standard: verify the "
              "Mirrulations comment JSON/attachment join and write a "
              "source-metadata receipt to the private sidecar."),
    )

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

    p.add_argument(
        "--language-status",
        choices=[
            "native", "non_native_advanced", "non_native_intermediate",
            "learner", "unknown",
        ],
        default="unknown",
        help=(
            "Author language status relative to the text language (default: "
            "unknown). Every non-unknown value is a batch-wide operator "
            "assertion requiring evidence for every affected record; English "
            "text, institutional source, dates, and metadata custody do not "
            "establish native status."
        ),
    )

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
    ac.add_allow_empty_arg(p)
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
        bucket=args.bucket,
        metadata_mode=args.metadata_mode,
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
        language_status=getattr(args, "language_status", "unknown"),
    )


def _record_metadata_skip(
    summary: ac.RunSummary, item: ItemMeta, skip: MetadataSkip,
) -> None:
    if skip.reason in {"metadata-missing", "metadata-transport"}:
        summary.skipped_network_error += 1
    elif skip.reason == "metadata-unsupported-key":
        summary.skipped_filtered += 1
    else:
        summary.skipped_parse_error += 1
    summary.log_skip(reason=skip.reason, url=item.locator)


def run(args: argparse.Namespace, store: ObjectStore | None = None) -> int:
    """Top-level acquisition driver. Returns the shell exit code."""
    options = parse_options(args)
    if options.metadata_mode not in {"off", "standard"}:
        raise ValueError("unknown metadata mode")
    if options.metadata_mode == "standard" and options.bucket != DEFAULT_BUCKET:
        raise ValueError("standard metadata mode requires the mirrulations bucket")

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
        f"Metadata mode: {options.metadata_mode}\n"
        "  note: exact-hash dedup only; near-duplicate campaign text is not "
        "removed (LSH follow-up).\n"
    )

    for item in discover_items(options, store):
        if summary.acquired >= options.max_items:
            break
        try:
            try:
                body_text, title, author, date = extract_one(item, options, store)
            except MetadataSkip:
                raise
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
                emit_piece(piece, options=options, summary=summary, item=item)
        except MetadataSkip as skip:
            _record_metadata_skip(summary, item, skip)
        finally:
            item.metadata_receipt = None

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
            "derived-data/ (raw-data/ also holds comment JSON), the "
            "--text-key-pattern (with --dry-run), and S3 connectivity; pass "
            "--allow-empty to allow an empty run.\n"
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.metadata_mode == "standard" and args.bucket != DEFAULT_BUCKET:
        parser.error("standard metadata mode requires the mirrulations bucket")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
