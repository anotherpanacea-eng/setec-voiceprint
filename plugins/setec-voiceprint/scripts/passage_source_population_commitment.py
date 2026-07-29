"""Strict, synthetic-testable Spec-80 source-population commitment helpers.

This module deliberately has no corpus discovery, fallback identifiers, or
repair path.  A later reviewed near-duplicate producer invokes it only after
it has produced the complete frozen passage report.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import argparse
import subprocess
import struct
import sys
from pathlib import Path
from typing import Any

from passage_tokenizer_v1 import DATA_FILE, load_data

SCHEMA = "setec-passage-source-population-commitment/1"
RECEIPT_SCHEMA = "setec-passage-source-population-receipt/1"
RECORD_SCHEMA = "voicewright-author-corpus/1"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_OID = re.compile(r"[0-9a-f]{40}\Z")
_REGISTER = re.compile(r"[a-z][a-z0-9_-]{0,31}(?:\.[a-z][a-z0-9_-]{0,31})+\Z")
_KINDS = frozenset({"imessage_sent", "imessage_sent_atomic", "gmail_sent", "document_local", "interaction_context"})
_RECORD_KEYS = {"schema","id","persona","register","role","text_path","source_entry_fingerprint","source_group","conversation_id","date","unit_kind","unit_index","unit_count","corpus_role","use","consent_status","ai_status","source_kind","content_sha256","normalized_text_sha256"}

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
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"): raise CommitmentError("jsonl framing")
    rows=[]
    for number,line in enumerate(raw.splitlines(),1):
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

def build_commitment(*, manifest: Path, inventory_bytes: bytes, producer_revision: str, producer_blob_oid: str, producer_script_bytes: bytes, algorithm_parameters: dict[str, Any], source_kind_by_id: dict[str,str], root: Path) -> dict[str, Any]:
    if not _OID.fullmatch(producer_revision) or not _OID.fullmatch(producer_blob_oid): raise CommitmentError("producer identity")
    ranges,mappings,data=load_data(DATA_FILE)
    del ranges,mappings
    if root.is_symlink() or not root.is_dir(): raise CommitmentError("source root")
    pinned_root = root.resolve()
    sources=[]; seen_ids=set(); seen_paths=set()
    for line,raw,row in _strict_jsonl(manifest):
        if row.get("schema") != RECORD_SCHEMA or set(row) != _RECORD_KEYS: raise CommitmentError("record schema")
        ident=row.get("id"); fingerprint=row.get("source_entry_fingerprint"); text_path=row.get("text_path"); register=row.get("register"); kind=row.get("source_kind")
        if not (isinstance(ident,str) and re.fullmatch(r"sha256:[0-9a-f]{64}", ident) and isinstance(fingerprint,str) and re.fullmatch(r"src:hmac-sha256:[0-9a-f]{64}", fingerprint) and isinstance(text_path,str) and _REGISTER.fullmatch(register or "") and kind in _KINDS): raise CommitmentError("record fields")
        if ident in seen_ids or text_path in seen_paths or Path(text_path).is_absolute() or ".." in Path(text_path).parts: raise CommitmentError("identity/path")
        target=root / text_path
        if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(pinned_root): raise CommitmentError("source file")
        payload=target.read_bytes()
        try: payload.decode("utf-8")
        except UnicodeDecodeError as exc: raise CommitmentError("source utf8") from exc
        seen_ids.add(ident); seen_paths.add(text_path)
        sources.append({"manifest_line":line,"manifest_row_sha256":_sha(raw),"source_doc_id":ident,"text_path":text_path,"source_entry_fingerprint":fingerprint,"content_sha256":_sha(payload),"register":register,"source_kind":kind})
    parameters=dict(algorithm_parameters)
    parameters["tokenizer"]={"schema":"setec-frozen-unicode-word-lower/1","implementation_sha256":_sha(Path(__file__).with_name("passage_tokenizer_v1.py").read_bytes()),"data_sha256":_sha(DATA_FILE.read_bytes()),"data_commitment_sha256":data["data_commitment_sha256"]}
    core={"schema":SCHEMA,"producer_revision":producer_revision,"producer_script_git_blob_oid":"sha1:"+producer_blob_oid,"producer_script_sha256":_sha(producer_script_bytes),"algorithm_parameters":parameters,"inventory_semantics":"complete_canonical_passage_report_v1","inventory_sha256":_sha(inventory_bytes),"original_manifest_sha256":_sha(manifest.read_bytes()),"manifest_entry_count":len(sources),"sources":sources,"manifest_source_bijection":{"relation":"one_manifest_row_to_one_source","manifest_rows":len(sources),"admitted_sources":len(sources),"skipped_rows":0,"duplicate_source_ids":0,"duplicate_source_paths":0},"destructive_export_lineage":{"input_population":"original_source_manifest","passage_dedup_applied":False,"parent_passage_export_receipt_sha256":None}}
    core["algorithm_commitment_sha256"]=_digest(b"setec-passage-algorithm-commitment-v1\n", {key:core[key] for key in ("producer_revision","producer_script_git_blob_oid","producer_script_sha256","algorithm_parameters")})
    core["commitment_sha256"]=_digest(b"setec-passage-source-population-commitment-v1\n",core)
    return core

def build_receipt(commitment: dict[str,Any], *, inventory_bytes: bytes, commitment_bytes: bytes) -> dict[str,Any]:
    return {"schema":RECEIPT_SCHEMA,"result":"complete","inventory_sha256":_sha(inventory_bytes),"commitment_artifact_sha256":_sha(commitment_bytes),"commitment_sha256":commitment["commitment_sha256"],"producer_revision":commitment["producer_revision"],"producer_script_git_blob_oid":commitment["producer_script_git_blob_oid"],"producer_script_sha256":commitment["producer_script_sha256"],"algorithm_commitment_sha256":commitment["algorithm_commitment_sha256"]}

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="passage_source_population_commitment")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--producer-script", type=Path, required=True)
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--source-kinds", type=Path, required=True)
    parser.add_argument("--commitment-out", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        parameters = json.loads(args.parameters.read_text(encoding="utf-8"))
        kinds = json.loads(args.source_kinds.read_text(encoding="utf-8"))
        if type(parameters) is not dict or type(kinds) is not dict:
            raise CommitmentError("auxiliary schema")
        revision, blob, producer_bytes = committed_producer_identity(
            repository=args.repository, script=args.producer_script,
        )
        commitment = build_commitment(manifest=args.manifest, inventory_bytes=args.inventory.read_bytes(), producer_revision=revision, producer_blob_oid=blob, producer_script_bytes=producer_bytes, algorithm_parameters=parameters, source_kind_by_id=kinds, root=args.root)
        commitment_bytes = _canonical(commitment)
        receipt = build_receipt(commitment, inventory_bytes=args.inventory.read_bytes(), commitment_bytes=commitment_bytes)
        for path, payload in ((args.commitment_out, commitment_bytes), (args.receipt_out, _canonical(receipt))):
            if path.exists(): raise CommitmentError("output exists")
            path.write_bytes(payload)
    except (CommitmentError, OSError, UnicodeError, json.JSONDecodeError):
        return 3
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
