#!/usr/bin/env python3
"""Deterministic local 8-gram staging-overlap measurement.

This tool intentionally handles only opaque control identifiers in its public
receipts.  Source text, paths, token strings, and shingle values stay local.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata
from typing import Any, Iterable

from shingle_dedup_checkpoint import CheckpointDirectory, CheckpointRefusal, CheckpointState
from shingle_dedup_io import (
    SecureIOError,
    publish_create_new,
    read_bounded_regular,
    read_bounded_regular_excluding_siblings,
)
from shingle_dedup_validate import (
    IndexValidationError,
    configure_creation_connection,
    configure_read_connection,
    validate_index,
)


SCHEMA_VERSION = "setec-shingle-index/1"
REPORT_SCHEMA_VERSION = "setec-shingle-report/1"
CHECKPOINT_SCHEMA_VERSION = "setec-shingle-checkpoint/1"
TASK_SURFACE = "voice_coherence_acquisition"
TOKENIZER_ID = "unicode-w-lower-v1"
SHINGLE_K = 8
MINIMUM_TOKENS = 8
LOW_NUMERATOR, LOW_DENOMINATOR = 35, 100
HIGH_NUMERATOR, HIGH_DENOMINATOR = 60, 100
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_QUERY_BYTES = 4 * 1024 * 1024
MAX_DESCRIPTORS = 5000
MAX_TOKENS = 500000
MAX_POSTINGS = 5000000
MAX_POSTING_FANOUT = 5000
MAX_INDEX_BYTES = 512 * 1024 * 1024
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_PAIR_COUNT = 1000000
MAX_TOTAL_DOCUMENT_BYTES = 512 * 1024 * 1024
MAX_TOTAL_TOKENS = 5_000_000
MAX_SHINGLES_PER_DOCUMENT = 500_000
MAX_DISTINCT_SHINGLES = 5_000_000
MAX_QUERY_TOKENS = 500_000
MAX_QUERY_SHINGLES = 500_000
MAX_EMITTED_PAIRS = 50_000
MAX_POSTINGS_VISITED = 5_000_000
MAX_CANDIDATE_DOCUMENTS = 5_000
MAX_PAIR_COUNTER_INCREMENTS = 10_000_000
WORD_RE = re.compile(r"\w+", re.UNICODE)
ID_REJECT = re.compile(r"[\x00-\x1f\\/]", re.UNICODE)


class Refusal(Exception):
    """A stable, deliberately non-disclosing operational refusal."""


class UsageError(Exception):
    """Argument syntax failed without echoing untrusted argument values."""


class _SafeParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise UsageError()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8") + b"\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _console(value: dict[str, Any], *, error: bool = False) -> None:
    stream = sys.stderr.buffer if error and hasattr(sys.stderr, "buffer") else (
        sys.stdout.buffer if hasattr(sys.stdout, "buffer") else (sys.stderr if error else sys.stdout)
    )
    payload = _canonical(value)
    try:
        stream.write(payload)
    except TypeError:  # injected text stream only
        stream.write(payload.decode("ascii"))
    stream.flush()


def _opaque(value: Any) -> str:
    if not isinstance(value, str):
        raise Refusal()
    encoded = value.encode("utf-8")
    if not (1 <= len(encoded) <= 128) or value != value.strip() or value in {".", ".."} or ID_REJECT.search(value):
        raise Refusal()
    return value


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Refusal()
    if value < -(2**63) or value > 2**63 - 1:
        raise Refusal()
    return value


def _strict_json(text: str) -> Any:
    def no_constant(_value: str) -> None:
        raise ValueError("nonfinite")
    def no_dupes(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result
    try:
        return json.loads(text, parse_constant=no_constant, object_pairs_hook=no_dupes)
    except RecursionError:
        raise Refusal() from None


def _read_bytes(path: Path, *, maximum: int) -> bytes:
    try:
        data = read_bounded_regular(path, maximum)
    except (SecureIOError, OSError, UnicodeError):
        raise Refusal() from None
    return data


def _decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        raise Refusal()
    try:
        return data.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise Refusal() from None


def _tokens(text: str) -> list[str]:
    return [part.lower() for part in WORD_RE.findall(text)]


def _shingle_digests(tokens: list[str]) -> set[bytes]:
    if len(tokens) < SHINGLE_K:
        return set()
    return {hashlib.sha256("\x1f".join(tokens[offset:offset + SHINGLE_K]).encode("utf-8")).digest()
            for offset in range(len(tokens) - SHINGLE_K + 1)}


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6)


def _tier(numerator: int, denominator: int) -> str:
    if numerator * HIGH_DENOMINATOR >= HIGH_NUMERATOR * denominator:
        return "containment_at_least_0_60"
    if numerator * LOW_DENOMINATOR >= LOW_NUMERATOR * denominator:
        return "containment_0_35_to_0_60"
    return "below_0_35"


def _reject_path_aliases(*paths: Path) -> None:
    """Fail closed when two command inputs/outputs identify the same node."""
    absolute = [Path(os.path.abspath(os.fspath(path))) for path in paths]
    for index, left in enumerate(absolute):
        for right in absolute[index + 1:]:
            if left == right:
                raise Refusal()
            try:
                if left.exists() and right.exists() and os.path.samefile(left, right):
                    raise Refusal()
            except OSError:
                raise Refusal() from None


def _parse_manifest_descriptors(path: Path) -> tuple[list[dict[str, Any]], str]:
    """Validate bounded manifest control rows without opening document sources."""
    raw = _read_bytes(path, maximum=MAX_MANIFEST_BYTES)
    text = _decode_text(raw)
    # Physical JSONL separators: LF/CRLF/lone CR.  Empty physical rows remain
    # invalid; a missing final terminator is valid.
    rows = re.split(r"\r\n|\r|\n", text)
    if rows and rows[-1] == "":
        rows.pop()
    if not rows or len(rows) > MAX_DESCRIPTORS or any(not row for row in rows):
        raise Refusal()
    seen_ids: set[str] = set()
    seen_stages: set[tuple[str, str]] = set()
    seen_orders: set[tuple[str, int]] = set()
    parsed: list[dict[str, Any]] = []
    for row in rows:
        if len(row.encode("utf-8")) > MAX_LINE_BYTES:
            raise Refusal()
        try:
            value = _strict_json(row)
        except (ValueError, TypeError, json.JSONDecodeError):
            raise Refusal() from None
        if not isinstance(value, dict):
            raise Refusal()
        expected = {"id", "draft_id", "stage", "stage_order", "text"}
        expected_path = {"id", "draft_id", "stage", "stage_order", "path"}
        if set(value) != expected and set(value) != expected_path:
            raise Refusal()
        doc_id = _opaque(value["id"])
        draft_id = _opaque(value["draft_id"])
        stage = _opaque(value["stage"])
        stage_order = _integer(value["stage_order"])
        if doc_id in seen_ids or (draft_id, stage) in seen_stages or (draft_id, stage_order) in seen_orders:
            raise Refusal()
        seen_ids.add(doc_id)
        seen_stages.add((draft_id, stage))
        seen_orders.add((draft_id, stage_order))
        if "text" in value:
            if not isinstance(value["text"], str):
                raise Refusal()
            try:
                value["text"].encode("utf-8")
            except UnicodeError:
                raise Refusal() from None
            source_control: tuple[str, object] = ("text", value["text"])
        else:
            candidate = value["path"]
            if not isinstance(candidate, str) or not candidate or "\x00" in candidate:
                raise Refusal()
            candidate_path = Path(candidate)
            if candidate_path.is_absolute() or ".." in candidate_path.parts:
                raise Refusal()
            source_control = ("path", candidate_path)
        parsed.append({"doc_id": doc_id, "draft_id": draft_id, "stage": stage,
                       "stage_order": stage_order, "source_control": source_control})
    return parsed, _sha256(raw)


def _materialize_descriptors(descriptors: Iterable[dict[str, Any]], root: Path, *,
                             compute_shingles: bool) -> tuple[list[dict[str, Any]], int, int]:
    """Read/hash and optionally score one bounded (at most 250-item) shard."""
    items = list(descriptors)
    if len(items) > 250:
        raise Refusal()
    parsed: list[dict[str, Any]] = []
    total_bytes = total_tokens = 0
    for descriptor in items:
        source_kind, source_value = descriptor["source_control"]
        if source_kind == "text":
            try:
                source = str(source_value).encode("utf-8")
            except UnicodeError:
                raise Refusal() from None
        elif source_kind == "path" and isinstance(source_value, Path):
            try:
                source = read_bounded_regular(root / source_value, MAX_DOCUMENT_BYTES, root=root)
            except (SecureIOError, OSError):
                raise Refusal() from None
        else:
            raise Refusal()
        if len(source) > MAX_DOCUMENT_BYTES:
            raise Refusal()
        total_bytes += len(source)
        decoded = _decode_text(source)
        tokens = _tokens(decoded) if compute_shingles else []
        if len(tokens) > MAX_TOKENS:
            raise Refusal()
        total_tokens += len(tokens)
        shingles = _shingle_digests(tokens) if compute_shingles else set()
        if len(shingles) > MAX_SHINGLES_PER_DOCUMENT:
            raise Refusal()
        parsed.append({"doc_id": descriptor["doc_id"], "draft_id": descriptor["draft_id"],
                       "stage": descriptor["stage"], "stage_order": descriptor["stage_order"],
                       "content_sha256": _sha256(source),
                       "token_count": len(tokens), "shingles": shingles,
                       "status": "eligible" if shingles else "too_short_unassessed"})
    return parsed, total_bytes, total_tokens


def _descriptor_seal(rows: Iterable[dict[str, Any]]) -> str:
    stream = b"".join(_canonical({"content_sha256": row["content_sha256"], "doc_id": row["doc_id"],
                                    "draft_id": row["draft_id"], "record": "descriptor",
                                    "stage": row["stage"], "stage_order": row["stage_order"]})
                      for row in sorted(rows, key=lambda item: item["doc_id"].encode("utf-8")))
    return _sha256(stream)


def _config_sha256() -> str:
    # This intentionally includes every frozen checkpoint control, even where
    # the current small-run path cannot approach the corresponding ceiling.
    values: dict[str, Any] = {
        "checkpoint_chunk_items": 250, "checkpoint_max_bytes": 2147483648,
        "checkpoint_max_entries": 4056, "checkpoint_max_reserved_temps": 16,
        "checkpoint_max_shard_bytes": 134217728, "checkpoint_max_shards": 4040,
        "checkpoint_max_validation_vm_opcodes": 2000000000, "high_threshold_denominator": 100,
        "high_threshold_numerator": 60, "low_threshold_denominator": 100, "low_threshold_numerator": 35,
        "max_candidate_documents": 5000, "max_descriptors": 5000, "max_distinct_shingles": 5000000,
        "max_control_field_bytes": 128, "max_document_bytes": 4194304, "max_emitted_pairs": 50000,
        "max_index_bytes": 536870912, "max_line_bytes": 8388608, "max_manifest_bytes": 67108864,
        "max_pair_counter_increments": 10000000, "max_posting_fanout": 5000, "max_postings": 5000000,
        "max_postings_visited": 5000000, "max_potential_pairs": 1000000, "max_query_bytes": 4194304,
        "max_query_shingles": 500000, "max_query_tokens": 500000, "max_report_bytes": 67108864,
        "max_shingles_per_document": 500000, "max_sqlite_pages": 131072, "max_tokens_per_document": 500000,
        "max_total_document_bytes": 536870912, "max_total_tokens": 5000000, "minimum_tokens": 8,
        "progress_items": 250, "shingle_k": 8, "sqlite_cache_bytes": 16777216,
        "sqlite_limit_attached": 0, "sqlite_limit_columns": 64, "sqlite_limit_compound_selects": 16,
        "sqlite_limit_expression_depth": 32, "sqlite_limit_length": 16777216, "sqlite_limit_sql_length": 65536,
        "sqlite_limit_trigger_depth": 0, "sqlite_limit_variables": 32, "sqlite_page_size": 4096,
        "sqlite_vm_callback_budget": 500000, "sqlite_vm_callback_interval": 1000,
        "tokenizer_id": TOKENIZER_ID, "unicode_version": unicodedata.unidata_version,
    }
    return _sha256(_canonical(values))


def _logical_seal(connection: sqlite3.Connection, meta: dict[str, str]) -> str:
    header_meta = {key: value for key, value in meta.items() if key != "logical_sha256"}
    digest = hashlib.sha256()
    digest.update(_canonical({"domain": "setec-shingle-index-logical-v1", "meta": header_meta, "record": "header"}))
    for row in connection.execute("SELECT doc_id,draft_id,stage,stage_order,content_sha256,token_count,shingle_count,status FROM documents ORDER BY doc_id COLLATE BINARY"):
        digest.update(_canonical({"content_sha256": bytes(row[4]).hex(), "doc_id": row[0], "draft_id": row[1],
                                  "record": "document", "shingle_count": row[6], "stage": row[2],
                                  "stage_order": row[3], "status": row[7], "token_count": row[5]}))
    for row in connection.execute("SELECT shingle_sha256,doc_id FROM postings ORDER BY shingle_sha256,doc_id COLLATE BINARY"):
        digest.update(_canonical({"doc_id": row[1], "record": "posting", "shingle_sha256": bytes(row[0]).hex()}))
    return digest.hexdigest()


def _publish_bytes(destination: Path, payload: bytes) -> None:
    try:
        publish_create_new(destination, payload)
    except (SecureIOError, OSError, ValueError):
        raise Refusal() from None


def _checkpoint_meta(kind: str, *, chunk_number: int, first_item: str, next_item: str,
                     item_count: int, source_sha: str = "-", descriptor_sha: str = "-",
                     index_sha: str = "-", logical_sha: str = "-",
                     counters: dict[str, int] | None = None) -> dict[str, str]:
    counts = counters or {}
    return {"schema_version": CHECKPOINT_SCHEMA_VERSION, "tool": "shingle_dedup", "method_version": "1",
            "checkpoint_kind": {"inventory": "build_inventory", "build": "build_index", "batch": "batch_report"}[kind],
            "chunk_number": str(chunk_number), "source_manifest_sha256": source_sha,
            "canonical_descriptors_sha256": descriptor_sha, "index_sha256": index_sha,
            "logical_index_sha256": logical_sha, "config_sha256": _config_sha256(),
            "first_item": first_item, "next_item": next_item, "item_count": str(item_count),
            "potential_pairs": str(counts.get("potential_pairs", item_count if kind == "batch" else 0)),
            "unassessed_pairs": str(counts.get("unassessed_pairs", 0)),
            "assessed_pairs": str(counts.get("assessed_pairs", item_count if kind == "batch" else 0)),
            "no_overlap_pairs": str(counts.get("no_overlap_pairs", 0)),
            "below_0_35_pairs": str(counts.get("below_0_35_pairs", 0)),
            "containment_0_35_to_0_60_pairs": str(counts.get("containment_0_35_to_0_60_pairs", 0)),
            "containment_at_least_0_60_pairs": str(counts.get("containment_at_least_0_60_pairs", 0)),
            "reported_pairs": str(counts.get("reported_pairs", 0))}


def _restore_build_rows(state: CheckpointState) -> list[dict[str, Any]]:
    """Restore only exact rows from the already-owned validated snapshots."""
    restored: dict[str, dict[str, Any]] = {}
    for shard in state.build:
        for row in shard.document_rows:
            restored[row[0]] = {"doc_id": row[0], "draft_id": row[1], "stage": row[2], "stage_order": row[3],
                                "content_sha256": bytes(row[4]).hex(), "token_count": row[5], "shingles": set(), "status": row[7]}
        for digest, doc_id in shard.posting_rows:
            restored[doc_id]["shingles"].add(bytes(digest))
    for row in restored.values():
        if len(row["shingles"]) > 500000:
            raise Refusal()
    return sorted(restored.values(), key=lambda row: row["doc_id"].encode("utf-8"))


def _build_index(manifest: Path, index_out: Path, checkpoint_dir: Path, *, resume: bool) -> dict[str, Any]:
    _reject_path_aliases(manifest, index_out, checkpoint_dir)
    descriptors, source_sha = _parse_manifest_descriptors(manifest)
    ordered_descriptors = sorted(descriptors, key=lambda item: item["doc_id"].encode("utf-8"))
    try:
        checkpoints = (CheckpointDirectory.open_resume(checkpoint_dir) if resume
                       else CheckpointDirectory.open_new(checkpoint_dir))
    except CheckpointRefusal:
        raise Refusal() from None
    with checkpoints:
        try:
            state = (checkpoints.load(mode="build", config_sha256=_config_sha256(),
                                      source_manifest_sha256=source_sha)
                     if resume else CheckpointState("build", (), {}))
            existing_inventory = [row for shard in state.inventory for row in shard.inventory_rows]
            inventory_continuation = state.continuation("inventory")
            if inventory_continuation is None and existing_inventory and len(existing_inventory) != len(ordered_descriptors):
                raise Refusal()
            if inventory_continuation is not None:
                if len(existing_inventory) >= len(ordered_descriptors):
                    raise Refusal()
                expected_cursor = _canonical({"doc_id": ordered_descriptors[len(existing_inventory)]["doc_id"]}).decode("ascii").strip()
                if inventory_continuation != expected_cursor:
                    raise Refusal()
            ordered_inventory: list[dict[str, Any]] = []
            inventory_bytes = 0
            for offset in range(0, len(ordered_descriptors), 250):
                descriptor_shard = ordered_descriptors[offset:offset + 250]
                shard, shard_bytes, _unused_tokens = _materialize_descriptors(
                    descriptor_shard, manifest.parent, compute_shingles=False,
                )
                inventory_bytes += shard_bytes
                if inventory_bytes > MAX_TOTAL_DOCUMENT_BYTES:
                    raise Refusal()
                expected_rows = [(row["doc_id"], row["draft_id"], row["stage"], row["stage_order"],
                                  bytes.fromhex(row["content_sha256"])) for row in shard]
                if offset < len(existing_inventory):
                    if existing_inventory[offset:offset + len(shard)] != expected_rows:
                        raise Refusal()
                else:
                    next_item = "null" if offset + 250 >= len(ordered_descriptors) else _canonical({"doc_id": ordered_descriptors[offset + 250]["doc_id"]}).decode("ascii").strip()
                    meta = _checkpoint_meta("inventory", chunk_number=offset // 250,
                                            first_item=_canonical({"doc_id": shard[0]["doc_id"]}).decode("ascii").strip(),
                                            next_item=next_item, item_count=len(shard), source_sha=source_sha)
                    checkpoints.publish(kind="inventory", meta=meta, inventory_rows=expected_rows)
                    _console({"schema_version": "setec-shingle-progress/1", "tool": "shingle_dedup", "phase": "inventory", "processed": offset + len(shard)}, error=True)
                ordered_inventory.extend(shard)

            descriptor_sha = _descriptor_seal(ordered_inventory)
            if any(shard.meta["canonical_descriptors_sha256"] != descriptor_sha for shard in state.build):
                raise Refusal()

            restored = {row["doc_id"]: row for row in _restore_build_rows(state)}
            rows: list[dict[str, Any]] = []
            build_bytes = 0
            build_tokens = sum(row["token_count"] for row in restored.values())
            if build_tokens > MAX_TOTAL_TOKENS:
                raise Refusal()
            for offset in range(0, len(ordered_inventory), 250):
                inventory_shard = ordered_inventory[offset:offset + 250]
                restored_ids = {row["doc_id"] for row in inventory_shard if row["doc_id"] in restored}
                if restored_ids and len(restored_ids) != len(inventory_shard):
                    raise Refusal()
                if restored_ids:
                    build_shard = [restored[row["doc_id"]] for row in inventory_shard]
                else:
                    build_shard, shard_bytes, shard_tokens = _materialize_descriptors(
                        ordered_descriptors[offset:offset + 250], manifest.parent, compute_shingles=True,
                    )
                    build_bytes += shard_bytes; build_tokens += shard_tokens
                    if build_bytes > MAX_TOTAL_DOCUMENT_BYTES or build_tokens > MAX_TOTAL_TOKENS:
                        raise Refusal()
                    next_item = "null" if offset + 250 >= len(ordered_inventory) else _canonical({"doc_id": ordered_inventory[offset + 250]["doc_id"]}).decode("ascii").strip()
                    meta = _checkpoint_meta("build", chunk_number=offset // 250,
                                            first_item=_canonical({"doc_id": build_shard[0]["doc_id"]}).decode("ascii").strip(),
                                            next_item=next_item, item_count=len(build_shard), source_sha=source_sha,
                                            descriptor_sha=descriptor_sha)
                    documents = [(row["doc_id"], row["draft_id"], row["stage"], row["stage_order"],
                                  bytes.fromhex(row["content_sha256"]), row["token_count"], len(row["shingles"]), row["status"])
                                 for row in build_shard]
                    postings = [(digest, row["doc_id"]) for row in build_shard for digest in sorted(row["shingles"])]
                    checkpoints.publish(kind="build", meta=meta, document_rows=documents, posting_rows=postings)
                    _console({"schema_version": "setec-shingle-progress/1", "tool": "shingle_dedup", "phase": "build", "processed": offset + len(build_shard)}, error=True)
                rows.extend(build_shard)
        except CheckpointRefusal:
            raise Refusal() from None

        if not any(row["status"] == "eligible" for row in rows):
            raise Refusal()
        ordered_rows = sorted(rows, key=lambda item: item["doc_id"].encode("utf-8"))
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(":memory:")
            configure_creation_connection(connection, in_memory=True)
            connection.execute("PRAGMA application_id=1397244977")  # SHD1
            connection.execute("PRAGMA user_version=1")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID")
            connection.execute("CREATE TABLE documents(doc_id TEXT PRIMARY KEY COLLATE BINARY,draft_id TEXT NOT NULL COLLATE BINARY,stage TEXT NOT NULL COLLATE BINARY,stage_order INTEGER NOT NULL,content_sha256 BLOB NOT NULL,token_count INTEGER NOT NULL,shingle_count INTEGER NOT NULL,status TEXT NOT NULL) WITHOUT ROWID")
            connection.execute("CREATE TABLE postings(shingle_sha256 BLOB NOT NULL,doc_id TEXT NOT NULL COLLATE BINARY REFERENCES documents(doc_id),PRIMARY KEY(shingle_sha256,doc_id)) WITHOUT ROWID")
            connection.execute("CREATE INDEX documents_shingle_lookup ON postings(doc_id,shingle_sha256)")
            posting_count = 0
            for row in ordered_rows:
                shingle_count = len(row["shingles"])
                posting_count += shingle_count
                if posting_count > MAX_POSTINGS:
                    raise Refusal()
                connection.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)", (row["doc_id"], row["draft_id"], row["stage"], row["stage_order"], bytes.fromhex(row["content_sha256"]), row["token_count"], shingle_count, row["status"]))
                connection.executemany("INSERT INTO postings VALUES(?,?)", ((digest, row["doc_id"]) for digest in sorted(row["shingles"])))
            distinct = connection.execute("SELECT COUNT(DISTINCT shingle_sha256) FROM postings").fetchone()[0]
            fanout = connection.execute("SELECT COALESCE(MAX(count),0) FROM (SELECT COUNT(*) count FROM postings GROUP BY shingle_sha256)").fetchone()[0]
            if distinct > MAX_DISTINCT_SHINGLES or fanout > MAX_POSTING_FANOUT:
                raise Refusal()
            meta = {"schema_version": SCHEMA_VERSION, "tool": "shingle_dedup", "method_version": "1", "tokenizer_id": TOKENIZER_ID,
                    "unicode_version": unicodedata.unidata_version, "shingle_k": "8", "minimum_tokens": "8",
                    "low_threshold_numerator": "35", "low_threshold_denominator": "100", "high_threshold_numerator": "60", "high_threshold_denominator": "100",
                    "source_manifest_sha256": source_sha, "canonical_descriptors_sha256": descriptor_sha,
                    "document_count": str(len(rows)), "eligible_document_count": str(sum(item["status"] == "eligible" for item in rows)),
                    "unassessed_document_count": str(sum(item["status"] != "eligible" for item in rows)), "posting_count": str(posting_count),
                    "distinct_shingle_count": str(distinct), "maximum_posting_fanout": str(fanout), "logical_sha256": "0" * 64}
            logical = _logical_seal(connection, meta)
            meta["logical_sha256"] = logical
            connection.executemany("INSERT INTO meta VALUES(?,?)", sorted(meta.items()))
            connection.commit()
            raw = connection.serialize()
            connection.close(); connection = None
            if len(raw) > MAX_INDEX_BYTES:
                raise Refusal()
            checked, checked_meta = _load_index_bytes(raw, _sha256(raw))
            if checked_meta["source_manifest_sha256"] != source_sha or checked_meta["canonical_descriptors_sha256"] != descriptor_sha or checked_meta["logical_sha256"] != logical:
                checked.close(); raise Refusal()
            checked.close()
            _publish_bytes(index_out, raw)
            index_sha = _sha256(raw)
            return {"index_sha256": index_sha, "logical_index_sha256": logical,
                    "indexed_documents": len(rows), "eligible_documents": sum(item["status"] == "eligible" for item in rows)}
        except (sqlite3.Error, OSError, MemoryError):
            raise Refusal() from None
        finally:
            if connection is not None:
                connection.close()


def _load_index(path: Path, pin: str) -> tuple[sqlite3.Connection, dict[str, str]]:
    try:
        raw = read_bounded_regular_excluding_siblings(
            path, MAX_INDEX_BYTES, forbidden_suffixes=("-wal", "-shm", "-journal"),
        )
    except (SecureIOError, OSError, UnicodeError):
        raise Refusal() from None
    return _load_index_bytes(raw, pin)


def _load_index_bytes(raw: bytes, pin: str) -> tuple[sqlite3.Connection, dict[str, str]]:
    if not re.fullmatch(r"[0-9a-f]{64}", pin):
        raise Refusal()
    if not isinstance(raw, bytes) or len(raw) > MAX_INDEX_BYTES or _sha256(raw) != pin or not hasattr(sqlite3.Connection, "deserialize"):
        raise Refusal()
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(":memory:")
        connection.deserialize(raw)
        configure_read_connection(connection)
        validated_meta = validate_index(connection, logical_seal=_logical_seal, raw_length=len(raw))
        return connection, validated_meta
    except (sqlite3.Error, OSError, ValueError, IndexValidationError):
        if connection is not None:
            connection.close()
        raise Refusal() from None


def _summary(*, potential: int, unassessed: int, no_overlap: int, below: int, mid: int, high: int,
             documents: list[sqlite3.Row], tied: int = 0) -> dict[str, int]:
    assessed = potential - unassessed
    return {"potential_pairs": potential, "unassessed_pairs": unassessed, "assessed_pairs": assessed,
            "no_overlap_pairs": no_overlap, "below_0_35_pairs": below,
            "containment_0_35_to_0_60_pairs": mid, "containment_at_least_0_60_pairs": high,
            "reported_pairs": mid + high, "indexed_documents": len(documents),
            "eligible_documents": sum(doc[7] == "eligible" for doc in documents),
            "unassessed_documents": sum(doc[7] != "eligible" for doc in documents), "tied_best_count": tied}


def _pair(query: tuple[Any, ...], reference: tuple[Any, ...], query_shingles: set[bytes], reference_shingles: set[bytes], *, batch: bool) -> dict[str, Any]:
    shared = len(query_shingles & reference_shingles)
    containment_denominator, reverse_denominator = len(query_shingles), len(reference_shingles)
    containment = _ratio(shared, containment_denominator)
    reverse = _ratio(shared, reverse_denominator)
    union = containment_denominator + reverse_denominator - shared
    jaccard = _ratio(shared, union)
    if batch:
        left = shared * reverse_denominator
        right = shared * containment_denominator
        if left > right:
            metric_n, metric_d, direction = shared, containment_denominator, "query_in_reference"
        elif right > left:
            metric_n, metric_d, direction = shared, reverse_denominator, "reference_in_query"
        else:
            metric_n, metric_d, direction = shared, containment_denominator, "equal"
    else:
        metric_n, metric_d, direction = shared, containment_denominator, None
    return {"pair_kind": "draft_stage_pair_candidate" if batch else "query_reference_candidate",
            "query_id": query[0], "reference_id": reference[0], "draft_id": query[1] if batch else None,
            "query_stage": query[2] if batch else None, "query_stage_order": query[3] if batch else None,
            "reference_stage": reference[2] if batch else None, "reference_stage_order": reference[3] if batch else None,
            "query_tokens": query[5], "reference_tokens": reference[5], "query_shingles": containment_denominator,
            "reference_shingles": reverse_denominator, "shared_shingles": shared,
            "containment_numerator": shared, "containment_denominator": containment_denominator, "containment": containment,
            "reverse_containment_numerator": shared, "reverse_containment_denominator": reverse_denominator, "reverse_containment": reverse,
            "jaccard_numerator": shared, "jaccard_denominator": union, "jaccard": jaccard,
            "tier_metric_numerator": metric_n, "tier_metric_denominator": metric_d, "tier_metric": _ratio(metric_n, metric_d),
            "pair_containment_direction": direction, "overlap_tier": _tier(metric_n, metric_d)}


def _report_base(*, kind: str, index_sha: str, meta: dict[str, str], source_sha: str, summary: dict[str, int], pairs: list[dict[str, Any]]) -> dict[str, Any]:
    tier_metric = "query_in_reference_containment" if kind == "query_doc" else "maximum_directional_containment"
    result: dict[str, Any] = {"schema_version": REPORT_SCHEMA_VERSION, "tool": "shingle_dedup", "method_version": 1,
        "report_kind": kind, "calibration_status": "operational_uncalibrated", "index_sha256": index_sha,
        "logical_index_sha256": meta["logical_sha256"], "source_sha256": source_sha,
        "method": {"tokenizer_id": TOKENIZER_ID, "unicode_version": meta["unicode_version"], "shingle_k": 8,
                   "minimum_tokens": 8, "tier_metric": tier_metric, "low_threshold_numerator": 35,
                   "low_threshold_denominator": 100, "high_threshold_numerator": 60, "high_threshold_denominator": 100},
        "summary": summary, "pairs": pairs}
    result["payload_sha256"] = _sha256(_canonical(result))
    return result


def _write_report(path: Path, report: dict[str, Any]) -> str:
    raw = _canonical(report)
    if len(raw) > MAX_REPORT_BYTES:
        raise Refusal()
    _publish_bytes(path, raw)
    return _sha256(raw)


def _query(index: Path, pin: str, query_file: Path, query_id: str, report_out: Path) -> dict[str, Any]:
    _reject_path_aliases(index, query_file, report_out)
    query_id = _opaque(query_id)
    raw_query = _read_bytes(query_file, maximum=MAX_QUERY_BYTES)
    tokens = _tokens(_decode_text(raw_query))
    if len(tokens) > MAX_QUERY_TOKENS:
        raise Refusal()
    query_shingles = _shingle_digests(tokens)
    if not query_shingles or len(query_shingles) > MAX_QUERY_SHINGLES:
        raise Refusal()
    connection, meta = _load_index(index, pin)
    try:
        documents = list(connection.execute("SELECT doc_id,draft_id,stage,stage_order,content_sha256,token_count,shingle_count,status FROM documents ORDER BY doc_id COLLATE BINARY"))
        if len(documents) > MAX_CANDIDATE_DOCUMENTS:
            raise Refusal()
        postings: dict[str, set[bytes]] = {doc[0]: set() for doc in documents}
        postings_visited = 0
        for digest, doc_id in connection.execute("SELECT shingle_sha256,doc_id FROM postings"):
            postings_visited += 1
            if postings_visited > MAX_POSTINGS_VISITED:
                raise Refusal()
            postings[doc_id].add(bytes(digest))
        query_doc: tuple[Any, ...] = (query_id, None, None, None, None, len(tokens), len(query_shingles), "eligible")
        all_pairs: list[dict[str, Any]] = []
        unassessed = no_overlap = below = mid = high = 0
        processed = 0
        for reference in documents:
            if reference[0] == query_id:
                continue
            processed += 1
            if processed > MAX_PAIR_COUNTER_INCREMENTS:
                raise Refusal()
            if reference[7] != "eligible":
                unassessed += 1
                if processed % 250 == 0:
                    _console({"schema_version": "setec-shingle-progress/1", "tool": "shingle_dedup", "phase": "query", "processed": processed}, error=True)
                continue
            row = _pair(query_doc, reference, query_shingles, postings[reference[0]], batch=False)
            if row["shared_shingles"] == 0:
                no_overlap += 1
            elif row["overlap_tier"] == "below_0_35":
                below += 1
            elif row["overlap_tier"] == "containment_0_35_to_0_60":
                mid += 1; all_pairs.append(row)
            else:
                high += 1; all_pairs.append(row)
            if len(all_pairs) > MAX_EMITTED_PAIRS:
                raise Refusal()
            if processed % 250 == 0:
                _console({"schema_version": "setec-shingle-progress/1", "tool": "shingle_dedup", "phase": "query", "processed": processed}, error=True)
        all_pairs.sort(key=lambda row: (-Fraction(row["containment_numerator"], row["containment_denominator"]),
                                        -Fraction(row["jaccard_numerator"], row["jaccard_denominator"]), -row["shared_shingles"],
                                        row["reference_id"].encode("utf-8")))
        tied = 0
        if all_pairs:
            best = all_pairs[0]
            tied = sum((row["containment_numerator"] * best["containment_denominator"] == best["containment_numerator"] * row["containment_denominator"] and
                        row["jaccard_numerator"] * best["jaccard_denominator"] == best["jaccard_numerator"] * row["jaccard_denominator"] and
                        row["shared_shingles"] == best["shared_shingles"]) for row in all_pairs)
        summary = _summary(potential=len(documents) - (1 if any(doc[0] == query_id for doc in documents) else 0),
                           unassessed=unassessed, no_overlap=no_overlap, below=below, mid=mid, high=high, documents=documents, tied=tied)
        report = _report_base(kind="query_doc", index_sha=pin, meta=meta, source_sha=_sha256(raw_query), summary=summary, pairs=all_pairs)
        return {"report_sha256": _write_report(report_out, report), "reported_pairs": len(all_pairs), "summary": summary}
    finally:
        connection.close()


def _batch(index: Path, pin: str, report_out: Path, checkpoint_dir: Path, *, resume: bool) -> dict[str, Any]:
    _reject_path_aliases(index, report_out, checkpoint_dir)
    connection, meta = _load_index(index, pin)
    checkpoints: CheckpointDirectory | None = None
    try:
        try:
            checkpoints = (CheckpointDirectory.open_resume(checkpoint_dir) if resume
                           else CheckpointDirectory.open_new(checkpoint_dir))
        except CheckpointRefusal:
            raise Refusal() from None
        try:
            state = (checkpoints.load(mode="batch", config_sha256=_config_sha256(), index_sha256=pin,
                                      logical_index_sha256=meta["logical_sha256"])
                     if resume else CheckpointState("batch", (), {}))
        except CheckpointRefusal:
            raise Refusal() from None
        documents = list(connection.execute("SELECT doc_id,draft_id,stage,stage_order,content_sha256,token_count,shingle_count,status FROM documents ORDER BY doc_id COLLATE BINARY"))
        if len(documents) > MAX_CANDIDATE_DOCUMENTS:
            raise Refusal()
        restored_counts = {"potential_pairs": 0, "unassessed_pairs": 0, "assessed_pairs": 0,
                           "no_overlap_pairs": 0, "below_0_35_pairs": 0,
                           "containment_0_35_to_0_60_pairs": 0, "containment_at_least_0_60_pairs": 0,
                           "reported_pairs": 0}
        restored_pairs: list[dict[str, Any]] = []
        for shard in state.batch:
            for key in restored_counts:
                restored_counts[key] += int(shard.meta[key])
            for _sequence, raw_pair, _pair_sha in shard.pair_rows:
                pair = _strict_json(bytes(raw_pair).decode("utf-8"))
                if not isinstance(pair, dict):
                    raise Refusal()
                restored_pairs.append(pair)
        resume_cursor = state.continuation("batch")
        if resume and state.batch and resume_cursor is None:
            restored_pairs.sort(key=lambda row: (row["draft_id"].encode("utf-8"), row["query_stage_order"],
                                                  row["reference_stage_order"], row["query_id"].encode("utf-8"), row["reference_id"].encode("utf-8")))
            summary = _summary(potential=restored_counts["potential_pairs"], unassessed=restored_counts["unassessed_pairs"],
                               no_overlap=restored_counts["no_overlap_pairs"], below=restored_counts["below_0_35_pairs"],
                               mid=restored_counts["containment_0_35_to_0_60_pairs"], high=restored_counts["containment_at_least_0_60_pairs"], documents=documents)
            report = _report_base(kind="draft_stage_pair_candidates", index_sha=pin, meta=meta,
                                  source_sha=meta["source_manifest_sha256"], summary=summary, pairs=restored_pairs)
            return {"report_sha256": _write_report(report_out, report), "reported_pairs": len(restored_pairs), "summary": summary}
        by_draft: dict[str, list[tuple[Any, ...]]] = {}
        postings: dict[str, set[bytes]] = {doc[0]: set() for doc in documents}
        postings_visited = 0
        for digest, doc_id in connection.execute("SELECT shingle_sha256,doc_id FROM postings"):
            postings_visited += 1
            if postings_visited > MAX_POSTINGS_VISITED:
                raise Refusal()
            postings[doc_id].add(bytes(digest))
        for doc in documents:
            by_draft.setdefault(doc[1], []).append(doc)
        potential = restored_counts["potential_pairs"]
        unassessed = restored_counts["unassessed_pairs"]
        no_overlap = restored_counts["no_overlap_pairs"]
        below = restored_counts["below_0_35_pairs"]
        mid = restored_counts["containment_0_35_to_0_60_pairs"]
        high = restored_counts["containment_at_least_0_60_pairs"]
        results: list[dict[str, Any]] = restored_pairs
        shard_number = len(state.batch)
        shard_pairs: list[dict[str, Any]] = []
        shard_counts = {"potential_pairs": 0, "unassessed_pairs": 0, "assessed_pairs": 0,
                        "no_overlap_pairs": 0, "below_0_35_pairs": 0,
                        "containment_0_35_to_0_60_pairs": 0,
                        "containment_at_least_0_60_pairs": 0, "reported_pairs": 0}
        shard_first: str | None = None

        def cursor_for(query: tuple[Any, ...], reference: tuple[Any, ...]) -> str:
            return _canonical({"draft_id": query[1], "query_id": query[0], "query_stage_order": query[3],
                               "reference_id": reference[0], "reference_stage_order": reference[3]}).decode("ascii").strip()

        def flush(next_item: str) -> None:
            nonlocal shard_number, shard_pairs, shard_counts, shard_first
            if shard_first is None:
                return
            checkpoint_meta = _checkpoint_meta("batch", chunk_number=shard_number,
                                               first_item=shard_first, next_item=next_item,
                                               item_count=shard_counts["potential_pairs"],
                                               index_sha=pin, logical_sha=meta["logical_sha256"], counters=shard_counts)
            try:
                checkpoints.publish(kind="batch", meta=checkpoint_meta, pairs=shard_pairs)
            except CheckpointRefusal:
                raise Refusal() from None
            _console({"schema_version": "setec-shingle-progress/1", "tool": "shingle_dedup", "phase": "batch",
                      "processed": potential}, error=True)
            shard_number += 1
            shard_pairs = []
            shard_counts = {"potential_pairs": 0, "unassessed_pairs": 0, "assessed_pairs": 0,
                            "no_overlap_pairs": 0, "below_0_35_pairs": 0,
                            "containment_0_35_to_0_60_pairs": 0,
                            "containment_at_least_0_60_pairs": 0, "reported_pairs": 0}
            shard_first = None

        for draft in sorted(by_draft, key=lambda value: value.encode("utf-8")):
            items = by_draft[draft]
            seen_stages: set[str] = set()
            seen_orders: set[int] = set()
            for item in items:
                if item[2] in seen_stages or item[3] in seen_orders:
                    raise Refusal()
                seen_stages.add(item[2]); seen_orders.add(item[3])
            ordered = sorted(items, key=lambda item: item[3])
            for earlier_index, earlier in enumerate(ordered):
                for later in ordered[earlier_index + 1:]:
                    cursor = cursor_for(later, earlier)
                    if resume_cursor is not None:
                        if cursor != resume_cursor:
                            continue
                        resume_cursor = None
                    if shard_counts["potential_pairs"] == 250:
                        flush(cursor)
                    if shard_first is None:
                        shard_first = cursor
                    potential += 1
                    if potential > MAX_PAIR_COUNT or potential > MAX_PAIR_COUNTER_INCREMENTS:
                        raise Refusal()
                    shard_counts["potential_pairs"] += 1
                    if earlier[7] != "eligible" or later[7] != "eligible":
                        unassessed += 1; shard_counts["unassessed_pairs"] += 1; continue
                    shard_counts["assessed_pairs"] += 1
                    row = _pair(later, earlier, postings[later[0]], postings[earlier[0]], batch=True)
                    if row["shared_shingles"] == 0:
                        no_overlap += 1; shard_counts["no_overlap_pairs"] += 1
                    elif row["overlap_tier"] == "below_0_35":
                        below += 1; shard_counts["below_0_35_pairs"] += 1
                    elif row["overlap_tier"] == "containment_0_35_to_0_60":
                        mid += 1; results.append(row); shard_pairs.append(row)
                        shard_counts["containment_0_35_to_0_60_pairs"] += 1; shard_counts["reported_pairs"] += 1
                    else:
                        high += 1; results.append(row); shard_pairs.append(row)
                        shard_counts["containment_at_least_0_60_pairs"] += 1; shard_counts["reported_pairs"] += 1
                    if len(results) > MAX_EMITTED_PAIRS:
                        raise Refusal()
        if resume_cursor is not None:
            raise Refusal()
        if shard_first is None:
            checkpoint_meta = _checkpoint_meta("batch", chunk_number=shard_number,
                                               first_item="null", next_item="null", item_count=0,
                                               index_sha=pin, logical_sha=meta["logical_sha256"], counters=shard_counts)
            try:
                checkpoints.publish(kind="batch", meta=checkpoint_meta, pairs=[])
            except CheckpointRefusal:
                raise Refusal() from None
        else:
            flush("null")
        results.sort(key=lambda row: (row["draft_id"].encode("utf-8"), row["query_stage_order"],
                                      row["reference_stage_order"], row["query_id"].encode("utf-8"), row["reference_id"].encode("utf-8")))
        summary = _summary(potential=potential, unassessed=unassessed, no_overlap=no_overlap, below=below,
                           mid=mid, high=high, documents=documents)
        report = _report_base(kind="draft_stage_pair_candidates", index_sha=pin, meta=meta,
                              source_sha=meta["source_manifest_sha256"], summary=summary, pairs=results)
        return {"report_sha256": _write_report(report_out, report), "reported_pairs": len(results), "summary": summary}
    finally:
        if checkpoints is not None:
            checkpoints.close()
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = _SafeParser(description="deterministic local shingle staging measurement")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-index")
    build.add_argument("--manifest", required=True); build.add_argument("--index-out", required=True)
    build.add_argument("--checkpoint-dir", required=True); build.add_argument("--resume", action="store_true")
    query = commands.add_parser("query-doc")
    query.add_argument("--index", required=True); query.add_argument("--index-sha256", required=True)
    query.add_argument("--query-file", required=True); query.add_argument("--query-id", required=True); query.add_argument("--report-out", required=True)
    batch = commands.add_parser("batch-report")
    batch.add_argument("--index", required=True); batch.add_argument("--index-sha256", required=True)
    batch.add_argument("--report-out", required=True); batch.add_argument("--checkpoint-dir", required=True); batch.add_argument("--resume", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "build-index":
            receipt = _build_index(Path(arguments.manifest), Path(arguments.index_out), Path(arguments.checkpoint_dir), resume=arguments.resume)
        elif arguments.command == "query-doc":
            receipt = _query(Path(arguments.index), arguments.index_sha256, Path(arguments.query_file), arguments.query_id, Path(arguments.report_out))
        else:
            receipt = _batch(Path(arguments.index), arguments.index_sha256, Path(arguments.report_out), Path(arguments.checkpoint_dir), resume=arguments.resume)
        _console({"schema_version": "setec-shingle-progress/1", "tool": "shingle_dedup",
                  "status": "complete", "reported_pairs": int(receipt.get("reported_pairs", 0))}, error=True)
        _console({"schema_version": "setec-shingle-receipt/1", "tool": "shingle_dedup", "status": "complete", **receipt})
        return 0
    except (Refusal, MemoryError):
        try:
            _console({"schema_version": "setec-shingle-receipt/1", "tool": "shingle_dedup", "status": "refused", "code": 3}, error=True)
        except (MemoryError, OSError):
            pass
        return 3
    except UsageError:
        _console({"schema_version": "setec-shingle-receipt/1", "tool": "shingle_dedup", "status": "usage", "code": 2}, error=True)
        return 2
    except (OSError, ValueError, sqlite3.Error, SecureIOError, IndexValidationError):
        _console({"schema_version": "setec-shingle-receipt/1", "tool": "shingle_dedup", "status": "refused", "code": 3}, error=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
