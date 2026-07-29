"""Strict, synthetic-testable Spec-80 source-population commitment helpers.

This module deliberately has no corpus discovery, fallback identifiers, or
repair path.  A later reviewed near-duplicate producer invokes it only after
it has produced the complete frozen passage report.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
import struct
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import author_corpus_export as author_export
from passage_tokenizer_v1 import DATA_FILE, load_data

SCHEMA = "setec-passage-source-population-commitment/1"
RECEIPT_SCHEMA = "setec-passage-source-population-receipt/1"
RECORD_SCHEMA = "voicewright-author-corpus/1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_OID = re.compile(r"[0-9a-f]{40}\Z")
_RECORD_KEYS = author_export.RECORD_KEYS

class CommitmentError(ValueError): pass

def committed_producer_identity(*, repository: Path, script: Path) -> tuple[str, str, bytes]:
    """Return the exact committed identity of ``script`` at ``HEAD``.

    The strict profile binds a Git object, never a caller-supplied revision.
    Refuse a dirty or untracked producer rather than describing working-tree
    bytes as committed code.
    """
    try:
        relative = script.resolve().relative_to(repository.resolve()).as_posix()
        revision = subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True
        ).strip()
        blob = subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", f"HEAD:{relative}"], text=True
        ).strip()
        committed = subprocess.check_output(
            ["git", "-C", str(repository), "show", f"HEAD:{relative}"]
        )
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise CommitmentError("committed producer identity unavailable") from exc
    if not _OID.fullmatch(revision) or not _OID.fullmatch(blob):
        raise CommitmentError("committed producer identity")
    if script.read_bytes() != committed:
        raise CommitmentError("producer working tree differs from committed blob")
    return revision, blob, committed

def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()+b"\n"

def _sha(data: bytes) -> str: return "sha256:" + hashlib.sha256(data).hexdigest()

def canonical_frame_v1(value: Any) -> bytes:
    if value is None: tag, payload = b"n", b""
    elif type(value) is bool: tag, payload = b"b", bytes([int(value)])
    elif type(value) is int: tag, payload = b"i", str(value).encode("ascii")
    elif type(value) is float:
        if not math.isfinite(value): raise CommitmentError("nonfinite frame")
        tag, payload = b"f", struct.pack(">d", value)
    elif type(value) is str: tag, payload = b"s", value.encode("utf-8")
    elif type(value) is bytes: tag, payload = b"y", value
    elif type(value) is list:
        tag, payload = b"l", b"".join(canonical_frame_v1(item) for item in value)
    elif type(value) is dict:
        if not all(type(key) is str for key in value): raise CommitmentError("frame key")
        keys = sorted(value, key=lambda key: key.encode("utf-8"))
        tag, payload = b"o", b"".join(
            canonical_frame_v1(key) + canonical_frame_v1(value[key]) for key in keys
        )
    else: raise CommitmentError("unsupported frame value")
    return tag + len(payload).to_bytes(8, "big") + payload

def _digest(domain: bytes, value: Any) -> str: return _sha(domain + canonical_frame_v1(value))

def _require_digest(value: Any) -> str:
    if type(value) is not str or not value.startswith("sha256:") or not _HEX.fullmatch(value[7:]): raise CommitmentError("digest")
    return value

def _strict_jsonl(path: Path) -> list[tuple[int, bytes, dict[str, Any]]]:
    raw = path.read_bytes()
    if (
        not raw.endswith(b"\n")
        or raw.endswith(b"\n\n")
        or b"\r" in raw
    ):
        raise CommitmentError("jsonl framing")
    rows=[]
    for number,line in enumerate(raw[:-1].split(b"\n"),1):
        if not line: raise CommitmentError("blank row")
        def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise CommitmentError("duplicate object key")
                result[key] = value
            return result
        try: row=json.loads(line.decode("utf-8"), object_pairs_hook=reject_duplicate)
        except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise CommitmentError("jsonl") from exc
        if type(row) is not dict: raise CommitmentError("row")
        rows.append((number,line,row))
    return rows

def _validate_algorithm_parameters(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "mode", "stages", "stage_a", "stage_b", "manifest_loader",
    }:
        raise CommitmentError("algorithm parameter schema")
    if (
        value["mode"] != "passages"
        or value["stages"] != ["a", "b"]
        or value["manifest_loader"] != "strict_file_backed_jsonl_v1"
    ):
        raise CommitmentError("algorithm parameter value")
    stage_a = value["stage_a"]
    if type(stage_a) is not dict or set(stage_a) != {
        "threshold_decimal", "num_perm", "shingle_size", "min_passage_words",
        "chunking", "tokenization",
    }:
        raise CommitmentError("stage A parameter schema")
    decimal = stage_a["threshold_decimal"]
    if type(decimal) is not str or not re.fullmatch(r"(?:0\.[0-9]+|1(?:\.0+)?)", decimal):
        raise CommitmentError("stage A threshold decimal")
    try:
        threshold = Decimal(decimal)
    except InvalidOperation as exc:
        raise CommitmentError("stage A threshold decimal") from exc
    if not (Decimal(0) < threshold <= Decimal(1)):
        raise CommitmentError("stage A threshold decimal")
    if (
        any(type(stage_a[name]) is not int or stage_a[name] <= 0 for name in (
            "num_perm", "shingle_size", "min_passage_words",
        ))
        or stage_a["chunking"] != "raw_paragraphs_never_coalesced_never_split"
        or stage_a["tokenization"] != "setec_frozen_unicode_word_lower_v1"
    ):
        raise CommitmentError("stage A parameter value")
    stage_b = value["stage_b"]
    if type(stage_b) is not dict or set(stage_b) != {
        "span_shingle_k", "min_span_words", "tokenization",
    }:
        raise CommitmentError("stage B parameter schema")
    if (
        any(type(stage_b[name]) is not int or stage_b[name] <= 0 for name in (
            "span_shingle_k", "min_span_words",
        ))
        or stage_b["tokenization"] != "setec_frozen_unicode_word_lower_v1"
    ):
        raise CommitmentError("stage B parameter value")
    return json.loads(json.dumps(value, allow_nan=False))


def _pin_source_root(root: Path) -> tuple[Path, tuple[Path, int, int, int]]:
    """Acquire the supplied root once without following a final symlink."""
    pinned_root = root.absolute()
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(pinned_root, flags)
        try:
            opened = os.fstat(descriptor)
            current = pinned_root.lstat()
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise CommitmentError("source root") from exc
    identity = (
        pinned_root, opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode),
    )
    if (
        not stat.S_ISDIR(opened.st_mode)
        or stat.S_ISLNK(current.st_mode)
        or (current.st_dev, current.st_ino, stat.S_IFMT(current.st_mode))
        != identity[1:]
    ):
        raise CommitmentError("source root")
    return pinned_root, identity


def _preflight_sources(
    manifest: Path, root: Path,
) -> list[
    tuple[
        int, bytes, dict[str, Any], Path,
        tuple[tuple[Path, int, int, int], ...],
    ]
]:
    """Validate every manifest row and file identity before opening any source."""
    rows = _strict_jsonl(manifest)
    records = [row for _line, _raw, row in rows]
    try:
        author_export._verify_record_population_metadata(records)
    except (TypeError, ValueError) as exc:
        raise CommitmentError("author corpus record metadata") from exc
    pinned_root, pinned_identity = _pin_source_root(root)
    seen_paths: set[str] = set()
    planned: list[
        tuple[
            int, bytes, dict[str, Any], Path,
            tuple[tuple[Path, int, int, int], ...],
        ]
    ] = []
    for line, raw, row in rows:
        text_path = row["text_path"]
        if (
            text_path in seen_paths
            or "\\" in text_path
            or Path(text_path).is_absolute()
            or any(part in {"", ".", ".."} for part in text_path.split("/"))
        ):
            raise CommitmentError("identity/path")
        target = pinned_root.joinpath(*text_path.split("/"))
        cursor = pinned_root
        identities: list[tuple[Path, int, int, int]] = [pinned_identity]
        final_link_count = 0
        try:
            root_metadata = cursor.lstat()
            if (
                stat.S_ISLNK(root_metadata.st_mode)
                or not stat.S_ISDIR(root_metadata.st_mode)
                or (root_metadata.st_dev, root_metadata.st_ino, stat.S_IFMT(root_metadata.st_mode))
                != pinned_identity[1:]
            ):
                raise CommitmentError("source root")
            parts = text_path.split("/")
            for index, part in enumerate(parts):
                cursor = cursor / part
                component = cursor.lstat()
                if (
                    stat.S_ISLNK(component.st_mode)
                    or (
                        index < len(parts) - 1
                        and not stat.S_ISDIR(component.st_mode)
                    )
                    or (
                        index == len(parts) - 1
                        and not stat.S_ISREG(component.st_mode)
                    )
                ):
                    raise CommitmentError("source file")
                identities.append((
                    cursor, component.st_dev, component.st_ino,
                    stat.S_IFMT(component.st_mode),
                ))
                final_link_count = component.st_nlink
        except (OSError, RuntimeError) as exc:
            raise CommitmentError("source file") from exc
        metadata = identities[-1]
        if (
            not stat.S_ISREG(metadata[3])
            or final_link_count != 1
        ):
            raise CommitmentError("source file")
        seen_paths.add(text_path)
        planned.append((line, raw, row, target, tuple(identities)))
    return planned


def load_strict_sources(
    manifest: Path, root: Path,
) -> list[tuple[int, bytes, dict[str, Any], bytes]]:
    """Return fully verified source bytes after a metadata-only population pass."""
    planned = _preflight_sources(manifest, root)
    loaded: list[tuple[int, bytes, dict[str, Any], bytes]] = []
    texts: dict[str, bytes] = {}
    for line, raw, row, target, identities in planned:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(target, flags)
            try:
                metadata = os.fstat(descriptor)
                expected_target = identities[-1]
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or (metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode))
                    != expected_target[1:]
                ):
                    raise CommitmentError("source file")
                for component, device, inode, mode in identities:
                    current = component.lstat()
                    if (
                        stat.S_ISLNK(current.st_mode)
                        or (current.st_dev, current.st_ino, stat.S_IFMT(current.st_mode))
                        != (device, inode, mode)
                    ):
                        raise CommitmentError("source path changed after preflight")
                chunks: list[bytes] = []
                while chunk := os.read(descriptor, 1024 * 1024):
                    chunks.append(chunk)
                payload = b"".join(chunks)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise CommitmentError("source file") from exc
        texts.setdefault(row["content_sha256"], payload)
        if texts[row["content_sha256"]] != payload:
            raise CommitmentError("content-addressed source collision")
        loaded.append((line, raw, row, payload))
    try:
        author_export._verify_record_population_texts(
            [item[2] for item in loaded], texts,
        )
    except (TypeError, ValueError) as exc:
        raise CommitmentError("author corpus record text") from exc
    return loaded


def build_commitment(*, manifest: Path, inventory_bytes: bytes, producer_revision: str, producer_blob_oid: str, producer_script_bytes: bytes, algorithm_parameters: dict[str, Any], source_kind_by_id: dict[str,str], root: Path) -> dict[str, Any]:
    if not _OID.fullmatch(producer_revision) or not _OID.fullmatch(producer_blob_oid): raise CommitmentError("producer identity")
    ranges,mappings,data=load_data(DATA_FILE)
    del ranges,mappings
    loaded = load_strict_sources(manifest, root)
    expected_kinds = {row["id"]: row["source_kind"] for _line, _raw, row, _payload in loaded}
    if source_kind_by_id and source_kind_by_id != expected_kinds:
        raise CommitmentError("source kind binding")
    sources=[]
    for line, raw, row, payload in loaded:
        sources.append({"manifest_line":line,"manifest_row_sha256":_sha(raw),"source_doc_id":row["id"],"text_path":row["text_path"],"source_entry_fingerprint":row["source_entry_fingerprint"],"content_sha256":_sha(payload),"register":row["register"],"source_kind":row["source_kind"]})
    parameters=_validate_algorithm_parameters(algorithm_parameters)
    parameters["tokenizer"]={"schema":"setec-frozen-unicode-word-lower/1","implementation_sha256":_sha(Path(__file__).with_name("passage_tokenizer_v1.py").read_bytes()),"data_sha256":_sha(DATA_FILE.read_bytes()),"data_commitment_sha256":data["data_commitment_sha256"]}
    core={"schema":SCHEMA,"producer_revision":producer_revision,"producer_script_git_blob_oid":"sha1:"+producer_blob_oid,"producer_script_sha256":_sha(producer_script_bytes),"algorithm_parameters":parameters,"inventory_semantics":"complete_canonical_passage_report_v1","inventory_sha256":_sha(inventory_bytes),"original_manifest_sha256":_sha(manifest.read_bytes()),"manifest_entry_count":len(sources),"sources":sources,"manifest_source_bijection":{"relation":"one_manifest_row_to_one_source","manifest_rows":len(sources),"admitted_sources":len(sources),"skipped_rows":0,"duplicate_source_ids":0,"duplicate_source_paths":0},"destructive_export_lineage":{"input_population":"original_source_manifest","passage_dedup_applied":False,"parent_passage_export_receipt_sha256":None}}
    core["algorithm_commitment_sha256"]=_digest(b"setec-passage-algorithm-commitment-v1\n", {key:core[key] for key in ("producer_revision","producer_script_git_blob_oid","producer_script_sha256","algorithm_parameters")})
    core["commitment_sha256"]=_digest(b"setec-passage-source-population-commitment-v1\n",core)
    return core

def build_receipt(commitment: dict[str,Any], *, inventory_bytes: bytes, commitment_bytes: bytes) -> dict[str,Any]:
    return {"schema":RECEIPT_SCHEMA,"result":"complete","inventory_sha256":_sha(inventory_bytes),"commitment_artifact_sha256":_sha(commitment_bytes),"commitment_sha256":commitment["commitment_sha256"],"producer_revision":commitment["producer_revision"],"producer_script_git_blob_oid":commitment["producer_script_git_blob_oid"],"producer_script_sha256":commitment["producer_script_sha256"],"algorithm_commitment_sha256":commitment["algorithm_commitment_sha256"]}
