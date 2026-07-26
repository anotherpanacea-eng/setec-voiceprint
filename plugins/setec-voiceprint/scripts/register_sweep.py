#!/usr/bin/env python3
"""Aggregate register-composition hygiene inventory (Spec 73 / H2).

This module runs the landed H1 register-family classifier over an explicitly
scoped private manifest slice and emits a deterministic aggregate count
inventory. The inventory answers one hygiene question only: **is this corpus
obviously register-mixed enough to warrant a human check?** It does not answer
that question. It emits no score, threshold, band, flag, or verdict.

The H1 classifier is a confounded heuristic proxy: its family labels vary with
topic, project, date, and document length. Nothing here is a calibrated or
reportable register distribution, classifier accuracy evidence, source or
provenance analysis, or authorization to change, register, activate, train on,
or publish a corpus.

The manifest fields ``source``, ``source_id``, and ``source_family`` are outside
this contract. They are never read, normalized, hashed, inferred from, grouped
by, checkpointed, or emitted.

This increment supplies the canonical-encoding layer and the H1 binding layer:
every framed digest domain in the spec, the strict H1 receipt read, and the
receipt-bound classifier load plus its closed public-result validation. The
manifest projection, checkpoint codec, runner, and report land in later
increments against these exact encoders.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import struct
import unicodedata
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Fixed identities
# --------------------------------------------------------------------------

TOOL = "register_sweep"
VERSION = "2.0.0"
TAXONOMY = "register_families/v2"
REPORT_SCHEMA_VERSION = "setec-register-sweep-report/2"
CHECKPOINT_SCHEMA_VERSION = "setec-register-sweep-checkpoint/2"
CHECKPOINT_KIND = "register"
PRIVACY_POLICY = "owner_private_v1"
IMMUTABLE_SHARD_CONTRACT_VERSION = 1

#: Raw SHA-256 of the immutable H1 closeout receipt bytes. Pinned from the
#: landed receipt in merge commit c6d7cbc72da2a7429fbe986c5cd7df38aad69da3
#: (PR #357). H2 trusts the already-committed receipt bytes and never queries
#: GitHub; ``tools/check_register_sweep_h1_gate.py`` is the CI-side gate that
#: validated the Actions attempt before the receipt was committed.
H1_RECEIPT_SHA256 = "626e32652d476ac88d7d0caf3c78de17dd93c0c81f175405502b83f563922839"

H1_RECEIPT_SCHEMA_VERSION = "setec-h1-landing-receipt/2"
H1_LANDED_COMMIT = "7ffabd343066585de2a80c22b4aeba25d27d5450"
H1_SPEC_PATH = "specs/37-register-classifier-repair.md"
H1_SPEC_SHA256 = "7a2eb4c6c97662415bfbe707529947d93b83635a698404d1c591aafc2da056c1"
H1_REFUSAL_SPEC_PATH = "specs/76-register-classifier-refusal-reasons.md"
H1_REFUSAL_SPEC_SHA256 = (
    "5be5f74d74a8f9243d1cbeef4e24ed49ef1a14c932867ecb80cafcabfc734722"
)
H1_BASE_CLASSIFIER_SHA256 = (
    "740556a87ab9fc08b0de743198ea67bd40038aa20223553500133c90320b163d"
)
#: The final landed H1 classifier raw digest and the two public identities it
#: must reproduce. These are the same constants the CI-side gate pins in
#: ``tools/check_register_sweep_h1_gate.py``; the committed receipt is the
#: single H1 identity for both sides, so H2 refuses any receipt whose fields
#: disagree with them rather than trusting a receipt that agrees only with
#: itself.
H1_FINAL_CLASSIFIER_SHA256 = (
    "808da9eb369fd3aad725d9e6a799a6151b2f751b0f8f2ca8332dc037fbaaf2d8"
)
H1_MAPPING_SHA256 = (
    "8866d6033ccb0254d7ff474a6daa7bc26fc0e887e294b283e58528dc5e9814ef"
)
H1_REFUSAL_CONTRACT_SHA256 = (
    "f2255796634c1e1f2269029cc25afede25f4c033576b5dfba31f160c975a40c5"
)
H1_WORKFLOW_PATH = ".github/workflows/tests.yml"
H1_WORKFLOW_SHA256_ALLOWLIST = (
    "1003c42d078616a3188dc876588289a4f54e2e0ed67049c32eb9df367cb6ecfd",
    "2c8f8e9621039a051d9c23ae093b38a8b8320a14f6017ee8345cdb5f304ccf50",
)
H1_REQUIRED_JOBS = (
    "pytest",
    "macos-descriptor-confinement",
    "windows-descriptor-backend",
    "windows-owner-corrections",
    "windows-shingle-dedup",
    "windows-nonprose-sweep",
    "windows-private-writer-guards",
)

RECEIPT_FILENAME = "register-classifier-h1-receipt.json"
CLASSIFIER_FILENAME = "register_classifier.py"

# --------------------------------------------------------------------------
# Frozen framed domains. Every domain is ASCII and includes its terminal LF as
# its final byte. Domain reuse with a different payload schema is forbidden.
# --------------------------------------------------------------------------

DOMAIN_MAPPING = b"setec-register-family-mapping-v2\n"
DOMAIN_REFUSAL_CONTRACT = b"setec-register-classifier-refusal-contract-v1\n"
DOMAIN_SCOPE = b"setec-register-sweep-scope-v2\n"
DOMAIN_PROJECTED_ROW = b"setec-register-sweep-projected-row-v1\n"
DOMAIN_PROJECTED_MANIFEST = b"setec-register-sweep-projected-manifest-v1\n"
DOMAIN_SCOPED_ROWS = b"setec-register-sweep-scoped-rows-v1\n"
DOMAIN_TARGET_PATH = b"setec-register-sweep-target-path-v2\n"
DOMAIN_FILE_FINGERPRINT = b"setec-register-sweep-file-fingerprint-v2\n"
DOMAIN_DOCUMENT_PLAN = b"setec-register-sweep-document-plan-v2\n"
DOMAIN_CHECKPOINT_BINDING = b"setec-register-sweep-checkpoint-binding-v2\n"
DOMAIN_AGGREGATE_DELTA = b"setec-register-sweep-aggregate-delta-v1\n"
DOMAIN_SHARD = b"setec-register-sweep-shard-v2\n"

FROZEN_DOMAINS = (
    DOMAIN_MAPPING,
    DOMAIN_REFUSAL_CONTRACT,
    DOMAIN_SCOPE,
    DOMAIN_PROJECTED_ROW,
    DOMAIN_PROJECTED_MANIFEST,
    DOMAIN_SCOPED_ROWS,
    DOMAIN_TARGET_PATH,
    DOMAIN_FILE_FINGERPRINT,
    DOMAIN_DOCUMENT_PLAN,
    DOMAIN_CHECKPOINT_BINDING,
    DOMAIN_AGGREGATE_DELTA,
    DOMAIN_SHARD,
)

# --------------------------------------------------------------------------
# Hard ceilings
# --------------------------------------------------------------------------

MAX_MANIFEST_BYTES = 134_217_728
MAX_CLASSIFIER_SOURCE_BYTES = 1_048_576
MAX_H1_RECEIPT_BYTES = 65_536
MAX_DOCUMENT_BYTES = 16_777_216
MAX_SCOPED_DOCUMENTS = 100_000
MAX_SCOPED_BYTES = 8_589_934_592
MAX_FINAL_SHARDS = 400
MAX_RESERVED_TEMPORARY_NAMES = 16
MAX_SHARD_BYTES = 4_194_304
MAX_CHECKPOINT_CUMULATIVE_BYTES = 1_677_721_600
SHARD_ROWS = 250

MIN_WORDS_FLOOR = 1
MIN_WORDS_CEILING = 1_000_000
MAX_PERSONA_BYTES = 128
MAX_PATH_BYTES = 4_096
MAX_USE_MEMBERS = 32
MAX_WARNING_BYTES = 4_096
MAX_SECONDARY = 8
INT64_MAX = 2**63 - 1

#: The exact closed report ``limits`` object.
LIMITS: dict[str, int] = {
    "checkpoint_cumulative_bytes": MAX_CHECKPOINT_CUMULATIVE_BYTES,
    "classifier_source_bytes": MAX_CLASSIFIER_SOURCE_BYTES,
    "document_bytes": MAX_DOCUMENT_BYTES,
    "final_shards": MAX_FINAL_SHARDS,
    "h1_receipt_bytes": MAX_H1_RECEIPT_BYTES,
    "manifest_bytes": MAX_MANIFEST_BYTES,
    "reserved_temporary_names": MAX_RESERVED_TEMPORARY_NAMES,
    "scoped_bytes": MAX_SCOPED_BYTES,
    "scoped_documents": MAX_SCOPED_DOCUMENTS,
    "shard_bytes": MAX_SHARD_BYTES,
    "shard_rows": SHARD_ROWS,
}

#: Match-inventory domain. These are count buckets, not accuracy labels.
MATCH_DOMAIN = ("same", "different", "unresolved")

BIDI_CONTROLS = frozenset(
    "؜‎‏‪‫‬‭‮"
    "⁦⁧⁨⁩"
)

ZERO_WORD_EVIDENCE_KEYS = ("n_words", "n_chars")
FULL_EVIDENCE_KEYS = (
    "n_words",
    "n_chars",
    "n_sentences",
    "n_paragraphs",
    "mean_paragraph_words",
    "heading_density_per_1k",
    "first_person_per_1k",
    "second_person_per_1k",
    "dialogue_ratio",
    "question_per_1k",
    "exclamation_per_1k",
    "inline_citation_per_1k",
    "statutory_per_1k",
    "formal_address_per_1k",
    "shall_pursuant_per_1k",
    "attributed_quote_per_1k",
    "imperative_open_per_1k",
    "past_tense_narrative_per_1k",
    "academic_voice_per_1k",
)
EVIDENCE_INTEGER_KEYS = frozenset(
    {"n_words", "n_chars", "n_sentences", "n_paragraphs"}
)

CLASSIFICATION_KEYS = frozenset(
    {
        "primary",
        "confidence",
        "secondary",
        "scores",
        "evidence",
        "warning",
        "taxonomy",
        "refusal_reason",
    }
)

H1_PUBLIC_SYMBOLS = (
    "REGISTER_TAXONOMY",
    "REGISTER_FAMILIES",
    "KNOWN_REGISTERS",
    "REGISTER_REFUSAL_REASONS",
    "CANONICAL_REGISTER_TO_FAMILY",
    "LEGACY_REGISTER_TO_FAMILY",
    "resolve_family",
    "classify_register",
)

H1_REFUSAL_REASONS = ("short_text", "all_weak", "exact_top_tie")

#: The frozen ``REGISTER_FAMILIES`` tuple of the receipt-pinned H1 classifier.
#: The committed receipt byte-pins ``register_classifier.py``, so this module
#: may state the identity as a constant instead of deriving the value domains
#: from whatever namespace happens to be bound. ``H1Binding`` refuses any
#: namespace whose tuple differs, which is what keeps the constant honest.
H1_REGISTER_FAMILIES = (
    "formal_legal_policy",
    "formal_first_person",
    "academic",
    "journalism",
    "narrative_fiction",
    "first_person_essay",
    "promotional",
    "short_social",
    "unknown",
)
#: ``F`` in the spec's freeze: the scored families. ``unknown`` is a refusal
#: sentinel and is never a classified family.
H1_CLASSIFIED_FAMILIES = tuple(
    name for name in H1_REGISTER_FAMILIES if name != "unknown"
)
#: ``D`` in the spec's freeze: ``F + ("unknown",) == REGISTER_FAMILIES``.
H1_DECLARED_FAMILIES = H1_CLASSIFIED_FAMILIES + ("unknown",)

#: The exact per-shard ``counts`` key set.
SHARD_COUNT_KEYS = frozenset(
    {
        "scoped_documents",
        "scoped_bytes",
        "scoped_words",
        "resolved_declared_documents",
        "resolved_declared_words",
        "unresolved_declared_documents",
        "unresolved_declared_words",
        "classified_documents",
        "classified_words",
        "refused_documents",
        "refused_words",
    }
)


# --------------------------------------------------------------------------
# Controlled refusals
# --------------------------------------------------------------------------


class SweepRefusal(Exception):
    """Base controlled refusal.

    Refusals carry no path, digest, commit, filter value, or caught-exception
    text: the CLI maps each subclass to one fixed golden envelope.
    """

    exit_code = 4
    reason_category = "internal_error"
    reason = "register composition sweep unavailable after internal failure"


class BadInput(SweepRefusal):
    """Manifest, document, or CLI input failure."""

    exit_code = 2
    reason_category = "bad_input"
    reason = "register composition sweep refused invalid input"


class PolicyRefused(SweepRefusal):
    """H1 identity/contract, checkpoint/privacy/platform, or create-new failure."""

    exit_code = 3
    reason_category = "policy_refused"
    reason = "register composition sweep refused by policy"


class InternalError(SweepRefusal):
    """Violation in H2's already-validated in-memory construction."""


