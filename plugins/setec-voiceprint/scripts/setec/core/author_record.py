"""Author-corpus record core: record schema constants, canonical hashing and population checks.

Moved verbatim from ``setec.surfaces.author_corpus_export`` (CB-5) so the passage
and document-adapter modules can verify an exported record population without
importing that surface. ``author_corpus_export`` re-exports every name here as
the same object. Stdlib only.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import unicodedata
from typing import Any


RECORD_SCHEMA = "voicewright-author-corpus/1"


REGISTER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}(?:\.[a-z][a-z0-9_-]{0,31})+$")


SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


SOURCE_VALUES = {
    "imessage_sent": "imessage_local",
    "imessage_sent_atomic": "imessage_local",
    "gmail_sent": "gmail_takeout_local",
    "document_local": None,
}


ALLOWED_AI_STATUS = {
    "pre_ai_human", "ai_generated", "ai_generated_from_outline",
    "ai_assisted", "ai_edited", "mixed", "unknown",
}


UNIT_KINDS = {
    "message_batch", "atomic_message", "turn", "email", "essay", "section",
    "chapter", "document",
}


BIDI_CONTROLS = {
    chr(cp) for cp in (
        0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
        0x2066, 0x2067, 0x2068, 0x2069,
    )
}


DOMAIN_RECORD = b"voicewright-author-corpus-record-v1\n"


DOMAIN_DOCUMENT_MAP = b"setec-author-document-map-v1\n"


RECORD_KEYS = {
    "schema", "id", "persona", "register", "role", "text_path",
    "source_entry_fingerprint", "source_group", "conversation_id", "date",
    "unit_kind", "unit_index", "unit_count",
    "corpus_role", "use", "consent_status", "ai_status", "source_kind",
    "content_sha256", "normalized_text_sha256",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _framed(domain: bytes, value: Any) -> bytes:
    payload = _canonical(value)
    return domain + len(payload).to_bytes(8, "big") + payload


def _digest(domain: bytes, value: Any) -> str:
    return "sha256:" + hashlib.sha256(_framed(domain, value)).hexdigest()


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


def _document_map_hash(rows: list[dict[str, Any]]) -> str:
    return _digest(DOMAIN_DOCUMENT_MAP, {
        "schema": "setec-author-document-map-hash/1",
        "rows": rows,
    })


def _record_id(record: dict[str, Any]) -> str:
    payload = {k: v for k, v in record.items() if k not in {"id", "text_path"}}
    return _digest(DOMAIN_RECORD, payload)


def _verify_record_population_metadata(
    records: list[dict[str, Any]],
) -> tuple[list[str], set[str]]:
    """Verify the authoritative closed record schema without opening source text."""
    if type(records) is not list or not records:
        raise ValueError("package records must be a non-empty list")
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
        if record["unit_kind"] not in UNIT_KINDS:
            raise ValueError("record unit_kind is invalid")
        if type(record["unit_index"]) is not int or record["unit_index"] < 0:
            raise ValueError("record unit_index must be a nonnegative exact integer")
        if type(record["unit_count"]) is not int or record["unit_count"] <= 0:
            raise ValueError("record unit_count must be a positive exact integer")
        if record["unit_index"] >= record["unit_count"]:
            raise ValueError("record unit_index must be below unit_count")
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
        ids.append(record["id"])
        fingerprints.append(record["source_entry_fingerprint"])
        expected_text_keys.add(record["content_sha256"])
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        raise ValueError("record ids must be sorted and unique")
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError("source-entry fingerprints must be unique")

    by_group: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_group.setdefault(record["source_group"], []).append(record)
    for group in by_group.values():
        identities = {
            (
                record["persona"], record["source_kind"], record["register"],
                record["conversation_id"], record["unit_count"],
            )
            for record in group
        }
        if len(identities) != 1:
            raise ValueError("source group identity is inconsistent")
        if group[0]["source_kind"] == "imessage_sent_atomic":
            if any(
                record["unit_kind"] != "atomic_message"
                or record["unit_index"] != 0
                or record["unit_count"] != 1
                for record in group
            ):
                raise ValueError("atomic iMessage unit semantics are invalid")
            continue
        if any(record["unit_kind"] == "atomic_message" for record in group):
            raise ValueError("atomic_message is reserved for atomic iMessage records")
        count = group[0]["unit_count"]
        if len(group) != count or {row["unit_index"] for row in group} != set(range(count)):
            raise ValueError("source group unit order is incomplete or ambiguous")
    return ids, expected_text_keys


def _verify_record_population_texts(
    records: list[dict[str, Any]], texts: dict[str, bytes],
) -> None:
    """Verify exact and normalized text bindings for metadata-verified rows."""
    if type(texts) is not dict or not texts:
        raise ValueError("package texts must be a non-empty object")
    expected_text_keys = {record["content_sha256"] for record in records}
    for record in records:
        text_bytes = texts.get(record["content_sha256"])
        if type(text_bytes) is not bytes or _sha(text_bytes) != record["content_sha256"]:
            raise ValueError("record exact text bytes failed content verification")
        try:
            normalized = _normalize_text(text_bytes.decode("utf-8")).encode("utf-8")
        except UnicodeError as exc:
            raise ValueError("record text is not strict UTF-8") from exc
        if _sha(normalized) != record["normalized_text_sha256"]:
            raise ValueError("record normalized text hash failed verification")
    if set(texts) != expected_text_keys:
        raise ValueError("text object contains missing or unreferenced content")
