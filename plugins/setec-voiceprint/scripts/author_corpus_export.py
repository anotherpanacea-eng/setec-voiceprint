#!/usr/bin/env python3
"""Export sent-only acquisition products as a private author-corpus package.

This is the normalized R1a producer for setec-voicewright spec 53 / issue #311.
It never returns prose through the SETEC envelope: stdout carries only a closed,
hash-bound receipt under ``results.producer_receipt``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import sys
import unicodedata
import uuid
import weakref
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402
from claim_license import ClaimLicense  # noqa: E402
from output_schema import build_error_output, build_output  # noqa: E402


TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "author_corpus_export"
SCRIPT_VERSION = "1.0"
SURFACE_VERSION = "1"

RECORD_SCHEMA = "voicewright-author-corpus/1"
RECEIPT_SCHEMA = "setec-author-corpus-export/1"
PACKAGE_HASH_SCHEMA = "setec-author-corpus-package-hash/1"

REGISTER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}(?:\.[a-z][a-z0-9_-]{0,31})+$")
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PRIVATE_LOCATOR_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

SOURCE_VALUES = {
    "imessage_sent": "imessage_local",
    "gmail_sent": "gmail_takeout_local",
}
ALLOWED_AI_STATUS = {
    "pre_ai_human", "ai_generated", "ai_generated_from_outline",
    "ai_assisted", "ai_edited", "mixed", "unknown",
}
ALLOWED_ERA = {"pre_chatgpt", "pre_ai_widespread", "post_ai_widespread", "undated"}
SOURCE_MANIFEST_KEYS = {
    "id", "path", "author", "persona", "register", "date_written",
    "ai_status", "language_status", "word_count", "use", "split",
    "privacy", "content_hash", "source", "corpus_role", "notes", "era",
    "consent_status", "acquired_via",
}
BIDI_CONTROLS = {
    chr(cp) for cp in (
        0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
        0x2066, 0x2067, 0x2068, 0x2069,
    )
}

DOMAIN_KEY_ID = b"setec-author-corpus-hmac-key-id-v1\x00"
DOMAIN_GROUP = b"setec-author-corpus-group-v1\x00"
DOMAIN_ENTRY = b"setec-author-corpus-source-entry-v1\x00"
DOMAIN_RECORD = b"voicewright-author-corpus-record-v1\n"
DOMAIN_PACKAGE = b"setec-author-corpus-package-v1\n"
DOMAIN_RECEIPT = b"setec-author-corpus-export-receipt-v1\n"
DOMAIN_SNAPSHOT = b"setec-author-corpus-source-snapshot-v1\n"
DOMAIN_CONFIG = b"setec-author-corpus-export-smoke-config-v1\n"

SMOKE_RECEIPT = ".author_corpus_export_live_smoke.json"
MAX_SMOKE_RECORDS = 20
SMOKE_MAX_AGE = dt.timedelta(hours=24)

RECORD_KEYS = {
    "schema", "id", "persona", "register", "role", "text_path",
    "source_entry_fingerprint", "source_group", "conversation_id", "date",
    "corpus_role", "use", "consent_status", "ai_status", "source_kind",
    "content_sha256", "normalized_text_sha256",
}
RECEIPT_KEYS = {
    "schema", "surface", "surface_version", "producer_revision",
    "source_snapshot_sha256", "hmac_key_id", "register_map",
    "allowed_ai_status", "entries", "record_ids", "package_hash", "counts",
    "record_atomic_degraded",
}
ENTRY_KEYS = {"source_entry_fingerprint", "source_group", "record_id"}
COUNT_KEYS = {"records", "by_register", "by_ai_status", "by_source_kind", "by_era"}
SMOKE_KEYS = {
    "schema", "config_hash", "producer_revision", "bounded_package_hash",
    "bounded_receipt_hash", "bounded_destination_name", "source_kinds",
    "registers", "source_register_pairs", "confirmed_at",
}
class _BuildEvidence:
    """Opaque identity registered by build_export; carries no authority itself."""

    __slots__ = ("__weakref__",)


_BUILD_EVIDENCE_REGISTRY: dict[
    int,
    tuple[
        weakref.ReferenceType[_BuildEvidence], str, str, bool,
        tuple[tuple[str, int], ...],
    ],
] = {}


def _register_build_evidence(
    *, config_hash: str, receipt_hash: str, record_atomic_degraded: bool,
    by_era: tuple[tuple[str, int], ...],
) -> _BuildEvidence:
    evidence = _BuildEvidence()
    identity = id(evidence)

    def discard(_reference: weakref.ReferenceType[_BuildEvidence]) -> None:
        _BUILD_EVIDENCE_REGISTRY.pop(identity, None)

    reference = weakref.ref(evidence, discard)
    _BUILD_EVIDENCE_REGISTRY[identity] = (
        reference, config_hash, receipt_hash, record_atomic_degraded, by_era,
    )
    return evidence


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _framed(domain: bytes, value: Any) -> bytes:
    payload = _canonical(value)
    return domain + len(payload).to_bytes(8, "big") + payload


def _digest(domain: bytes, value: Any) -> str:
    return "sha256:" + hashlib.sha256(_framed(domain, value)).hexdigest()


def _hmac(key: bytes, domain: bytes, value: Any, prefix: str) -> str:
    return prefix + hmac.new(key, _framed(domain, value), hashlib.sha256).hexdigest()


def _producer_revision() -> str:
    return hashlib.sha1(Path(__file__).read_bytes()).hexdigest()


def _require_string(name: str, value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{name} must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value:
        raise ValueError(f"{name} must be NFC-normalized")
    if any(ch in BIDI_CONTROLS or ord(ch) < 32 or 0x7F <= ord(ch) <= 0x9F for ch in value):
        raise ValueError(f"{name} contains a forbidden control character")
    return value


def canonical_register(value: Any) -> str:
    value = _require_string("register", value)
    if len(value) > 96 or not REGISTER_RE.fullmatch(value):
        raise ValueError(f"invalid canonical register {value!r}")
    return value


def _normalize_text(text: str) -> str:
    if "\x00" in text:
        raise ValueError("text contains NUL")
    for ch in text:
        code = ord(ch)
        if (code < 32 and ch not in "\t\n\r") or 0x7F <= code <= 0x9F:
            raise ValueError("text contains a forbidden non-whitespace control")
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _load_json_object(text: str, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"{label} contains a duplicate JSON key")
            out[key] = value
        return out

    try:
        value = json.loads(text, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _check_private(paths: Iterable[Path]) -> None:
    checked = [Path(path) for path in paths]
    if any(not ac.is_private_safe_path(path) for path in checked):
        raise PermissionError("private-path policy refused the requested operation")
    try:
        ac.check_output_privacy(checked, allow_public=False, tool=TOOL_NAME)
    except SystemExit as exc:
        raise PermissionError("private-path policy refused the requested operation") from exc


def _read_key(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("HMAC key must be a regular non-symlink file")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError("HMAC key must be owner-only (0600 or stricter)")
    key = path.read_bytes()
    if len(key) < 32:
        raise ValueError("HMAC key must contain at least 32 random bytes")
    return key


def _parse_assignment(raw: str, name: str) -> tuple[str, str]:
    left, sep, right = raw.partition("=")
    if not sep or not left or not right:
        raise ValueError(f"{name} must be KEY=VALUE")
    return left, right


def parse_sources(values: Iterable[str]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for raw in values:
        kind, path = _parse_assignment(raw, "--source-manifest")
        if kind not in SOURCE_VALUES:
            raise ValueError(f"unknown source kind {kind!r}")
        if kind in out:
            raise ValueError(f"duplicate source kind {kind!r}")
        out[kind] = Path(path).expanduser()
    if not out:
        raise ValueError("at least one --source-manifest is required")
    return out


def parse_register_map(values: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in values:
        key, register = _parse_assignment(raw, "--register-map")
        kind, sep, legacy = key.partition(":")
        if not sep or kind not in SOURCE_VALUES or not legacy:
            raise ValueError("register-map keys must be SOURCE_KIND:LEGACY")
        _require_string("legacy register", legacy)
        if key in out:
            raise ValueError(f"duplicate register map key {key!r}")
        out[key] = canonical_register(register)
    if not out:
        raise ValueError("at least one --register-map is required")
    return dict(sorted(out.items()))


def _safe_source_file(manifest: Path, rel: Any) -> Path:
    rel = _require_string("manifest path", rel)
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("source manifest text path must be relative and traversal-free")
    unresolved = manifest.parent / candidate
    if unresolved.is_symlink():
        raise ValueError("source text must not be a symlink")
    root = manifest.parent.resolve()
    path = unresolved.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("source text escapes its manifest directory") from exc
    if path.is_symlink() or not path.is_file():
        raise ValueError("source text must be a regular non-symlink file")
    return path


def _read_meta(text_path: Path) -> dict[str, Any]:
    meta_path = text_path.with_suffix(".meta.json")
    if meta_path.is_symlink() or not meta_path.is_file():
        raise ValueError("source text is missing a regular private metadata sidecar")
    return _load_json_object(
        meta_path.read_text(encoding="utf-8"), "private metadata sidecar",
    )


def _validate_source_entry(entry: dict[str, Any], *, kind: str, persona: str) -> None:
    unknown = set(entry) - SOURCE_MANIFEST_KEYS
    if unknown:
        raise ValueError("source manifest contains unknown keys")
    required = {
        "id", "path", "persona", "register", "ai_status", "use",
        "content_hash", "source", "corpus_role", "consent_status", "era",
    }
    if not required <= set(entry):
        raise ValueError("source manifest is missing required keys")
    for name in (
        "id", "path", "persona", "register", "ai_status", "content_hash",
        "source", "corpus_role", "consent_status", "era",
    ):
        _require_string(name, entry[name])
    if "date_written" in entry and entry["date_written"] is not None:
        _require_string("date_written", entry["date_written"])
    if type(entry["use"]) is not list or any(type(v) is not str for v in entry["use"]):
        raise ValueError("use must be a JSON string array")
    if entry["persona"] != persona:
        raise ValueError("source persona does not match requested persona")
    if entry["corpus_role"] != "identity_baseline" or entry["use"] != ["voice_profile"]:
        raise ValueError("source entry is not an identity-baseline voice profile")
    if entry["consent_status"] != "author_consent":
        raise ValueError("source entry lacks author_consent")
    if entry["source"] != SOURCE_VALUES[kind]:
        raise ValueError("source entry kind does not match its declared source manifest")
    if not SHA_RE.fullmatch(entry["content_hash"]):
        raise ValueError("source content_hash is malformed")
    if entry["era"] not in ALLOWED_ERA:
        raise ValueError("source era is not recognized")


def _source_locators(kind: str, entry: dict[str, Any], meta: dict[str, Any]) -> tuple[str, str, bool]:
    if kind == "imessage_sent":
        group = meta.get("author_corpus_group_locator")
        item = meta.get("author_corpus_entry_locator")
    else:
        group = meta.get("author_corpus_thread_locator")
        item = meta.get("author_corpus_entry_locator")
    degraded = not (
        isinstance(group, str) and PRIVATE_LOCATOR_RE.fullmatch(group)
        and isinstance(item, str) and PRIVATE_LOCATOR_RE.fullmatch(item)
    )
    if degraded:
        fallback = _digest(
            b"setec-author-corpus-record-atomic-fallback-v1\n",
            {"source_kind": kind, "source_id": entry["id"], "content_hash": entry["content_hash"]},
        )
        group = item = fallback
    return group, item, degraded


def _record_id(record: dict[str, Any]) -> str:
    payload = {k: v for k, v in record.items() if k not in {"id", "text_path"}}
    return _digest(DOMAIN_RECORD, payload)


def _source_snapshot_hash(rows: list[dict[str, Any]]) -> str:
    payload = {
        "schema": "setec-author-corpus-source-snapshot-hash/1",
        "entries": [{
            "source_kind": row["source_kind"],
            "source_manifest_sha256": row["source_manifest_sha256"],
            "source_id": row["source_id"],
            "content_sha256": row["content_sha256"],
            "private_group_locator": row["private_group_locator"],
            "private_entry_locator": row["private_entry_locator"],
        } for row in sorted(rows, key=lambda r: (r["source_kind"], r["source_id"]))],
    }
    return _digest(DOMAIN_SNAPSHOT, payload)


def _config_hash(receipt: dict[str, Any], persona: str) -> str:
    return _digest(DOMAIN_CONFIG, {
        "producer_revision": receipt["producer_revision"],
        "source_snapshot_sha256": receipt["source_snapshot_sha256"],
        "hmac_key_id": receipt["hmac_key_id"],
        "register_map": receipt["register_map"],
        "allowed_ai_status": receipt["allowed_ai_status"],
        "persona": persona,
    })


def build_export(
    *, sources: dict[str, Path], register_map: dict[str, str],
    allowed_ai_status: list[str], persona: str, hmac_key: bytes,
    max_records: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, bytes], dict[str, Any], str, _BuildEvidence]:
    """Validate private manifests and return records/text/receipt/config hash."""
    persona = _require_string("persona", persona)
    if type(sources) is not dict or not sources or any(
        type(kind) is not str or kind not in SOURCE_VALUES or not isinstance(path, Path)
        for kind, path in sources.items()
    ):
        raise ValueError("sources must be a non-empty source-kind to Path map")
    if type(register_map) is not dict or not register_map or any(
        type(key) is not str or type(value) is not str
        for key, value in register_map.items()
    ):
        raise ValueError("register_map must be a non-empty string map")
    for map_key, canonical in register_map.items():
        source_kind, separator, legacy = map_key.partition(":")
        if not separator or source_kind not in SOURCE_VALUES or not legacy:
            raise ValueError("register_map contains an invalid source key")
        _require_string("legacy register", legacy)
        canonical_register(canonical)
    if type(allowed_ai_status) is not list or any(
        type(value) is not str for value in allowed_ai_status
    ):
        raise ValueError("allowed AI statuses must be a string list")
    if not allowed_ai_status or allowed_ai_status != sorted(set(allowed_ai_status)):
        raise ValueError("allowed AI statuses must be sorted, unique, and non-empty")
    if any(v not in ALLOWED_AI_STATUS for v in allowed_ai_status):
        raise ValueError("unknown allowed AI status")
    if type(hmac_key) is not bytes or len(hmac_key) < 32:
        raise ValueError("HMAC key must contain at least 32 bytes")
    key_id = _sha(DOMAIN_KEY_ID + hmac_key)
    source_rows: list[dict[str, Any]] = []
    built: list[tuple[dict[str, Any], bytes, str]] = []
    degraded_any = False

    for kind, manifest in sorted(sources.items()):
        if manifest.name in {"contact_map.json", "recipient_map.json"}:
            raise ValueError("contact/recipient maps are forbidden inputs")
        _check_private([manifest])
        if manifest.is_symlink() or not manifest.is_file():
            raise ValueError("source manifest is not a regular non-symlink file")
        manifest_bytes = manifest.read_bytes()
        manifest_hash = _sha(manifest_bytes)
        for lineno, raw in enumerate(manifest_bytes.decode("utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            entry = _load_json_object(raw, f"source manifest line {lineno}")
            _validate_source_entry(entry, kind=kind, persona=persona)
            if entry["ai_status"] not in allowed_ai_status:
                raise ValueError("source AI status was not explicitly allowed")
            map_key = f"{kind}:{entry['register']}"
            if map_key not in register_map:
                raise ValueError("source register is missing an explicit mapping")
            text_path = _safe_source_file(manifest, entry["path"])
            text_bytes = text_path.read_bytes()
            text = text_bytes.decode("utf-8")
            exact_hash = _sha(text_bytes)
            if exact_hash != entry["content_hash"] or not SHA_RE.fullmatch(exact_hash):
                raise ValueError("source content hash mismatch")
            normalized_hash = _sha(_normalize_text(text).encode("utf-8"))
            meta = _read_meta(text_path)
            private_group, private_entry, degraded = _source_locators(kind, entry, meta)
            degraded_any |= degraded
            source_fp = _hmac(hmac_key, DOMAIN_ENTRY, {
                "source_kind": kind,
                "private_entry_locator": private_entry,
                "content_sha256": exact_hash,
            }, "src:hmac-sha256:")
            source_group = _hmac(hmac_key, DOMAIN_GROUP, {
                "source_kind": kind,
                "private_group_locator": private_group,
            }, "grp:hmac-sha256:")
            content_hex = exact_hash.removeprefix("sha256:")
            record: dict[str, Any] = {
                "schema": RECORD_SCHEMA,
                "id": "",
                "persona": persona,
                "register": register_map[map_key],
                "role": "author",
                "text_path": f"texts/{content_hex[:2]}/{content_hex[2:4]}/{content_hex}.txt",
                "source_entry_fingerprint": source_fp,
                "source_group": source_group,
                "conversation_id": None,
                "date": entry.get("date_written"),
                "corpus_role": "identity_baseline",
                "use": ["voice_profile"],
                "consent_status": "author_consent",
                "ai_status": entry["ai_status"],
                "source_kind": kind,
                "content_sha256": exact_hash,
                "normalized_text_sha256": normalized_hash,
            }
            if record["date"] is not None:
                try:
                    dt.date.fromisoformat(record["date"])
                except (TypeError, ValueError) as exc:
                    raise ValueError("date_written must be YYYY-MM-DD when present") from exc
            record["id"] = _record_id(record)
            source_rows.append({
                "source_kind": kind, "source_manifest_sha256": manifest_hash,
                "source_id": entry["id"], "content_sha256": exact_hash,
                "private_group_locator": private_group,
                "private_entry_locator": private_entry,
            })
            built.append((record, text_bytes, entry["era"]))

    built.sort(key=lambda item: item[0]["id"])
    if not built:
        raise ValueError("source manifests produced zero author records")
    if max_records is not None:
        if type(max_records) is not int or not 1 <= max_records <= MAX_SMOKE_RECORDS:
            raise ValueError(f"max_records must be an exact int in [1, {MAX_SMOKE_RECORDS}]")
        representative: list[tuple[dict[str, Any], bytes, str]] = []
        represented: set[tuple[str, str]] = set()
        for item in built:
            key = (item[0]["source_kind"], item[0]["register"])
            if key not in represented:
                represented.add(key)
                representative.append(item)
        if len(representative) > max_records:
            raise ValueError(
                "max_records is too small to cover every source-kind/register pair"
            )
        chosen_ids = {item[0]["id"] for item in representative}
        for item in built:
            if len(representative) >= max_records:
                break
            if item[0]["id"] not in chosen_ids:
                representative.append(item)
                chosen_ids.add(item[0]["id"])
        built = sorted(representative, key=lambda item: item[0]["id"])
    records = [item[0] for item in built]
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("duplicate normalized record id")
    if len({r["source_entry_fingerprint"] for r in records}) != len(records):
        raise ValueError("duplicate source-entry fingerprint")
    texts = {r["content_sha256"]: text_bytes for r, text_bytes, _ in built}

    package_hash = _package_hash(records)
    by_register = Counter(r["register"] for r in records)
    by_ai = Counter(r["ai_status"] for r in records)
    by_kind = Counter(r["source_kind"] for r in records)
    by_era = Counter(era for _, _, era in built)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "surface": TOOL_NAME,
        "surface_version": SURFACE_VERSION,
        "producer_revision": _producer_revision(),
        "source_snapshot_sha256": _source_snapshot_hash(source_rows),
        "hmac_key_id": key_id,
        "register_map": dict(sorted(register_map.items())),
        "allowed_ai_status": list(allowed_ai_status),
        "entries": [{
            "source_entry_fingerprint": r["source_entry_fingerprint"],
            "source_group": r["source_group"],
            "record_id": r["id"],
        } for r in sorted(records, key=lambda x: x["source_entry_fingerprint"])],
        "record_ids": sorted(r["id"] for r in records),
        "package_hash": package_hash,
        "counts": {
            "records": len(records),
            "by_register": dict(sorted(by_register.items())),
            "by_ai_status": dict(sorted(by_ai.items())),
            "by_source_kind": dict(sorted(by_kind.items())),
            "by_era": dict(sorted(by_era.items())),
        },
        "record_atomic_degraded": degraded_any,
    }
    # Compute in tests/loaders to pin the entire receipt, even though the hash is
    # intentionally not embedded in its own preimage.
    _digest(DOMAIN_RECEIPT, receipt)
    config_hash = _config_hash(receipt, persona)
    receipt_hash = _digest(DOMAIN_RECEIPT, receipt)
    evidence = _register_build_evidence(
        config_hash=config_hash,
        receipt_hash=receipt_hash,
        record_atomic_degraded=receipt["record_atomic_degraded"],
        by_era=tuple(sorted(receipt["counts"]["by_era"].items())),
    )
    return records, texts, receipt, config_hash, evidence


def _package_hash(records: list[dict[str, Any]]) -> str:
    return _digest(DOMAIN_PACKAGE, {
        "schema": PACKAGE_HASH_SCHEMA,
        "records": [{
            "record_id": record["id"],
            "content_sha256": record["content_sha256"],
            "normalized_text_sha256": record["normalized_text_sha256"],
        } for record in records],
    })


def _exact_counter(value: Any, label: str) -> dict[str, int]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    result: dict[str, int] = {}
    for key, count in value.items():
        _require_string(f"{label} key", key)
        if type(count) is not int or count < 1:
            raise ValueError(f"{label} counts must be positive exact integers")
        result[key] = count
    return result


def _verify_package(records: list[dict[str, Any]], texts: dict[str, bytes],
                    receipt: dict[str, Any], *, hmac_key: bytes | None = None,
                    config_hash: str | None = None,
                    producer_revision: str | None = None) -> str:
    if type(records) is not list or not records:
        raise ValueError("package records must be a non-empty list")
    if type(texts) is not dict or not texts:
        raise ValueError("package texts must be a non-empty object")
    ids: list[str] = []
    fingerprints: list[str] = []
    expected_text_keys: set[str] = set()
    for record in records:
        if type(record) is not dict or set(record) != RECORD_KEYS:
            raise ValueError("record does not match the closed record schema")
        if record["schema"] != RECORD_SCHEMA or record["role"] != "author":
            raise ValueError("record schema or role is invalid")
        for name in ("id", "persona", "source_entry_fingerprint", "source_group"):
            _require_string(f"record {name}", record[name])
        canonical_register(record["register"])
        if record["conversation_id"] is not None:
            raise ValueError("R1a conversation_id must be null")
        if record["date"] is not None:
            _require_string("record date", record["date"])
            try:
                dt.date.fromisoformat(record["date"])
            except ValueError as exc:
                raise ValueError("record date must be YYYY-MM-DD") from exc
        if record["corpus_role"] != "identity_baseline":
            raise ValueError("record corpus_role is invalid")
        if record["use"] != ["voice_profile"] or record["consent_status"] != "author_consent":
            raise ValueError("record authorization fields are invalid")
        if record["ai_status"] not in ALLOWED_AI_STATUS:
            raise ValueError("record ai_status is invalid")
        if record["source_kind"] not in SOURCE_VALUES:
            raise ValueError("record source_kind is invalid")
        if not SHA_RE.fullmatch(record["content_sha256"]):
            raise ValueError("record content hash is malformed")
        if not SHA_RE.fullmatch(record["normalized_text_sha256"]):
            raise ValueError("record normalized hash is malformed")
        if not re.fullmatch(r"src:hmac-sha256:[0-9a-f]{64}", record["source_entry_fingerprint"]):
            raise ValueError("record source fingerprint is malformed")
        if not re.fullmatch(r"grp:hmac-sha256:[0-9a-f]{64}", record["source_group"]):
            raise ValueError("record source group is malformed")
        content_hex = record["content_sha256"].removeprefix("sha256:")
        expected_path = f"texts/{content_hex[:2]}/{content_hex[2:4]}/{content_hex}.txt"
        if record["text_path"] != expected_path:
            raise ValueError("record text_path is not bound to content_sha256")
        if record["id"] != _record_id(record):
            raise ValueError("record id binding failed verification")
        text_bytes = texts.get(record["content_sha256"])
        if type(text_bytes) is not bytes or _sha(text_bytes) != record["content_sha256"]:
            raise ValueError("record exact text bytes failed content verification")
        try:
            normalized = _normalize_text(text_bytes.decode("utf-8")).encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("record text is not strict UTF-8") from exc
        if _sha(normalized) != record["normalized_text_sha256"]:
            raise ValueError("record normalized text hash failed verification")
        ids.append(record["id"])
        fingerprints.append(record["source_entry_fingerprint"])
        expected_text_keys.add(record["content_sha256"])
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        raise ValueError("record ids must be sorted and unique")
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError("source-entry fingerprints must be unique")
    if set(texts) != expected_text_keys:
        raise ValueError("text object contains missing or unreferenced content")

    if type(receipt) is not dict or set(receipt) != RECEIPT_KEYS:
        raise ValueError("producer receipt does not match the closed schema")
    if receipt["schema"] != RECEIPT_SCHEMA or receipt["surface"] != TOOL_NAME:
        raise ValueError("producer receipt schema or surface is invalid")
    if receipt["surface_version"] != SURFACE_VERSION:
        raise ValueError("producer receipt surface version is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", receipt["producer_revision"]):
        raise ValueError("producer revision is malformed")
    if producer_revision is not None and receipt["producer_revision"] != producer_revision:
        raise ValueError("producer revision does not match the running exporter")
    for name in ("source_snapshot_sha256", "hmac_key_id", "package_hash"):
        if type(receipt[name]) is not str or not SHA_RE.fullmatch(receipt[name]):
            raise ValueError(f"receipt {name} is malformed")
    if type(receipt["register_map"]) is not dict or not receipt["register_map"]:
        raise ValueError("receipt register_map must be a non-empty object")
    for key, value in receipt["register_map"].items():
        _require_string("register_map key", key)
        kind, sep, legacy = key.partition(":")
        if not sep or kind not in SOURCE_VALUES or not legacy:
            raise ValueError("receipt register_map key is invalid")
        canonical_register(value)
    allowed = receipt["allowed_ai_status"]
    if type(allowed) is not list or allowed != sorted(set(allowed)):
        raise ValueError("receipt allowed_ai_status must be sorted and unique")
    if any(type(value) is not str or value not in ALLOWED_AI_STATUS for value in allowed):
        raise ValueError("receipt allowed_ai_status is invalid")
    if receipt["record_ids"] != ids:
        raise ValueError("receipt record_ids do not bind the package records")
    expected_entries = [{
        "source_entry_fingerprint": record["source_entry_fingerprint"],
        "source_group": record["source_group"],
        "record_id": record["id"],
    } for record in sorted(records, key=lambda row: row["source_entry_fingerprint"])]
    if type(receipt["entries"]) is not list or any(
        type(entry) is not dict or set(entry) != ENTRY_KEYS for entry in receipt["entries"]
    ) or receipt["entries"] != expected_entries:
        raise ValueError("receipt entries do not exactly bind the package records")
    expected_package_hash = _package_hash(records)
    if receipt["package_hash"] != expected_package_hash:
        raise ValueError("receipt package_hash failed verification")
    if type(receipt["counts"]) is not dict or set(receipt["counts"]) != COUNT_KEYS:
        raise ValueError("receipt counts do not match the closed schema")
    counts = receipt["counts"]
    if type(counts["records"]) is not int or counts["records"] != len(records):
        raise ValueError("receipt record count failed verification")
    expected_counters = {
        "by_register": Counter(record["register"] for record in records),
        "by_ai_status": Counter(record["ai_status"] for record in records),
        "by_source_kind": Counter(record["source_kind"] for record in records),
    }
    for name, expected in expected_counters.items():
        if _exact_counter(counts[name], name) != dict(sorted(expected.items())):
            raise ValueError(f"receipt {name} failed verification")
    by_era = _exact_counter(counts["by_era"], "by_era")
    if set(by_era) - ALLOWED_ERA or sum(by_era.values()) != len(records):
        raise ValueError("receipt by_era failed verification")
    if type(receipt["record_atomic_degraded"]) is not bool:
        raise ValueError("record_atomic_degraded must be a boolean")
    personas = {record["persona"] for record in records}
    if len(personas) != 1:
        raise ValueError("package records must have exactly one persona")
    if hmac_key is not None and receipt["hmac_key_id"] != _sha(DOMAIN_KEY_ID + hmac_key):
        raise ValueError("receipt hmac_key_id does not match the supplied key")
    if config_hash is not None and config_hash != _config_hash(receipt, next(iter(personas))):
        raise ValueError("receipt configuration seal failed verification")
    return _digest(DOMAIN_RECEIPT, receipt)


def publish_package(destination: Path, records: list[dict[str, Any]],
                    texts: dict[str, bytes], receipt: dict[str, Any],
                    *, hmac_key: bytes, evidence: _BuildEvidence) -> None:
    registered = _BUILD_EVIDENCE_REGISTRY.get(id(evidence))
    if (
        type(evidence) is not _BuildEvidence or registered is None
        or registered[0]() is not evidence
    ):
        raise ValueError("publication requires immutable evidence from build_export")
    _, config_hash, receipt_hash, expected_degraded, expected_by_era = registered
    if receipt.get("record_atomic_degraded") != expected_degraded:
        raise ValueError("degraded posture changed after the build phase")
    counts = receipt.get("counts")
    by_era = counts.get("by_era") if type(counts) is dict else None
    if type(by_era) is not dict or tuple(sorted(by_era.items())) != expected_by_era:
        raise ValueError("producer-only era counts changed after the build phase")
    verify_args = {
        "hmac_key": hmac_key, "config_hash": config_hash,
        "producer_revision": _producer_revision(),
    }
    if _verify_package(records, texts, receipt, **verify_args) != receipt_hash:
        raise ValueError("producer receipt changed after the build phase")
    _check_private([destination])
    destination = destination.expanduser()
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination already exists; refusing overwrite")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination.parent, 0o700)
    staging = destination.parent / f".{destination.name}.staging-{uuid.uuid4().hex}"
    try:
        staging.mkdir(mode=0o700)
        for index, record in enumerate(records, 1):
            out = staging / record["text_path"]
            out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            current = out.parent
            while current != staging:
                os.chmod(current, 0o700)
                current = current.parent
            if not out.exists():
                out.write_bytes(texts[record["content_sha256"]])
                os.chmod(out, 0o600)
            if index % 500 == 0:
                sys.stderr.write(f"[{TOOL_NAME}] staged {index}/{len(records)} records\n")
        records_path = staging / "records.jsonl"
        records_path.write_text(
            "".join(json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
                    for r in records),
            encoding="utf-8",
        )
        os.chmod(records_path, 0o600)
        receipt_path = staging / "producer_receipt.json"
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(receipt_path, 0o600)
        staged_records = [
            _load_json_object(line, "staged record")
            for line in records_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        staged_receipt = _load_json_object(
            receipt_path.read_text(encoding="utf-8"), "staged producer receipt",
        )
        staged_texts = {
            record["content_sha256"]: (staging / record["text_path"]).read_bytes()
            for record in staged_records
        }
        if _verify_package(staged_records, staged_texts, staged_receipt, **verify_args) != receipt_hash:
            raise ValueError("staged producer receipt changed after the build phase")
        os.replace(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _smoke_path(destination: Path) -> Path:
    return destination.expanduser().parent / SMOKE_RECEIPT


def _source_register_pairs(records: list[dict[str, Any]]) -> list[str]:
    return sorted({
        f"{record['source_kind']}={record['register']}" for record in records
    })


def _write_smoke_receipt(destination: Path, config_hash: str,
                         receipt: dict[str, Any], records: list[dict[str, Any]]) -> None:
    path = _smoke_path(destination)
    if path.is_symlink():
        raise PermissionError("live-smoke receipt path must not be a symlink")
    payload = {
        "schema": "setec-author-corpus-export-live-smoke/1",
        "config_hash": config_hash,
        "producer_revision": receipt["producer_revision"],
        "bounded_package_hash": receipt["package_hash"],
        "bounded_receipt_hash": _digest(DOMAIN_RECEIPT, receipt),
        "bounded_destination_name": destination.name,
        "source_kinds": sorted(receipt["counts"]["by_source_kind"]),
        "registers": sorted(receipt["counts"]["by_register"]),
        "source_register_pairs": _source_register_pairs(records),
        "confirmed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    temporary = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_published_package(
    directory: Path,
) -> tuple[list[dict[str, Any]], dict[str, bytes], dict[str, Any]]:
    if directory.is_symlink() or not directory.is_dir():
        raise PermissionError("bounded smoke package is missing or not a regular directory")
    records_path = directory / "records.jsonl"
    receipt_path = directory / "producer_receipt.json"
    if any(path.is_symlink() or not path.is_file() for path in (records_path, receipt_path)):
        raise PermissionError("bounded smoke package metadata is missing or unsafe")
    records = [
        _load_json_object(line, "bounded smoke record")
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    bounded_receipt = _load_json_object(
        receipt_path.read_text(encoding="utf-8"), "bounded smoke producer receipt",
    )
    texts: dict[str, bytes] = {}
    for record in records:
        if type(record) is not dict or type(record.get("text_path")) is not str:
            raise PermissionError("bounded smoke record text path is malformed")
        relative = Path(record["text_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise PermissionError("bounded smoke record text path is unsafe")
        text_path = directory / relative
        if text_path.is_symlink() or not text_path.is_file():
            raise PermissionError("bounded smoke package text is missing or unsafe")
        texts[record.get("content_sha256", "")] = text_path.read_bytes()
    return records, texts, bounded_receipt


def _require_smoke_receipt(destination: Path, config_hash: str,
                           receipt: dict[str, Any], records: list[dict[str, Any]],
                           hmac_key: bytes) -> None:
    path = _smoke_path(destination)
    if path.is_symlink() or not path.is_file():
        raise PermissionError("full export requires a prior bounded live-smoke receipt")
    data = _load_json_object(path.read_text(encoding="utf-8"), "live-smoke receipt")
    if set(data) != SMOKE_KEYS or data["schema"] != "setec-author-corpus-export-live-smoke/1":
        raise PermissionError("live-smoke receipt is malformed or does not match this export")
    expected = {
        "config_hash": config_hash,
        "producer_revision": receipt["producer_revision"],
        "source_kinds": sorted(receipt["counts"]["by_source_kind"]),
        "registers": sorted(receipt["counts"]["by_register"]),
        "source_register_pairs": _source_register_pairs(records),
    }
    if any(data.get(key) != value for key, value in expected.items()):
        raise PermissionError("live-smoke receipt is malformed or does not match this export")
    if not SHA_RE.fullmatch(data.get("bounded_package_hash", "")):
        raise PermissionError("live-smoke receipt bounded package hash is malformed")
    if not SHA_RE.fullmatch(data.get("bounded_receipt_hash", "")):
        raise PermissionError("live-smoke receipt bounded receipt hash is malformed")
    bounded_name = data.get("bounded_destination_name")
    if (
        type(bounded_name) is not str or not bounded_name
        or Path(bounded_name).name != bounded_name or bounded_name in {".", ".."}
        or bounded_name == destination.name
    ):
        raise PermissionError("full export must use a distinct destination from the bounded smoke")
    try:
        _require_string("bounded destination name", bounded_name)
    except ValueError as exc:
        raise PermissionError("bounded smoke destination name is malformed") from exc
    confirmed = data.get("confirmed_at")
    if type(confirmed) is not str:
        raise PermissionError("live-smoke receipt confirmation time is malformed")
    try:
        confirmed_at = dt.datetime.fromisoformat(confirmed)
    except ValueError as exc:
        raise PermissionError("live-smoke receipt confirmation time is malformed") from exc
    now = dt.datetime.now(dt.timezone.utc)
    if (
        confirmed_at.tzinfo != dt.timezone.utc
        or confirmed != confirmed_at.isoformat(timespec="seconds")
        or not dt.timedelta(0) <= now - confirmed_at <= SMOKE_MAX_AGE
    ):
        raise PermissionError("live-smoke receipt is stale or has a non-UTC timestamp")
    bounded_records, bounded_texts, bounded_receipt = _load_published_package(
        destination.parent / bounded_name,
    )
    bounded_receipt_hash = _verify_package(
        bounded_records, bounded_texts, bounded_receipt,
        hmac_key=hmac_key, config_hash=config_hash,
        producer_revision=receipt["producer_revision"],
    )
    if bounded_receipt["package_hash"] != data["bounded_package_hash"]:
        raise PermissionError("bounded smoke package hash does not match its confirmation")
    if bounded_receipt_hash != data["bounded_receipt_hash"]:
        raise PermissionError("bounded smoke receipt hash does not match its confirmation")


def _claim_license(receipt: dict[str, Any]) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Confirms that SETEC locally exported a private author-corpus package "
            "whose records and delivery receipt passed the named mechanical checks."
        ),
        does_not_license=(
            "Does not verify authorship or consent, license safe model-weight export, "
            "or support an AI/human, quality, or provenance verdict."
        ),
        comparison_set={"records": receipt["counts"]["records"]},
        register_match=sorted(receipt["counts"]["by_register"]),
        additional_caveats=[
            "The receipt is no-prose metadata; inspect the private package locally.",
            "record_atomic_degraded packages are train-only and non-comparative.",
        ],
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=TOOL_NAME)
    p.add_argument("--source-manifest", action="append", default=[], metavar="KIND=PATH")
    p.add_argument("--register-map", action="append", default=[], metavar="KIND:LEGACY=CANONICAL")
    p.add_argument("--allowed-ai-status", action="append", default=[])
    p.add_argument("--persona", required=True)
    p.add_argument("--hmac-key", required=True, type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--max-records", type=int)
    p.add_argument("--live-smoke-confirmed", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    return p


def run(args: argparse.Namespace) -> dict[str, Any]:
    _check_private([args.output_dir])
    sources = parse_sources(args.source_manifest)
    register_map = parse_register_map(args.register_map)
    allowed = sorted(set(args.allowed_ai_status))
    if allowed != args.allowed_ai_status:
        raise ValueError("--allowed-ai-status values must already be sorted and unique")
    key = _read_key(args.hmac_key.expanduser())
    records, texts, receipt, config_hash, evidence = build_export(
        sources=sources, register_map=register_map, allowed_ai_status=allowed,
        persona=args.persona, hmac_key=key, max_records=args.max_records,
    )
    warnings: list[str] = []
    if receipt["record_atomic_degraded"]:
        warnings.append(
            "record_atomic_degraded: stable grouping was unavailable; consumers "
            "must restrict this package to train-only, non-comparative use."
        )
    if args.dry_run:
        warnings.append("dry-run: package was validated but not written")
    else:
        if args.max_records is not None:
            if not args.live_smoke_confirmed or not sys.stdin.isatty():
                raise PermissionError(
                    "bounded live smoke requires --live-smoke-confirmed in an interactive TTY"
                )
        else:
            if args.live_smoke_confirmed:
                raise ValueError("--live-smoke-confirmed is valid only with --max-records")
            _require_smoke_receipt(
                args.output_dir, config_hash, receipt, records, key,
            )
        publish_package(
            args.output_dir, records, texts, receipt,
            hmac_key=key, evidence=evidence,
        )
        if args.max_records is not None:
            _write_smoke_receipt(args.output_dir, config_hash, receipt, records)
    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=None, target_words=0, baseline=None,
        results={"producer_receipt": receipt},
        claim_license=_claim_license(receipt), warnings=warnings, ai_status=None,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        envelope = run(args)
    except (ValueError, TypeError, AttributeError, OSError, UnicodeError) as exc:
        if args.json:
            sys.stderr.write(f"{TOOL_NAME}: private input or policy validation failed\n")
            category = "policy_refused" if isinstance(exc, PermissionError) else "bad_input"
            public_reason = "author corpus export refused; inspect local stderr for details"
            print(json.dumps(build_error_output(
                task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                reason=public_reason, reason_category=category,
                warnings=["private input or policy validation failed"],
            ), indent=2))
            return 0
        sys.stderr.write(f"{TOOL_NAME}: private input or policy validation failed\n")
        return 2
    if args.json:
        print(json.dumps(envelope, indent=2))
    else:
        receipt = envelope["results"]["producer_receipt"]
        print(f"records: {receipt['counts']['records']}")
        print(f"package hash: {receipt['package_hash']}")
        print(f"record atomic degraded: {str(receipt['record_atomic_degraded']).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