# --------------------------------------------------------------------------
# Canonical encoding core
# --------------------------------------------------------------------------


INT64_MIN = -(2**63)

#: Nesting ceiling for the closed-domain walk. Every canonical payload in the
#: spec is at most five levels deep; the bound also terminates a cyclic value
#: before it can be walked forever.
MAX_CANONICAL_DEPTH = 64


def _require_nfc(text: str) -> None:
    if unicodedata.normalize("NFC", text) != text:
        raise InternalError()


def _require_canonical_value(value: Any, depth: int) -> None:
    """Refuse anything outside the closed canonical JSON domain.

    Admissible: JSON null, Boolean, signed-64-bit non-Boolean integer, NFC
    string, array, and object with string NFC keys. Floats are forbidden
    everywhere, including inside arrays and nested objects.
    """
    if depth > MAX_CANONICAL_DEPTH:
        raise InternalError()
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not (INT64_MIN <= value <= INT64_MAX):
            raise InternalError()
        return
    if type(value) is str:
        _require_nfc(value)
        return
    if type(value) is list:
        for item in value:
            _require_canonical_value(item, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise InternalError()
            _require_nfc(key)
            _require_canonical_value(item, depth + 1)
        return
    raise InternalError()


def canonical_json(value: Any) -> bytes:
    """Return the exact canonical JSON encoding of a closed-domain value.

    Inputs are composed only of string keys, NFC valid Unicode strings, JSON
    null/Boolean, signed-64-bit non-Boolean integers, and arrays/objects whose
    order and key set the spec fixes. The whole value is walked against that
    closed domain before it is dumped: floats are forbidden in every canonical
    payload, every key and string must already be NFC, and every integer must
    fit in signed 64 bits. ``allow_nan=False`` additionally refuses non-finite
    values that reach ``json.dumps``.
    """
    _require_canonical_value(value, 0)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InternalError() from exc


def raw_sha256(data: bytes) -> str:
    """Return lowercase hex SHA-256 over exact bytes, with no prefix."""
    if type(data) is not bytes:
        raise InternalError()
    return hashlib.sha256(data).hexdigest()


def framed_sha256(domain: bytes, payload: bytes) -> str:
    """Return ``SHA256(domain || uint64_be(len(payload)) || payload)`` as hex.

    No NUL separator, extra newline, hex decoding, or Unicode normalization
    occurs inside this function. ``domain`` must be one of the twelve frozen
    domains: domain reuse with a different payload schema is forbidden, and an
    unfrozen domain has no schema at all.
    """
    if type(domain) is not bytes or type(payload) is not bytes:
        raise InternalError()
    if not domain.endswith(b"\n") or not domain.isascii():
        raise InternalError()
    if domain not in FROZEN_DOMAINS:
        raise InternalError()
    return hashlib.sha256(
        domain + struct.pack(">Q", len(payload)) + payload
    ).hexdigest()


def framed_object(domain: bytes, value: Any) -> str:
    """Frame a canonical object payload under ``domain``."""
    return framed_sha256(domain, canonical_json(value))


def prefixed(digest: str) -> str:
    """Return the runtime/report/checkpoint digest string form."""
    if type(digest) is not str or len(digest) != 64:
        raise InternalError()
    if any(char not in "0123456789abcdef" for char in digest):
        raise InternalError()
    return "sha256:" + digest


def _require_prefixed(value: Any) -> str:
    if type(value) is not str or not value.startswith("sha256:"):
        raise InternalError()
    body = value[len("sha256:") :]
    if len(body) != 64 or any(char not in "0123456789abcdef" for char in body):
        raise InternalError()
    return value


def _require_int(value: Any, low: int, high: int) -> int:
    if type(value) is not int or not (low <= value <= high):
        raise InternalError()
    return value


def _require_count(value: Any) -> int:
    """Require a non-Boolean unsigned 64-bit inventory/count scalar."""
    return _require_int(value, 0, INT64_MAX)


def _require_cell(cell: Any) -> None:
    """Require the fixed ``{"documents", "words"}`` inventory cell shape."""
    if type(cell) is not dict or set(cell) != {"documents", "words"}:
        raise InternalError()
    _require_count(cell["documents"])
    _require_count(cell["words"])


def _require_inventory(inventory: Any, domain: tuple[str, ...]) -> None:
    """Require an inventory object keyed by exactly ``domain``."""
    if type(inventory) is not dict or set(inventory) != set(domain):
        raise InternalError()
    for cell in inventory.values():
        _require_cell(cell)


# --------------------------------------------------------------------------
# Framed payload builders
#
# Each builder returns ``(payload_bytes, digest_hex)`` so callers and the
# normative vector test can pin the exact preimage as well as the digest.
# --------------------------------------------------------------------------


def projected_row_binding(row: dict[str, Any]) -> tuple[bytes, str]:
    """Frame one projected manifest row's seven owned fields."""
    payload = canonical_json(row)
    return payload, framed_sha256(DOMAIN_PROJECTED_ROW, payload)


def projected_manifest_binding(
    row_digests: tuple[str, ...] | list[str],
) -> tuple[bytes, str]:
    """Frame every projected row digest in ascending manifest ordinal.

    Rows later excluded by scope filters are included here: the projected
    manifest binds the whole parsed input, not the scoped slice.
    """
    rows = [_require_prefixed(item) for item in row_digests]
    payload = canonical_json({"rows": rows})
    return payload, framed_sha256(DOMAIN_PROJECTED_MANIFEST, payload)


def scope_binding(
    *,
    use: str | None,
    split: str | None,
    ai_status: str | None,
    persona: str | None,
    min_words: int,
) -> tuple[bytes, str]:
    """Frame the private scope binding.

    This is the only H2 identity that binds the raw persona filter. The raw
    value never reaches stdout, the report, a checkpoint, an exception, or a
    log; only this digest does.
    """
    _require_int(min_words, MIN_WORDS_FLOOR, MIN_WORDS_CEILING)
    payload = canonical_json(
        {
            "ai_status": ai_status,
            "min_words": min_words,
            "persona": persona,
            "privacy_policy": PRIVACY_POLICY,
            "split": split,
            "use": use,
        }
    )
    return payload, framed_sha256(DOMAIN_SCOPE, payload)


def scoped_rows_binding(
    entries: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> tuple[bytes, str]:
    """Frame the scoped-row slice in manifest order.

    ``scoped_ordinal`` is zero-based and contiguous; an empty scope frames
    ``{"rows":[]}``. Entries are in manifest order, so ``manifest_ordinal`` is
    strictly ascending across the slice.
    """
    rows = []
    previous_manifest = -1
    for expected, entry in enumerate(entries):
        if set(entry) != {
            "manifest_ordinal",
            "projected_row_sha256",
            "scoped_ordinal",
        }:
            raise InternalError()
        if entry["scoped_ordinal"] != expected:
            raise InternalError()
        _require_int(entry["scoped_ordinal"], 0, MAX_SCOPED_DOCUMENTS - 1)
        manifest_ordinal = _require_int(entry["manifest_ordinal"], 0, INT64_MAX)
        if manifest_ordinal <= previous_manifest:
            raise InternalError()
        previous_manifest = manifest_ordinal
        _require_prefixed(entry["projected_row_sha256"])
        rows.append(
            {
                "manifest_ordinal": entry["manifest_ordinal"],
                "projected_row_sha256": entry["projected_row_sha256"],
                "scoped_ordinal": entry["scoped_ordinal"],
            }
        )
    payload = canonical_json({"rows": rows})
    return payload, framed_sha256(DOMAIN_SCOPED_ROWS, payload)


def target_path_binding(absolute_path: os.PathLike[str] | str) -> tuple[bytes, str]:
    """Frame the raw ``os.fsencode`` bytes of a frozen absolute path."""
    payload = os.fsencode(absolute_path)
    return payload, framed_sha256(DOMAIN_TARGET_PATH, payload)


def posix_fingerprint_binding(
    fields: tuple[int, ...] | list[int],
) -> tuple[bytes, str]:
    """Frame a POSIX identity/mutation fingerprint.

    Field order is exactly ``[dev, ino, size, mtime_ns, ctime_ns]``.
    """
    values = [_require_int(item, 0, INT64_MAX) for item in fields]
    if len(values) != 5:
        raise InternalError()
    payload = canonical_json({"fields": values, "platform": "posix"})
    return payload, framed_sha256(DOMAIN_FILE_FINGERPRINT, payload)


def windows_fingerprint_binding(
    fields: tuple[int, ...] | list[int],
) -> tuple[bytes, str]:
    """Frame a native-Windows identity/mutation fingerprint.

    Field order is exactly ``[volume_serial, file_id, size, write_time,
    change_time, creation_time, mode, links, attributes]``. ``change_time`` is
    present so same-size content mutation with a restored LastWriteTime still
    refuses.
    """
    values = [_require_int(item, 0, INT64_MAX) for item in fields]
    if len(values) != 9:
        raise InternalError()
    payload = canonical_json({"fields": values, "platform": "windows"})
    return payload, framed_sha256(DOMAIN_FILE_FINGERPRINT, payload)


def document_plan_binding(
    entries: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> tuple[bytes, str]:
    """Frame the frozen document plan in ascending scoped ordinal."""
    documents = []
    for expected, entry in enumerate(entries):
        if set(entry) != {
            "candidate_index",
            "file_fingerprint_sha256",
            "projected_row_sha256",
            "scoped_ordinal",
            "target_path_sha256",
        }:
            raise InternalError()
        if entry["scoped_ordinal"] != expected:
            raise InternalError()
        _require_int(entry["scoped_ordinal"], 0, MAX_SCOPED_DOCUMENTS - 1)
        _require_int(entry["candidate_index"], 0, 2)
        _require_prefixed(entry["file_fingerprint_sha256"])
        _require_prefixed(entry["projected_row_sha256"])
        _require_prefixed(entry["target_path_sha256"])
        documents.append(
            {
                "candidate_index": entry["candidate_index"],
                "file_fingerprint_sha256": entry["file_fingerprint_sha256"],
                "projected_row_sha256": entry["projected_row_sha256"],
                "scoped_ordinal": entry["scoped_ordinal"],
                "target_path_sha256": entry["target_path_sha256"],
            }
        )
    payload = canonical_json({"documents": documents})
    return payload, framed_sha256(DOMAIN_DOCUMENT_PLAN, payload)


def checkpoint_binding(
    *,
    classifier_sha256: str,
    document_plan_sha256: str,
    h1_receipt_sha256: str,
    mapping_sha256: str,
    projected_manifest_sha256: str,
    refusal_contract_sha256: str,
    scope_sha256: str,
    scoped_rows_sha256: str,
) -> tuple[bytes, str]:
    """Frame the checkpoint binding.

    No completed ordinal belongs in the binding: progress is sealed by the
    shard hash chain, so the binding is a pure function of the run's inputs.
    """
    payload = canonical_json(
        {
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "classifier_sha256": _require_prefixed(classifier_sha256),
            "document_plan_sha256": _require_prefixed(document_plan_sha256),
            "h1_receipt_sha256": _require_prefixed(h1_receipt_sha256),
            "immutable_shard_contract_version": IMMUTABLE_SHARD_CONTRACT_VERSION,
            "limits": dict(LIMITS),
            "mapping_sha256": _require_prefixed(mapping_sha256),
            "privacy_policy": PRIVACY_POLICY,
            "projected_manifest_sha256": _require_prefixed(
                projected_manifest_sha256
            ),
            "refusal_contract_sha256": _require_prefixed(refusal_contract_sha256),
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "scope_sha256": _require_prefixed(scope_sha256),
            "scoped_rows_sha256": _require_prefixed(scoped_rows_sha256),
            "taxonomy": TAXONOMY,
            "tool": TOOL,
            "version": VERSION,
        }
    )
    return payload, framed_sha256(DOMAIN_CHECKPOINT_BINDING, payload)


def checkpoint_row_binding(row: dict[str, Any]) -> tuple[bytes, bytes]:
    """Return ``(row_json, row_sha256_blob)`` for one completed checkpoint row.

    ``row_sha256`` is exactly the 32 raw digest bytes; text or hex storage is
    invalid. No raw register, row id, path, fingerprint, prose, persona,
    evidence vector, warning, or free-text metadata is stored.

    ``declared_family`` is in ``D``, ``classified_family`` is in ``F`` or null,
    and ``refusal_reason`` is in ``R`` or null, with exactly one of the latter
    two non-null. ``unknown`` is a declared value only: it is never a
    classified family.
    """
    if set(row) != {
        "manifest_ordinal",
        "projected_row_sha256",
        "content_sha256",
        "document_bytes",
        "words",
        "declared_family",
        "classified_family",
        "refusal_reason",
    }:
        raise InternalError()
    _require_int(row["manifest_ordinal"], 0, INT64_MAX)
    _require_prefixed(row["projected_row_sha256"])
    _require_prefixed(row["content_sha256"])
    _require_int(row["document_bytes"], 0, MAX_DOCUMENT_BYTES)
    _require_int(row["words"], 0, INT64_MAX)
    if row["declared_family"] not in H1_DECLARED_FAMILIES:
        raise InternalError()
    classified = row["classified_family"]
    refusal = row["refusal_reason"]
    if classified is not None and classified not in H1_CLASSIFIED_FAMILIES:
        raise InternalError()
    if refusal is not None and refusal not in H1_REFUSAL_REASONS:
        raise InternalError()
    if (classified is None) == (refusal is None):
        raise InternalError()
    row_json = canonical_json(row)
    return row_json, hashlib.sha256(row_json).digest()


def aggregate_delta_binding(delta: dict[str, Any]) -> tuple[bytes, str]:
    """Frame the reassembled six-key aggregate delta object.

    This is a structural and value-domain check only: the exact key sets of
    ``counts`` and of the five fixed inventories, and the closed cell shape and
    unsigned-64-bit integer domain of every leaf. The cross-cell equations
    (row sums, per-shard document ceilings, match-bucket derivation) belong to
    the runner increment that produces the deltas, not to this encoder.
    """
    if set(delta) != {
        "counts",
        "declared_family_inventory",
        "classified_family_inventory",
        "declared_by_classified_family",
        "refusal_inventory",
        "match_inventory",
    }:
        raise InternalError()
    counts = delta["counts"]
    if type(counts) is not dict or set(counts) != SHARD_COUNT_KEYS:
        raise InternalError()
    for value in counts.values():
        _require_count(value)
    _require_inventory(delta["declared_family_inventory"], H1_DECLARED_FAMILIES)
    _require_inventory(
        delta["classified_family_inventory"], H1_CLASSIFIED_FAMILIES
    )
    crosstab = delta["declared_by_classified_family"]
    if type(crosstab) is not dict or set(crosstab) != set(H1_DECLARED_FAMILIES):
        raise InternalError()
    for inner in crosstab.values():
        _require_inventory(inner, H1_CLASSIFIED_FAMILIES)
    _require_inventory(delta["refusal_inventory"], H1_REFUSAL_REASONS)
    _require_inventory(delta["match_inventory"], MATCH_DOMAIN)
    payload = canonical_json(delta)
    return payload, framed_sha256(DOMAIN_AGGREGATE_DELTA, payload)


def shard_binding(
    *,
    aggregate_delta_sha256: str,
    metadata: dict[str, Any],
    rows: tuple[dict[str, Any], ...] | list[dict[str, Any]],
) -> tuple[bytes, str]:
    """Frame the logical shard payload.

    The hash binds logical content and makes no claim about cross-runtime
    SQLite byte determinism.

    Every metadata value is domain-checked, not merely present: the binding
    digest is prefixed, ``prior_shard_sha256`` is null or prefixed,
    ``shard_number`` is in ``[0, 399]``, ``first_scoped_ordinal`` is in
    ``[0, 99_999]``, ``next_scoped_ordinal`` is in ``[1, 100_000]`` and exceeds
    the first ordinal by ``[1, 250]``, and ``kind``/``schema_version`` are the
    frozen literals. The row list carries ``[1, 250]`` rows whose scoped
    ordinals are contiguous from ``first_scoped_ordinal``, and
    ``next_scoped_ordinal`` is exactly one past the last of them; a shard with
    no rows, a gap, or a next ordinal that disagrees with the row count cannot
    be framed. An empty scope creates no shard at all.
    """
    if set(metadata) != {
        "checkpoint_binding_sha256",
        "first_scoped_ordinal",
        "kind",
        "next_scoped_ordinal",
        "prior_shard_sha256",
        "schema_version",
        "shard_number",
    }:
        raise InternalError()
    binding_digest = _require_prefixed(metadata["checkpoint_binding_sha256"])
    prior = metadata["prior_shard_sha256"]
    if prior is not None:
        _require_prefixed(prior)
    shard_number = _require_int(
        metadata["shard_number"], 0, MAX_FINAL_SHARDS - 1
    )
    first = _require_int(
        metadata["first_scoped_ordinal"], 0, MAX_SCOPED_DOCUMENTS - 1
    )
    following = _require_int(
        metadata["next_scoped_ordinal"], 1, MAX_SCOPED_DOCUMENTS
    )
    # This bound, the row-count bound, and the ``following == first + len``
    # equality below are each a separate spec clause (metadata domain, shard
    # size, and shard/row agreement), and any two of them imply the third.
    # They are kept separate so each clause has its own refusal site.
    if not (1 <= following - first <= SHARD_ROWS):
        raise InternalError()
    if metadata["kind"] != CHECKPOINT_KIND:
        raise InternalError()
    if metadata["schema_version"] != CHECKPOINT_SCHEMA_VERSION:
        raise InternalError()

    ordered_rows = []
    previous = -1
    for offset, entry in enumerate(rows):
        if set(entry) != {"row_json_sha256", "scoped_ordinal"}:
            raise InternalError()
        ordinal = _require_int(
            entry["scoped_ordinal"], 0, MAX_SCOPED_DOCUMENTS - 1
        )
        if ordinal != first + offset or ordinal <= previous:
            raise InternalError()
        previous = ordinal
        ordered_rows.append(
            {
                "row_json_sha256": _require_prefixed(entry["row_json_sha256"]),
                "scoped_ordinal": ordinal,
            }
        )
    if not (1 <= len(ordered_rows) <= SHARD_ROWS):
        raise InternalError()
    if following != first + len(ordered_rows):
        raise InternalError()

    payload = canonical_json(
        {
            "aggregate_delta_sha256": _require_prefixed(aggregate_delta_sha256),
            "metadata": {
                "checkpoint_binding_sha256": binding_digest,
                "first_scoped_ordinal": first,
                "kind": CHECKPOINT_KIND,
                "next_scoped_ordinal": following,
                "prior_shard_sha256": prior,
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "shard_number": shard_number,
            },
            "rows": ordered_rows,
        }
    )
    return payload, framed_sha256(DOMAIN_SHARD, payload)


# --------------------------------------------------------------------------
# Zero-filled fixed inventory domains
# --------------------------------------------------------------------------


def zero_cell() -> dict[str, int]:
    """Return the fixed zero cell. Every inventory cell has exactly this shape."""
    return {"documents": 0, "words": 0}


def zero_inventories(
    declared: tuple[str, ...],
    classified: tuple[str, ...],
    refusals: tuple[str, ...],
) -> dict[str, Any]:
    """Return the five zero-filled fixed inventory domains.

    There are no percentages, rates, shares, entropies, effective-mode counts,
    thresholds, bands, ranks, dominant-family labels, or mixture flags: these
    are count buckets only.
    """
    return {
        "declared_family_inventory": {name: zero_cell() for name in declared},
        "classified_family_inventory": {name: zero_cell() for name in classified},
        "declared_by_classified_family": {
            outer: {inner: zero_cell() for inner in classified}
            for outer in declared
        },
        "refusal_inventory": {name: zero_cell() for name in refusals},
        "match_inventory": {name: zero_cell() for name in MATCH_DOMAIN},
    }


# --------------------------------------------------------------------------
# Strict H1 receipt read
# --------------------------------------------------------------------------


def _decode_strict_json(data: bytes) -> Any:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise PolicyRefused()
            out[key] = value
        return out

    def reject_constant(_: str) -> None:
        raise PolicyRefused()

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise PolicyRefused() from exc
    if text.startswith("﻿"):
        raise PolicyRefused()
    try:
        return json.loads(
            text, object_pairs_hook=pairs_hook, parse_constant=reject_constant
        )
    except (json.JSONDecodeError, RecursionError, ValueError, TypeError) as exc:
        raise PolicyRefused() from exc


def _valid_receipt_string(value: Any) -> str:
    if type(value) is not str or unicodedata.normalize("NFC", value) != value:
        raise PolicyRefused()
    if len(value.encode("utf-8")) > 131_072:
        raise PolicyRefused()
    for char in value:
        code = ord(char)
        if (
            code == 0
            or code < 0x20
            or 0x7F <= code <= 0x9F
            or 0xD800 <= code <= 0xDFFF
            or char in BIDI_CONTROLS
        ):
            raise PolicyRefused()
    return value


def _guard_receipt_tree(root: Any) -> None:
    stack: list[tuple[Any, int]] = [(root, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > 100_000 or depth > 64:
            raise PolicyRefused()
        if type(value) is str:
            _valid_receipt_string(value)
        elif type(value) is dict:
            for key, child in value.items():
                _valid_receipt_string(key)
                nodes += 1
                if nodes > 100_000:
                    raise PolicyRefused()
                stack.append((child, depth + 1))
        elif type(value) is list:
            stack.extend((child, depth + 1) for child in value)
        elif value is None or type(value) in (bool, int, float):
            if type(value) is float and not math.isfinite(value):
                raise PolicyRefused()
        else:
            raise PolicyRefused()


def read_h1_receipt(path: os.PathLike[str] | str) -> tuple[dict[str, Any], bytes]:
    """Read and canonically verify the H1 closeout receipt exactly once.

    Reads at most :data:`MAX_H1_RECEIPT_BYTES` from a direct non-symlink
    regular file, decodes strict UTF-8 with no BOM, parses with duplicate-key
    and non-finite rejection, and requires the input bytes to equal the
    canonical re-encoding plus one terminal LF.
    """
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if os.name == "posix" and not nofollow:
        raise PolicyRefused()
    descriptor = -1
    try:
        named = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(named.st_mode) or getattr(named, "st_reparse_tag", 0):
            raise PolicyRefused()
        descriptor = os.open(path, flags | nofollow)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (
            int(named.st_dev),
            int(named.st_ino),
        ) != (int(info.st_dev), int(info.st_ino)):
            raise PolicyRefused()
        data = os.read(descriptor, MAX_H1_RECEIPT_BYTES + 1)
        if len(data) > MAX_H1_RECEIPT_BYTES or os.read(descriptor, 1):
            raise PolicyRefused()
    except (OSError, ValueError, MemoryError) as exc:
        raise PolicyRefused() from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    value = _decode_strict_json(data)
    _guard_receipt_tree(value)
    if type(value) is not dict:
        raise PolicyRefused()
    # A receipt that cannot even be canonically encoded (a float, an
    # out-of-range integer, a non-NFC string) is an H1-identity failure, not an
    # H2 internal failure: re-raise it into the identity taxonomy.
    try:
        canonical = canonical_json(value)
    except InternalError as exc:
        raise PolicyRefused() from exc
    if data != canonical + b"\n":
        raise PolicyRefused()
    return value, data


_RECEIPT_TOP_KEYS = frozenset(
    {
        "schema_version",
        "landed_commit",
        "spec_review",
        "implementation_review",
        "refusal_spec_review",
        "refusal_implementation_review",
        "ci",
        "spec_sha256",
        "refusal_spec_path",
        "refusal_spec_sha256",
        "base_classifier_sha256",
        "classifier_sha256",
        "mapping_sha256",
        "refusal_contract_sha256",
        "taxonomy",
    }
)
_RECEIPT_REVIEW_KEYS = frozenset({"reviewed_head", "verdict"})
_RECEIPT_CI_KEYS = frozenset(
    {
        "attempt",
        "branch",
        "event",
        "head",
        "required_jobs",
        "result",
        "run_id",
        "workflow_name",
        "workflow_path",
        "workflow_sha256",
    }
)
_RECEIPT_HEX_FIELDS = (
    "spec_sha256",
    "refusal_spec_sha256",
    "base_classifier_sha256",
    "classifier_sha256",
    "mapping_sha256",
    "refusal_contract_sha256",
)


def _hex_field(value: Any, length: int) -> str:
    if type(value) is not str or len(value) != length:
        raise PolicyRefused()
    if any(char not in "0123456789abcdef" for char in value):
        raise PolicyRefused()
    return value


def validate_h1_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Validate the complete ``setec-h1-landing-receipt/2`` schema.

    Every absent, extra, or wrong-type field refuses. H2 does not infer review
    status from Git history or runtime behavior: ``verdict`` is a human
    repository-governance attestation whose exact artifact bytes the CI-side
    checker already verified.
    """
    if set(receipt) != _RECEIPT_TOP_KEYS:
        raise PolicyRefused()
    if receipt["schema_version"] != H1_RECEIPT_SCHEMA_VERSION:
        raise PolicyRefused()
    _hex_field(receipt["landed_commit"], 40)
    for key in _RECEIPT_HEX_FIELDS:
        _hex_field(receipt[key], 64)
    if receipt["landed_commit"] != H1_LANDED_COMMIT:
        raise PolicyRefused()
    if receipt["spec_sha256"] != H1_SPEC_SHA256:
        raise PolicyRefused()
    if receipt["refusal_spec_path"] != H1_REFUSAL_SPEC_PATH:
        raise PolicyRefused()
    if receipt["refusal_spec_sha256"] != H1_REFUSAL_SPEC_SHA256:
        raise PolicyRefused()
    if receipt["base_classifier_sha256"] != H1_BASE_CLASSIFIER_SHA256:
        raise PolicyRefused()
    # Parity with the CI-side gate: the receipt's three H1 identity digests are
    # pinned to the same constants that checker holds, so a receipt that agrees
    # only with itself (regenerated around a drifted classifier) refuses here.
    if receipt["classifier_sha256"] != H1_FINAL_CLASSIFIER_SHA256:
        raise PolicyRefused()
    if receipt["mapping_sha256"] != H1_MAPPING_SHA256:
        raise PolicyRefused()
    if receipt["refusal_contract_sha256"] != H1_REFUSAL_CONTRACT_SHA256:
        raise PolicyRefused()
    if receipt["taxonomy"] != TAXONOMY:
        raise PolicyRefused()

    for key in (
        "spec_review",
        "implementation_review",
        "refusal_spec_review",
        "refusal_implementation_review",
    ):
        review = receipt[key]
        if type(review) is not dict or set(review) != _RECEIPT_REVIEW_KEYS:
            raise PolicyRefused()
        _hex_field(review["reviewed_head"], 40)
        if review["verdict"] != "READY":
            raise PolicyRefused()

    ci = receipt["ci"]
    if type(ci) is not dict or set(ci) != _RECEIPT_CI_KEYS:
        raise PolicyRefused()
    for key in ("run_id", "attempt"):
        if type(ci[key]) is not int or not (1 <= ci[key] <= INT64_MAX):
            raise PolicyRefused()
    _hex_field(ci["head"], 40)
    _hex_field(ci["workflow_sha256"], 64)
    if type(ci["required_jobs"]) is not list:
        raise PolicyRefused()
    if (
        ci["branch"] != "main"
        or ci["event"] != "push"
        or ci["result"] != "PASS"
        or ci["workflow_name"] != "tests"
        or ci["workflow_path"] != H1_WORKFLOW_PATH
        or tuple(ci["required_jobs"]) != H1_REQUIRED_JOBS
        or ci["workflow_sha256"] not in H1_WORKFLOW_SHA256_ALLOWLIST
        or ci["head"] != receipt["landed_commit"]
    ):
        raise PolicyRefused()
    return receipt


# --------------------------------------------------------------------------
# Receipt-bound H1 classifier
# --------------------------------------------------------------------------


class H1Binding:
    """The receipt-bound H1 namespace and its verified public identities.

    The classifier's exact source bytes are compiled and executed in a private
    module namespace. H2 consumes only the receipt-bound public symbols and
    never inspects ``_SCORERS``, decodes warning prose, or reconstructs H1
    behavior.
    """

    __slots__ = (
        "namespace",
        "receipt",
        "receipt_sha256",
        "classifier_sha256",
        "mapping_sha256",
        "refusal_contract_sha256",
        "families",
        "declared_domain",
        "classified_domain",
        "refusal_domain",
    )

    def __init__(
        self,
        *,
        namespace: dict[str, Any],
        receipt: dict[str, Any],
        receipt_sha256: str,
        classifier_sha256: str,
        mapping_sha256: str,
        refusal_contract_sha256: str,
    ) -> None:
        self.namespace = namespace
        self.receipt = receipt
        self.receipt_sha256 = receipt_sha256
        self.classifier_sha256 = classifier_sha256
        self.mapping_sha256 = mapping_sha256
        self.refusal_contract_sha256 = refusal_contract_sha256
        # The value domains are the receipt-pinned H1 identity, not whatever
        # the bound namespace happens to export. Deriving ``D`` from the
        # namespace and then validating against that same namespace would be
        # circular, so the tuples are equality-pinned to the module constants
        # first; this is also the spec's ``D == REGISTER_FAMILIES`` freeze.
        families = namespace.get("REGISTER_FAMILIES")
        if type(families) is not tuple or families != H1_REGISTER_FAMILIES:
            raise PolicyRefused()
        refusals = namespace.get("REGISTER_REFUSAL_REASONS")
        if type(refusals) is not tuple or refusals != H1_REFUSAL_REASONS:
            raise PolicyRefused()
        self.families = H1_REGISTER_FAMILIES
        self.classified_domain = H1_CLASSIFIED_FAMILIES
        self.declared_domain = H1_DECLARED_FAMILIES
        self.refusal_domain = H1_REFUSAL_REASONS

    def resolve_family(self, value: str | None) -> str:
        """Resolve declared manifest metadata through H1's public mapping."""
        try:
            resolved = self.namespace["resolve_family"](value)
        except Exception as exc:  # noqa: BLE001 - closed controlled refusal
            raise PolicyRefused() from exc
        if type(resolved) is not str or resolved not in self.declared_domain:
            raise PolicyRefused()
        return resolved

    def classify(self, text: str, *, min_words: int) -> dict[str, Any]:
        """Call H1 with no hint and validate its complete public result."""
        try:
            result = self.namespace["classify_register"](
                text, min_words=min_words
            )
        except Exception as exc:  # noqa: BLE001 - closed controlled refusal
            raise PolicyRefused() from exc
        return self.validate_classification(result, min_words=min_words)

    def validate_classification(
        self, result: Any, *, min_words: int
    ) -> dict[str, Any]:
        """Validate the closed eight-key H1 result and refusal biconditional.

        H2 validates the complete public result rather than silently ignoring
        fields, but recomputes no score, rank, threshold, tie, or secondary
        band, and never branches on ``warning`` prose.
        """
        classified = self.classified_domain
        refusals = self.refusal_domain
        if type(result) is not dict or set(result) != CLASSIFICATION_KEYS:
            raise PolicyRefused()
        if result["taxonomy"] != TAXONOMY:
            raise PolicyRefused()

        primary = result["primary"]
        if type(primary) is not str or primary not in (
            *classified,
            "unknown",
        ):
            raise PolicyRefused()

        reason = result["refusal_reason"]
        if reason is not None and (
            type(reason) is not str or reason not in refusals
        ):
            raise PolicyRefused()
        if (primary == "unknown") != (reason is not None):
            raise PolicyRefused()

        confidence = result["confidence"]
        if (
            type(confidence) is not float
            or not math.isfinite(confidence)
            or not (0.0 <= confidence <= 1.0)
        ):
            raise PolicyRefused()

        secondary = result["secondary"]
        if type(secondary) is not list or len(secondary) > MAX_SECONDARY:
            raise PolicyRefused()
        seen: set[str] = set()
        for item in secondary:
            if type(item) is not str or item not in classified or item in seen:
                raise PolicyRefused()
            seen.add(item)
        if primary in classified and primary in seen:
            raise PolicyRefused()

        warning = result["warning"]
        if warning is not None:
            if type(warning) is not str:
                raise PolicyRefused()
            try:
                encoded = warning.encode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise PolicyRefused() from exc
            if len(encoded) > MAX_WARNING_BYTES:
                raise PolicyRefused()

        evidence = self._validate_evidence(result["evidence"])
        self._validate_scores(
            result["scores"],
            reason=reason,
            n_words=evidence["n_words"],
            min_words=min_words,
        )
        return result

    def _validate_evidence(self, evidence: Any) -> dict[str, Any]:
        if type(evidence) is not dict:
            raise PolicyRefused()
        keys = set(evidence)
        if keys == set(ZERO_WORD_EVIDENCE_KEYS):
            expected_zero_word = True
        elif keys == set(FULL_EVIDENCE_KEYS):
            expected_zero_word = False
        else:
            raise PolicyRefused()

        for key in EVIDENCE_INTEGER_KEYS & keys:
            value = evidence[key]
            if type(value) is not int or not (0 <= value <= INT64_MAX):
                raise PolicyRefused()
        for key in keys - EVIDENCE_INTEGER_KEYS:
            value = evidence[key]
            if (
                type(value) is not float
                or not math.isfinite(value)
                or value < 0.0
            ):
                raise PolicyRefused()
        if "dialogue_ratio" in keys and evidence["dialogue_ratio"] > 1.0:
            raise PolicyRefused()

        if expected_zero_word:
            # Only ``n_words`` is pinned here. Whitespace- or punctuation-only
            # input legitimately reports zero words with a nonzero character
            # count, so ``n_chars`` is bounded but not forced to zero; the
            # spec's "n_chars may be zero only in the zero-word shape" clause
            # constrains the full shape below, not this one.
            if evidence["n_words"] != 0:
                raise PolicyRefused()
        else:
            if (
                evidence["n_words"] < 1
                or evidence["n_sentences"] < 1
                or evidence["n_paragraphs"] < 1
                or evidence["n_chars"] < 1
            ):
                raise PolicyRefused()
        return evidence

    def _validate_scores(
        self,
        scores: Any,
        *,
        reason: str | None,
        n_words: int,
        min_words: int,
    ) -> None:
        if type(scores) is not dict:
            raise PolicyRefused()
        if reason == "short_text":
            if scores != {}:
                raise PolicyRefused()
            if n_words >= min_words:
                raise PolicyRefused()
            return
        if set(scores) != set(self.classified_domain):
            raise PolicyRefused()
        if n_words < min_words:
            raise PolicyRefused()
        for value in scores.values():
            if (
                type(value) is not float
                or not math.isfinite(value)
                or not (0.0 <= value <= 1.0)
            ):
                raise PolicyRefused()


def mapping_binding(namespace: dict[str, Any]) -> tuple[bytes, str]:
    """Frame the public register-family mapping from the H1 namespace.

    The payload has exactly ``canonical_register_to_family``,
    ``legacy_register_to_family``, ``register_families`` in public tuple order,
    and ``taxonomy``. At the landed H1 identity its canonical length is exactly
    1,147 bytes; the CI-side gate pins the same length, so any mapping that
    encodes to a different size refuses before it is framed.
    """
    families = namespace.get("REGISTER_FAMILIES")
    canonical = namespace.get("CANONICAL_REGISTER_TO_FAMILY")
    legacy = namespace.get("LEGACY_REGISTER_TO_FAMILY")
    if (
        namespace.get("REGISTER_TAXONOMY") != TAXONOMY
        or type(families) is not tuple
        or type(canonical) is not dict
        or type(legacy) is not dict
        or namespace.get("KNOWN_REGISTERS") != families
        or any(type(item) is not str for item in families)
        or len(set(families)) != len(families)
        or "unknown" not in families
        or any(
            type(key) is not str or type(val) is not str
            for key, val in canonical.items()
        )
        or any(
            type(key) is not str or type(val) is not str
            for key, val in legacy.items()
        )
        or any(
            val not in families
            for val in (*canonical.values(), *legacy.values())
        )
    ):
        raise PolicyRefused()
    payload = canonical_json(
        {
            "canonical_register_to_family": canonical,
            "legacy_register_to_family": legacy,
            "register_families": list(families),
            "taxonomy": TAXONOMY,
        }
    )
    if len(payload) != 1_147:
        raise PolicyRefused()
    return payload, framed_sha256(DOMAIN_MAPPING, payload)


REFUSAL_CONTRACT_PAYLOAD = (
    b'{"field":"refusal_reason","null_when":"scored_family","reasons":'
    b'["short_text","all_weak","exact_top_tie"],'
    b'"taxonomy":"register_families/v2"}'
)


def refusal_contract_binding(namespace: dict[str, Any]) -> tuple[bytes, str]:
    """Frame the public refusal contract from the H1 namespace.

    The payload is constructed from the exported tuple, field/null rule, and
    taxonomy, then equality-checked against the exact 140 spec bytes before
    hashing. Copying the expected digest without constructing the payload is
    invalid.
    """
    reasons = namespace.get("REGISTER_REFUSAL_REASONS")
    if reasons != H1_REFUSAL_REASONS:
        raise PolicyRefused()
    payload = canonical_json(
        {
            "field": "refusal_reason",
            "null_when": "scored_family",
            "reasons": list(reasons),
            "taxonomy": TAXONOMY,
        }
    )
    if payload != REFUSAL_CONTRACT_PAYLOAD or len(payload) != 140:
        raise PolicyRefused()
    return payload, framed_sha256(DOMAIN_REFUSAL_CONTRACT, payload)


def _validate_h1_callables(namespace: dict[str, Any]) -> None:
    """Exact-validate the receipt-bound callable names, kinds, and defaults."""
    import inspect

    for name in H1_PUBLIC_SYMBOLS:
        if name not in namespace:
            raise PolicyRefused()

    resolve = namespace["resolve_family"]
    classify = namespace["classify_register"]
    if not callable(resolve) or not callable(classify):
        raise PolicyRefused()
    try:
        resolve_params = list(inspect.signature(resolve).parameters.values())
        classify_params = list(inspect.signature(classify).parameters.values())
    except (TypeError, ValueError) as exc:
        raise PolicyRefused() from exc

    if len(resolve_params) != 1:
        raise PolicyRefused()
    only = resolve_params[0]
    if (
        only.name != "value"
        or only.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
        or only.default is not inspect.Parameter.empty
    ):
        raise PolicyRefused()

    expected = (
        ("text", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
        ("hint", inspect.Parameter.KEYWORD_ONLY, None),
        ("min_words", inspect.Parameter.KEYWORD_ONLY, 100),
    )
    if len(classify_params) != len(expected):
        raise PolicyRefused()
    for param, (name, kind, default) in zip(classify_params, expected):
        if param.name != name or param.kind is not kind:
            raise PolicyRefused()
        if default is inspect.Parameter.empty:
            if param.default is not inspect.Parameter.empty:
                raise PolicyRefused()
        elif type(param.default) is not type(default) or param.default != default:
            raise PolicyRefused()


def load_h1_binding(
    *,
    receipt_path: os.PathLike[str] | str,
    classifier_path: os.PathLike[str] | str,
    expected_receipt_sha256: str = H1_RECEIPT_SHA256,
) -> H1Binding:
    """Bind the H1 classifier to the committed closeout receipt.

    Order is fixed:

    1. strict-read the receipt and check its pinned raw digest and its exact
       schema, which equality-pins ``classifier_sha256``, ``mapping_sha256``,
       and ``refusal_contract_sha256`` to the frozen module constants;
    2. read the expected sibling classifier source once under the 1 MiB ceiling
       and check its raw digest against the receipt's post-follow-on
       ``classifier_sha256``. That raw-digest check is the only gate on
       execution: nothing is compiled or executed until it passes;
    3. compile and execute those exact source bytes in a private namespace and
       validate the receipt-bound callables; and
    4. immediately after execution and before any classifier call, derive the
       public mapping and refusal-contract digests *from* the executed
       namespace and check them against the receipt.

    The mapping and refusal-contract digests cannot gate the execution, because
    they are computed from the executed namespace. What they gate is every
    later use of the binding.
    """
    receipt, receipt_bytes = read_h1_receipt(receipt_path)
    receipt_digest = raw_sha256(receipt_bytes)
    if receipt_digest != expected_receipt_sha256:
        raise PolicyRefused()
    validate_h1_receipt(receipt)

    source = _read_classifier_source(classifier_path)
    classifier_digest = raw_sha256(source)
    if classifier_digest != receipt["classifier_sha256"]:
        raise PolicyRefused()

    namespace = _execute_classifier(source, classifier_path)
    _validate_h1_callables(namespace)

    _, mapping_digest = mapping_binding(namespace)
    if mapping_digest != receipt["mapping_sha256"]:
        raise PolicyRefused()
    _, refusal_digest = refusal_contract_binding(namespace)
    if refusal_digest != receipt["refusal_contract_sha256"]:
        raise PolicyRefused()

    return H1Binding(
        namespace=namespace,
        receipt=receipt,
        receipt_sha256=receipt_digest,
        classifier_sha256=classifier_digest,
        mapping_sha256=mapping_digest,
        refusal_contract_sha256=refusal_digest,
    )


def _read_classifier_source(path: os.PathLike[str] | str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if os.name == "posix" and not nofollow:
        raise PolicyRefused()
    descriptor = -1
    try:
        named = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(named.st_mode) or getattr(named, "st_reparse_tag", 0):
            raise PolicyRefused()
        descriptor = os.open(path, flags | nofollow)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (
            int(named.st_dev),
            int(named.st_ino),
        ) != (int(info.st_dev), int(info.st_ino)):
            raise PolicyRefused()
        data = os.read(descriptor, MAX_CLASSIFIER_SOURCE_BYTES + 1)
        if len(data) > MAX_CLASSIFIER_SOURCE_BYTES or os.read(descriptor, 1):
            raise PolicyRefused()
    except (OSError, ValueError, MemoryError) as exc:
        raise PolicyRefused() from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return data


def _execute_classifier(
    source: bytes, path: os.PathLike[str] | str
) -> dict[str, Any]:
    try:
        text = source.decode("utf-8", errors="strict")
        namespace: dict[str, Any] = {"__name__": "_h2_bound_register_classifier"}
        exec(compile(text, os.fspath(path), "exec"), namespace, namespace)
    except Exception as exc:  # noqa: BLE001 - closed controlled refusal
        raise PolicyRefused() from exc
    return namespace


def default_h1_paths() -> tuple[Path, Path]:
    """Return ``(receipt_path, classifier_path)`` for this script's plugin.

    The receipt is the plugin's ``references/`` copy; the classifier is the
    sibling module in ``scripts/``. The return order matches the keyword order
    of :func:`load_h1_binding`.
    """
    scripts = Path(__file__).resolve().parent
    classifier = scripts / CLASSIFIER_FILENAME
    receipt = scripts.parent / "references" / RECEIPT_FILENAME
    return receipt, classifier
